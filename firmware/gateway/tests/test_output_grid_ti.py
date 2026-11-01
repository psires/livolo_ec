"""Exercise fractional 200 ms slots and ACK stamps at TI's native timer rate."""

import binascii
import test_uart_v3 as fixture
from test_uart_v3 import EPOCH, heartbeat, phased_frame, report


class TIOutputGridTests(fixture.FirmwareUART):
    scheduler = True
    rtimer_hz = 65536
    baudrate = 460800
    auto_ack_phase = True

    def poll(self, tick):
        """Dispatch the scheduler at a wrapping TI tick count.

        @param tick Absolute 65,536 Hz radio-clock tick.
        """
        self.lib.test_set_rtimer(tick & 0xFFFFFFFF)
        self.lib.ec_gateway_poll()

    def begin(self, anchor):
        """Start an output transaction after a real heartbeat reference.

        @param anchor Absolute heartbeat tick, including wraparound cases.
        """
        self.configured()
        self.arm()
        self.lib.test_set_rtimer((anchor - 100) & 0xFFFFFFFF)
        self.cmd(f"HEARTBEAT {EPOCH} {anchor} {heartbeat().hex()}")
        self.poll(anchor)
        self.lib.test_set_rtimer((anchor + 1000) & 0xFFFFFFFF)
        self.cmd(f"OUTPUT_GRID {EPOCH} 1 3 ffffff {phased_frame().hex()}")
        self.poll(anchor + 1000)

    def status(self):
        """Read the retained transaction through the real parser."""
        return dict(
            v.split("=", 1)
            for v in self.cmd(f"QUERY_GET {EPOCH} 1").split()
            if "=" in v
        )

    def test_fractional_slots_and_deadline_across_wrap(self):
        """Keep six attempts on ceil(n*65536/5), including 32-bit wrap."""
        anchor = 0xFFFF0000
        self.begin(anchor)
        for attempt in range(1, 7):
            due = (anchor + (attempt * 65536 + 4) // 5) & 0xFFFFFFFF
            self.assertEqual(int(self.status()["due"]), due)
            self.poll(due - 1)
            self.assertEqual(int(self.status()["attempts"]), attempt - 1)
            self.poll(due)
            self.assertEqual(int(self.status()["attempts"]), attempt)
        q = self.status()
        self.assertEqual(
            (int(q["deadline"]) - int(q["started"])) & 0xFFFFFFFF, 4 * 65536
        )
        self.poll(int(q["deadline"]) - 1)
        self.assertEqual(self.status()["state"], "pending")
        self.poll(int(q["deadline"]))
        self.assertEqual(self.status()["outcome"], "2")
        self.assertEqual(self.status()["attempts"], "6")

    def test_fresh_confirmation_and_ack_use_ti_clock(self):
        """Stamp the immediate ACK at 65,536 Hz and cancel future output slots."""
        anchor = 65536
        self.begin(anchor)
        self.poll(int(self.status()["due"]))
        raw = bytearray(report())
        raw[31], raw[32], raw[35], raw[36] = 1, 0, 2, 17
        raw[37:] = binascii.crc_hqx(raw[21:37], 0xFFFF).to_bytes(2, "big")
        self.lib.test_set_rtimer(anchor + 32768)
        self.lib.test_receive(bytes(raw), len(raw))
        self.assertEqual(self.status()["outcome"], "1")
        self.assertEqual(self.sent()[-3:], (32750).to_bytes(2, "little") + b"\x00")
        self.poll(anchor + 65536)
        self.assertEqual(self.status()["attempts"], "1")
        self.assertIn("auto_ack_phase=tx_entry_or_zero_v1", self.cmd("CAPS"))
