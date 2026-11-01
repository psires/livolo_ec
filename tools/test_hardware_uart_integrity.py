"""Exercise the UART integrity harness without touching hardware."""

import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

PATH = (
    Path(__file__).resolve().parents[1]
    / "firmware/gateway/tests/hardware_uart_integrity.py"
)
SPEC = importlib.util.spec_from_file_location("hardware_uart_integrity", PATH)
HARNESS = importlib.util.module_from_spec(SPEC)
with patch.dict(sys.modules, {"serial": types.SimpleNamespace()}):
    SPEC.loader.exec_module(HARNESS)


class FakePort:
    def __init__(self, armed=False, corrupt=False, overrun=False):
        """Simulate fragmented UART records and optional device-side failures.

        @param armed Whether preflight must refuse this adapter.
        @param corrupt Whether CHECK returns a parse error.
        @param overrun Whether final STATUS reports a receive overrun.
        """
        self.armed = armed
        self.corrupt = corrupt
        self.overrun = overrun
        self.data = b""
        self.commands = []
        self.status_count = 0

    def __enter__(self):
        """Return this fake exclusive connection."""
        return self

    def __exit__(self, *args):
        """Allow exceptions to propagate as a serial context would.

        @param args Exception context supplied by Python.
        """
        return False

    @property
    def in_waiting(self):
        """Expose at most three bytes to force fragmented reads."""
        return min(3, len(self.data))

    def reset_input_buffer(self):
        """Discard startup data."""
        self.data = b""

    def read(self, size):
        """Consume at most the requested bytes.

        @param size Maximum byte count requested by the harness.
        """
        data, self.data = self.data[:size], self.data[size:]
        return data

    def write(self, data):
        """Queue a matching reply after unsolicited and unrelated records.

        @param data Complete encoded command from the harness.
        """
        if not data.strip():
            return len(data)
        ident, command = data.decode().strip().split(" ", 1)
        self.commands.append(command)
        if command == "CAPS":
            body = "OK proto=3 max_raw=125 baud=460800"
        elif command == "STATUS":
            self.status_count += 1
            errors = int(self.overrun and self.status_count > 1)
            body = (
                f"OK configured=1 armed={int(self.armed)} ack=0 session=abcd "
                "tx=0 ack_tx=0 input_errors=0 uart_drops=0 rx_drops=0 "
                f"uart_rx_errors={errors}"
            )
        elif command.startswith("CHECK "):
            body = "ERR HEX" if self.corrupt else "OK NO_ACK"
        else:
            raise AssertionError(command)
        self.data += f"STATUS rx=1\nRSP 0 OK unrelated\nRSP {ident} {body}\n".encode()
        return len(data)


class IntegrityTests(unittest.TestCase):
    def exercise(self, port, failure=None):
        """Run two real harness requests against a fake serial context.

        @param port Fake connection specifying the observed device behavior.
        @param failure Expected exception type, or None for success.
        @return Saved structured test evidence.
        """
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "result.json"
            argv = [
                str(PATH),
                "--port",
                "fake",
                "--baud",
                "460800",
                "--samples",
                "2",
                "--output",
                str(output),
                "--log",
                str(Path(directory) / "uart.log"),
            ]
            with (
                patch.object(sys, "argv", argv),
                patch.object(
                    HARNESS,
                    "serial",
                    types.SimpleNamespace(Serial=lambda *a, **k: port),
                ),
                patch("builtins.print"),
            ):
                if failure:
                    with self.assertRaises(failure):
                        HARNESS.main()
                else:
                    HARNESS.main()
            return json.loads(output.read_text())

    def test_fragmented_replies(self):
        """Reassemble fragmented replies and ignore unrelated IDs without retries."""
        port = FakePort()
        result = self.exercise(port)
        self.assertTrue(result["passed"])
        self.assertEqual(result["completed"], 2)
        self.assertEqual(len(port.commands), 5)

    def test_corrupt_command(self):
        """Retain a device parse error as a failed request without replaying it."""
        port = FakePort(corrupt=True)
        result = self.exercise(port, SystemExit)
        self.assertFalse(result["passed"])
        self.assertEqual(result["mismatches"], [{"sample": 2, "actual": "ERR HEX"}])
        self.assertEqual(sum(c.startswith("CHECK ") for c in port.commands), 1)

    def test_error_counter(self):
        """Fail even with intact replies when the hardware error counter rises."""
        result = self.exercise(FakePort(overrun=True), SystemExit)
        self.assertEqual(result["mismatches"], [])
        self.assertEqual(result["deltas"]["uart_rx_errors"], 1)
        self.assertFalse(result["passed"])

    def test_armed_adapter(self):
        """Refuse an armed adapter without mutating its state or sending CHECK."""
        port = FakePort(armed=True)
        result = self.exercise(port, RuntimeError)
        self.assertFalse(result["passed"])
        self.assertEqual(port.commands, ["CAPS", "STATUS"])
