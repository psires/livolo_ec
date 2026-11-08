"""Offline provider tests: fake sysfs/serial and real temporary cooperative file locks."""

from dataclasses import replace
import errno
import ctypes
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from livolod.firmware_bundle import BundleError
from livolod.preserved_gecko import GeckoTarget, check_loader
from livolod.zbgw04_provider import (
    BOARD_EVIDENCE,
    _check_attachment_environment,
    _LinuxStatFS,
    DedicatedAccess,
    LinuxUSB,
    LoaderPolicy,
    PhysicalLease,
    ZBGW04Provider,
    select_target,
)


MENU = b"Gecko Bootloader v1.9.2\r\n" b"1. upload gbl\r\n2. run\r\n3. ebl info\r\nBL >"
PATTERN = re.compile(
    rb"(?P<type>Gecko|\w+ Serial) Bootloader v(?P<version>[0-9.]+)\r\n"
    rb"1\. upload (?:gbl|ebl)\r\n2\. run\r\n3\. ebl info\r\nBL >"
)


class Clock:
    """Deterministic advancing clock to bound empty-read tests without wall-clock sleeps."""

    def __init__(self):
        """Start at a fixed monotonic origin."""
        self.now = 100.0

    def __call__(self):
        """Advance slightly on every budget check."""
        self.now += 0.001
        return self.now

    def sleep(self, seconds):
        """Advance an injected bounded startup wait without sleeping the test process.

        @param seconds Requested startup grace in seconds.
        """
        self.now += seconds


class Serial:
    """Fake deferred-open byte stream; never opens a real device."""

    def __init__(self, clock, data=MENU):
        """Configure initial loader bytes and a test clock.

        @param clock Deterministic test clock.
        @param data Initial response bytes; None enables correlated application replies.
        """
        self.clock, self.data = clock, data
        self.pending = bytearray()
        self.events, self.writes = [], []
        self.dtr = self.rts = True
        self.closed = False
        self.rewrite = lambda raw: raw

    def open(self):
        """Record modem-line state at open without accessing hardware."""
        self.events.append(("open", self.dtr, self.rts, self.port))

    def fileno(self):
        """Return a fake descriptor consumed only by injected stat."""
        return 9876

    def write(self, data):
        """Queue a fixed menu or a correlated application reply.

        @param data Provider command bytes.
        """
        self.writes.append(data)
        if data == b"2":
            self.data = None
            self.pending.clear()
            return 1
        if self.data is not None:
            self.pending.extend(self.data)
        else:
            ident, command = data.decode().split()
            fields = {
                "VERSION": "board=zb-gw04-1v2 fw_version=0.14 proto=3",
                "CAPS": "board=zb-gw04-1v2 fw_version=0.14 proto=3 baud=460800",
                "STATUS": "armed=0 ack=0",
            }
            response = f"RSP {ident} OK {command} {fields[command]}\n".encode()
            self.pending.extend(self.rewrite(response))
        return len(data)

    def read(self, size):
        """Consume at most requested bytes and advance the simulated timeout.

        @param size Requested maximum read size.
        """
        self.clock.now += 0.05
        data = bytes(self.pending[:size])
        del self.pending[:size]
        return data

    def close(self):
        """Record cleanup, allowing tests to verify failure paths close the stream."""
        self.closed = True


class ProviderTests(unittest.TestCase):
    def setUp(self):
        """Create an exact fake target and real temporary lease; no physical I/O."""
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.target = GeckoTarget(
            port="/dev/ttyUSB0",
            usb_path="devices/platform/usb1/1-2",
            attachment="generation-one",
            device_number=(20, 0),
            interface="00",
            loader_version="1.9.2",
        )
        self.lease = PhysicalLease.acquire(self.target, self.root)
        self.addCleanup(self.lease.close)
        self.clock = Clock()
        self.serial = Serial(self.clock)
        self.factory = Mock(return_value=self.serial)
        self.inventory = Mock(return_value=(self.target,))
        self.access = DedicatedAccess(
            self.target.attachment, self.lease.name, True, True, ()
        )
        self.exclusion = Mock(return_value=self.access)
        self.node = SimpleNamespace(
            st_mode=stat.S_IFCHR | 0o600, st_rdev=os.makedev(20, 0)
        )
        self.policy = LoaderPolicy(
            "generation-one", "reviewed-unit-policy", "1.9.2", False, False
        )
        self.provider = ZBGW04Provider(
            self.target,
            self.lease,
            board_evidence=BOARD_EVIDENCE,
            access_check=self.exclusion,
            loader_policy=self.policy,
            inventory=self.inventory,
            serial_factory=self.factory,
            clock=self.clock,
            fd_stat=lambda fd: self.node,
            menu_pattern=lambda: PATTERN,
            claim_exclusive=Mock(),
            sleep=self.clock.sleep,
        )

    def fails(self, code, action):
        """Require the stable expected failure code.

        @param code Expected BundleError code.
        @param action Zero-argument operation under test.
        """
        with self.assertRaises(BundleError) as caught:
            action()
        self.assertEqual(caught.exception.code, code)

    def loader(self):
        """Invoke the explicit bounded loader inspection on the frozen endpoint."""
        return self.provider.probe_loader(self.target.port, 115200, timeout=3)

    def application(self):
        """Invoke the three-query application inspection with one shared budget."""
        return self.provider.verify_application(self.target.port, 460800, timeout=3)

    def test_metadata_observation_never_opens_serial(self):
        """Constructing and observing a provider cannot reset or probe the radio."""
        observed = self.provider.observe()
        self.assertEqual(observed["identity"], self.target.identity())
        self.factory.assert_not_called()

    def test_board_evidence_not_inferred_from_ch340(self):
        """A transport match never substitutes for exact board confirmation."""
        self.provider.board_evidence = "usb_vid_pid"
        self.fails("board_evidence_required", self.loader)
        self.factory.assert_not_called()

    def test_changed_attachment_and_ambiguous_alias_block_before_open(self):
        """Reattachment or duplicate physical endpoint metadata prevents any serial write."""
        for candidates in (
            (replace(self.target, attachment="new"),),
            (self.target, replace(self.target, port="/dev/ttyUSB1")),
            (),
        ):
            with self.subTest(candidates=candidates):
                self.inventory.return_value = candidates
                self.fails("target_changed", self.loader)
        self.factory.assert_not_called()

    def test_missing_external_exclusion_is_not_capability(self):
        """Unknown, denied and truthy nonboolean ownership assertions fail closed."""
        for value in (False, None, "exclusive", 1):
            self.exclusion.return_value = value
            self.fails("ownership_unresolved", self.loader)
        self.factory.assert_not_called()

    def test_dedicated_access_rejects_conflict_active_service_or_stale_binding(self):
        """Consent never overrides detected conflicts, active Livolo ownership or stale binding."""
        for access in (
            replace(self.access, attachment="old"),
            replace(self.access, lease_name="wrong.lock"),
            replace(self.access, dedicated_confirmed=False),
            replace(self.access, own_service_quiesced=False),
            replace(self.access, detected_conflicts=("another-owner",)),
            replace(self.access, detected_conflicts=[]),
        ):
            self.exclusion.return_value = access
            self.fails("ownership_unresolved", self.loader)
        self.factory.assert_not_called()

    def test_secure_unknown_or_stale_loader_policy_blocks_before_open(self):
        """A known version cannot override secure flags or an unknown exact-unit policy."""
        for policy in (
            None,
            replace(self.policy, requires_signed=True),
            replace(self.policy, requires_encrypted=True),
            replace(self.policy, requires_signed=0),
            replace(self.policy, attachment="stale"),
            replace(self.policy, evidence_id=""),
        ):
            self.provider.loader_policy = policy
            self.fails("loader_unverified", self.loader)
        self.factory.assert_not_called()

    def test_loader_inspection_only_menu_with_inactive_modem_lines(self):
        """The complete reviewed menu yields adapter evidence without upload/run/reset."""
        result = self.loader()
        check_loader(self.target, result)
        self.assertEqual(self.serial.writes, [b"\n3"])
        self.assertEqual(self.serial.events, [("open", False, False, "/dev/ttyUSB0")])
        self.assertTrue(self.serial.closed)
        self.assertIsNone(self.factory.call_args.kwargs["port"])
        self.assertTrue(self.factory.call_args.kwargs["exclusive"])

    def test_loader_mismatch_security_banner_and_overflow_rejected(self):
        """Wrong version, secure indications and excessive menu bytes do not qualify."""
        for data in (
            MENU.replace(b"1.9.2", b"2.0.0"),
            MENU.replace(b"Gecko", b"Ember Serial"),
            MENU.replace(b"upload gbl", b"upload ebl"),
            b"signed images required\n" + MENU,
            b"x" * 4097,
        ):
            self.serial.data = data
            self.fails("loader_unverified", self.loader)
            self.assertTrue(self.serial.closed)

    def test_partial_menu_timeout_closes_and_does_not_retry(self):
        """A banner alone is not a menu and inspection has one bounded write."""
        self.serial.data = b"Gecko Bootloader v1.9.2"
        self.fails("inspection_timeout", self.loader)
        self.assertEqual(self.serial.writes, [b"\n3"])
        self.assertTrue(self.serial.closed)
        self.assertLess(self.clock.now, 104)

    def test_wrong_opened_device_closes_before_write(self):
        """Replaced pathname resolving to a different rdev is rejected after open."""
        self.node.st_rdev = os.makedev(20, 1)
        self.fails("target_changed", self.loader)
        self.assertEqual(self.serial.writes, [])
        self.assertTrue(self.serial.closed)
        self.provider.claim_exclusive.assert_not_called()

    def test_serial_busy_or_kernel_exclusion_failure_never_writes(self):
        """A detected busy device or failed exclusive claim cannot be ignored."""
        self.provider.claim_exclusive.side_effect = OSError(errno.EBUSY, "busy")
        self.fails("radio_busy", self.loader)
        self.assertEqual(self.serial.writes, [])
        self.assertTrue(self.serial.closed)

    def test_partial_menu_write_is_not_retried(self):
        """An uncertain partial query is stopped, closed and reported without retransmission."""
        self.serial.write = Mock(return_value=1)
        self.fails("inspection_failed", self.loader)
        self.serial.write.assert_called_once_with(b"\n3")
        self.assertTrue(self.serial.closed)

    def test_postread_attachment_change_rejects_evidence(self):
        """A target change during serial inspection invalidates otherwise valid results."""
        self.inventory.side_effect = [
            (self.target,),
            (self.target,),
            (replace(self.target, attachment="new"),),
        ]
        self.fails("target_changed", self.loader)
        self.assertTrue(self.serial.closed)

    def test_application_separate_correlated_replies_disarmed(self):
        """Three independently identified replies establish identity and disabled ACK/RF."""
        self.serial.data = None
        result = self.application()
        self.assertEqual(result["version"]["fw_version"], "0.14")
        commands = [data.decode().split()[1] for data in self.serial.writes]
        self.assertEqual(commands, ["VERSION", "CAPS", "STATUS"])
        self.assertEqual(len({data.split()[0] for data in self.serial.writes}), 3)
        self.assertTrue(self.serial.closed)

    def test_application_rejects_board_version_baud_armed_and_duplicate_fields(self):
        """Independent identity disagreements or active RF cannot become postflight success."""
        replacements = (
            (b"board=zb-gw04-1v2", b"board=sonoff-zbdongle-e"),
            (
                b"CAPS board=zb-gw04-1v2 fw_version=0.14",
                b"CAPS board=zb-gw04-1v2 fw_version=0.13",
            ),
            (b"baud=460800", b"baud=115200"),
            (b"armed=0", b"armed=1"),
            (b"ack=0", b"ack=1"),
            (b"proto=3", b"proto=3 proto=3"),
        )
        for old, new in replacements:
            with self.subTest(old=old, new=new):
                self.serial.data = None
                self.serial.rewrite = lambda raw: raw.replace(old, new)
                self.fails("application_mismatch", self.application)
                self.assertTrue(self.serial.closed)

    def test_application_stale_ids_timeout_without_retries(self):
        """A response for another request cannot satisfy postflight."""
        self.serial.data = (
            b"RSP 0 OK VERSION board=zb-gw04-1v2 fw_version=0.14 proto=3\n"
        )
        self.fails("inspection_timeout", self.application)
        self.assertEqual(len(self.serial.writes), 1)
        self.assertTrue(self.serial.closed)

    def test_application_rejects_non_ascii_oversized_and_negative_replies(self):
        """Malformed correlated replies cannot be accepted as identity evidence."""
        for suffix in (b"\xff\n", b"x" * 1025 + b"\n", b"ERR rejected\n"):
            self.serial.data = None
            self.serial.pending.clear()
            self.serial.rewrite = (
                lambda raw: b" ".join(raw.split(b" ")[:2]) + b" " + suffix
            )
            self.fails("application_mismatch", self.application)
            self.assertTrue(self.serial.closed)

    def test_invalid_endpoint_baud_or_budget_rejected(self):
        """Callers cannot widen timeout, select a new port or inject another baud."""
        for port, baud, budget in (
            ("/dev/ttyUSB1", 115200, 3),
            (self.target.port, 9600, 3),
            (self.target.port, 115200, 4),
            (self.target.port, 115200, float("nan")),
        ):
            self.fails(
                "unsupported_profile",
                lambda: self.provider.probe_loader(port, baud, timeout=budget),
            )
        self.factory.assert_not_called()

    def test_cancel_runs_only_verified_loader_then_exact_baseline(self):
        """Cancel observes a menu and journals before one run request, then verifies baseline."""
        before = Mock(
            side_effect=lambda: self.assertEqual(self.serial.writes, [b"\n3"])
        )
        result = self.provider.resume_intact_application(
            "0.14", mode="bootloader", before_run=before
        )
        before.assert_called_once_with()
        self.assertEqual(self.serial.writes[:2], [b"\n3", b"2"])
        self.assertEqual(self.serial.writes.count(b"2"), 1)
        self.assertEqual(result["version"]["fw_version"], "0.14")
        self.assertTrue(self.serial.closed)
        self.assertLess(self.clock.now, 108)

    def test_cancel_known_application_only_verifies_without_menu(self):
        """Already-running baseline verification cannot select a loader or reset the app."""
        self.serial.data = None
        self.provider.resume_intact_application("0.14", mode="application")
        self.assertEqual(len(self.serial.writes), 3)
        self.assertNotIn(b"2", self.serial.writes)
        self.assertNotIn(b"\n3", self.serial.writes)

    def test_cancel_unknown_mode_or_missing_ack_never_opens(self):
        """No blind mode probing or unjournaled run command is allowed."""
        self.fails(
            "resume_unverified",
            lambda: self.provider.resume_intact_application("0.14", mode="unknown"),
        )
        self.fails(
            "resume_unverified",
            lambda: self.provider.resume_intact_application("0.14", mode="bootloader"),
        )
        self.factory.assert_not_called()

    def test_cancel_wrong_loader_or_failed_journal_never_runs(self):
        """A mismatched loader or failed durable acknowledgement cannot trigger the intact app."""
        self.serial.data = MENU.replace(b"1.9.2", b"2.0.0")
        before = Mock()
        self.fails(
            "loader_unverified",
            lambda: self.provider.resume_intact_application(
                "0.14", mode="bootloader", before_run=before
            ),
        )
        before.assert_not_called()
        self.serial.data = MENU
        before.side_effect = RuntimeError("journal unavailable")
        with self.assertRaises(RuntimeError):
            self.provider.resume_intact_application(
                "0.14", mode="bootloader", before_run=before
            )
        self.assertNotIn(b"2", self.serial.writes)
        self.assertTrue(self.serial.closed)

    def test_cancel_changed_target_after_ack_never_runs(self):
        """Attachment is rechecked after durable acknowledgement before selecting run."""
        before = lambda: setattr(
            self.inventory, "return_value", (replace(self.target, attachment="new"),)
        )
        self.fails(
            "target_changed",
            lambda: self.provider.resume_intact_application(
                "0.14", mode="bootloader", before_run=before
            ),
        )
        self.assertEqual(self.serial.writes, [b"\n3"])

    def test_cancel_wrong_return_version_is_not_success_or_retried(self):
        """A running app that differs from saved baseline does not complete cancellation."""
        self.fails(
            "application_mismatch",
            lambda: self.provider.resume_intact_application(
                "0.13", mode="bootloader", before_run=lambda: None
            ),
        )
        self.assertEqual(self.serial.writes.count(b"2"), 1)

    def test_cancel_partial_run_write_stops_without_application_probes(self):
        """An uncertain run request is never retried or presented as a successful resume."""
        original = self.serial.write
        self.serial.write = Mock(
            side_effect=lambda raw: 0 if raw == b"2" else original(raw)
        )
        self.fails(
            "inspection_failed",
            lambda: self.provider.resume_intact_application(
                "0.14", mode="bootloader", before_run=lambda: None
            ),
        )
        self.assertEqual(self.serial.write.call_args_list[-1].args, (b"2",))
        self.assertEqual(self.serial.write.call_count, 2)
        self.assertTrue(self.serial.closed)

    def test_lease_is_cross_configuration_and_attachment(self):
        """Different config paths, tty names and attachment tokens cannot obtain a second guard."""
        other = replace(self.target, port="/dev/ttyUSB9", attachment="new-attachment")
        self.fails("lease_unavailable", lambda: PhysicalLease.acquire(other, self.root))

    def test_child_inherits_lease_after_parent_close(self):
        """A surviving worker descriptor excludes a second owner after its parent closes."""
        child = subprocess.Popen(
            [
                sys.executable,
                "-c",
                "import os,sys;os.fstat(int(sys.argv[1]));print('held',flush=True);sys.stdin.readline()",
                str(self.lease.fileno()),
            ],
            pass_fds=(self.lease.fileno(),),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            text=True,
        )
        try:
            self.assertEqual(child.stdout.readline().strip(), "held")
            self.lease.close()
            self.fails(
                "lease_unavailable",
                lambda: PhysicalLease.acquire(self.target, self.root),
            )
        finally:
            child.communicate("done\n", timeout=3)
        replacement = PhysicalLease.acquire(self.target, self.root)
        replacement.close()

    def test_adoption_retains_guard_and_rejects_unlocked_descriptor(self):
        """Handoff duplicates held ownership, but never quietly reacquires a released guard."""
        adopted = PhysicalLease.adopt(self.lease.fileno(), self.target, self.root)
        self.lease.close()
        adopted.check()
        fd = adopted.fd
        fcntl.flock(fd, fcntl.LOCK_UN)
        self.fails("lease_unavailable", adopted.check)
        self.fails(
            "lease_unavailable", lambda: PhysicalLease.adopt(fd, self.target, self.root)
        )
        adopted.close()

    def test_guard_replacement_symlink_and_hardlink_rejected(self):
        """Guard aliases or inode replacement cannot split cooperative exclusion."""
        path = self.root / self.lease.name
        os.link(path, self.root / "alias")
        self.fails("lease_unavailable", self.lease.check)
        (self.root / "alias").unlink()
        path.unlink()
        path.symlink_to(self.root / "other")
        self.fails("lease_unavailable", self.lease.check)
        self.fails(
            "lease_unavailable", lambda: PhysicalLease.acquire(self.target, self.root)
        )

    def test_unprotected_directory_and_noncanonical_topology_rejected(self):
        """Writable shared directories and topology spelling tricks fail before lock creation."""
        os.chmod(self.root, 0o777)
        self.fails(
            "lease_unavailable", lambda: PhysicalLease.acquire(self.target, self.root)
        )
        os.chmod(self.root, 0o700)
        self.fails(
            "unsupported_profile",
            lambda: PhysicalLease.acquire(
                replace(self.target, usb_path="devices/a/../usb1/1-2"), self.root
            ),
        )

    def test_replaced_guard_directory_is_detected(self):
        """Replacing the named shared directory cannot leave two unrelated guard namespaces."""
        moved = self.root.with_name(self.root.name + "-moved")
        self.root.rename(moved)
        self.root.mkdir(mode=0o700)
        try:
            self.fails("lease_unavailable", self.lease.check)
        finally:
            self.root.rmdir()
            moved.rename(self.root)

    def test_provider_cannot_use_another_devices_lease(self):
        """A valid but unrelated physical lease cannot authorize this target's serial actions."""
        other = replace(self.target, usb_path="devices/platform/usb1/1-3")
        with self.assertRaises(BundleError):
            ZBGW04Provider(
                other,
                self.lease,
                board_evidence=BOARD_EVIDENCE,
                access_check=self.exclusion,
                loader_policy=self.policy,
            )


class InventoryTests(unittest.TestCase):
    def setUp(self):
        """Build synthetic sysfs with a CH340 tty and injected character-device stat."""
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.usb = self.root / "devices/platform/usb1/1-2"
        self.interface = self.usb / "1-2:1.0"
        self.tty = self.interface / "ttyUSB0"
        self.tty.mkdir(parents=True)
        for name, value in {
            "idVendor": "1a86",
            "idProduct": "7523",
            "busnum": "1",
            "devnum": "2",
        }.items():
            (self.usb / name).write_text(value)
        (self.interface / "bInterfaceNumber").write_text("00")
        entry = self.root / "class/tty/ttyUSB0"
        entry.mkdir(parents=True)
        (entry / "device").symlink_to(self.tty)
        self.boot = self.root / "boot_id"
        self.boot.write_text("boot-one")
        self.node = SimpleNamespace(st_mode=stat.S_IFCHR, st_rdev=os.makedev(20, 0))
        self.reader = LinuxUSB(
            self.root,
            "/dev",
            self.boot,
            node_stat=lambda path: self.node,
            environment_check=Mock(),
        )

    def test_inventory_metadata_and_explicit_selection(self):
        """Sysfs supplies topology, interface and rdev without inferring a confirmed PCB."""
        result = self.reader.inventory()
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].usb_path, "devices/platform/usb1/1-2")
        self.assertEqual(result[0].interface, "00")
        self.assertEqual(result[0].device_number, (20, 0))
        self.assertEqual(
            select_target("/dev/ttyUSB0", BOARD_EVIDENCE, self.reader.inventory),
            result[0],
        )
        with self.assertRaises(BundleError):
            select_target(
                "/dev/serial/by-id/usb-generic", BOARD_EVIDENCE, self.reader.inventory
            )

    def test_boot_and_attachment_changes_invalidate_generation(self):
        """Reboot or changed USB device number alters the frozen attachment token."""
        original = self.reader.inventory()[0]
        self.boot.write_text("boot-two")
        rebooted = self.reader.inventory()[0]
        self.assertNotEqual(original.attachment, rebooted.attachment)
        (self.usb / "devnum").write_text("3")
        self.assertNotEqual(rebooted.attachment, self.reader.inventory()[0].attachment)

    def test_missing_metadata_or_wrong_node_fails_closed(self):
        """Incomplete matching bridge metadata cannot produce an actionable target."""
        (self.usb / "devnum").unlink()
        with self.assertRaises(BundleError):
            self.reader.inventory()
        (self.usb / "devnum").write_text("2")
        self.node.st_mode = stat.S_IFREG
        with self.assertRaises(BundleError):
            self.reader.inventory()

    def test_mount_local_timestamps_and_device_number_do_not_change_attachment(self):
        """Independent sysfs views retain one generation despite local ctime/st_dev."""
        original_stat = Path.stat
        readings = []

        def view_stat(path, *args, **kwargs):
            """Emulate a new VFS view without changing the underlying kernfs ID.

            @param path Metadata path requested by the provider.
            @param args Positional stat arguments forwarded unchanged.
            @param kwargs Keyword stat arguments forwarded unchanged.
            """
            value = original_stat(path, *args, **kwargs)
            if path == self.usb:
                readings.append(len(readings) + 1)
                return SimpleNamespace(
                    st_ino=value.st_ino,
                    st_ctime_ns=readings[-1],
                    st_dev=readings[-1],
                    st_mode=value.st_mode,
                )
            return value

        expected = self.reader.inventory()[0]
        with patch.object(Path, "stat", view_stat):
            self.assertEqual(self.reader.inventory()[0], expected)
            self.assertEqual(self.reader.inventory()[0], expected)
        self.assertGreaterEqual(len(readings), 4)

    def test_full_inode_generation_survives_address_and_low_id_reuse(self):
        """A reused devnum or low inode ID cannot hide a new high generation."""
        original_stat = Path.stat
        inode = 1234

        def node_stat(path, *args, **kwargs):
            """Expose a complete kernel node ID independently of the test filesystem.

            @param path Metadata path requested by the provider.
            @param args Positional stat arguments forwarded unchanged.
            @param kwargs Keyword stat arguments forwarded unchanged.
            """
            value = original_stat(path, *args, **kwargs)
            if path == self.usb:
                return SimpleNamespace(st_ino=inode, st_mode=value.st_mode)
            return value

        with patch.object(Path, "stat", node_stat):
            original = self.reader.inventory()[0]
            for changed in (1235, (1 << 32) + 1234):
                inode = changed
                self.assertNotEqual(
                    self.reader.inventory()[0].attachment, original.attachment
                )
            for invalid in (0, -1, 1 << 64):
                inode = invalid
                with self.assertRaises(BundleError):
                    self.reader.inventory()

    def test_bus_number_and_boot_changes_remain_bound(self):
        """Neither another USB bus nor a host reboot can reuse the previous token."""
        original = self.reader.inventory()[0]
        (self.usb / "busnum").write_text("2")
        self.assertNotEqual(self.reader.inventory()[0].attachment, original.attachment)
        (self.usb / "busnum").write_text("1")
        self.boot.write_text("new-host-boot")
        self.assertNotEqual(self.reader.inventory()[0].attachment, original.attachment)

    def test_disappearance_and_torn_metadata_fail_before_selection(self):
        """A second sample cannot silently validate a mixed or vanished attachment."""
        snapshot = self.reader._snapshot
        for mutation in ("device", "boot", "disappear"):
            calls = 0

            def changing(boot):
                """Change the selected metadata between the two inventory samples.

                @param boot Boot identity read before the first sample.
                """
                nonlocal calls
                calls += 1
                if calls == 2:
                    if mutation == "device":
                        (self.usb / "devnum").write_text("3")
                    elif mutation == "boot":
                        self.boot.write_text("changed")
                    else:
                        (self.root / "class/tty/ttyUSB0/device").unlink()
                return snapshot(boot)

            with patch.object(self.reader, "_snapshot", side_effect=changing):
                with self.assertRaises(BundleError) as error:
                    select_target("/dev/ttyUSB0", BOARD_EVIDENCE, self.reader.inventory)
                self.assertEqual(error.exception.code, "target_changed")
            (self.usb / "devnum").write_text("2")
            self.boot.write_text("boot-one")

    def test_legacy_policy_cannot_authorize_the_new_generation_scheme(self):
        """The old ctime hash has no compatibility fallback in the loader policy."""
        metadata = self.usb.stat()
        legacy = hashlib.sha256(
            json.dumps(
                dict(
                    boot="boot-one",
                    bus=1,
                    device=2,
                    inode=metadata.st_ino,
                    ctime_ns=metadata.st_ctime_ns,
                ),
                sort_keys=True,
            ).encode()
        ).hexdigest()
        target = self.reader.inventory()[0]
        policy = LoaderPolicy(legacy, "reviewed", "1.9.2", False, False)
        with self.assertRaises(BundleError) as error:
            policy.validate(target)
        self.assertEqual(error.exception.code, "loader_unverified")

    def test_environment_rejection_precedes_any_metadata_or_device_read(self):
        """Unsupported environments never reach inventory or character-device stat."""
        self.reader.environment_check.side_effect = BundleError(
            "unsupported_profile", "unsupported"
        )
        with patch.object(self.reader, "_snapshot") as snapshot:
            with self.assertRaises(BundleError) as error:
                self.reader.inventory()
            self.assertEqual(error.exception.code, "unsupported_profile")
            snapshot.assert_not_called()
        self.reader.environment_check.side_effect = OSError("missing sysfs")
        with self.assertRaises(BundleError) as error:
            self.reader.inventory()
        self.assertEqual(error.exception.code, "inventory_unavailable")


class InventoryEnvironmentTests(unittest.TestCase):
    def test_narrow_unknown_or_nonlinux_platform_rejected_before_open(self):
        """Native ABI and kernel machine admission are both required before sysfs I/O."""
        real_sizeof = ctypes.sizeof
        for system, machine, narrow in (
            ("darwin", "arm64", False),
            ("linux", "armv7l", False),
            ("linux", "unknown", False),
            ("linux", "aarch64", True),
        ):
            with (
                patch("livolod.zbgw04_provider.sys.platform", system),
                patch(
                    "livolod.zbgw04_provider.os.uname",
                    return_value=SimpleNamespace(machine=machine),
                ),
                patch(
                    "livolod.zbgw04_provider.ctypes.sizeof",
                    side_effect=lambda kind: (
                        4 if narrow and kind is ctypes.c_ulong else real_sizeof(kind)
                    ),
                ),
                patch("livolod.zbgw04_provider.os.open") as opened,
            ):
                with self.assertRaises(BundleError) as error:
                    _check_attachment_environment(Path("/sys"))
                self.assertEqual(error.exception.code, "unsupported_profile")
                opened.assert_not_called()

    def test_statfs_admits_only_the_supported_real_sysfs_layout(self):
        """Copied metadata and failed statfs calls cannot masquerade as kernel sysfs."""
        self.assertEqual(ctypes.sizeof(_LinuxStatFS), 120)
        for magic, result in ((0x62656572, 0), (0xEF53, 0), (0x62656572, -1)):

            def statfs(descriptor, address):
                """Model the supported native statfs ABI without a real Linux syscall.

                @param descriptor Read-only directory descriptor supplied by the provider.
                @param address Pointer to the explicit native statfs result structure.
                """
                address._obj.f_type = magic
                return result

            function = Mock(side_effect=statfs)
            with (
                patch("livolod.zbgw04_provider.sys.platform", "linux"),
                patch(
                    "livolod.zbgw04_provider.os.uname",
                    return_value=SimpleNamespace(machine="aarch64"),
                ),
                patch("livolod.zbgw04_provider.os.open", return_value=42),
                patch("livolod.zbgw04_provider.os.close") as closed,
                patch(
                    "livolod.zbgw04_provider.ctypes.CDLL",
                    return_value=SimpleNamespace(fstatfs=function),
                ),
            ):
                if magic == 0x62656572 and result == 0:
                    _check_attachment_environment(Path("/sys"))
                else:
                    with self.assertRaises(BundleError):
                        _check_attachment_environment(Path("/sys"))
                closed.assert_called_once_with(42)


if __name__ == "__main__":
    unittest.main()
