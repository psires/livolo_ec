"""Linux USB identity checks and a root-owned narrow device-node publisher."""

import os
from pathlib import Path
import re
import stat
import argparse
import fcntl
import grp
import json
import uuid

SYSFS = Path("/sys/class/tty")
MODES = {"application": (0xCAFE, 0x4001), "dfu": (0x1915, 0x521F)}


def profile(raw):
    """Validate an operator-only USB identity and isolated helper configuration.

    @param raw Empty mapping to disable integration, otherwise the complete profile.
    @return Detached validated mapping; never inferred from a first matching device.
    """
    keys = {
        "usb_path",
        "application_serial",
        "dfu_serial",
        "device_directory",
        "helper_python",
    }
    if raw == {}:
        return {}
    if not isinstance(raw, dict) or set(raw) != keys:
        raise ValueError("firmware_nordic requires a complete explicit USB profile")
    for key in ("usb_path", "device_directory", "helper_python"):
        value = raw[key]
        if (
            not isinstance(value, str)
            or not value.startswith("/")
            or "\0" in value
            or ".." in Path(value).parts
        ):
            raise ValueError("Invalid Nordic profile path")
    if not raw["usb_path"].startswith("/sys/devices/"):
        raise ValueError("Nordic USB topology must identify its physical sysfs device")
    for key in ("application_serial", "dfu_serial"):
        if not isinstance(raw[key], str) or not re.fullmatch(
            r"[A-Za-z0-9_-]{1,128}", raw[key]
        ):
            raise ValueError("An explicit Nordic USB serial is required for each mode")
    if raw["application_serial"] == raw["dfu_serial"]:
        raise ValueError("Nordic application and factory DFU serials must be distinct")
    return dict(raw)


def enumerate_usb():
    """Read Linux USB tty identities without opening any device.

    @return Detached records of USB parent, VID/PID, serial and character-device number.
    """
    result = []
    for tty in SYSFS.glob("ttyACM*"):
        try:
            device = (tty / "device").resolve(strict=True)
            parent = next(p for p in device.parents if (p / "idVendor").is_file())
            major, minor = map(int, (tty / "dev").read_text().strip().split(":"))
            result.append(
                dict(
                    usb_path=str(parent),
                    vid=int((parent / "idVendor").read_text(), 16),
                    pid=int((parent / "idProduct").read_text(), 16),
                    serial=(parent / "serial").read_text().strip(),
                    major=major,
                    minor=minor,
                )
            )
        except (OSError, ValueError, StopIteration):
            continue
    return result


def usb_identity(settings, mode):
    """Require a unique serial and the exact physical USB path in the requested mode.

    @param settings Validated explicit operator profile.
    @param mode Application or factory DFU mode.
    @return Matching sysfs record; absence, duplication or replacement raises ValueError.
    """
    settings = profile(settings)
    if not settings or mode not in MODES:
        raise ValueError("Nordic USB integration is not configured")
    vid, pid = MODES[mode]
    ports = enumerate_usb()
    matches = [
        p
        for p in ports
        if (p["vid"], p["pid"], p["serial"]) == (vid, pid, settings[mode + "_serial"])
    ]
    topology = [p for p in ports if p["usb_path"] == settings["usb_path"]]
    if (
        len(matches) != 1
        or len(topology) != 1
        or matches[0] != topology[0]
        or matches[0]["major"] != 166
    ):
        raise ValueError(
            "The configured Nordic USB identity is unavailable or ambiguous"
        )
    return matches[0]


def publish(settings, group):
    """Publish only the configured board's current nodes into a protected directory.

    @param settings Explicit operator profile read from a root-owned configuration.
    @param group Host group permitted to open the two character-device nodes.
    """
    settings = profile(settings)
    if os.geteuid() != 0 or not settings:
        raise ValueError("A configured root-owned publisher is required")
    destination = Path(settings["device_directory"])
    if str(destination) != "/run/livolod-usb":
        raise ValueError("Publisher directory must be /run/livolod-usb")
    gid = grp.getgrnam(group).gr_gid
    destination.mkdir(mode=0o750, parents=False, exist_ok=True)
    info = destination.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
        raise ValueError("Unsafe USB publisher directory")
    os.chown(destination, 0, gid)
    lock = os.open(
        "/run/livolod-usb.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600
    )
    try:
        fcntl.flock(lock, fcntl.LOCK_EX)
        for mode in MODES:
            path = destination / mode
            try:
                record = usb_identity(settings, mode)
            except ValueError:
                path.unlink(missing_ok=True)
                continue
            temporary = destination / ("." + uuid.uuid4().hex)
            try:
                os.mknod(
                    temporary,
                    stat.S_IFCHR | 0o660,
                    os.makedev(record["major"], record["minor"]),
                )
                os.chown(temporary, 0, gid)
                os.chmod(temporary, 0o660)
                os.replace(temporary, path)
            finally:
                temporary.unlink(missing_ok=True)
    finally:
        os.close(lock)


def main():
    """Run the bounded udev publisher with an explicit root-owned configuration file."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True)
    parser.add_argument("--group", default="dialout")
    args = parser.parse_args()
    path = Path(args.config)
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
        raise ValueError(
            "USB publisher configuration must be a protected root-owned regular file"
        )
    publish(json.loads(path.read_text()), args.group)


if __name__ == "__main__":
    main()
