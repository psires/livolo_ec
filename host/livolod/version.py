"""Release versions use numeric major.minor; wire/schema numbers are separate."""

import re
from . import __version__


def parse_version(value):
    """Validate a release identifier for numeric ordering.

    @param value Release text in major.minor form without leading zeroes.
    @return The major and minor integer components.
    """
    if not isinstance(value, str) or not re.fullmatch(
        r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)", value
    ):
        raise ValueError("Expected major.minor version")
    parts = tuple(map(int, value.split(".")))
    if max(parts) > 65535:
        raise ValueError("Version component exceeds 65535")
    return parts


def firmware_info(caps):
    """Describe the running firmware and its compatibility with this host release.

    @param caps Adapter capability fields returned by CAPS.
    @return Firmware release, update support, and initial trial-status metadata.
    """
    running = caps.get("fw_version")
    if running is not None:
        parse_version(running)
    return {
        "running_version": running,
        "target_version": __version__,
        "update_available": (
            parse_version(running) < parse_version(__version__) if running else None
        ),
        "update_supported": caps.get("update") == "uart_signed_v1",
        "boot_version": caps.get("boot_version"),
        "trial": None,
    }
