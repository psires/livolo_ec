"""Exercise phase-stamped ACKs through the actual callback and MG21 scheduler."""

import unittest
import test_scheduler_mg21 as fixture
from test_uart_v3 import EPOCH, heartbeat, report


class SonoffAckPhaseTests(fixture.MG21ClockTests):
    auto_ack_phase = True

    def test_all_automatic_replies_stamp_entry_and_log_actual_bytes(self):
        """Automatic ACKs use radio entry phase and log the exact transmitted bytes."""
        self.configured()
        self.arm()
        self.assertIn("auto_ack_phase=tx_entry_or_zero_v1", self.cmd("CAPS"))
        self.lib.test_set_rtimer(1000000)
        self.cmd(f"SEND {EPOCH} {heartbeat().hex()}")
        for opcode in (5, 0x15, 0xA0, 8):
            self.lib.test_set_rtimer(2234567)
            # A synthetic slow radio must not turn the trailer into return time.
            self.lib.test_radio(0, 90000)
            settings = self.lib.test_settings()
            sends = self.lib.test_sends()
            output = self.receive(report(opcode=opcode))
            self.assertEqual(self.lib.test_settings(), settings)
            self.assertEqual(self.lib.test_sends(), sends + 1)
            wire = self.sent()
            expected = int(234567 * 65500 // 1000000).to_bytes(2, "little") + b"\x01"
            self.assertEqual(wire[-3:], expected)
            self.assertIn(wire.hex(), output)
            self.assertEqual(wire[2], report(opcode=opcode)[2])
            self.assertEqual(wire[37], sum(wire[21:37]) & 255)
            # CHECK stays a pure template builder and never claims TX phase.
            checked = bytes.fromhex(
                self.cmd("CHECK " + report(opcode=opcode).hex()).split()[-1]
            )
            self.assertEqual(wire[:-3], checked[:-3])
            self.assertEqual(checked[-3:], bytes(3))

    def test_missing_and_expired_reference_still_dispatch_immediately(self):
        """Missing phase references zero the trailer without delaying an ACK."""
        self.configured()
        self.arm()
        before = self.lib.test_sends()
        self.receive(report())
        self.assertEqual(self.lib.test_sends(), before + 1)
        self.assertEqual(self.sent()[-3:], bytes(3))
        self.cmd(f"SEND {EPOCH} {heartbeat().hex()}")
        self.lib.test_set_rtimer(12000000)
        before = self.lib.test_sends()
        self.receive(report())
        self.assertEqual(self.lib.test_sends(), before + 1)
        self.assertEqual(self.sent()[-3:], bytes(3))
        self.cmd("DISARM")
        self.receive(report())
        self.assertEqual(self.lib.test_sends(), before + 1)

    def test_ack_stamp_wrap_does_not_change_heartbeat_anchor(self):
        """ACK stamping handles timer wrap without moving the heartbeat anchor."""
        self.configured()
        self.arm()
        anchor = 0xFFFF0000
        self.lib.test_set_rtimer(anchor)
        self.cmd(f"SEND {EPOCH} {heartbeat().hex()}")
        for age in (750000, 1250000):
            self.lib.test_set_rtimer((anchor + age) & 0xFFFFFFFF)
            self.receive(report())
            self.assertEqual(
                self.sent()[-3:],
                int((age % 1000000) * 65500 // 1000000).to_bytes(2, "little")
                + bytes([age // 1000000]),
            )


if __name__ == "__main__":
    unittest.main()
