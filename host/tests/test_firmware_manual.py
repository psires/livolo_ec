"""Manual Nordic recovery validates identity and phase ownership before DFU."""

import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from livolod.firmware_bundle import BundleError
from livolod.firmware_manual import (
    _helper,
    _target,
    application_target_status,
    install_manual,
    manual_backend_status,
)
from test_firmware_artifacts import nrf_zip


def candidate():
    """Return an in-memory validated-format test package for explicit recovery."""
    data = nrf_zip()
    return dict(
        board="nordic-pca10059",
        backend="nrf52840_usb_dfu_v1",
        version="0.12",
        operation="recovery",
        _data=data,
        sha256=hashlib.sha256(data).hexdigest(),
    )


def usb(device="/dev/ttyACM0", serial="test-serial", vid=0x1915, pid=0x521F):
    """Describe a fake OS-enumerated USB endpoint without opening a device.

    @param device Device path.
    @param serial USB serial identity.
    @param vid USB vendor ID.
    @param pid USB product ID.
    @return Minimal pyserial-compatible metadata record.
    """
    return SimpleNamespace(device=device, serial_number=serial, vid=vid, pid=pid)


class ManualFirmwareTests(unittest.TestCase):
    """Refuse wrong devices/helpers and preserve durable boundaries and child locks."""

    def test_exact_unique_usb_serial_and_factory_mode_are_required(self):
        """The first tty, an application-mode dongle or duplicated serial cannot qualify."""
        for entries, expected in (
            ([usb()], True),
            ([], False),
            ([usb(serial="other")], False),
            ([usb(vid=0xCAFE, pid=0x4001)], False),
            ([usb(), usb(device="/dev/ttyACM1")], False),
            ([usb(device="/dev/ttyACM1")], False),
        ):
            with patch("serial.tools.list_ports.comports", return_value=entries):
                self.assertEqual(_target("/dev/ttyACM0", "test-serial"), expected)
                self.assertFalse(_target("ttyACM0", "test-serial"))
                self.assertFalse(_target("/dev/ttyACM0", ""))

    def test_application_verification_requires_separately_selected_usb_identity(self):
        """A factory-DFU serial or another tty cannot authorize application restoration."""
        with patch("serial.tools.list_ports.comports", return_value=[usb()]):
            self.assertFalse(application_target_status("/dev/ttyACM0", "test-serial"))
        app = usb(serial="application-serial", vid=0xCAFE, pid=0x4001)
        with patch("serial.tools.list_ports.comports", return_value=[app]):
            self.assertTrue(
                application_target_status("/dev/ttyACM0", "application-serial")
            )
            self.assertFalse(application_target_status("/dev/ttyACM0", "test-serial"))
            self.assertFalse(
                application_target_status("/dev/ttyACM1", "application-serial")
            )

    def test_helper_requires_pinned_runtime_and_importable_usb_command(self):
        """Version metadata alone cannot qualify a missing or broken legacy USB helper."""
        for info, code, expected in (
            ({"python": [3, 10], "nrfutil": "6.1.7"}, 0, True),
            ({"python": [3, 13], "nrfutil": "6.1.7"}, 0, False),
            ({"python": [3, 10], "nrfutil": "6.1.6"}, 0, False),
            ({"python": [3, 10], "nrfutil": "6.1.7"}, 1, False),
        ):
            with (
                patch("os.path.isfile", return_value=True),
                patch("os.access", return_value=True),
                patch(
                    "livolod.firmware_manual.subprocess.run",
                    side_effect=[
                        SimpleNamespace(returncode=0, stdout=json.dumps(info).encode()),
                        SimpleNamespace(returncode=code),
                    ],
                ) as run,
            ):
                self.assertEqual(_helper("/isolated/python"), expected)
                for call in run.call_args_list:
                    self.assertEqual(call.kwargs["cwd"], "/")
                    self.assertIn("-I", call.args[0])
                    self.assertNotIn("--port", call.args[0])

    def test_status_is_sanitized_and_wrong_operations_never_probe(self):
        """Public checks expose reasons rather than local paths or helper exceptions."""
        args = dict(
            helper_python="/private/python",
            dfu_port="/private/port",
            serial_number="private-serial",
        )
        with (
            patch("livolod.firmware_manual._target", return_value=True),
            patch("livolod.firmware_manual._helper", side_effect=OSError("private")),
        ):
            result = manual_backend_status(candidate(), **args)
            self.assertFalse(result["executable"])
            self.assertNotIn("private", json.dumps(result))
        with (
            patch("livolod.firmware_manual._target") as target,
            patch("livolod.firmware_manual._helper") as helper,
        ):
            result = manual_backend_status(
                dict(candidate(), operation="update"), **args
            )
            self.assertEqual(result["blocked_reason"], "unsupported_operation")
            target.assert_not_called()
            helper.assert_not_called()

    def invoke(self, value=None, before=None, progress=None):
        """Call the manual transfer with fixed fake endpoints under patched dependencies.

        @param value Optional candidate override.
        @param before Optional phase callback.
        @param progress Optional public transfer event callback.
        """
        install_manual(
            candidate() if value is None else value,
            port_name="/dev/ttyACM0",
            helper_python="/isolated/python",
            serial_number="test-serial",
            lock_fd=42,
            before=before or Mock(),
            progress=progress or Mock(),
        )

    def test_fixed_helper_command_inherits_guard_and_stages_validated_bytes(self):
        """Transfer uses immutable bytes and reports only transfer completion."""
        value, phases, events = candidate(), [], []
        child = Mock()
        child.wait.return_value = 0

        def launch(command, **kwargs):
            """Inspect staged input before returning the fake child process.

            @param command Fixed helper argument vector.
            @param kwargs Process ownership and I/O options.
            @return Completed fake child.
            """
            self.assertEqual(
                command[:6],
                ["/isolated/python", "-I", "-m", "nordicsemi", "dfu", "usb-serial"],
            )
            self.assertEqual(command[-2:], ["--port", "/dev/ttyACM0"])
            path = Path(command[command.index("--package") + 1])
            self.assertEqual(path.read_bytes(), value["_data"])
            self.assertEqual(kwargs["pass_fds"], (42,))
            self.assertEqual(kwargs["cwd"], str(path.parent))
            self.assertNotIn("start_new_session", kwargs)
            self.assertEqual(phases, ["flash"])
            return child

        with (
            patch("livolod.firmware_worker.validate_guard"),
            patch("livolod.firmware_manual._target", return_value=True),
            patch("livolod.firmware_manual._helper", return_value=True),
            patch("livolod.firmware_manual.subprocess.Popen", side_effect=launch),
        ):
            self.invoke(value, before=phases.append, progress=events.append)
        self.assertEqual(events, [{"stage": "transferred"}])
        child.wait.assert_called_once_with(timeout=120)

    def test_parent_loss_changed_payload_or_target_cannot_start_helper(self):
        """A failed durable boundary or changed artifact/USB target prevents mutation."""
        for variant in ("parent", "payload", "target"):
            value = candidate()
            if variant == "payload":
                value["sha256"] = "0" * 64
            before = (
                Mock(side_effect=BundleError("parent_unavailable", "parent lost"))
                if variant == "parent"
                else Mock()
            )
            with (
                patch("livolod.firmware_worker.validate_guard"),
                patch(
                    "livolod.firmware_manual._target",
                    side_effect=[True, variant != "target"],
                ),
                patch("livolod.firmware_manual._helper", return_value=True),
                patch("livolod.firmware_manual.subprocess.Popen") as launch,
            ):
                with self.assertRaises(BundleError):
                    self.invoke(value, before=before)
                launch.assert_not_called()

    def test_timeout_terminates_kills_and_reaps_same_group_helper(self):
        """The helper cannot outlive the recovery worker's bounded timeout handling."""
        child = Mock()
        child.wait.side_effect = [
            subprocess.TimeoutExpired("helper", 120),
            subprocess.TimeoutExpired("helper", 3),
            0,
        ]
        with (
            patch("livolod.firmware_worker.validate_guard"),
            patch("livolod.firmware_manual._target", return_value=True),
            patch("livolod.firmware_manual._helper", return_value=True),
            patch("livolod.firmware_manual.subprocess.Popen", return_value=child),
        ):
            with self.assertRaises(subprocess.TimeoutExpired):
                self.invoke()
        child.terminate.assert_called_once()
        child.kill.assert_called_once()
        self.assertEqual(child.wait.call_count, 3)

    def test_helper_failure_never_reports_transfer_complete(self):
        """A helper's nonzero exit remains a failed transfer requiring reconciliation."""
        child, progress = Mock(), Mock()
        child.wait.return_value = 1
        with (
            patch("livolod.firmware_worker.validate_guard"),
            patch("livolod.firmware_manual._target", return_value=True),
            patch("livolod.firmware_manual._helper", return_value=True),
            patch("livolod.firmware_manual.subprocess.Popen", return_value=child),
            self.assertRaises(BundleError) as caught,
        ):
            self.invoke(progress=progress)
        self.assertEqual(caught.exception.code, "flash_failed")
        progress.assert_not_called()
