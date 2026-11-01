"""Baud-specific query pacing preserves fresh-report stopping and retry bounds."""

from dataclasses import replace
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from livolod.config import load
from livolod.protocol import decode
from livolod.service import Service
from livolod.storage import Store

EXAMPLE = Path(__file__).parents[1] / "config.example.json"


class QueryTimingTests(unittest.IsolatedAsyncioTestCase):
    async def trial(self, baudrate, report_at=None, send_duration=0):
        """Run real refresh policy against a deterministic radio and clock.

        @param baudrate Application baud rate selecting the query delay.
        @param report_at Optional elapsed seconds when a fresh report arrives.
        @param send_duration Simulated time consumed by each transmit call.
        @return Transmission times/opcodes, result, diagnostics and elapsed time.
        """
        clock = [100.0]
        packets = []
        with tempfile.TemporaryDirectory() as directory:
            config = replace(
                load(EXAMPLE),
                serial_baudrate=baudrate,
                database=directory + "/state.db",
            )
            store = Store(config.database, "network")
            service = Service(config, store)
            service.ready = True
            service.session = "test-session"
            store.begin("timing", dict(action="refresh", device="46715621"))

            async def transmit(packet, **kwargs):
                """Record sends without advancing through the real UART.

                @param packet Encoded query frame.
                @param kwargs Transmission options passed by refresh policy.
                """
                packets.append((clock[0] - 100, decode(packet).opcode))
                clock[0] += send_duration
                return True

            async def sleep(seconds):
                """Advance the clock and optionally deliver fresh state.

                @param seconds Requested bounded wait in seconds.
                """
                clock[0] += seconds
                if report_at is not None and clock[0] - 100 >= report_at:
                    service.states["46715621"] = {"state_bits": 0}
                    service.state_received["46715621"] = clock[0]

            try:
                with (
                    patch(
                        "livolod.service.time",
                        SimpleNamespace(monotonic=lambda: clock[0]),
                    ),
                    patch("livolod.service.asyncio.sleep", side_effect=sleep),
                    patch.object(service, "_transmit", side_effect=transmit),
                ):
                    await service.refresh_state("timing", "46715621")
                return (
                    packets,
                    store.command_result("timing"),
                    service.diagnostics["query_timing"],
                    clock[0] - 100,
                )
            finally:
                store.close()

    async def test_baud_specific_pair_gap_preserves_round_and_attempt_limits(self):
        """Only 460800 gains the measured delay; silence still exhausts six sends."""
        for baudrate, interval in (
            (115200, 0.2),
            (230400, 0.2),
            (460800, 0.224),
            (921600, 0.2),
        ):
            with self.subTest(baudrate=baudrate):
                packets, result, diagnostics, elapsed = await self.trial(baudrate)
                self.assertEqual([opcode for _, opcode in packets], [0x10, 0xAC] * 3)
                self.assertEqual(
                    (result["query_rounds"], result["queries_sent"]), (3, 6)
                )
                self.assertEqual(result["status"], "unconfirmed")
                for index in (0, 2, 4):
                    self.assertAlmostEqual(
                        packets[index + 1][0] - packets[index][0], interval
                    )
                for index in (1, 3):
                    self.assertAlmostEqual(
                        packets[index + 1][0] - packets[index][0], 4.0
                    )
                self.assertEqual(
                    diagnostics,
                    dict(
                        query_interval_seconds=interval,
                        response_wait_seconds=4.0,
                        rounds=3,
                        deadline_seconds=14.0,
                    ),
                )
                self.assertLess(elapsed, 14)

    async def test_fresh_report_in_added_pair_delay_suppresses_second_query(self):
        """Fresh state during the added 24 ms prevents the next send."""
        packets, result, _, _ = await self.trial(460800, report_at=0.21)
        self.assertEqual([opcode for _, opcode in packets], [0x10])
        self.assertEqual((result["status"], result["queries_sent"]), ("confirmed", 1))

    async def test_slow_transmits_do_not_extend_fourteen_second_deadline(self):
        """Time spent transmitting reduces the remaining retry budget."""
        packets, result, _, elapsed = await self.trial(460800, send_duration=3)
        self.assertEqual([opcode for _, opcode in packets], [0x10, 0xAC, 0x10, 0xAC])
        self.assertEqual((result["status"], result["query_rounds"]), ("unconfirmed", 2))
        self.assertTrue(all(when < 14 for when, _ in packets))
        # An in-flight call may finish beyond the deadline; no further wait/send follows.
        self.assertAlmostEqual(elapsed, packets[-1][0] + 3)
