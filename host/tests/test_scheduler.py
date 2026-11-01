"""Protect heartbeat ownership, capability fallback and queued acceptance semantics."""

import asyncio
from dataclasses import replace
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from livolod.config import load
from livolod.phase import PhaseClient
from livolod.protocol import heartbeat, key_values
from livolod.scheduler import SchedulerClient
from livolod.service import Service
from livolod.simulator import Simulator
from livolod.storage import Store
from livolod.uart import LinkError
from test_phase import CAPS, RAW, GatewayStub, response

CAPABILITY = dict(
    scheduler="timed_v1",
    scheduler_hz="65536",
    scheduler_slots="8",
    tx_sequence="adapter_when_hb",
)


class SchedulerHostTests(unittest.IsolatedAsyncioTestCase):
    async def test_native_clock_scales_heartbeat_query_and_queue_without_changing_time(
        self,
    ):
        """TI and MG21 use different tick counts for the same bounded wire schedule."""
        for hz in (65536, 1000000):
            history = []

            class Stub:
                async def command(self, text):
                    """Emulate queue and heartbeat replies while recording clock-scaled commands.

                    @param text UART scheduler command to record and classify.
                    """
                    history.append(text)
                    if text.startswith("QUERY "):
                        return "OK QUERY"
                    if text.startswith("QUEUE "):
                        return "OK QUEUED"
                    if text.startswith("HEARTBEAT "):
                        return "OK HEARTBEAT"
                    sent = int(any(x.startswith("HEARTBEAT ") for x in history))
                    return f"OK now=4294967290 hb_due=0 hb_sent={sent} hb_missed=0 hb_late_max=0 hb_active={sent} phase_valid={sent} free=8 pending=0 done=0"

            caps = dict(
                CAPABILITY,
                scheduler_hz=str(hz),
                rtimer_hz=str(hz),
                query="transaction_v1",
                query_slots="1",
            )
            c = SchedulerClient(Stub(), "session", caps)
            await c.start_heartbeat(RAW)
            self.assertIn(
                f"HEARTBEAT session {(4294967290 + (hz + 9) // 10) & 0xFFFFFFFF} {RAW.hex()}",
                history,
            )
            await c.start_query(RAW, 3)
            self.assertIn(
                f"QUERY session 1 {round(.2355 * hz)} {round(4.012 * hz)} 3 {RAW.hex()}",
                history,
            )
            await c.enqueue(RAW, 100000)
            self.assertIn(f"QUEUE session 2 100000 {hz // 50} 0 {RAW.hex()}", history)
            with self.assertRaises(LinkError):
                SchedulerClient(Stub(), "session", dict(caps, rtimer_hz="1000"))

    async def test_ac_only_capability_and_unchanged_timing(self):
        """Opt in explicitly and change only opcode/checksum in the query template."""
        from livolod.protocol import frame

        raw = frame("2a992b12", "a7", "46715621", "0002", 1, 0x10, bytes(7) + b"\x01")
        history = []

        class Stub:
            async def command(self, text):
                """Record an accepted query for comparison with the original frame.

                @param text Query submission containing the opcode and timing fields.
                """
                history.append(text)
                return "OK QUERY"

        caps = dict(
            CAPABILITY, scheduler_hz="1000000", query="transaction_v1", query_slots="1"
        )
        c = SchedulerClient(Stub(), "session", caps)
        with self.assertRaises(LinkError):
            await c.start_query(raw, 3, ac_only=True)
        self.assertEqual(history, [])
        c = SchedulerClient(Stub(), "session", dict(caps, query_ac="slot_preserved_v1"))
        await c.start_query(raw, 3, ac_only=True)
        parts = history[-1].split()
        self.assertEqual(parts[:6], ["QUERY", "session", "1", "235500", "4012000", "3"])
        sent = bytes.fromhex(parts[6])
        self.assertEqual([i for i in range(41) if sent[i] != raw[i]], [22, 37])
        self.assertEqual(sent[22], 0xAC)
        await c.start_query(raw, 3)
        self.assertEqual(history[-1].split()[-1], raw.hex())

    async def test_grid_query_is_explicit_capability_gated_ac_only(self):
        """Keep grid opt-in separate from default and unaligned AC transactions."""
        from livolod.protocol import frame

        raw = frame("2a992b12", "a7", "46715621", "0002", 1, 0x10, bytes(7) + b"\x01")
        history = []

        class Stub:
            async def command(self, text):
                """Record a grid or ordinary query accepted by the test adapter.

                @param text Query command whose capability gating is under test.
                """
                history.append(text)
                return "OK QUERY"

        caps = dict(
            CAPABILITY,
            scheduler_hz="1000000",
            query="transaction_v1",
            query_slots="1",
            query_ac="slot_preserved_v1",
        )
        c = SchedulerClient(Stub(), "session", caps)
        with self.assertRaises(LinkError):
            await c.start_query(raw, 3, ac_only=True, grid=True)
        c = SchedulerClient(
            Stub(), "session", dict(caps, query_grid="heartbeat_200ms_v1")
        )
        with self.assertRaises(LinkError):
            await c.start_query(raw, 3, grid=True)
        self.assertEqual(history, [])
        await c.start_query(raw, 3, ac_only=True, grid=True)
        self.assertEqual(
            history[-1].split()[:6],
            ["QUERY_GRID", "session", "1", "235500", "4012000", "3"],
        )
        self.assertEqual(bytes.fromhex(history[-1].split()[-1])[22], 0xAC)
        await c.start_query(raw, 3, ac_only=True)
        self.assertEqual(history[-1].split()[0], "QUERY")

    async def test_mg21_retained_deadline_and_freshness_use_microseconds(self):
        """Validate a wrapped MG21 result and reject TI-scaled or expired reports."""
        from test_daemon import report
        from livolod.protocol import frame

        raw = frame("2a992b12", "a7", "46715621", "0002", 1, 0x10, bytes(7) + b"\x01")
        start = 0xFFFF0000
        fields = dict(
            id=1,
            state="done",
            outcome=1,
            started=start,
            deadline=(start + 14000000) & 0xFFFFFFFF,
            rx_ticks=(start + 13000000) & 0xFFFFFFFF,
            rounds=3,
            sent=6,
            attempts=6,
            retries=0,
            rx_sequence=1,
            rssi=-40,
            lqi=60,
            report=report().split()[-1],
        )

        class Stub:
            async def command(self, text):
                """Return the mutable retained report used to test timestamp bounds.

                @param text Retained-result lookup submitted by the scheduler client.
                """
                return "OK QUERY " + " ".join(f"{k}={v}" for k, v in fields.items())

        c = SchedulerClient(
            Stub(),
            "session",
            dict(
                CAPABILITY,
                scheduler_hz="1000000",
                rtimer_hz="1000000",
                query="transaction_v1",
                query_slots="1",
            ),
        )
        self.assertEqual((await c.query_result(1, raw, 3))["outcome"], "1")
        fields["rx_ticks"] = fields["deadline"]
        with self.assertRaises(LinkError):
            await c.query_result(1, raw, 3)
        fields["deadline"] = (start + 14 * 65536) & 0xFFFFFFFF
        with self.assertRaises(LinkError):
            await c.query_result(1, raw, 3)

    async def test_adapter_sequence_is_capability_gated_and_other_bytes_are_checked(
        self,
    ):
        """Only negotiated MAC sequence ownership may differ from the requested frame."""
        raw = bytearray(RAW)
        raw[2] ^= 0x80
        g = GatewayStub(response(bytes(raw)))
        c = PhaseClient(
            g,
            "session",
            dict(CAPS, **CAPABILITY),
            gateway_id="2a992b12",
            allow_outputs=True,
        )
        await c.send(RAW)
        old = PhaseClient(g, "session", CAPS, gateway_id="2a992b12", allow_outputs=True)
        with self.assertRaises(LinkError):
            await old.send(RAW)
        raw[29] ^= 1
        g.reply = response(bytes(raw))
        with self.assertRaises(LinkError):
            await c.send(RAW)

    async def test_mcu_heartbeat_owns_period_and_query_retry_bounds_are_unchanged(self):
        """Startup delegates one cadence; monitoring never emits host heartbeats or adds retries."""
        with tempfile.TemporaryDirectory() as directory:
            config = replace(
                load(Path(__file__).parents[1] / "config.example.json"),
                output_mode="experimental_phase",
                database=directory + "/db",
            )
            store = Store(config.database, "network")
            self.addCleanup(store.close)
            service = Service(config, store)
            g = Simulator()
            service.gateway = g
            original = g.command
            active = False
            sent = 0
            history = []

            async def command(text, retries=1):
                """Emulate only the scheduler boundary over the existing radio simulator.

                @param text UART command.
                @param retries Existing transport retry allowance.
                """
                nonlocal active, sent
                history.append(text)
                if text == "CAPS":
                    return (
                        await original(text, retries)
                        + " "
                        + " ".join(f"{k}={v}" for k, v in CAPABILITY.items())
                    )
                if text.startswith("HEARTBEAT "):
                    active = True
                    sent += 1
                    await original("SEND " + text.split()[1] + " " + text.split()[3])
                    return "OK HEARTBEAT"
                if text.startswith("SCHED "):
                    st = key_values(await original("STATUS"))
                    return (
                        f"OK now=10000 free=8 pending=0 done=0 hb_active={int(active)} hb_due=665360 hb_sent={sent} hb_missed=0 hb_late_max=1 "
                        + " ".join(
                            f"{k}={st[k]}"
                            for k in (
                                "phase_valid",
                                "phase_anchor_ticks",
                                "phase_anchor_gen",
                            )
                        )
                    )
                return await original(text, retries)

            g.command = command
            await service.restore()
            self.assertEqual(service.diagnostics["heartbeat_owner"], "mcu")
            for _ in range(3):
                self.assertTrue(await service._emit_heartbeat())
            self.assertEqual(sum(c.startswith("HEARTBEAT ") for c in history), 1)
            self.assertFalse(any(c.startswith("SEND ") for c in history))
            store.begin("silent", {"action": "refresh", "device": "46715621"})
            with (
                patch("livolod.service.REFRESH_QUERY_INTERVAL", 0.001),
                patch("livolod.service.REFRESH_RESPONSE_WAIT", 0.005),
            ):
                await service.refresh_state("silent", "46715621")
            result = store.command_result("silent")
            self.assertEqual((result["query_rounds"], result["queries_sent"]), (3, 6))
            active = False
            with self.assertRaisesRegex(LinkError, "schedule lost"):
                await service._emit_heartbeat()
            await g.close()

    async def test_queue_acceptance_is_not_completion_and_cancellation_retains_result(
        self,
    ):
        """Draining releases only completed jobs, and job identities never rewind."""

        class Stub:
            async def command(self, text):
                """Return a pending or cancelled job without simulating RF confirmation.

                @param text UART operation to inspect.
                """
                history.append(text)
                if text.startswith("QUEUE"):
                    return "OK QUEUED"
                if text.startswith("QGET"):
                    return "OK JOB id=1 state=pending result=0"
                if text.startswith("QCANCEL"):
                    return "OK JOB id=1 state=done result=-4"
                return "OK DROPPED"

        history = []
        client = SchedulerClient(Stub(), "session", CAPABILITY)
        self.assertEqual(await client.enqueue(RAW, 20000), 1)
        self.assertEqual((await client.result(1, release=True))["state"], "pending")
        self.assertFalse(any(c.startswith("QDROP") for c in history))
        self.assertEqual(
            (await client.result(1, cancel=True, release=True))["result"], "-4"
        )
        self.assertTrue(history[-1].startswith("QDROP"))
        self.assertEqual(await client.enqueue(RAW, 30000), 2)
        with self.assertRaises(ValueError):
            await client.enqueue(RAW, -1)
        with self.assertRaises(LinkError):
            SchedulerClient(Stub(), "session", dict(CAPABILITY, scheduler_hz="1000"))

    async def test_query_retained_report_validation_and_counter_reuse(self):
        """Require exact peer/suffix/CRC and callback freshness, not counter advancement."""
        from test_daemon import report
        from livolod.protocol import frame

        raw = frame("2a992b12", "a7", "46715621", "0002", 1, 0x10, bytes(7) + b"\x01")
        rx = report(counter=255).split()[-1]
        fields = dict(
            id="1",
            state="done",
            outcome="1",
            started="100",
            deadline=str(100 + 14 * 65536),
            rx_ticks="101",
            rounds="0",
            sent="0",
            attempts="0",
            retries="0",
            rx_sequence="1",
            rssi="-40",
            lqi="60",
            report=rx,
        )

        class Stub:
            async def command(self, text):
                """Return one retained test result.

                @param text Query command submitted by the real client.
                """
                return "OK QUERY " + " ".join(f"{k}={v}" for k, v in fields.items())

        c = SchedulerClient(
            Stub(), "session", dict(CAPABILITY, query="transaction_v1", query_slots="1")
        )
        self.assertEqual((await c.query_result(1, raw, 3))["outcome"], "1")
        for key, bad in (
            ("rx_ticks", "99"),
            ("attempts", "25"),
            ("sent", "7"),
            ("id", "2"),
        ):
            old = fields[key]
            fields[key] = bad
            with self.assertRaises(LinkError):
                await c.query_result(1, raw, 3)
            fields[key] = old
        import binascii

        bad = bytearray.fromhex(rx)
        bad[28] = 1
        bad[37:] = binascii.crc_hqx(bad[21:37], 0xFFFF).to_bytes(2, "big")
        fields["report"] = bad.hex()
        with self.assertRaises(LinkError):
            await c.query_result(1, raw, 3)

    async def test_mcu_refresh_recovers_dropped_rx_and_does_not_replay_old_state(self):
        """A retained native frame confirms and persists without a bulk RX event or host send."""
        import time
        from test_daemon import report
        from unittest.mock import AsyncMock
        from types import SimpleNamespace

        with tempfile.TemporaryDirectory() as directory:
            config = replace(
                load(Path(__file__).parents[1] / "config.example.json"),
                database=directory + "/db",
            )
            store = Store(config.database, "network")
            self.addCleanup(store.close)
            s = Service(config, store)
            s.ready = True
            s.session = "session"
            result = dict(
                state="done",
                outcome="1",
                rounds="0",
                sent="0",
                attempts="0",
                rx_sequence="10",
                rssi="-40",
                lqi="60",
                report=report(sequence=10, counter=0).split()[-1],
            )
            s.scheduler = SimpleNamespace(
                query_supported=True,
                start_query=AsyncMock(return_value=1),
                query_result=AsyncMock(return_value=result),
                drop_query=AsyncMock(),
            )
            s._transmit = AsyncMock(
                side_effect=AssertionError("host must not send queries")
            )
            store.begin("mcu", dict(action="refresh", device="46715621"))
            await s.refresh_state("mcu", "46715621")
            self.assertEqual(store.command_result("mcu")["status"], "confirmed")
            s.scheduler.drop_query.assert_awaited_once_with(1)
            s.handle_rx(time.monotonic(), report(sequence=11, counter=1, bits=3))
            s.handle_rx(
                time.monotonic(), report(sequence=10, counter=0, bits=0), retained=True
            )
            self.assertEqual(s.states["46715621"]["state_bits"], 3)
            self.assertEqual(s.last_rx, 11)

    async def test_mcu_timeout_is_terminal_without_host_retry(self):
        """Exhausted MCU work is drained once; host polling never adds radio attempts."""
        from unittest.mock import AsyncMock
        from types import SimpleNamespace

        with tempfile.TemporaryDirectory() as directory:
            config = replace(
                load(Path(__file__).parents[1] / "config.example.json"),
                database=directory + "/db",
            )
            store = Store(config.database, "network")
            self.addCleanup(store.close)
            s = Service(config, store)
            s.ready = True
            s.session = "session"
            result = dict(state="done", outcome="2", rounds="3", sent="0", attempts="0")
            s.scheduler = SimpleNamespace(
                query_supported=True,
                start_query=AsyncMock(return_value=1),
                query_result=AsyncMock(return_value=result),
                drop_query=AsyncMock(),
            )
            s._transmit = AsyncMock(side_effect=AssertionError("host must not retry"))
            store.begin("mcu", dict(action="refresh", device="46715621"))
            await s.refresh_state("mcu", "46715621")
            self.assertEqual(store.command_result("mcu")["status"], "unconfirmed")
            s.scheduler.start_query.assert_awaited_once()
            s.scheduler.drop_query.assert_awaited_once_with(1)
