"""Synthetic receive failures preserve admission and expose only finite counters."""

from dataclasses import replace
import tempfile
import unittest
from unittest.mock import patch

from livolod.config import load
from livolod.native_api import native_snapshot
from livolod.protocol import ReceiveError, frame, rx_envelope, rx_record
from livolod.service import Service
from livolod.simulator import Simulator
from livolod.storage import Store
from test_daemon import EXAMPLE, report


def rejected_records():
    """Build synthetic failures covering each existing receive rejection predicate."""
    raw = bytearray.fromhex(report().split()[-1])
    header = raw.copy()
    header[0] ^= 1
    family = raw.copy()
    family[21] = 0x8C  # This family requires length 41, not 39.
    checksum = raw.copy()
    checksum[32] ^= 1
    command = bytearray(frame("2a992b12", "a7", "46715621", "0002", 1, 2, bytes(8)))
    command[37] ^= 1
    return (
        ("envelope", "RX"),
        ("envelope", "RX x 0 -42 99 3 020000"),
        ("envelope", "RX 1 0 -42 99 3 zzzzzz"),
        ("envelope", "RX 1 0 -42 99 4 020000"),
        ("envelope", "RX -1 0 -42 99 3 020000"),
        ("envelope", "RX 1 4294967296 -42 99 3 020000"),
        ("unsupported_frame", "RX 1 0 -42 99 3 020000"),
        ("unsupported_frame", f"RX 1 0 -42 99 39 {header.hex()}"),
        ("unsupported_family", f"RX 1 0 -42 99 39 {family.hex()}"),
        ("checksum", f"RX 1 0 -42 99 39 {checksum.hex()}"),
        ("checksum", f"RX 1 0 -42 99 41 {command.hex()}"),
    )


class ReceiveDiagnosticsTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        """Create isolated host state without opening a UART or network listener."""
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        config = replace(load(EXAMPLE), database=self.temp.name + "/state.db")
        self.store = Store(config.database, "test")
        self.addCleanup(self.store.close)
        self.service = Service(config, self.store, gateway_factory=Simulator)

    def test_stable_reasons_preserve_value_error_compatibility(self):
        """Every original rejection predicate remains a ValueError with a reason."""
        for reason, line in rejected_records():
            with self.subTest(reason=reason, line=line):
                with self.assertRaises(ValueError) as caught:
                    rx_record(line)
                self.assertIsInstance(caught.exception, ReceiveError)
                self.assertEqual(caught.exception.reason, reason)
        self.assertEqual(rx_record(report())[-1].family, 0x9E)
        # Diagnostics must not add new RSSI/LQI validation.
        self.assertEqual(
            rx_envelope(report().replace("-42 99", "-999 999"))[1:3], (-999, 999)
        )

    def test_each_rejection_counts_once_without_admitting_packets(self):
        """Rejects increment one category and the legacy total without discovery."""
        expected = dict.fromkeys(self.service.diagnostics["rx_rejections"], 0)
        with patch.object(self.service, "observe_discovery") as observe:
            for reason, line in rejected_records():
                expected[reason] += 1
                self.service.handle_rx(1.0, line)
                self.assertEqual(self.service.diagnostics["rx_rejections"], expected)
                self.assertEqual(
                    self.service.diagnostics["invalid_rx"], sum(expected.values())
                )
            observe.assert_not_called()
        for error in (
            ValueError("private detail"),
            ReceiveError("future", "private detail"),
        ):
            with patch("livolod.service.decode", side_effect=error):
                self.service.handle_rx(1.0, report())
            expected["other"] += 1
            self.assertEqual(self.service.diagnostics["rx_rejections"], expected)
        self.assertEqual(self.service.diagnostics["invalid_rx"], sum(expected.values()))

    def test_sequence_and_retained_report_semantics_are_unchanged(self):
        """Valid envelopes advance RX order even if their payload is unsupported."""
        self.service.handle_rx(1.0, report(sequence=1))
        self.service.handle_rx(2.0, "RX 2 0 -42 99 3 020000")
        self.service.handle_rx(3.0, report(sequence=3))
        self.assertEqual(self.service.diagnostics["rx_gaps"], 0)
        self.service.handle_rx(4.0, "RX 99 0 -42 99 3 020000", retained=True)
        self.assertEqual(self.service.last_rx, 3)
        self.service.handle_rx(5.0, "RX 4 0 -42 99 4 020000")
        self.assertEqual(self.service.last_rx, 3)
        self.service.handle_rx(6.0, report(sequence=5))
        self.assertEqual(self.service.diagnostics["rx_gaps"], 1)
        self.assertEqual(self.service.diagnostics["invalid_rx"], 3)
        self.assertEqual(
            self.service.diagnostics["rx_rejections"]["unsupported_frame"], 2
        )
        self.assertEqual(self.service.diagnostics["rx_rejections"]["envelope"], 1)

    def test_native_snapshot_allows_only_named_numeric_counters(self):
        """Public diagnostics omit raw content, unknown keys and malformed values."""
        self.service.handle_rx(1.0, "RX 1 0 -42 99 3 020000")
        counters = self.service.diagnostics["rx_rejections"]
        self.assertEqual(
            native_snapshot(self.service)["diagnostics"]["rx_rejections"], counters
        )
        counters.update(
            raw="private payload", checksum="private detail", envelope=True, other=-1
        )
        public = native_snapshot(self.service)["diagnostics"]["rx_rejections"]
        self.assertEqual(public, {"unsupported_frame": 1, "unsupported_family": 0})
        del self.service.diagnostics["rx_rejections"]
        self.assertNotIn("rx_rejections", native_snapshot(self.service)["diagnostics"])
