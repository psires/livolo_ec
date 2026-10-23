import tempfile
import unittest
from pathlib import Path
import struct
from capture_cc26xx import block, packet
from decode_ec import read_pcapng


class CaptureFormatTests(unittest.TestCase):
    def test_roundtrip_tap_frame(self):
        """Verify roundtrip tap frame."""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "test.pcapng"
            raw = bytes(range(41))
            path.write_bytes(
                block(0x0A0D0D0A, struct.pack("<IHHq", 0x1A2B3C4D, 1, 0, -1))
                + block(1, struct.pack("<HHI", 283, 0, 256))
                + packet(1750000000123456, raw, -57, 99)
            )
            records = list(read_pcapng(path))
            self.assertEqual(len(records), 1)
            number, timestamp, actual, metadata = records[0]
            self.assertEqual(actual, raw)
            self.assertAlmostEqual(timestamp, 1750000000.123456, places=5)
            self.assertEqual(
                metadata,
                dict(channel=11, channel_page=0, rssi_dbm=-57, lqi=99, tap_length=36),
            )


if __name__ == "__main__":
    unittest.main()
