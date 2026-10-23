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
from livolod.uart import LinkError

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
