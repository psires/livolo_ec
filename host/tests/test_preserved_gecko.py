"""Exercise the unregistered Gecko worker using only synthetic bytes and injected I/O."""

from copy import deepcopy
from contextlib import closing
from dataclasses import replace
import fcntl
import hashlib
import os
from pathlib import Path
import sqlite3
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch

from livolod import FIRMWARE_COMPAT_VERSION
from livolod.firmware_backends import _run_flasher
from livolod.firmware_bundle import BundleError
from livolod.preserved_gecko import (
    APP_BAUD,
    BOARD,
    LOADER_BAUD,
    PHYSICAL_INSTALL_SUPPORTED,
    GeckoInstallError,
    GeckoTarget,
    install_preserved_gecko,
)
from test_firmware_bundle import gbl, mg21_image


class PreservedGeckoTests(unittest.TestCase):
    def setUp(self):
        """Create a real local guard/journal but no device nodes or serial connections."""
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.guard = os.open(self.root / "lease", os.O_CREAT | os.O_RDWR, 0o600)
        fcntl.flock(self.guard, fcntl.LOCK_EX | fcntl.LOCK_NB)
        self.journal = sqlite3.connect(self.root / "journal.sqlite3")
        self.journal.execute("CREATE TABLE boundaries (phase TEXT)")
        self.addCleanup(self.directory.cleanup)
        self.addCleanup(os.close, self.guard)
        self.addCleanup(self.journal.close)
        self.target = GeckoTarget(
            port="/dev/test-only-zbgw04",
            usb_path="fixture-1-2",
            attachment="fixture-generation-1",
            device_number=(188, 0),
            interface="00",
            loader_version="1.9.2",
        )
        self.endpoint = dict(
            identity=self.target.identity(),
            unique=True,
            owner="exclusive",
            board_evidence="operator_confirmed_zbgw04_1v2",
        )
        self.loader = dict(
            kind="gecko",
            version=self.target.loader_version,
            baud=115200,
            upload_supported=True,
            requires_signed=False,
            requires_encrypted=False,
        )
        facts = dict(board=BOARD, fw_version=FIRMWARE_COMPAT_VERSION, proto="3")
        self.application = dict(
            version=dict(facts),
            caps=dict(facts, baud="460800"),
            status=dict(armed="0", ack="0"),
        )
        self.data = gbl(mg21_image(BOARD, FIRMWARE_COMPAT_VERSION))
        self.events, self.uploads, self.paths = [], [], []
        self.observe = Mock(side_effect=lambda: deepcopy(self.endpoint))
        self.probe = Mock(side_effect=lambda *args, **kwargs: deepcopy(self.loader))
        self.verify = Mock(
            side_effect=lambda *args, **kwargs: deepcopy(self.application)
        )

    def acknowledge(self, stage):
        """Durably acknowledge the phase before granting fake transfer permission.

        @param stage Exact worker mutation boundary.
        """
        with self.journal:
            self.journal.execute("INSERT INTO boundaries VALUES (?)", (stage,))
        self.events.append(stage)

    def flash(self, path, port, guard):
        """Assert durable permission and retained ownership, then record a fake upload.

        @param path Private immutable staging file, not a caller-supplied path.
        @param port Exact frozen endpoint passed without fallback.
        @param guard Inherited parent descriptor that must remain open and held.
        """
        with closing(sqlite3.connect(self.root / "journal.sqlite3")) as reader:
            self.assertEqual(
                reader.execute("SELECT phase FROM boundaries").fetchall(), [("flash",)]
            )
        competitor = os.open(self.root / "lease", os.O_RDWR)
        try:
            with self.assertRaises(BlockingIOError):
                fcntl.flock(competitor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        finally:
            os.close(competitor)
        self.assertEqual(port, self.target.port)
        self.assertEqual(guard, self.guard)
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.uploads.append(path.read_bytes())
        self.paths.append(path)

    def install(self, **overrides):
        """Run the internal function with every physical boundary injected.

        @param overrides Replace a selected input or callback for one failure case.
        """
        arguments = dict(
            target=self.target,
            data=self.data,
            sha256=hashlib.sha256(self.data).hexdigest(),
            version=FIRMWARE_COMPAT_VERSION,
            lease_fd=self.guard,
            before=self.acknowledge,
            progress=self.events.append,
            observe=self.observe,
            probe_loader=self.probe,
            verify_application=self.verify,
            helper_status=lambda candidate: None,
            flash=self.flash,
        )
        arguments.update(overrides)
        return install_preserved_gecko(**arguments)

    def test_same_version_provisioning_has_durable_checkpoint_and_firmware_only_receipt(
        self,
    ):
        """Explicit provisioning can verify the selected version without restoring a network."""
        result = self.install()
        self.assertFalse(PHYSICAL_INSTALL_SUPPORTED)
        self.assertEqual(result["stage"], "application_verified")
        self.assertTrue(result["application_verified"])
        self.assertFalse(result["network_ready"])
        self.assertTrue(result["setup_required"])
        self.assertFalse(result["image_readback_verified"])
        self.assertEqual(self.uploads, [self.data])
        self.assertFalse(any(path.exists() for path in self.paths))
        self.probe.assert_called_once_with(self.target.port, LOADER_BAUD, timeout=3)
        self.verify.assert_called_once_with(self.target.port, APP_BAUD, timeout=3)
        self.assertEqual(self.observe.call_count, 5)
        self.assertEqual(
            self.events,
            [
                {"stage": "verifying_target"},
                "flash",
                {"stage": "verifying_application"},
            ],
        )
        os.fstat(self.guard)  # The parent still owns the lease after worker completion.

    def test_wrong_hash_profile_and_protected_gbl_fail_before_any_physical_io(self):
        """No target inspection occurs for unsupported or changed application bytes."""
        bad_gbl = gbl(mg21_image(BOARD, FIRMWARE_COMPAT_VERSION), address=0)
        cases = (
            dict(sha256="0" * 64),
            dict(target=replace(self.target, board="sonoff-zbdongle-e")),
            dict(target=replace(self.target, port="https://invalid")),
            dict(data=bad_gbl, sha256=hashlib.sha256(bad_gbl).hexdigest()),
            dict(version="0.1"),
        )
        for arguments in cases:
            with (
                self.subTest(arguments=list(arguments)),
                self.assertRaises(GeckoInstallError) as error,
            ):
                self.install(**arguments)
            self.assertFalse(error.exception.mutation_possible)
        self.observe.assert_not_called()
        self.probe.assert_not_called()
        self.assertFalse(self.uploads)

    def test_missing_helper_and_invalid_parent_guard_block_before_probe(self):
        """Availability and inherited ownership must be established before loader I/O."""
        for arguments, code in (
            (
                dict(helper_status=lambda candidate: "backend_unavailable"),
                "backend_unavailable",
            ),
            (dict(lease_fd=-1), "radio_busy"),
        ):
            with self.subTest(code=code), self.assertRaises(GeckoInstallError) as error:
                self.install(**arguments)
            self.assertEqual(error.exception.code, code)
        self.observe.assert_not_called()
        self.probe.assert_not_called()

    def test_ambiguous_owner_and_bridge_only_board_evidence_are_rejected(self):
        """A known USB bridge never substitutes for exact board or exclusive owner evidence."""
        for fields, code in (
            ({"unique": False}, "target_changed"),
            ({"owner": "unknown"}, "ownership_unresolved"),
            ({"owner": "conflict"}, "ownership_unresolved"),
            ({"board_evidence": "usb_bridge"}, "board_evidence_required"),
        ):
            original = dict(self.endpoint)
            self.endpoint.update(fields)
            with self.subTest(code=code), self.assertRaises(GeckoInstallError) as error:
                self.install()
            self.assertEqual(error.exception.code, code)
            self.assertFalse(error.exception.mutation_possible)
            self.endpoint = original
        self.probe.assert_not_called()

    def test_unknown_or_security_restricted_loader_never_uploads(self):
        """Historical version and helper support do not admit another loader mode."""
        for field, value in (
            ("kind", "unknown"),
            ("version", "other"),
            ("baud", 460800),
            ("upload_supported", False),
            ("upload_supported", 1),
            ("requires_signed", True),
            ("requires_encrypted", True),
        ):
            original = dict(self.loader)
            self.loader[field] = value
            with (
                self.subTest(field=field),
                self.assertRaises(GeckoInstallError) as error,
            ):
                self.install()
            self.assertEqual(error.exception.code, "loader_unverified")
            self.assertFalse(error.exception.mutation_possible)
            self.loader = original
        self.assertFalse(self.uploads)
        self.verify.assert_not_called()

    def test_reused_node_after_durable_ack_does_not_upload(self):
        """Identity is rechecked after parent acknowledgement, immediately before helper open."""

        def replace_after_ack(stage):
            """Simulate unplug/replug while the parent persisted the boundary.

            @param stage Mutation boundary to commit before changing observed identity.
            """
            self.acknowledge(stage)
            self.endpoint["identity"]["attachment"] = "another-device"

        with self.assertRaises(GeckoInstallError) as error:
            self.install(before=replace_after_ack)
        self.assertEqual(error.exception.code, "target_changed")
        self.assertFalse(error.exception.mutation_possible)
        self.assertFalse(self.uploads)

    def test_ack_failure_never_invokes_helper(self):
        """Lost parent acknowledgement stops before transfer and retains the parent lease."""
        with self.assertRaises(GeckoInstallError) as error:
            self.install(before=Mock(side_effect=OSError("journal unavailable")))
        self.assertFalse(error.exception.mutation_possible)
        self.assertEqual(error.exception.stage, "flash")
        self.assertFalse(self.uploads)
        self.verify.assert_not_called()

    def test_helper_failure_is_uncertain_and_payload_is_cleaned(self):
        """An upload failure can require recovery even when the helper reports no success."""

        def fail_upload(path, port, guard):
            """Record partial fake transfer before reporting helper failure.

            @param path Private application file to inspect and later delete.
            @param port Exact test-only endpoint.
            @param guard Parent-owned guard descriptor.
            """
            self.flash(path, port, guard)
            raise BundleError("flash_failed", "private helper detail")

        with self.assertRaises(GeckoInstallError) as error:
            self.install(flash=fail_upload)
        self.assertEqual(error.exception.code, "flash_failed")
        self.assertTrue(error.exception.mutation_possible)
        self.assertEqual(error.exception.recovery, "recovery_required")
        self.assertNotIn("private helper detail", str(error.exception))
        self.assertFalse(any(path.exists() for path in self.paths))
        self.verify.assert_not_called()

    def test_changed_endpoint_after_upload_requires_recovery(self):
        """Successful upload cannot authorize verification against a substituted node."""

        def swap_after_upload(path, port, guard):
            """Complete fake upload, then replace the selected USB attachment.

            @param path Private synthetic application file.
            @param port Exact test endpoint.
            @param guard Parent lease.
            """
            self.flash(path, port, guard)
            self.endpoint["identity"]["device_number"] = [188, 1]

        with self.assertRaises(GeckoInstallError) as error:
            self.install(flash=swap_after_upload)
        self.assertTrue(error.exception.mutation_possible)
        self.assertEqual(error.exception.code, "target_changed")
        self.verify.assert_not_called()

    def test_separate_version_caps_and_status_all_must_agree(self):
        """Partial identity agreement or enabled RF cannot establish application verification."""
        for group, field, value in (
            ("version", "board", "sonoff-zbdongle-e"),
            ("caps", "fw_version", "0.1"),
            ("version", "proto", "2"),
            ("caps", "baud", "115200"),
            ("status", "armed", "1"),
            ("status", "ack", "1"),
        ):
            original = deepcopy(self.application)
            self.application[group][field] = value
            with self.journal:
                self.journal.execute("DELETE FROM boundaries")
            with (
                self.subTest(group=group, field=field),
                self.assertRaises(GeckoInstallError) as error,
            ):
                self.install()
            self.assertEqual(error.exception.code, "application_mismatch")
            self.assertTrue(error.exception.mutation_possible)
            self.application = original

    def test_provider_timeout_after_flash_never_returns_verified_receipt(self):
        """A missing application reply remains unknown after a possibly successful upload."""
        with self.assertRaises(GeckoInstallError) as error:
            self.install(verify_application=Mock(side_effect=TimeoutError("no reply")))
        self.assertEqual(error.exception.stage, "verifying_application")
        self.assertTrue(error.exception.mutation_possible)

    def test_reused_helper_has_fixed_arguments_and_bounded_timeout_cleanup(self):
        """The shared helper inherits ownership and has no reset/force/mass-erase flags."""
        child = Mock()
        child.wait.side_effect = [
            subprocess.TimeoutExpired("fake-helper", 120),
            subprocess.TimeoutExpired("fake-helper", 3),
            0,
        ]
        with patch(
            "livolod.firmware_backends.subprocess.Popen", return_value=child
        ) as launch:
            with self.assertRaises(subprocess.TimeoutExpired):
                _run_flasher(self.root / "synthetic.gbl", self.target.port, self.guard)
        arguments = launch.call_args.args[0]
        self.assertEqual(arguments[1:3], ["-m", "universal_silabs_flasher"])
        self.assertEqual(
            arguments[3:],
            [
                "--device",
                self.target.port,
                "--bootloader-baudrate",
                "115200",
                "--probe-method",
                "bootloader",
                "flash",
                "--firmware",
                str(self.root / "synthetic.gbl"),
            ],
        )
        self.assertEqual(launch.call_args.kwargs["pass_fds"], (self.guard,))
        child.terminate.assert_called_once()
        child.kill.assert_called_once()
        self.assertEqual(
            [call.kwargs["timeout"] for call in child.wait.call_args_list], [120, 3, 3]
        )

    def test_injected_adapter_does_not_open_serial_scan_or_run_real_process(self):
        """A complete offline adapter test is physically inert at every external boundary."""
        with (
            patch("serial.Serial", side_effect=AssertionError("no serial")),
            patch(
                "serial.tools.list_ports.comports",
                side_effect=AssertionError("no scan"),
            ),
            patch("subprocess.Popen", side_effect=AssertionError("no process")),
        ):
            self.assertTrue(self.install()["application_verified"])

    def test_unreaped_helper_is_not_reported_as_released_or_verified(self):
        """Exhausting kill/reap timeouts leaves recovery and supervision outstanding."""
        child = Mock()
        child.wait.side_effect = [
            subprocess.TimeoutExpired("fake-helper", 120),
            subprocess.TimeoutExpired("fake-helper", 3),
            subprocess.TimeoutExpired("fake-helper", 3),
        ]
        with patch("livolod.firmware_backends.subprocess.Popen", return_value=child):
            with self.assertRaises(GeckoInstallError) as error:
                self.install(flash=_run_flasher)
        self.assertTrue(error.exception.mutation_possible)
        self.assertEqual(error.exception.recovery, "recovery_required")
        self.assertEqual(error.exception.stage, "flash")
        self.verify.assert_not_called()
        os.fstat(self.guard)  # No claim that the parent/descendant guard was released.
