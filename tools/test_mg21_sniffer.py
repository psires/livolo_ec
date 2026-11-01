"""Verify the actual C wire encoder and host resynchronization/loss handling."""

import binascii
import ctypes
from pathlib import Path
import subprocess
import tempfile
import unittest

from capture_mg21_sniffer import Decoder


class WireTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        """Compile the firmware encoder into a temporary host test library."""
        cls.temp = tempfile.TemporaryDirectory()
        root = Path(cls.temp.name)
        (root / "wrapper.c").write_text(
            '#include "sniffer-wire.h"\nsize_t encode(uint8_t *out, unsigned kind, uint32_t seq, uint64_t ts, const void *p, size_t n) { return sniff_record(out, kind, seq, ts, p, n); }\n'
        )
        include = Path(__file__).resolve().parents[1] / "firmware/sniffer-mg21"
        lib = root / "wire.so"
        subprocess.run(
            [
                "cc",
                "-shared",
                "-fPIC",
                "-I",
                str(include),
                str(root / "wrapper.c"),
                "-o",
                str(lib),
            ],
            check=True,
        )
        cls.lib = ctypes.CDLL(str(lib))
        cls.lib.encode.argtypes = [
            ctypes.c_void_p,
            ctypes.c_uint,
            ctypes.c_uint32,
            ctypes.c_uint64,
            ctypes.c_void_p,
            ctypes.c_size_t,
        ]
        cls.lib.encode.restype = ctypes.c_size_t

    @classmethod
    def tearDownClass(cls):
        """Remove the temporary compiled test artifacts."""
        cls.temp.cleanup()

    def record(self, sequence, timestamp=0xFFFFFFFF + 15, body=b"\xd6\x64\x0b\0ESN1"):
        """Encode a record using production C, preserving arbitrary payload bytes.

        @param sequence UART record sequence.
        @param timestamp Extended hardware microseconds.
        @param body Binary metadata and MAC bytes.
        """
        out = ctypes.create_string_buffer(153)
        n = self.lib.encode(out, 1, sequence, timestamp, body, len(body))
        return out.raw[:n]

    def test_chunking_wrap_and_payload_magic(self):
        """Preserve long timestamps and sequence wrap despite bytewise fragmentation."""
        data = self.record(0xFFFFFFFF) + self.record(0)
        d = Decoder()
        rows = []
        for byte in data:
            rows.extend(d.feed(bytes([byte])))
        self.assertEqual([r["sequence"] for r in rows], [0xFFFFFFFF, 0])
        self.assertEqual(rows[0]["timestamp_us"], 0xFFFFFFFF + 15)
        self.assertEqual(rows[0]["raw"], b"ESN1")
        self.assertEqual(rows[0]["rssi"], -42)
        self.assertEqual(d.sequence_gaps, 0)

    def test_corruption_and_gap_are_visible(self):
        """Recover from corrupt/truncated records while retaining CRC and loss counts."""
        bad = bytearray(self.record(2))
        bad[-1] ^= 1
        d = Decoder()
        rows = d.feed(b"noise" + self.record(1) + bad + self.record(4))
        self.assertEqual([r["sequence"] for r in rows], [1, 4])
        self.assertEqual(d.crc_errors, 1)
        self.assertEqual(d.sequence_gaps, 2)
        self.assertGreater(d.discarded, 5)

    def test_reset_is_not_a_huge_silent_gap(self):
        """Reject a backward sequence instead of treating a reboot as normal capture."""
        d = Decoder()
        d.feed(self.record(100))
        with self.assertRaises(ValueError):
            d.feed(self.record(1))

    def test_size_bound_and_crc(self):
        """Accept the largest frame and reject an oversized body in production C."""
        raw = self.record(1, body=b"\xd6\x64\x0b\0" + bytes(range(125)))
        self.assertEqual(len(raw), 153)
        self.assertEqual(int.from_bytes(raw[-4:], "little"), binascii.crc32(raw[:-4]))
        self.assertEqual(len(Decoder().feed(raw)[0]["raw"]), 125)
        self.assertEqual(self.record(1, body=bytes(130)), b"")


if __name__ == "__main__":
    unittest.main()
