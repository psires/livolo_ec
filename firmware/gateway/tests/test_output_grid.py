"""Exercise the real output scheduler/parser across timing and confirmation faults."""

import binascii
import test_uart_v3 as fixture
from test_uart_v3 import EPOCH, heartbeat, phased_frame, report


class OutputGridTests(fixture.FirmwareUART):
    scheduler = True
    rtimer_hz = 1000000
    baudrate = 460800

    def poll(self, tick):
        """Dispatch pending work at a selected radio-clock tick.

        @param tick Wrapping 1 MHz radio timestamp.
        """
        self.lib.test_set_rtimer(tick & 0xFFFFFFFF)
        self.lib.ec_gateway_poll()

    def begin(self, anchor=100000, baseline="ffffff"):
        """Arm a peer and submit an output transaction after a heartbeat.

        @param anchor Heartbeat timestamp in wrapping 1 MHz radio ticks.
        @param baseline Hex state bits, report counter and token; ffffff means unknown.
        @return Heartbeat anchor used by the test.
        """
        self.configured()
        self.arm()
        self.lib.test_set_rtimer((anchor - 100) & 0xFFFFFFFF)
        self.cmd(f"HEARTBEAT {EPOCH} {anchor} {heartbeat().hex()}")
        self.poll(anchor)
        self.lib.test_set_rtimer((anchor + 50000) & 0xFFFFFFFF)
        self.cmd(f"OUTPUT_GRID {EPOCH} 1 3 {baseline} {phased_frame().hex()}")
        self.poll(anchor + 50000)
        return anchor

    def status(self):
        """Read the retained transaction fields through the UART parser."""
        return dict(
            p.split("=", 1)
            for p in self.cmd(f"QUERY_GET {EPOCH} 1").split()
            if "=" in p
        )

    def inject_state(self, counter=2, token=17, mask=1, bits=0, broadcast=False):
        """Deliver a synthetic state report with a recomputed CRC.

        @param counter Eight-bit report counter.
        @param token Eight-bit command token echoed by the report.
        @param mask Output mask in the report.
        @param bits Reported output-state bits.
        @param broadcast Whether to use a broadcast MAC destination.
        """
        raw = bytearray(report(broadcast=broadcast))
        raw[31], raw[32], raw[35], raw[36] = mask, bits, counter, token
        raw[37:] = binascii.crc_hqx(raw[21:37], 0xFFFF).to_bytes(2, "big")
        self.lib.test_receive(bytes(raw), len(raw))

    def test_six_absolute_slots_then_wait_until_four_second_deadline(self):
        """Six attempts occupy distinct grid slots before the four-second deadline."""
        anchor = self.begin()
        self.assertIn("output_grid=transaction_200ms_v1", self.cmd("CAPS"))
        for attempt in range(1, 7):
            q = self.status()
            due = int(q["due"])
            self.assertEqual(due, anchor + attempt * 200000)
            self.poll(due - 1)
            self.assertEqual(int(self.status()["attempts"]), attempt - 1)
            self.poll(due)
            self.assertEqual(int(self.status()["attempts"]), attempt)
        q = self.status()
        self.assertEqual(q["sent"], "6")
        self.assertEqual(q["state"], "pending")
        deadline = int(q["deadline"])
        self.assertEqual(deadline - int(q["started"]), 4000000)
        self.poll(deadline - 1)
        self.assertEqual(self.status()["state"], "pending")
        self.poll(deadline)
        self.assertEqual(self.status()["outcome"], "2")
        self.assertEqual(self.status()["attempts"], "6")

    def test_only_fresh_exact_confirmation_stops_before_uart(self):
        """Only a fresh post-send report matching the command stops retries."""
        self.begin(baseline="000111")
        self.inject_state()
        self.assertEqual(self.status()["state"], "pending")  # before first send
        self.poll(300000)
        for kw in ({"token": 16}, {"mask": 2}, {"bits": 1}, {"counter": 1}):
            self.inject_state(**kw)
            self.assertEqual(self.status()["state"], "pending")
        self.inject_state(broadcast=True)
        self.assertEqual(self.status()["outcome"], "1")
        self.poll(500000)
        self.assertEqual(self.status()["attempts"], "1")

    def test_busy_attempts_late_slots_and_cancel_remain_bounded(self):
        """Late slots are skipped and busy attempts still consume the retry budget."""
        self.begin()
        self.lib.test_radio(2, 0)
        self.poll(302000)  # miss slot; defer, do not send late
        q = self.status()
        self.assertEqual(q["attempts"], "0")
        self.assertEqual(q["grid_skips"], "1")
        for attempt in range(1, 7):
            self.poll(int(self.status()["due"]))
            self.assertEqual(int(self.status()["attempts"]), attempt)
        self.assertEqual(self.status()["sent"], "0")
        self.cmd(f"QUERY_CANCEL {EPOCH} 1")
        self.poll(2000000)
        self.assertEqual(self.status()["outcome"], "3")

    def test_wrap_and_disarm_clear_pending_output(self):
        """Timer wrap preserves confirmation and disarm clears retained jobs."""
        anchor = self.begin(anchor=0xFFFF0000)
        self.poll(anchor + 200000)
        self.assertEqual(self.status()["attempts"], "1")
        self.inject_state()
        self.assertEqual(self.status()["outcome"], "1")
        self.cmd("DISARM")
        self.poll(anchor + 400000)
        self.arm()
        self.cmd(f"QUERY_GET {EPOCH} 1", "ERR JOB_UNKNOWN")

    def test_invalid_masks_and_occupied_transaction_rejected(self):
        """Invalid masks, occupied slots and reused job IDs are rejected."""
        self.begin()
        self.cmd(
            f"OUTPUT_GRID {EPOCH} 2 3 ffffff {phased_frame().hex()}", "ERR QUERY_BUSY"
        )
        self.cmd(f"QUERY_DROP {EPOCH} 1", "ERR JOB_PENDING")
        self.cmd(f"QUERY_CANCEL {EPOCH} 1")
        self.cmd(f"QUERY_DROP {EPOCH} 1")
        self.cmd(
            f"OUTPUT_GRID {EPOCH} 2 0 ffffff {phased_frame().hex()}", "ERR SCHEDULE"
        )
        self.cmd(f"OUTPUT_GRID {EPOCH} 1 3 ffffff {phased_frame().hex()}", "ERR JOB_ID")

    def test_report_at_deadline_cannot_confirm(self):
        """A report at the deadline cannot turn an expired command into success."""
        self.begin()
        self.poll(300000)
        self.lib.test_set_rtimer(int(self.status()["deadline"]))
        self.inject_state()
        self.poll(int(self.status()["deadline"]))
        self.assertEqual(self.status()["outcome"], "2")
