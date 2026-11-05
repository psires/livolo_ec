"""Keep the isolated DFU helper on its selected port without vendor discovery."""

from contextlib import ExitStack
import sys
import types
import unittest
from unittest.mock import patch

from livolod import nordic_helper


class DfuTransportSerial:
    """Model the pinned transport's private bootloader check and serial lifetime."""

    instances = []

    def __init__(self, **settings):
        """Record transport policy without opening a real serial device.

        @param settings Exact constructor arguments supplied by the wrapper.
        """
        self.settings = settings
        self.serial_port = None
        self.discovery_calls = 0
        self.closed = 0
        self.instances.append(self)

    def __ensure_bootloader(self):
        """Model the vendor behavior that would search for a different USB port."""
        self.discovery_calls += 1
        self.settings["com_port"] = "/dev/wrong-first-match"

    def open(self):
        """Invoke the vendor's private hook exactly as its serial transport does."""
        self.__ensure_bootloader()
        self.serial_port = object()

    def close(self):
        """Record release of an open transport after either success or failure."""
        self.closed += 1
        self.serial_port = None


class Dfu:
    """Exercise the wrapper around a transfer that can fail at either boundary."""

    instances = []
    failure = None

    def __init__(self, **settings):
        """Capture the exact package, transport and connection delay.

        @param settings Arguments passed to the pinned vendor image sender.
        """
        self.settings = settings
        self.instances.append(self)

    def dfu_send_images(self):
        """Open the selected endpoint and optionally emulate a transport failure."""
        if self.failure == "before_open":
            raise RuntimeError("fixture preparation failure")
        self.settings["dfu_transport"].open()
        if self.failure == "after_open":
            raise RuntimeError("fixture transfer failure")


class NordicHelperTests(unittest.TestCase):
    """Reject unqualified runtimes and prevent implicit USB target substitution."""

    def setUp(self):
        """Install synthetic vendor modules; no USB or serial access is possible."""
        DfuTransportSerial.instances = []
        Dfu.instances = []
        Dfu.failure = None
        modules = {
            name: types.ModuleType(name)
            for name in (
                "nordicsemi",
                "nordicsemi.dfu",
                "nordicsemi.dfu.dfu",
                "nordicsemi.dfu.dfu_transport_serial",
            )
        }
        modules["nordicsemi.dfu.dfu"].Dfu = Dfu
        modules["nordicsemi.dfu.dfu_transport_serial"].DfuTransportSerial = (
            DfuTransportSerial
        )
        stack = ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(patch.dict(sys.modules, modules))
        stack.enter_context(patch.object(sys, "version_info", (3, 10, 22)))
        stack.enter_context(
            patch.object(
                nordic_helper.importlib.metadata, "version", return_value="6.1.7"
            )
        )
        stack.enter_context(
            patch.object(
                sys,
                "argv",
                [
                    "nordic_helper",
                    "--package",
                    "/private/application.zip",
                    "--port",
                    "/dev/livolod-usb/dfu",
                ],
            )
        )

    def test_selected_endpoint_never_enters_vendor_discovery(self):
        """The private hook override must prevent port replacement and USB triggering."""
        nordic_helper.main()
        transport = DfuTransportSerial.instances[0]
        self.assertEqual(transport.discovery_calls, 0)
        self.assertEqual(
            transport.settings,
            dict(
                com_port="/dev/livolod-usb/dfu",
                baud_rate=115200,
                flow_control=False,
                prn=0,
                do_ping=False,
                timeout=20,
            ),
        )
        self.assertEqual(
            Dfu.instances[0].settings["zip_file_path"], "/private/application.zip"
        )
        self.assertEqual(Dfu.instances[0].settings["connect_delay"], 0)
        self.assertEqual(transport.closed, 1)

    def test_transfer_failure_releases_selected_endpoint(self):
        """A failed image transfer cannot leave its serial transport open."""
        Dfu.failure = "after_open"
        with self.assertRaisesRegex(RuntimeError, "fixture transfer failure"):
            nordic_helper.main()
        self.assertEqual(DfuTransportSerial.instances[0].closed, 1)

    def test_preparation_failure_does_not_close_an_unopened_port(self):
        """Preserve the original preparation exception before opening the transport."""
        Dfu.failure = "before_open"
        with self.assertRaisesRegex(RuntimeError, "fixture preparation failure"):
            nordic_helper.main()
        self.assertEqual(DfuTransportSerial.instances[0].closed, 0)

    def test_unqualified_interpreter_or_vendor_version_is_rejected(self):
        """Fail before creating a transport when either pinned runtime identity differs."""
        with patch.object(sys, "version_info", (3, 13, 0)):
            with self.assertRaisesRegex(RuntimeError, "Unsupported isolated"):
                nordic_helper.main()
        with patch.object(
            nordic_helper.importlib.metadata, "version", return_value="6.2.0"
        ):
            with self.assertRaisesRegex(RuntimeError, "Unsupported isolated"):
                nordic_helper.main()
        self.assertEqual(DfuTransportSerial.instances, [])


if __name__ == "__main__":
    unittest.main()
