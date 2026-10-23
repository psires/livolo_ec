"""Compile the actual MCU protocol code and check captured packets and bounds."""

import ctypes as C
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from decode_ec import ROOT, analyze


class Peer(C.Structure):
    _fields_ = [
        ("gateway", C.c_uint8 * 4),
        ("device", C.c_uint8 * 4),
        ("tag", C.c_uint8),
    ]


class ProtocolCore(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        """Prepare shared capture or compiled-library fixtures for this test group."""
        cls.temp = tempfile.TemporaryDirectory()
        library = Path(cls.temp.name) / "ec.so"
        compiler = shutil.which("cc")
        if not compiler:
            raise RuntimeError("A C compiler is required for MCU protocol tests")
        subprocess.run(
            [
                compiler,
                "-std=c99",
                "-Wall",
                "-Wextra",
                "-Werror",
                "-O2",
                "-fPIC",
                "-dynamiclib" if sys.platform == "darwin" else "-shared",
                str(ROOT / "firmware/gateway/ec-protocol.c"),
                "-o",
                str(library),
            ],
            check=True,
        )
        cls.lib = C.CDLL(str(library))
        cls.lib.ec_make_ack.argtypes = [
            C.POINTER(Peer),
            C.c_void_p,
            C.c_size_t,
            C.c_void_p,
            C.c_size_t,
        ]
        cls.lib.ec_make_ack.restype = C.c_size_t
        cls.lib.ec_tx_allowed.argtypes = [C.POINTER(Peer), C.c_void_p, C.c_size_t]
        cls.lib.ec_crc16.argtypes = [C.c_void_p, C.c_size_t]
        cls.lib.ec_crc16.restype = C.c_uint16
        cls.lib.ec_unhex.argtypes = [C.c_char_p, C.c_void_p, C.c_size_t]
        cls.rows, _ = analyze(
            ROOT
            / "captured/development/gateway-pairing-20261017T182031Z-nrf52840.pcapng",
            True,
        )
        cls.peer = Peer(
            (C.c_uint8 * 4).from_buffer_copy(bytes.fromhex("9bf4ea38")),
            (C.c_uint8 * 4).from_buffer_copy(bytes.fromhex("46715621")),
            0xB1,
        )
        cls.report = bytes.fromhex(
            next(
                r["raw_hex"]
                for r in cls.rows
                if r["family"] == "9e"
                and r["opcode"] == "05"
                and r["network_tag"] == "b1"
            )
        )

    @classmethod
    def tearDownClass(cls):
        """Release the shared temporary fixtures after this test group."""
        cls.temp.cleanup()

    def ack(self, raw, capacity=41):
        """Generate an ACK with the compiled firmware and check output-buffer guards.

        @param raw MAC frame bytes for this scenario.
        @param capacity Output-buffer capacity passed to the compiled ACK builder.
        """
        out = C.create_string_buffer(b"\xa5" * 45, 45)
        n = self.lib.ec_make_ack(
            C.byref(self.peer), raw, len(raw), C.byref(out, 2), capacity
        )
        self.assertEqual(out.raw[:2], b"\xa5" * 2)
        self.assertEqual(out.raw[43:], b"\xa5" * 2)
        if not n:
            self.assertEqual(out.raw, b"\xa5" * 45)
        return out.raw[2 : 2 + n]

    def test_matches_captured_state_and_property_acks(self):
        """Verify matches captured state and property acks."""
        checked = 0
        for response in self.rows:
            if (
                response["ack_of_opcode"] not in ("05", "15")
                or not response["ack_of_frame"]
            ):
                continue
            request = next(
                r for r in self.rows if r["frame"] == response["ack_of_frame"]
            )
            if request["network_tag"] != "b1":
                continue
            actual = self.ack(bytes.fromhex(request["raw_hex"]))
            self.assertEqual(len(actual), 41)
            # Dynamic timing trailer is deliberately generated as zero.
            self.assertEqual(actual[:38], bytes.fromhex(response["raw_hex"])[:38])
            self.assertEqual(actual[38:], b"\0" * 3)
            checked += 1
        self.assertGreaterEqual(checked, 10)

    def test_rejects_wrong_peer_tag_checksum_and_truncation(self):
        """Verify rejects wrong peer tag checksum and truncation."""
        for offset in (0, 1, 3, 7, 19, 23, 29, 37, 38):
            raw = bytearray(self.report)
            raw[offset] ^= 1
            self.assertEqual(self.ack(bytes(raw)), b"")
        for size in range(39):
            self.assertEqual(self.ack(self.report[:size]), b"")
        self.assertEqual(self.ack(self.report + b"\0"), b"")
        self.assertEqual(self.ack(self.report, 40), b"")

    def test_no_join_admission_or_ack_loops(self):
        """Verify no join admission or ack loops."""
        for opcode in (1, 7, 0xFA, 0xFF):
            raw = bytearray(self.report)
            raw[22] = opcode
            raw[37:39] = self.lib.ec_crc16(bytes(raw[21:37]), 16).to_bytes(2, "big")
            self.assertEqual(self.ack(bytes(raw)), b"")

    def test_broadcast_state_only_from_configured_peer_and_tag(self):
        """Verify broadcast state only from configured peer and tag."""
        raw = bytearray(self.report)
        raw[3:7] = bytes.fromhex("ffffffff")
        self.assertEqual(self.ack(bytes(raw)), self.ack(self.report))
        for offset in (7, 19, 23, 37):
            bad = bytearray(raw)
            bad[offset] ^= 1
            self.assertEqual(self.ack(bytes(bad)), b"")
        for opcode in (1, 7, 8, 0x15, 0xA0, 0xFA):
            bad = bytearray(raw)
            bad[22] = opcode
            bad[37:39] = self.lib.ec_crc16(bytes(bad[21:37]), 16).to_bytes(2, "big")
            self.assertEqual(self.ack(bytes(bad)), b"")

    def test_keepalive_reply_and_standard_crc_vector(self):
        """Verify keepalive reply and standard crc vector."""
        self.assertEqual(self.lib.ec_crc16(b"123456789", 9), 0x29B1)
        raw = bytearray(self.report)
        raw[22] = 8
        raw[37:39] = self.lib.ec_crc16(bytes(raw[21:37]), 16).to_bytes(2, "big")
        reply = self.ack(bytes(raw))
        self.assertEqual(reply[22], 8)
        self.assertEqual(reply[29:37], bytes(8))

    def test_transmit_policy(self):
        """Verify transmit policy."""
        raw = bytes.fromhex(
            next(r["raw_hex"] for r in self.rows if r["opcode"] == "02")
        )
        # @brief Query the compiled transmit policy for this test's configured peer.
        # @param b Candidate MAC frame bytes without FCS.
        allowed = lambda b: self.lib.ec_tx_allowed(C.byref(self.peer), b, len(b))
        self.assertTrue(allowed(raw))
        for offset in (0, 1, 3, 7, 19, 37):
            bad = bytearray(raw)
            bad[offset] ^= 1
            self.assertFalse(allowed(bytes(bad)))
        self.assertFalse(allowed(self.report))
        self.assertFalse(allowed(raw + bytes(100)))
        diagnostic = b"\x01\x88\x01" + bytes(self.peer.gateway) * 2 + b"EC_UART_TEST"
        self.assertTrue(allowed(diagnostic))

    def test_hex_input_bounds(self):
        """Verify hex input bounds."""
        out = C.create_string_buffer(b"\xa5" * 8, 8)
        self.assertEqual(self.lib.ec_unhex(b"01234567", out, 4), 4)
        self.assertEqual(out.raw, bytes.fromhex("01234567") + b"\xa5" * 4)
        for bad in (b"0", b"zz", b"0011223344"):
            self.assertEqual(self.lib.ec_unhex(bad, out, 4), -1)


if __name__ == "__main__":
    unittest.main()
