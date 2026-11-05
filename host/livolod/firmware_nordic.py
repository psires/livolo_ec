"""Explicitly configured Nordic USB handoff under the durable firmware owner."""

import json
import os
from pathlib import Path
import re
import stat
import subprocess
import tempfile
import time

from .firmware_bundle import BundleError

from .nordic_usb import profile, usb_identity


def target(settings, mode):
    """Match a narrowly published device node to its current sysfs identity.

    @param settings Validated operator profile, including the mounted device directory.
    @param mode Application or factory DFU mode.
    @return Exact device node path after checking mode, topology, serial and device number.
    """
    try:
        record = usb_identity(settings, mode)
    except ValueError as error:
        raise BundleError(
            "usb_identity_unavailable",
            "The configured Nordic USB identity is unavailable or ambiguous",
        ) from error
    path = Path(settings["device_directory"]) / mode
    try:
        node = path.lstat()
        if not stat.S_ISCHR(node.st_mode) or node.st_rdev != os.makedev(
            record["major"], record["minor"]
        ):
            raise ValueError("Device node does not match USB identity")
        if os.statvfs(path).f_flag & getattr(os, "ST_NODEV", 4):
            raise ValueError("Device node is on a nodev filesystem")
        if not os.access(path, os.R_OK | os.W_OK):
            raise ValueError("Device node is not accessible")
    except (OSError, ValueError) as error:
        raise BundleError(
            "device_access_unavailable",
            "The selected Nordic device node is unavailable",
        ) from error
    return str(path)


def wait_target(settings, mode, timeout=20):
    """Wait boundedly for only the configured board to enter the requested USB mode.

    @param settings Validated operator profile.
    @param mode Expected USB mode.
    @param timeout Maximum handoff duration in seconds.
    @return Verified device node path; never a substitute serial port.
    """
    deadline = time.monotonic() + timeout
    while True:
        try:
            return target(settings, mode)
        except BundleError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(0.1)


def backend_status(settings):
    """Check the explicit application identity and isolated helper without serial I/O.

    @param settings Operator profile; an empty mapping keeps integration disabled.
    @return None when prerequisites pass, otherwise a stable blocking reason.
    """
    try:
        if not profile(settings):
            return "backend_unavailable"
        target(settings, "application")
        helper = Path(settings["helper_python"])
        marker = helper.parent.parent / "livolo-runtime.json"
        expected = {"python": [3, 10], "nrfutil": "6.1.7", "fixed_port": 1}
        if (
            not helper.is_file()
            or not os.access(helper, os.X_OK)
            or json.loads(marker.read_text()) != expected
        ):
            return "backend_unavailable"
        wrapper = Path(__file__).with_name("nordic_helper.py")
        if not wrapper.is_file():
            return "backend_unavailable"
    except BundleError as error:
        return error.code
    except (OSError, ValueError, subprocess.SubprocessError, ImportError):
        return "backend_unavailable"
    return None


def install(candidate, port_name, lock_fd, before, progress, settings):
    """Enter factory DFU, transfer one validated app, and verify its USB return.

    @param candidate Validated immutable newer Nordic application candidate.
    @param port_name Configured application device node.
    @param lock_fd Inherited lifetime guard held by the worker and its helper.
    @param before Durable parent acknowledgement callback for mutation boundaries.
    @param progress Worker progress callback; success still requires normal restoration.
    @param settings Exact operator-selected physical board and isolated helper profile.
    """
    from .firmware_backends import _preflight
    from .firmware_artifacts import inspect_artifact_bytes
    from .firmware_update import UpdatePort
    from .firmware_worker import validate_guard
    from .firmware_manual import _helper

    validate_guard(lock_fd)
    inspection = inspect_artifact_bytes(
        candidate["_data"], board=candidate["board"], version=candidate["version"]
    )
    if inspection["sha256"] != candidate["sha256"]:
        raise BundleError("artifact_invalid", "Nordic payload changed before transfer")
    reason = backend_status(settings)
    if reason or not _helper(settings["helper_python"]):
        raise BundleError(
            reason or "backend_unavailable", "Nordic runtime or target is unavailable"
        )
    if port_name != target(settings, "application"):
        raise BundleError(
            "usb_identity_unavailable",
            "Application port does not match the selected Nordic board",
        )
    with tempfile.TemporaryDirectory(prefix="livolo-nordic-") as directory:
        payload = Path(directory) / "application.zip"
        payload.write_bytes(candidate["_data"])
        port = UpdatePort(port_name, candidate["app_baudrate"])
        try:
            _preflight(port, candidate)
            before("bootloader_entry")
            target(settings, "application")
            try:
                port.command("BOOTLOADER", timeout=3, attempts=1)
            except (OSError, TimeoutError):
                pass  # Exact DFU identity below is required despite an expected USB disconnect.
        finally:
            port.close()
        dfu = wait_target(settings, "dfu")
        before("flash")
        if target(settings, "dfu") != dfu:
            raise BundleError("usb_identity_unavailable", "Nordic DFU identity changed")
        child = subprocess.Popen(
            [
                settings["helper_python"],
                "-I",
                str(Path(__file__).with_name("nordic_helper.py")),
                "--package",
                str(payload),
                "--port",
                dfu,
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            pass_fds=(lock_fd,),
            cwd=directory,
        )
        try:
            result = child.wait(timeout=120)
        except BaseException:
            child.terminate()
            try:
                child.wait(timeout=3)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()
            raise
        if result:
            raise BundleError(
                "flash_failed", "Nordic transfer failed; explicit recovery is required"
            )
        wait_target(settings, "application")
        # Check the exact target before allowing the parent to begin network restoration.
        port = UpdatePort(port_name, candidate["app_baudrate"])
        try:
            from .protocol import key_values

            version = key_values(port.command("VERSION", timeout=3, attempts=1))
            caps = key_values(port.command("CAPS", timeout=3, attempts=1))
            if any(
                v.get("board") != candidate["board"]
                or v.get("fw_version") != candidate["version"]
                or v.get("proto") != "3"
                for v in (version, caps)
            ):
                raise BundleError(
                    "restore_mismatch",
                    "Nordic application identity or version did not match",
                )
        finally:
            port.close()
        progress(dict(stage="transferred"))
