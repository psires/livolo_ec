"""Application baud configuration must reach every serial owner consistently."""

from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, Mock, patch

from livolod.config import parse
from livolod.firmware_update import UpdatePort
from livolod.service import Service
from livolod.simulator import Simulator
from livolod.storage import Store
from livolod.uart import Gateway, LinkError

EXAMPLE = Path(__file__).resolve().parents[1] / "config.example.json"


class BaudConfiguration(unittest.TestCase):
    def test_defaults_supported_rates_and_invalid_values(self):
        """Old configurations default safely; unsupported and coercible values fail."""
        raw = json.loads(EXAMPLE.read_text())
        raw.pop("serial_baudrate")
        self.assertEqual(parse(raw).serial_baudrate, 115200)
        for rate in (115200, 230400, 460800, 921600):
            self.assertEqual(
                parse(dict(raw, serial_baudrate=rate)).serial_baudrate, rate
            )
        for rate in (True, "460800", 460800.0, 0, 9600, 1000000, None):
            with self.subTest(rate=rate), self.assertRaises(ValueError):
                parse(dict(raw, serial_baudrate=rate))

    def test_serial_owners_use_configured_rate_without_flow_control(self):
        """Daemon and application updater pass the selected rate to pyserial."""
        with (
            patch("serial.Serial") as serial,
            patch.object(Gateway, "__init__", return_value=None),
        ):
            Gateway.open("/dev/test", 460800)
            self.assertEqual(serial.call_args.args, ("/dev/test", 460800))
            self.assertFalse(serial.call_args.kwargs["rtscts"])
            self.assertFalse(serial.call_args.kwargs["dsrdtr"])
        with patch("serial.Serial") as serial:
            port = UpdatePort("/dev/test", 460800)
            self.assertEqual(serial.call_args.args, ("/dev/test", 460800))
            port.close()
            serial.return_value.close.assert_called_once()
        with patch("serial.Serial") as serial, self.assertRaises(ValueError):
            UpdatePort("/dev/test", True)
        serial.assert_not_called()


class BaudService(unittest.IsolatedAsyncioTestCase):
    async def test_reader_removal_error_still_closes_serial(self):
        """A failing event-loop cleanup cannot skip closing the physical descriptor."""
        gateway = object.__new__(Gateway)
        gateway.closed = False
        gateway.command = AsyncMock(return_value="OK")
        gateway.serial = Mock()
        gateway.loop = Mock()
        gateway.loop.remove_reader.side_effect = RuntimeError("fixture reader failure")
        with self.assertRaisesRegex(RuntimeError, "reader failure"):
            await gateway.close()
        gateway.serial.close.assert_called_once()
        self.assertTrue(gateway.closed)

    async def test_failed_serial_close_does_not_mark_gateway_closed(self):
        """Failed descriptor closure remains visible to the lifetime owner."""
        gateway = object.__new__(Gateway)
        gateway.closed = False
        gateway.command = AsyncMock(return_value="OK")
        gateway.serial = Mock()
        gateway.loop = Mock()
        gateway.serial.close.side_effect = OSError("fixture close failure")
        with self.assertRaises(OSError):
            await gateway.close()
        self.assertFalse(gateway.closed)

    async def test_factory_and_capability_check_before_arming(self):
        """Use the configured rate and reject a contradictory adapter before ARM."""
        config = replace(parse(json.loads(EXAMPLE.read_text())), serial_baudrate=460800)
        with tempfile.TemporaryDirectory() as directory:
            store = Store(directory + "/state.db", "network")
            radio = Simulator()
            service = Service(config, store)
            original = radio.command

            async def command(text, retries=1):
                """Add an advertised baud to the simulator response.

                @param text Requested UART command.
                @param retries Additional transport attempts.
                """
                reply = await original(text, retries)
                return reply + " baud=115200" if text == "CAPS" else reply

            try:
                with patch(
                    "livolod.service.Gateway.open", return_value=radio
                ) as open_port:
                    service.gateway = service.gateway_factory()
                    open_port.assert_called_once_with(config.serial_port, 460800)
                with patch.object(radio, "command", side_effect=command):
                    with self.assertRaisesRegex(LinkError, "baud"):
                        await service.restore()
                self.assertFalse(service.ready)
                self.assertEqual(service.diagnostics["serial_baudrate"], 460800)
                await service.restore()  # Legacy adapters without baud remain compatible.
                self.assertTrue(service.ready)
            finally:
                await radio.close()
                store.close()
