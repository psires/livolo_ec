"""Nordic exact-target selection, fixed helper invocation and restoration boundaries."""

import asyncio
from dataclasses import replace
import hashlib
import io
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from livolod import __version__
from livolod.config import load, parse
from livolod.firmware_bundle import BundleError
from livolod import firmware_nordic as nordic
from livolod.nordic_usb import profile, usb_identity
from livolod.service import Service
from livolod.simulator import Simulator
from livolod.storage import Store
from livolod.uart import LinkError
from test_firmware_artifacts import nrf_zip
from test_firmware_backends import Radio

SETTINGS = dict(
    usb_path="/sys/devices/test/usb3/3-1",
    application_serial="application-serial",
    dfu_serial="dfu-serial",
    device_directory="/run/livolod-usb",
    helper_python="/opt/nrfutil/bin/python3.10",
)


class NordicTests(unittest.TestCase):
    """No manifest, alternate USB identity or unacknowledged phase can select a target."""

    def setUp(self):
        """Construct a nonexecutable package and explicit synthetic USB identities."""
        data = nrf_zip()
        self.candidate = dict(
            board="nordic-pca10059",
            version="0.12",
            backend="nrf52840_usb_dfu_v1",
            app_baudrate=460800,
            sha256=hashlib.sha256(data).hexdigest(),
            _data=data,
        )
        self.record = dict(
            usb_path=SETTINGS["usb_path"],
            serial=SETTINGS["application_serial"],
            vid=0xCAFE,
            pid=0x4001,
            major=166,
            minor=0,
        )

    def test_profile_defaults_disabled_and_requires_exact_application_node(self):
        """Merely installing helper code cannot opt a deployment into Nordic updates."""
        raw = json.loads(
            (Path(__file__).parents[1] / "config.example.json").read_text()
        )
        self.assertEqual(parse(raw).firmware_nordic, {})
        self.assertEqual(nordic.backend_status({}), "backend_unavailable")
        with self.assertRaises(ValueError):
            parse(dict(raw, firmware_nordic=SETTINGS))
        configured = parse(
            dict(
                raw,
                firmware_nordic=SETTINGS,
                serial_port="/run/livolod-usb/application",
            )
        )
        self.assertEqual(configured.firmware_nordic, SETTINGS)
        for value in (
            None,
            {"application_serial": "guessed"},
            dict(SETTINGS, dfu_serial="application-serial"),
        ):
            with self.assertRaises(ValueError):
                profile(value)

    def test_serial_topology_vid_pid_uniqueness_and_node_must_all_match(self):
        """Same tty number, same family, or same serial at another location is insufficient."""
        with patch("livolod.nordic_usb.enumerate_usb", return_value=[self.record]):
            self.assertEqual(usb_identity(SETTINGS, "application"), self.record)
            node = SimpleNamespace(
                st_mode=stat.S_IFCHR | 0o660, st_rdev=os.makedev(166, 0)
            )
            with (
                patch.object(Path, "lstat", return_value=node),
                patch("os.access", return_value=True),
            ):
                self.assertEqual(
                    nordic.target(SETTINGS, "application"),
                    "/run/livolod-usb/application",
                )
                node.st_rdev = os.makedev(166, 1)
                with self.assertRaises(BundleError):
                    nordic.target(SETTINGS, "application")
                node.st_mode = stat.S_IFLNK | 0o777
                with self.assertRaises(BundleError):
                    nordic.target(SETTINGS, "application")
        records = [[], [self.record, self.record]]
        records += [
            [dict(self.record, **changes)]
            for changes in (
                {"serial": "replacement"},
                {"usb_path": "/sys/devices/test/usb3/3-2"},
                {"vid": 0x1915},
                {"pid": 0x521F},
                {"major": 188},
            )
        ]
        for ports in records:
            with (
                self.subTest(ports=ports),
                patch("livolod.nordic_usb.enumerate_usb", return_value=ports),
            ):
                with self.assertRaises(ValueError):
                    usb_identity(SETTINGS, "application")

    def test_manual_published_nodes_require_explicit_serial_and_same_profile(self):
        """Recovery can use container nodes without falling back to tty enumeration."""
        from livolod.firmware_manual import (
            application_target_status,
            manual_backend_status,
        )

        with (
            patch.object(
                nordic,
                "target",
                side_effect=lambda settings, mode: settings["device_directory"]
                + "/"
                + mode,
            ),
            patch("livolod.firmware_manual._helper", return_value=True),
            patch(
                "serial.tools.list_ports.comports",
                side_effect=AssertionError("No fallback enumeration"),
            ),
        ):
            args = dict(
                helper_python=SETTINGS["helper_python"],
                dfu_port="/run/livolod-usb/dfu",
                serial_number=SETTINGS["dfu_serial"],
                usb_profile=SETTINGS,
            )
            candidate = dict(self.candidate, operation="recovery")
            self.assertTrue(manual_backend_status(candidate, **args)["executable"])
            self.assertFalse(
                manual_backend_status(candidate, **dict(args, serial_number="other"))[
                    "executable"
                ]
            )
            self.assertFalse(
                manual_backend_status(candidate, **dict(args, dfu_port="/dev/ttyACM0"))[
                    "executable"
                ]
            )
            self.assertTrue(
                application_target_status(
                    "/run/livolod-usb/application",
                    SETTINGS["application_serial"],
                    usb_profile=SETTINGS,
                )
            )
            self.assertFalse(
                application_target_status(
                    "/run/livolod-usb/application",
                    SETTINGS["dfu_serial"],
                    usb_profile=SETTINGS,
                )
            )

    def perform(self, *, acknowledgement=None, helper=None, returned_version="0.12"):
        """Run the backend with a fake UART/helper while retaining its phase order.

        @param acknowledgement Optional parent callback, including injected ACK failure.
        @param helper Optional child mock with timeout or nonzero exit behavior.
        @param returned_version Application version observed after synthetic USB return.
        @return Recorded phases, progress, UART history and helper spawn for assertions.
        """
        before = Radio("nordic-pca10059")
        after = Radio("nordic-pca10059")
        after.version = returned_version
        child = helper or Mock()
        child.wait.return_value = 0 if helper is None else child.wait.return_value
        phases, progress = [], Mock()
        with (
            patch("livolod.firmware_worker.validate_guard"),
            patch.object(nordic, "backend_status", return_value=None),
            patch("livolod.firmware_manual._helper", return_value=True),
            patch.object(
                nordic,
                "target",
                side_effect=lambda settings, mode: settings["device_directory"]
                + "/"
                + mode,
            ),
            patch.object(
                nordic,
                "wait_target",
                side_effect=lambda settings, mode: settings["device_directory"]
                + "/"
                + mode,
            ),
            patch("livolod.firmware_update.UpdatePort", side_effect=[before, after]),
            patch.object(nordic.subprocess, "Popen", return_value=child) as spawn,
        ):
            try:
                nordic.install(
                    self.candidate,
                    "/run/livolod-usb/application",
                    9,
                    acknowledgement or phases.append,
                    progress,
                    SETTINGS,
                )
            except BaseException:
                self.failure_spawn, self.failure_before, self.failure_progress = (
                    spawn,
                    before,
                    progress,
                )
                raise
        return phases, progress, before, after, spawn

    def test_fixed_helper_only_after_ack_and_exact_application_return(self):
        """The helper inherits ownership and may transfer only after durable flash ACK."""
        phases, progress, before, after, spawn = self.perform()
        self.assertEqual(phases, ["bootloader_entry", "flash"])
        self.assertIn("BOOTLOADER", before.commands)
        self.assertTrue(before.closed and after.closed)
        command = spawn.call_args.args[0]
        self.assertEqual(command[:2], [SETTINGS["helper_python"], "-I"])
        self.assertTrue(command[2].endswith("/nordic_helper.py"))
        self.assertEqual(command[-2:], ["--port", "/run/livolod-usb/dfu"])
        self.assertEqual(spawn.call_args.kwargs["pass_fds"], (9,))
        self.assertNotIn("start_new_session", spawn.call_args.kwargs)
        progress.assert_called_once_with({"stage": "transferred"})

    def test_missing_ack_prevents_bootloader_and_helper(self):
        """Lost durable parent ownership cannot trigger a USB-mode mutation."""
        with self.assertRaises(RuntimeError):
            self.perform(acknowledgement=Mock(side_effect=RuntimeError("parent gone")))
        self.failure_spawn.assert_not_called()
        self.assertNotIn("BOOTLOADER", self.failure_before.commands)
        self.failure_progress.assert_not_called()

    def test_failed_helper_or_wrong_application_never_reports_transfer_success(self):
        """A subprocess exit or USB return alone cannot establish a verified transfer."""
        child = Mock()
        child.wait.return_value = 1
        for values in ({"helper": child}, {"returned_version": "0.11"}):
            with self.subTest(values=values), self.assertRaises(BundleError):
                self.perform(**values)
            self.failure_progress.assert_not_called()

    def test_helper_timeout_kills_and_reaps_before_propagating_failure(self):
        """A timed-out helper cannot keep writing after its parent resumes cleanup."""
        child = Mock()
        child.wait.side_effect = [
            subprocess.TimeoutExpired("helper", 120),
            subprocess.TimeoutExpired("helper", 3),
            0,
        ]
        with self.assertRaises(subprocess.TimeoutExpired):
            self.perform(helper=child)
        child.terminate.assert_called_once()
        child.kill.assert_called_once()
        self.assertEqual(child.wait.call_count, 3)
        self.failure_progress.assert_not_called()


class NordicRestoreTests(unittest.IsolatedAsyncioTestCase):
    """Normal restoration must never arm an unverified or replaced Nordic board."""

    async def asyncSetUp(self):
        """Create an isolated service with a synthetic interrupted Nordic update."""
        self.temp = tempfile.TemporaryDirectory()
        self.config = replace(
            load(Path(__file__).parents[1] / "config.example.json"),
            database=self.temp.name + "/state.db",
            serial_baudrate=460800,
            serial_port="/run/livolod-usb/application",
            firmware_nordic=SETTINGS,
        )
        self.store = Store(self.config.database, "network")
        self.service = Service(self.config, self.store)
        self.manager = self.service.firmware_manager
        self.manager.job = dict(
            request_id="nordic-job",
            candidate_id="fixture:hash",
            board="nordic-pca10059",
            version=__version__,
            installed_version="0.12",
            backend="nrf52840_usb_dfu_v1",
            baudrate=460800,
            status="unknown",
            recovery="recovery_required",
            nordic_profile=dict(SETTINGS),
            transfer_completed=False,
        )

    async def asyncTearDown(self):
        """Release disposable database state after all synthetic service work stops."""
        self.store.close()
        self.temp.cleanup()

    async def test_partial_transfer_blocks_gateway_open_on_restart(self):
        """Restarting after an uncertain flash cannot reopen or arm the application."""
        self.service.gateway_factory = Mock(
            side_effect=AssertionError("must not open radio")
        )
        task = asyncio.create_task(self.service.run())
        await asyncio.sleep(0.02)
        self.service.stop.set()
        await asyncio.wait_for(task, 2)
        self.service.gateway_factory.assert_not_called()

    async def test_replaced_usb_blocks_before_network_or_arm(self):
        """Even a matching version response cannot authorize a different physical device."""
        self.manager.job["transfer_completed"] = True
        gateway = Simulator()
        original = gateway.command
        history = []

        async def command(text, retries=1):
            """Supply matching protocol facts and record any attempted network mutations.

            @param text Requested synthetic UART command.
            @param retries Simulated retry parameter.
            @return Protocol response from the fake adapter.
            """
            history.append(text)
            if text == "CAPS":
                return (
                    await original(text, retries)
                    + " board=nordic-pca10059 fw_version="
                    + __version__
                )
            if text == "VERSION":
                return "OK board=nordic-pca10059 proto=3 fw_version=" + __version__
            return await original(text, retries)

        gateway.command = command
        self.service.gateway = gateway
        with patch.object(
            nordic,
            "target",
            side_effect=BundleError("usb_identity_unavailable", "replaced"),
        ):
            with self.assertRaises(LinkError):
                await self.service.restore()
        self.assertEqual(history, ["PING", "CAPS", "VERSION"])
        self.assertFalse(self.service.ready)
        self.assertTrue(self.manager.busy)

    def test_success_requires_recorded_completion_profile_and_fresh_facts(self):
        """Only a matching successful transfer can clear the durable recovery gate."""
        caps = dict(
            board="nordic-pca10059", proto="3", fw_version=__version__, baud="460800"
        )
        self.manager.version_info = dict(caps)
        with patch.object(nordic, "target", return_value=self.config.serial_port):
            self.manager.restored(caps)
            self.assertTrue(self.manager.busy)
            self.manager.job["transfer_completed"] = True
            self.manager.restored(dict(caps, fw_version="0.1"))
            self.assertTrue(self.manager.busy)
            self.manager.restored(caps)
        self.assertEqual(self.manager.job["status"], "confirmed")
        self.assertFalse(self.manager.busy)


if __name__ == "__main__":
    unittest.main()
