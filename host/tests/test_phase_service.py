import asyncio
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from livolod.config import load, parse
from livolod.protocol import decode
from livolod.service import Service
from livolod.simulator import Simulator
from livolod.storage import Store
from livolod.uart import CommandError, LinkError

EXAMPLE = Path(__file__).parents[1] / "config.example.json"


class PhaseServiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        """Prepare isolated state and asynchronous collaborators for the next test."""
        self.temp = tempfile.TemporaryDirectory()
        self.config = replace(
            load(EXAMPLE),
            output_mode="experimental_phase",
            database=self.temp.name + "/state.db",
        )
        self.store = Store(self.config.database, "network")
        self.service = Service(self.config, self.store, gateway_factory=Simulator)
        self.service.gateway = Simulator()
        self.tasks = []

    async def asyncTearDown(self):
        """Stop asynchronous collaborators and release temporary test state."""
        for task in self.tasks:
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        if self.service.gateway:
            await self.service.gateway.close()
        self.store.close()
        self.temp.cleanup()

    async def start(self):
        """Start the test service or broker collaborator for this scenario."""
        await self.service.restore()
        self.tasks.append(asyncio.create_task(self.service.events()))

    async def control(self, name):
        """Submit an output command through the configured phase-mode test service.

        @param name Unique suffix used for the test command identifier.
        """
        self.store.begin(name, dict(action="set", device="46715621", mask=1, value=1))
        await self.service.set_outputs(name, "46715621", 1, 1)
        return self.store.command_result(name)

    async def test_mcu_output_retained_confirmation_updates_state_and_drains_slot(self):
        """A retained radio confirmation must reach MQTT state and release its slot."""
        from unittest.mock import AsyncMock, Mock
        from test_daemon import report

        await self.start()
        await asyncio.sleep(0.06)
        context = {}

        async def start_output(raw, mask, baseline):
            """Capture the output transaction for a matching retained confirmation.

            @param raw Requested output frame including the allocated token.
            @param mask Registry-authorized output bits.
            @param baseline Previous state, counter and token bytes.
            """
            context.update(raw=raw, mask=mask, baseline=baseline)
            return 9

        async def result(*args, **kwargs):
            """Return a successful retained report matching the captured request.

            @param args Scheduler result lookup positional arguments.
            @param kwargs Scheduler output validation options.
            """
            raw = context["raw"]
            return dict(
                state="done",
                outcome="1",
                attempts="1",
                sent="1",
                grid_skips="0",
                tx_result="0",
                entry_ticks="1",
                return_ticks="2",
                anchor_ticks="0",
                anchor_gen="1",
                age_ticks="1",
                raw=raw.hex(),
                report=report(bits=1, mask=1, counter=2, token=raw[36]).split()[-1],
                rx_sequence="100",
                rssi="-40",
                lqi="60",
            )

        scheduler = Mock(output_supported=True)
        scheduler.start_output = AsyncMock(side_effect=start_output)
        scheduler.query_result = AsyncMock(side_effect=result)
        scheduler.drop_query = AsyncMock()
        self.service.scheduler = scheduler
        self.service.phase_client = Mock(validate_reply=Mock(return_value={}))
        answer = await self.control("mcu-output")
        self.assertEqual(answer["status"], "confirmed")
        self.assertEqual(answer["state"]["state_bits"], 1)
        self.assertEqual(answer["output_attempts"], 1)
        self.assertEqual(self.service.states["46715621"]["state_bits"], 1)
        self.assertEqual(context["mask"], 3)
        self.assertEqual(len(context["baseline"]), 3)
        scheduler.drop_query.assert_awaited_once_with(9)
        self.assertFalse(
            any(c.startswith("SEND_PHASE ") for c in self.service.gateway.commands)
        )

    async def test_phase_queries_keep_round_bound_and_stop_on_fresh_state(self):
        """Stamp query pairs, exhaust three rounds, and stop early on a fresh report."""
        await self.start()
        await asyncio.sleep(0.06)  # The ARM report must predate the refresh.
        original = self.service.gateway.command
        attempts = []
        confirm_at = None

        async def command(text, retries=1):
            """Record actual stamped bytes and optionally publish fresh state.

            @param text UART command to execute.
            @param retries UART retry allowance.
            """
            reply = await original(text, retries)
            if text.startswith("SEND_PHASE "):
                attempts.append(bytes.fromhex(reply.split("raw=")[1]))
                if len(attempts) == confirm_at:
                    self.service.gateway.current_state("46715621")
                    await asyncio.sleep(0)
            return reply

        self.service.gateway.command = command
        self.service.gateway.commands.clear()
        for name, stop_at, expected in (("silent", None, 6), ("fresh", 5, 5)):
            attempts.clear()
            confirm_at = stop_at
            self.store.begin(name, dict(action="refresh", device="46715621"))
            with (
                patch("livolod.service.REFRESH_QUERY_INTERVAL", 0.001),
                patch("livolod.service.REFRESH_RESPONSE_WAIT", 0.005),
            ):
                await self.service.refresh_state(name, "46715621")
            result = self.store.command_result(name)
            self.assertEqual(
                result["status"], "confirmed" if stop_at else "unconfirmed"
            )
            self.assertEqual(
                (result["query_rounds"], result["queries_sent"]), (3, expected)
            )
            self.assertEqual(
                [decode(p).opcode for p in attempts],
                [0x10, 0xAC, 0x10, 0xAC, 0x10, 0xAC][:expected],
            )
            self.assertTrue(
                all(
                    decode(p).data == bytes(7) + b"\x01" and p[-3:] != bytes(3)
                    for p in attempts
                )
            )
        self.assertFalse(
            any(c.startswith("SEND ") for c in self.service.gateway.commands)
        )

    async def test_query_reference_loss_and_uncertain_send_never_fall_back(self):
        """Missing phase or uncertain TX cannot cause a raw query or unbounded replay."""
        await self.start()
        await asyncio.sleep(0.06)
        original = self.service.gateway.command
        attempts = []

        async def command(text, retries=1):
            """Inject uncertain transmission after restoring a missing reference.

            @param text UART command to execute.
            @param retries UART retry allowance.
            """
            if text.startswith("SEND_PHASE "):
                attempts.append(text)
                raise CommandError("ERR TX 1")
            return await original(text, retries)

        self.service.gateway.phase_anchor = None
        self.service.gateway.commands.clear()
        self.store.begin("lost", dict(action="refresh", device="46715621"))
        with (
            patch("livolod.service.REFRESH_QUERY_INTERVAL", 0.001),
            patch("livolod.service.REFRESH_RESPONSE_WAIT", 0.005),
        ):
            await self.service.refresh_state("lost", "46715621")
        self.assertEqual(self.store.command_result("lost")["queries_sent"], 0)
        self.assertEqual(len(self.service.gateway.commands), 1)
        self.assertTrue(self.service.gateway.commands[0].startswith("SEND_PHASE "))
        self.service.phase_reference_missing = False
        self.service.gateway.command = command
        with self.assertRaises(LinkError):
            await self.service.refresh_state("uncertain", "46715621")
        self.assertEqual(len(attempts), 1)

    async def test_phase_query_busy_retries_restamp_and_stop_on_report(self):
        """Keep existing CCA backoff and suppress its retry after fresh state arrives."""
        await self.start()
        await asyncio.sleep(0.06)
        original = self.service.gateway.command
        attempts = []

        async def command(text, retries=1):
            """Return valid CCA metadata and deliver a report during backoff.

            @param text UART command to execute.
            @param retries UART retry allowance.
            """
            reply = await original(text, retries)
            if text.startswith("SEND_PHASE "):
                attempts.append(reply)
                asyncio.get_running_loop().call_later(
                    0.005, self.service.gateway.current_state, "46715621"
                )
                raise CommandError(reply.replace("OK TX 0", "ERR TX 2"))
            return reply

        self.service.gateway.command = command
        self.store.begin("busy-query", dict(action="refresh", device="46715621"))
        await self.service.refresh_state("busy-query", "46715621")
        self.assertEqual(len(attempts), 1)
        result = self.store.command_result("busy-query")
        self.assertEqual((result["status"], result["queries_sent"]), ("confirmed", 0))

    def test_missing_config_mode_disables_outputs(self):
        """Verify missing config mode disables outputs."""
        raw = json.loads(EXAMPLE.read_text())
        raw.pop("output_mode")
        config = parse(raw)
        self.assertEqual(config.output_mode, "disabled")
        service = Service(replace(config, database=self.config.database), self.store)
        result = service.submit("set", dict(device="46715621", mask=1, value=1))
        self.assertEqual(result["status"], "rejected")

    async def test_missing_capability_fails_before_configuring_or_sending(self):
        """Verify missing capability fails before configuring or sending."""
        original = self.service.gateway.command

        async def command(text, retries=1):
            """Provide the scenario-specific UART response or injected failure.

            @param text Command or encoded protocol text for this exchange.
            @param retries Additional UART attempts allowed by the caller.
            """
            reply = await original(text, retries)
            return reply.split(" phase=")[0] if text == "CAPS" else reply

        self.service.gateway.command = command
        with self.assertRaisesRegex(LinkError, "capability"):
            await self.service.restore()
        self.assertFalse(self.service.ready)
        self.assertEqual(self.service.gateway.commands, ["PING", "CAPS"])

    async def test_reference_before_ready_then_immediate_control_without_prelude(self):
        """Verify reference before ready then immediate control without prelude."""
        original = self.service.gateway.command

        async def command(text, retries=1):
            """Provide the scenario-specific UART response or injected failure.

            @param text Command or encoded protocol text for this exchange.
            @param retries Additional UART attempts allowed by the caller.
            """
            if text.startswith("SEND "):
                self.assertFalse(self.service.ready)
            return await original(text, retries)

        self.service.gateway.command = command
        await self.start()
        self.assertFalse(self.service.phase_reference_missing)
        started = time.monotonic()
        result = await self.control("immediate")
        self.assertEqual(result["status"], "confirmed")
        self.assertLess(time.monotonic() - started, 0.5)
        self.assertEqual(
            sum(c.startswith("SEND ") for c in self.service.gateway.commands), 1
        )
        self.assertEqual(
            sum(c.startswith("SEND_PHASE ") for c in self.service.gateway.commands), 1
        )

    async def test_six_attempt_limit_and_heartbeat_boundary_keeps_token(self):
        """Verify six attempt limit and heartbeat boundary keeps token."""
        await self.start()
        original_report = self.service.gateway.report
        self.service.gateway.report = lambda *a, **kw: None
        original = self.service.gateway.command
        attempts = []

        async def command(text, retries=1):
            """Provide the scenario-specific UART response or injected failure.

            @param text Command or encoded protocol text for this exchange.
            @param retries Additional UART attempts allowed by the caller.
            """
            if text.startswith("SEND_PHASE "):
                attempts.append(bytes.fromhex(text.split()[-1]))
                if len(attempts) == 1:
                    self.service.last_heartbeat = time.monotonic() - 9.9
                    self.service.next_heartbeat = 0
            return await original(text, retries)

        self.service.gateway.command = command
        with patch("livolod.service.SET_CONFIRM_TIMEOUT", 1.4):
            result = await self.control("boundary")
        self.assertEqual(result["status"], "unconfirmed")
        self.assertEqual(len(attempts), 6)
        self.assertEqual(len({decode(r).data for r in attempts}), 1)
        self.assertEqual(len({r[2] for r in attempts}), 6)
        self.assertEqual(self.service.phase_client.last_tx["anchor_gen"], "2")
        sends = [
            c.split()[0] for c in self.service.gateway.commands if c.startswith("SEND")
        ]
        self.assertEqual(sends, ["SEND", "SEND_PHASE", "SEND"] + ["SEND_PHASE"] * 5)
        self.service.gateway.report = original_report

    async def test_late_confirmation_stops_expanded_window_with_one_token_and_fresh_trailers(
        self,
    ):
        """Verify late confirmation stops expanded window with one token and fresh trailers."""
        await self.start()
        original = self.service.gateway.command
        original_report = self.service.gateway.report
        self.service.gateway.report = lambda *a, **kw: None
        requests, replies = [], []

        async def command(text, retries=1):
            """Provide the scenario-specific UART response or injected failure.

            @param text Command or encoded protocol text for this exchange.
            @param retries Additional UART attempts allowed by the caller.
            """
            if text.startswith("SEND_PHASE "):
                requests.append(bytes.fromhex(text.split()[-1]))
                if len(requests) == 5:
                    self.service.gateway.report = original_report
                reply = await original(text, retries)
                replies.append(bytes.fromhex(reply.split("raw=")[1]))
                return reply
            return await original(text, retries)

        self.service.gateway.command = command
        result = await self.control("fifth")
        self.assertEqual(result["status"], "confirmed")
        self.assertEqual(len(requests), 5)
        self.assertEqual(len({decode(r).data for r in requests}), 1)
        self.assertEqual(len({r[2] for r in requests}), 5)
        self.assertEqual(len({r[-3:] for r in replies}), 5)
        self.assertEqual(
            self.store.db.execute("SELECT count(*) FROM tokens").fetchone()[0], 1
        )

    async def test_expanded_attempts_still_share_deadline_including_uart_latency(self):
        """Verify expanded attempts still share deadline including uart latency."""
        await self.start()
        original = self.service.gateway.command
        self.service.gateway.report = lambda *a, **kw: None
        attempts = []

        async def command(text, retries=1):
            """Provide the scenario-specific UART response or injected failure.

            @param text Command or encoded protocol text for this exchange.
            @param retries Additional UART attempts allowed by the caller.
            """
            if text.startswith("SEND_PHASE "):
                attempts.append(time.monotonic())
                await asyncio.sleep(0.02)
            return await original(text, retries)

        self.service.gateway.command = command
        started = time.monotonic()
        with (
            patch("livolod.service.SET_CONFIRM_TIMEOUT", 0.6),
            patch("livolod.service.SET_RETRY_INTERVAL", 0.03),
        ):
            result = await self.control("absolute")
        elapsed = time.monotonic() - started
        self.assertEqual(result["status"], "unconfirmed")
        self.assertEqual(len(attempts), 6)
        self.assertGreaterEqual(elapsed, 0.59)
        self.assertLess(elapsed, 1.0)

    async def test_reference_loss_is_non_send_then_recovers_at_normal_boundary(self):
        """Verify reference loss is non send then recovers at normal boundary."""
        await self.start()
        self.service.gateway.phase_anchor = None
        with patch("livolod.service.SET_CONFIRM_TIMEOUT", 0.7):
            result = await self.control("lost")
        self.assertEqual(result["status"], "busy")
        self.assertTrue(self.service.phase_reference_missing)
        self.assertEqual(
            sum(c.startswith("SEND_PHASE ") for c in self.service.gateway.commands), 1
        )
        self.service.last_heartbeat = time.monotonic() - 11
        self.service.next_heartbeat = 0
        result = await self.control("recovered")
        self.assertEqual(result["status"], "confirmed")
        self.assertFalse(self.service.phase_reference_missing)

    async def test_uncertain_send_records_unknown_and_reconnect_never_replays(self):
        """Verify uncertain send records unknown and reconnect never replays."""
        radios = []

        def factory():
            """Create a simulated gateway with the reconnect behavior under test."""
            radio = Simulator()
            original = radio.command

            async def command(text, retries=1):
                """Provide the scenario-specific UART response or injected failure.

                @param text Command or encoded protocol text for this exchange.
                @param retries Additional UART attempts allowed by the caller.
                """
                if text.startswith("SEND_PHASE ") and len(radios) == 1:
                    radio.commands.append(text)
                    raise LinkError("simulated uncertain phase UART outcome")
                return await original(text, retries)

            radio.command = command
            radios.append(radio)
            return radio

        self.service.gateway_factory = factory
        task = asyncio.create_task(self.service.run())
        self.tasks.append(task)

        async def wait_for(predicate):
            """Wait for a test observation, failing if its deadline expires.

            @param predicate Callable that becomes true when the expected observation is available.
            """

            async def wait():
                """Wait until the enclosing test's readiness or result condition is satisfied."""
                while not predicate():
                    await asyncio.sleep(0.01)

            await asyncio.wait_for(wait(), 3)

        await wait_for(lambda: self.service.ready)
        old_session = self.service.session
        self.service.submit(
            "set", dict(request_id="uncertain", device="46715621", mask=1, value=1)
        )
        await wait_for(lambda: len(radios) == 2 and self.service.ready)
        self.assertEqual(self.store.command_result("uncertain")["status"], "unknown")
        self.assertNotEqual(self.service.session, old_session)
        self.assertEqual(
            sum(c.startswith("SEND_PHASE ") for r in radios for c in r.commands), 1
        )
        self.service.stop.set()
        await asyncio.wait_for(task, 2)
