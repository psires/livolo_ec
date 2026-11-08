"""Explicit-action Linux providers for reviewed Gecko profiles, with no API registration.

Inventory reads sysfs only. Serial methods require a physical lease, recorded
dedicated-radio consent, quiesced Livolo service and no detected conflicts.
These establish a cooperative deployment contract, not global host exclusion.
Nothing here flashes, configures a network, arms RF or selects a device
by a generic bridge ID. A parent supervisor must bound kernel/driver stalls and
retain inherited leases until all workers and helpers have exited.
"""

from contextlib import contextmanager
from dataclasses import dataclass
import ctypes
import fcntl
import errno
import hashlib
import importlib.metadata
import json
import math
import os
from pathlib import Path
import re
import secrets
import stat
import struct
import sys
import termios
import time

from .firmware_bundle import BundleError
from .preserved_gecko import APP_BAUD, LOADER_BAUD, GeckoTarget, check_application
from .version import parse_version
from .gecko_profiles import gecko_profile, evidence_profile
from .sniffer_identity import SnifferIdentity, SNIFFER_BAUD

BOARD_EVIDENCE = "operator_confirmed_zbgw04_1v2"
LOADER_VERSION = "1.9.2"


def _require(condition, code, message):
    """Reject unmet invariants even under optimized Python.

    @param condition Required boolean expression.
    @param code Stable sanitized failure identifier.
    @param message Safe explanation without raw device output.
    """
    if not condition:
        raise BundleError(code, message)


class _LinuxStatFS(ctypes.Structure):
    """Linux arm64/amd64 LP64 statfs layout, used only after native ABI admission."""

    _fields_ = [
        ("f_type", ctypes.c_long),
        ("f_bsize", ctypes.c_long),
        ("f_blocks", ctypes.c_ulong),
        ("f_bfree", ctypes.c_ulong),
        ("f_bavail", ctypes.c_ulong),
        ("f_files", ctypes.c_ulong),
        ("f_ffree", ctypes.c_ulong),
        ("f_fsid", ctypes.c_int * 2),
        ("f_namelen", ctypes.c_long),
        ("f_frsize", ctypes.c_long),
        ("f_flags", ctypes.c_long),
        ("f_spare", ctypes.c_long * 4),
    ]


def _check_attachment_environment(sysfs):
    """Require a reviewed full-width Linux ABI and genuine kernel sysfs metadata.

    The 64-bit kernfs inode contains both allocation ID and wrap generation.
    A 32-bit kernel can expose only its low bits; pointer width alone is not
    enough to establish this contract. No timestamp fallback is permitted.
    @param sysfs Resolved sysfs directory; opened read-only, never a serial node.
    """
    _require(
        sys.platform == "linux"
        and os.uname().machine in {"aarch64", "x86_64"}
        and ctypes.sizeof(ctypes.c_void_p) == 8
        and ctypes.sizeof(ctypes.c_ulong) == 8
        and ctypes.sizeof(_LinuxStatFS) == 120,
        "unsupported_profile",
        "Physical inventory requires native 64-bit Linux arm64 or amd64",
    )
    descriptor = os.open(sysfs, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        buffer = _LinuxStatFS()
        call = ctypes.CDLL(None, use_errno=True).fstatfs
        call.argtypes = [ctypes.c_int, ctypes.POINTER(_LinuxStatFS)]
        call.restype = ctypes.c_int
        _require(
            call(descriptor, ctypes.byref(buffer)) == 0 and buffer.f_type == 0x62656572,
            "inventory_unavailable",
            "Physical inventory requires a real sysfs filesystem",
        )
    finally:
        os.close(descriptor)


class LinuxUSB:
    """Read Linux metadata without opening a serial endpoint or claiming board identity."""

    def __init__(
        self,
        sysfs="/sys",
        dev="/dev",
        boot_id="/proc/sys/kernel/random/boot_id",
        node_stat=os.stat,
        environment_check=None,
        profile="zb-gw04-1v2",
    ):
        """Inject filesystem roots/stat only for offline tests.

        @param sysfs Trusted Linux sysfs mount.
        @param dev Trusted device-node directory.
        @param boot_id Host boot-generation file.
        @param node_stat Device stat function; never supplied by an API client.
        @param environment_check Optional offline substitute for native sysfs validation.
        @param profile Fixed server-selected board; never inferred from the first USB match.
        """
        self.sysfs = Path(sysfs).resolve()
        self.dev = Path(dev)
        self.boot_id = Path(boot_id)
        self.node_stat = node_stat
        self.environment_check = environment_check or _check_attachment_environment
        self.profile = gecko_profile(profile)

    def inventory(self):
        """Return a coherent, mount-stable generation from real native 64-bit sysfs.

        Tokens bind host boot, USB bus/device and the complete kernfs node ID.
        View-local timestamps are not attachment evidence. A second snapshot
        rejects observed reattachment during inventory; later actions recheck.
        """
        try:
            self.environment_check(self.sysfs)
        except (OSError, AttributeError) as error:
            raise BundleError(
                "inventory_unavailable", "Cannot validate the sysfs environment"
            ) from error
        try:
            boot = self.boot_id.read_text().strip()
            _require(bool(boot), "inventory_unavailable", "Missing host boot identity")
            result = self._snapshot(boot)
            repeated = self._snapshot(boot)
            unchanged_boot = self.boot_id.read_text().strip() == boot
        except (OSError, ValueError, AttributeError) as error:
            raise BundleError(
                "inventory_unavailable", "Linux USB inventory is incomplete"
            ) from error
        _require(
            result == repeated and unchanged_boot,
            "target_changed",
            "USB metadata changed during inventory",
        )
        return result

    def _snapshot(self, boot):
        """Collect one metadata sample without opening any character device.

        @param boot Host boot ID already read by the enclosing consistency check.
        """
        result = []
        for tty in sorted((self.sysfs / "class/tty").iterdir()):
            device = tty / "device"
            if not device.exists():
                continue
            resolved = device.resolve(strict=True)
            _require(
                resolved.is_relative_to(self.sysfs),
                "inventory_unavailable",
                "TTY metadata is outside sysfs",
            )
            lineage = (resolved, *resolved.parents)
            usb = next((p for p in lineage if (p / "idVendor").exists()), None)
            if usb is None:
                continue
            self.environment_check(usb)
            vid = int((usb / "idVendor").read_text().strip(), 16)
            pid = int((usb / "idProduct").read_text().strip(), 16)
            if (vid, pid) != (self.profile.vid, self.profile.pid):
                continue
            interface = next(
                (p for p in lineage if (p / "bInterfaceNumber").exists()), None
            )
            _require(
                interface is not None and interface.is_relative_to(usb),
                "inventory_unavailable",
                "USB interface cannot be identified",
            )
            number = (interface / "bInterfaceNumber").read_text().strip().lower()
            _require(
                re.fullmatch(r"[0-9a-f]{2}", number) is not None,
                "inventory_unavailable",
                "Invalid USB interface",
            )
            port = self.dev / tty.name
            node = self.node_stat(port)
            _require(
                stat.S_ISCHR(node.st_mode),
                "target_changed",
                "Not a character device",
            )
            serial_path = usb / "serial"
            serial = serial_path.read_text().strip() if serial_path.exists() else None
            serial = serial or None
            _require(
                bool(serial) if self.profile.serial_required else serial is None,
                "unsupported_profile",
                "USB serial does not match the selected profile",
            )
            usb_stat = usb.stat()
            attachment = dict(
                boot=boot,
                bus=int((usb / "busnum").read_text()),
                device=int((usb / "devnum").read_text()),
                inode=usb_stat.st_ino,
                schema="linux-usb-kernfs64-v1",
            )
            _require(
                0 < attachment["inode"] < (1 << 64)
                and attachment["bus"] > 0
                and 1 <= attachment["device"] <= 127,
                "inventory_unavailable",
                "Invalid USB generation metadata",
            )
            result.append(
                GeckoTarget(
                    port=str(port),
                    usb_path=str(usb.relative_to(self.sysfs)),
                    attachment=hashlib.sha256(
                        json.dumps(attachment, sort_keys=True).encode()
                    ).hexdigest(),
                    device_number=(os.major(node.st_rdev), os.minor(node.st_rdev)),
                    interface=number,
                    loader_version=self.profile.loader_version,
                    board=self.profile.board,
                    vid=vid,
                    pid=pid,
                    serial=serial,
                )
            )
        return tuple(result)


def select_target(port, board_evidence, inventory):
    """Select one canonical endpoint only after explicit board/revision confirmation.

    @param port Canonical device path selected from server-owned inventory.
    @param board_evidence Trusted recorded operator confirmation, not USB inference.
    @param inventory Metadata-only callable returning GeckoTarget candidates.
    @return Unique current target; generic by-id aliases are not selected implicitly.
    """
    profile = evidence_profile(board_evidence)
    candidates = tuple(inventory())
    matches = [item for item in candidates if item.port == port]
    _require(len(matches) == 1, "target_changed", "Endpoint is absent or ambiguous")
    target = matches[0]
    target.validate()
    _require(
        target.board == profile.board, "unsupported_profile", "Selected board differs"
    )
    collisions = [
        item
        for item in candidates
        if item.device_number == target.device_number
        or (item.usb_path, item.interface) == (target.usb_path, target.interface)
    ]
    _require(len(collisions) == 1, "target_changed", "Endpoint aliases are ambiguous")
    return target


def _lease_name(target):
    """Derive a stable guard name independent of config, pathname and attachment.

    @param target Frozen physical topology/interface; all owners use the same sysfs view.
    """
    target.validate()
    _require(
        target.usb_path.startswith("devices/")
        and str(Path(target.usb_path)) == target.usb_path
        and ".." not in Path(target.usb_path).parts
        and re.fullmatch(r"[0-9a-f]{2}", target.interface) is not None,
        "unsupported_profile",
        "Canonical Linux topology and interface are required",
    )
    key = json.dumps([target.usb_path, target.interface], separators=(",", ":"))
    return "usb-" + hashlib.sha256(key.encode()).hexdigest() + ".lock"


class PhysicalLease:
    """Cooperative host-shared flock retained by inherited descriptors, never unlinked."""

    def __init__(self, fd, directory_fd, name, directory):
        """Own descriptors already validated by acquire/adopt.

        @param fd Exclusively locked regular-file descriptor.
        @param directory_fd Pinned protected shared guard directory descriptor.
        @param name Stable physical guard filename.
        @param directory Absolute shared mount pathname, checked for replacement.
        """
        self.fd, self.directory_fd, self.name = fd, directory_fd, name
        self.directory = directory

    @classmethod
    def acquire(cls, target, directory):
        """Acquire a shared physical guard, independent of the daemon's database lock.

        @param target Exact selected physical target.
        @param directory Pre-created protected shared host mount used by every owner.
        @return Owned lease; parent explicitly passes fileno through worker/helper exec.
        """
        return cls._open(target, directory, None)

    @classmethod
    def adopt(cls, fd, target, directory):
        """Duplicate an inherited held lease without a release/reacquire handoff gap.

        @param fd Parent's inherited locked descriptor, not an integer from HTTP input.
        @param target Exact selected physical target.
        @param directory Same shared guard directory mounted into this process.
        @return Owned duplicate; caller remains responsible for its original descriptor.
        """
        return cls._open(target, directory, fd)

    @classmethod
    def _open(cls, target, directory, inherited):
        """Pin a protected directory and guard and fail closed on replacement.

        @param target Physical key source.
        @param directory Shared lock directory, never created implicitly.
        @param inherited Existing locked descriptor, or None for a fresh acquisition.
        """
        directory_fd = fd = None
        try:
            directory_fd = os.open(
                directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
            )
            info = os.fstat(directory_fd)
            _require(
                info.st_uid in (0, os.geteuid()) and info.st_mode & 0o022 == 0,
                "lease_unavailable",
                "Shared guard directory is not protected",
            )
            name = _lease_name(target)
            if inherited is None:
                fd = os.open(
                    name,
                    os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK,
                    0o600,
                    dir_fd=directory_fd,
                )
            else:
                fd = os.dup(inherited)
            lease = cls(fd, directory_fd, name, os.path.abspath(directory))
            lease._validate_file()
            if inherited is not None:
                lease._check_held()
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return lease
        except BaseException as error:
            if fd is not None:
                os.close(fd)
            if directory_fd is not None:
                os.close(directory_fd)
            if isinstance(error, OSError):
                raise BundleError(
                    "lease_unavailable", "Physical lease is unavailable"
                ) from error
            raise

    def _validate_file(self):
        """Require a protected stable inode; refuse symlink, replacement and alias links."""
        opened = os.fstat(self.fd)
        named = os.stat(self.name, dir_fd=self.directory_fd, follow_symlinks=False)
        directory = os.fstat(self.directory_fd)
        named_directory = os.stat(self.directory, follow_symlinks=False)
        _require(
            stat.S_ISREG(opened.st_mode)
            and stat.S_ISDIR(named_directory.st_mode)
            and directory.st_uid in (0, os.geteuid())
            and directory.st_mode & 0o022 == 0
            and (directory.st_dev, directory.st_ino)
            == (named_directory.st_dev, named_directory.st_ino)
            and (opened.st_dev, opened.st_ino) == (named.st_dev, named.st_ino)
            and opened.st_nlink == 1
            and opened.st_uid == os.geteuid()
            and opened.st_mode & 0o077 == 0,
            "lease_unavailable",
            "Physical guard was replaced or is not protected",
        )

    def _check_held(self):
        """Reject a released guard rather than silently reacquiring it after a gap."""
        competitor = os.open(
            self.name,
            os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK,
            dir_fd=self.directory_fd,
        )
        try:
            try:
                fcntl.flock(competitor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return
            raise BundleError("lease_unavailable", "Physical lease was not held")
        finally:
            os.close(competitor)

    def check(self):
        """Check the retained lease inode and cooperative exclusion before physical I/O."""
        try:
            self._validate_file()
            self._check_held()
            fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (OSError, ValueError, TypeError) as error:
            raise BundleError("lease_unavailable", "Physical lease was lost") from error

    def fileno(self):
        """Return the checked descriptor for explicit pass_fds inheritance."""
        self.check()
        return self.fd

    def close(self):
        """Close this owner's references only; never unlock surviving child descriptors."""
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None
        if self.directory_fd is not None:
            os.close(self.directory_fd)
            self.directory_fd = None


@dataclass(frozen=True)
class LoaderPolicy:
    """Server-reviewed exact-attachment policy; menu/version cannot reveal secure settings."""

    attachment: str
    evidence_id: str
    version: str
    requires_signed: bool
    requires_encrypted: bool

    def validate(self, target):
        """Reject unknown, secure or detached loader policy before serial inspection.

        @param target Exact attachment covered by the reviewed policy.
        """
        _require(
            self.attachment == target.attachment
            and isinstance(self.evidence_id, str)
            and 1 <= len(self.evidence_id) <= 128
            and self.version
            == target.loader_version
            == gecko_profile(target.board).loader_version
            and self.requires_signed is False
            and self.requires_encrypted is False,
            "loader_unverified",
            "No supported exact-attachment loader policy",
        )


@dataclass(frozen=True)
class DedicatedAccess:
    """Trusted controller observation, explicitly not a global process-absence proof."""

    attachment: str
    lease_name: str
    dedicated_confirmed: bool
    own_service_quiesced: bool
    detected_conflicts: tuple

    def validate(self, target, lease):
        """Bind dedicated-use consent and observed conflict status to this held target.

        @param target Frozen current attachment selected by the user.
        @param lease Actual held physical lease, checked separately by the provider.
        """
        _require(
            self.attachment == target.attachment
            and self.lease_name == lease.name
            and self.dedicated_confirmed is True
            and self.own_service_quiesced is True
            and type(self.detected_conflicts) is tuple
            and not self.detected_conflicts,
            "ownership_unresolved",
            "Dedicated use, Livolo quiescence or conflict checks are unresolved",
        )


def _claim_exclusive(fd):
    """Refuse competing future opens during this inspection using Linux TIOCEXCL.

    Existing opens and privileged bypass are outside this kernel flag's promise.
    Closing the last serial reference releases it; never clear it preemptively.
    @param fd Open selected serial descriptor, with cooperative lease already held.
    """
    fcntl.ioctl(fd, termios.TIOCEXCL)


def _serial_factory(**kwargs):
    """Construct an unopened pyserial object only for an explicit physical action.

    @param kwargs Fixed provider-owned serial settings.
    """
    import serial

    return serial.Serial(**kwargs)


def _check_asserted_lines(fd):
    """Verify the reviewed Sonoff open policy without changing modem lines.

    @param fd Selected open descriptor after target and exclusive-ownership checks.
    """
    value = fcntl.ioctl(fd, termios.TIOCMGET, struct.pack("I", 0))
    bits = struct.unpack("I", value)[0]
    mask = termios.TIOCM_DTR | termios.TIOCM_RTS
    _require(
        bits & mask == mask,
        "modem_state_mismatch",
        "Unexpected serial control-line state",
    )


def _menu_pattern():
    """Use the reviewed helper's complete menu parser, never a loose version substring."""
    _require(
        importlib.metadata.version("universal-silabs-flasher") == "0.0.31",
        "loader_unverified",
        "Pinned loader parser is unavailable",
    )
    from universal_silabs_flasher.gecko_bootloader import MENU_REGEX

    return MENU_REGEX


class ZBGW04Provider:
    """Lease-bound explicit inspections; constructing this class performs no serial I/O."""

    def __init__(
        self,
        target,
        lease,
        *,
        board_evidence,
        access_check,
        loader_policy,
        inventory=None,
        serial_factory=_serial_factory,
        clock=time.monotonic,
        fd_stat=os.fstat,
        menu_pattern=_menu_pattern,
        claim_exclusive=_claim_exclusive,
        sleep=time.sleep,
        modem_check=_check_asserted_lines,
    ):
        """Freeze trusted dependencies; none are accepted from HTTP request payloads.

        @param target Frozen GeckoTarget selected by explicit operator action.
        @param lease PhysicalLease shared across normal daemon, controller and descendants.
        @param board_evidence Recorded exact board/revision confirmation.
        @param access_check Trusted controller callable(target)->DedicatedAccess observation.
        @param loader_policy Reviewed exact-attachment LoaderPolicy, not menu inference.
        @param inventory Metadata-only callable, default LinuxUSB.inventory.
        @param serial_factory Deferred-open serial constructor, injectable for offline tests.
        @param clock Monotonic clock in seconds.
        @param fd_stat Open descriptor stat function, injectable for offline tests.
        @param menu_pattern Reviewed complete Gecko menu regex factory.
        @param claim_exclusive Kernel serial exclusion function; injectable in offline tests.
        @param sleep Bounded startup-grace wait; injectable alongside the test clock.
        @param modem_check Read-only asserted-line check for the reviewed Sonoff policy.
        """
        target.validate()
        _require(
            isinstance(lease, PhysicalLease) and lease.name == _lease_name(target),
            "lease_unavailable",
            "Lease does not cover this physical target",
        )
        self.target, self.lease = target, lease
        self.board_evidence = board_evidence
        self.access_check = access_check
        self.loader_policy = loader_policy
        self.inventory = (
            inventory
            if inventory is not None
            else LinuxUSB(profile=target.board).inventory
        )
        self.serial_factory, self.clock = serial_factory, clock
        self.fd_stat, self.menu_pattern = fd_stat, menu_pattern
        self.claim_exclusive = claim_exclusive
        self.sleep = sleep
        self.modem_check = modem_check

    @property
    def lease_fd(self):
        """Return the checked guard for explicit inheritance by the worker and helper."""
        return self.lease.fileno()

    def __enter__(self):
        """Check physical ownership without opening serial at explicit context entry."""
        self.observe()
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        """Close this lease reference without releasing inherited child ownership.

        @param exc_type Exception class, or None.
        @param exc_value Exception instance, or None.
        @param traceback Exception traceback, or None.
        """
        self.close()

    def close(self):
        """Release only this provider's lease reference; supervisor owns descendant lifetime."""
        self.lease.close()

    def observe(self):
        """Recheck metadata and ownership; no serial opens or reset commands occur here."""
        self.lease.check()
        observed = select_target(self.target.port, self.board_evidence, self.inventory)
        _require(
            observed == self.target, "target_changed", "Selected attachment changed"
        )
        access = self.access_check(self.target)
        _require(
            isinstance(access, DedicatedAccess),
            "ownership_unresolved",
            "Dedicated-radio access evidence is missing",
        )
        access.validate(self.target, self.lease)
        return dict(
            identity=observed.identity(),
            unique=True,
            owner="exclusive",
            board_evidence=self.board_evidence,
        )

    def _remaining(self, deadline):
        """Return remaining action budget or stop without retries.

        @param deadline Monotonic absolute deadline in seconds.
        """
        remaining = deadline - self.clock()
        _require(remaining > 0, "inspection_timeout", "Radio inspection timed out")
        return remaining

    @contextmanager
    def _port(self, port, baud, expected_baud, timeout):
        """Open the frozen endpoint using its reviewed profile's initial modem states.

        @param port Must equal the frozen canonical endpoint.
        @param baud Must equal this action's fixed baud rate.
        @param expected_baud Fixed reviewed rate for this action.
        @param timeout Total budget, at most eight seconds for fixed sniffer entry or three otherwise.
        """
        _require(
            port == self.target.port
            and type(baud) is int
            and baud == expected_baud
            and type(timeout) in (int, float)
            and math.isfinite(timeout)
            and 0 < timeout <= (8 if expected_baud == SNIFFER_BAUD else 3),
            "unsupported_profile",
            "Unexpected inspection endpoint or timing",
        )
        deadline = self.clock() + timeout
        self.observe()
        serial = self.serial_factory(
            port=None,
            baudrate=baud,
            timeout=min(0.05, self._remaining(deadline)),
            write_timeout=min(0.2, self._remaining(deadline)),
            exclusive=True,
            rtscts=False,
            dsrdtr=False,
        )
        try:
            asserted = gecko_profile(self.target.board).modem_asserted
            # Configure both while closed. On the reviewed Sonoff, matching the
            # kernel's asserted pair avoids sequential False/True transitions.
            # This does not guarantee that kernel open/close cannot reset a board.
            serial.dtr = asserted
            serial.rts = asserted
            serial.port = port
            serial.open()
            info = self.fd_stat(serial.fileno())
            _require(
                stat.S_ISCHR(info.st_mode)
                and (os.major(info.st_rdev), os.minor(info.st_rdev))
                == self.target.device_number,
                "target_changed",
                "Opened descriptor is not the selected character device",
            )
            self.claim_exclusive(serial.fileno())
            if asserted:
                self.modem_check(serial.fileno())
            self.observe()
            self._remaining(deadline)
            yield serial, deadline
            self._remaining(deadline)
            self.observe()
        except OSError as error:
            code = (
                "radio_busy"
                if error.errno in (errno.EBUSY, errno.EAGAIN, errno.EACCES)
                else "inspection_failed"
            )
            raise BundleError(code, "Serial inspection is unavailable") from error
        finally:
            serial.close()

    def _write(self, port, raw, deadline):
        """Write one bounded fixed command; a partial write is never retried.

        @param port Already opened exact serial endpoint.
        @param raw Fixed provider command bytes.
        @param deadline Total action monotonic deadline.
        """
        port.write_timeout = min(0.2, self._remaining(deadline))
        _require(
            port.write(raw) == len(raw), "inspection_failed", "Partial probe write"
        )

    def _read(self, port, size, deadline):
        """Read within remaining budget and a hard byte bound.

        @param port Already opened exact serial endpoint.
        @param size Maximum bytes to consume this call.
        @param deadline Total action monotonic deadline.
        """
        port.timeout = min(0.05, self._remaining(deadline))
        data = port.read(size)
        _require(
            isinstance(data, bytes) and len(data) <= size,
            "inspection_failed",
            "Invalid serial read",
        )
        return data

    def probe_loader(self, port, baud, timeout=3):
        """Inspect the complete menu only; never select upload, run or reset.

        @param port Frozen canonical device path.
        @param baud Fixed loader rate, 115200.
        @param timeout Total action I/O budget in seconds, at most three.
        @return Adapter loader projection; security facts come from reviewed policy.
        """
        self._validate_loader_policy()
        with self._port(port, baud, LOADER_BAUD, timeout) as (serial, deadline):
            return self._loader_menu(serial, deadline)

    def prepare_from_sniffer(self, before_entry, progress):
        """Identify the supported sniffer, enter once with consent, then observe its loader.

        @param before_entry Parent checkpoint that durably grants the one possible entry write.
        @param progress Callback for nonmutating bounded observation phases.
        @return Fresh loader observation under the separately reviewed historical security policy.
        """
        _require(
            self.target.board == "sonoff-zbdongle-e",
            "unsupported_profile",
            "Software entry requires the reviewed Sonoff profile",
        )
        self._validate_loader_policy()
        with self._port(self.target.port, SNIFFER_BAUD, SNIFFER_BAUD, 8) as (
            serial,
            deadline,
        ):
            progress("identifying_application")
            serial.reset_input_buffer()
            decoder = SnifferIdentity()
            started = self.clock()
            identify_deadline = min(deadline, started + 3)
            while True:
                previous = decoder.statuses
                ready = decoder.feed(self._read(serial, 256, identify_deadline))
                # Buffered bursts alone cannot prove an app is still transmitting.
                if (
                    ready
                    and decoder.statuses > previous
                    and self.clock() - started >= 0.5
                ):
                    break
            self.observe()
            before_entry()
            self.observe()
            self._write(serial, b"\nBOOTLOADER\n", deadline)
            # The sniffer has no command ACK and may drain its TX queue for one second.
            self.sleep(min(1.1, self._remaining(deadline)))
            self._remaining(deadline)
            self.observe()
            serial.baudrate = LOADER_BAUD
            serial.reset_input_buffer()
            progress("verifying_loader")
            return self._loader_menu(serial, min(deadline, self.clock() + 3))

    def _validate_loader_policy(self):
        """Refuse unknown or unreviewed loader security assumptions before serial open."""
        _require(
            isinstance(self.loader_policy, LoaderPolicy),
            "loader_unverified",
            "Reviewed loader policy is required",
        )
        self.loader_policy.validate(self.target)

    def _loader_menu(self, serial, deadline):
        """Observe a complete supported menu while retaining the same serial descriptor.

        @param serial Selected open serial endpoint under the physical lease.
        @param deadline Monotonic deadline shared with any subsequent run selection.
        """
        pattern = self.menu_pattern()
        profile = gecko_profile(self.target.board)
        self._write(serial, profile.menu_request, deadline)
        data = bytearray()
        while len(data) <= 4096:
            data.extend(self._read(serial, min(256, 4097 - len(data)), deadline))
            _require(
                len(data) <= 4096,
                "loader_unverified",
                "Oversized bootloader response",
            )
            match = pattern.search(data)
            if match:
                _require(
                    match.groupdict().get("type") == b"Gecko"
                    and b"1. upload gbl\r\n" in match.group()
                    and match.group("version") in profile.loader_wire_versions
                    and re.search(rb"signed|encrypt|secure", data, re.I) is None,
                    "loader_unverified",
                    "Unexpected loader version or security indication",
                )
                break
        return dict(
            kind="gecko",
            version=profile.loader_version,
            baud=LOADER_BAUD,
            upload_supported=True,
            requires_signed=False,
            requires_encrypted=False,
        )

    def resume_intact_application(
        self, expected_version, *, mode, timeout=8, before_run=None
    ):
        """Resume only an untouched baseline after an explicit controller-authorized cancel.

        The controller must reject any prior installation attempt, uncertain
        mutation or recovery state, bind its saved baseline to this attachment,
        and supply a freshly known mode. Never use application queries to guess
        an unknown mode: numeric UART IDs can be interpreted as Gecko choices.

        @param expected_version Exact saved baseline application version.
        @param mode Fresh trusted observation, application or bootloader; no fallback guessing.
        @param timeout Total inspection/startup budget in seconds, at most eight.
        @param before_run Required durable zero-argument acknowledgement before menu choice 2.
        @return Independently verified disarmed baseline replies, never network readiness.
        """
        try:
            parse_version(expected_version)
        except (TypeError, ValueError) as error:
            raise BundleError(
                "application_mismatch", "Baseline version is invalid"
            ) from error
        _require(
            mode in ("application", "bootloader")
            and type(timeout) in (int, float)
            and math.isfinite(timeout)
            and 0 < timeout <= 8,
            "resume_unverified",
            "A known mode and bounded cancellation are required",
        )
        deadline = self.clock() + timeout
        if mode == "bootloader":
            _require(
                callable(before_run),
                "resume_unverified",
                "Durable run acknowledgement is required",
            )
            self._validate_loader_policy()
            with self._port(
                self.target.port,
                LOADER_BAUD,
                LOADER_BAUD,
                min(3, self._remaining(deadline)),
            ) as (serial, probe_deadline):
                self._loader_menu(serial, probe_deadline)
                before_run()
                self.observe()
                # A missing/partial run write is uncertain. Never repeat it.
                self._write(serial, b"2", min(deadline, probe_deadline))
            self.sleep(min(0.5, self._remaining(deadline)))
        evidence = self.verify_application(
            self.target.port, APP_BAUD, timeout=min(3, self._remaining(deadline))
        )
        self._remaining(deadline)
        check_application(expected_version, evidence, board=self.target.board)
        return evidence

    def verify_application(self, port, baud, timeout=3):
        """Read separately correlated VERSION/CAPS/STATUS replies without RF writes.

        @param port Frozen canonical device path.
        @param baud Fixed application rate, 460800.
        @param timeout Shared budget for all three queries in seconds, at most three.
        @return Raw independent mappings; adapter validates expected version/disarmed state.
        """
        replies = {}
        with self._port(port, baud, APP_BAUD, timeout) as (serial, deadline):
            request = secrets.randbelow(0xFFFFFF00) + 1
            consumed = 0
            buffer = bytearray()
            for offset, command in enumerate(("VERSION", "CAPS", "STATUS")):
                ident = request + offset
                self._write(serial, f"{ident} {command}\n".encode(), deadline)
                prefix = f"RSP {ident} ".encode()
                while True:
                    chunk = self._read(serial, 256, deadline)
                    consumed += len(chunk)
                    buffer.extend(chunk)
                    _require(
                        consumed <= 16384,
                        "application_mismatch",
                        "Excessive application response data",
                    )
                    found = None
                    while b"\n" in buffer:
                        line, _, rest = buffer.partition(b"\n")
                        buffer = bytearray(rest)
                        _require(
                            len(line) <= 1024, "application_mismatch", "Long reply"
                        )
                        if line.startswith(prefix):
                            _require(
                                found is None, "application_mismatch", "Duplicate reply"
                            )
                            try:
                                found = line[len(prefix) :].decode("ascii").strip()
                            except UnicodeDecodeError as error:
                                raise BundleError(
                                    "application_mismatch",
                                    "Non-ASCII application reply",
                                ) from error
                    _require(len(buffer) <= 1024, "application_mismatch", "Long reply")
                    if found is not None:
                        _require(
                            found.split()[:1] == ["OK"],
                            "application_mismatch",
                            "Application rejected inspection",
                        )
                        pairs = [
                            part.split("=", 1) for part in found.split() if "=" in part
                        ]
                        fields = dict(pairs)
                        _require(
                            len(fields) == len(pairs),
                            "application_mismatch",
                            "Duplicate application field",
                        )
                        replies[command.lower()] = fields
                        break
        version = replies.get("version", {}).get("fw_version")
        try:
            parse_version(version)
        except (TypeError, ValueError) as error:
            raise BundleError(
                "application_mismatch", "Invalid application version"
            ) from error
        check_application(version, replies, board=self.target.board)
        return replies
