"""Independent software identity, exact firmware compatibility and wire revisions."""

import json
import re
from . import (
    __version__,
    FIRMWARE_COMPAT_VERSION,
    NATIVE_API_VERSION,
    UART_PROTOCOL_VERSION,
)


def parse_daemon_version(value):
    """Validate an ordinary numeric three-component software version separately.

    @param value Daemon package SemVer without leading zeroes or suffixes.
    @return Major, minor and patch integers; never used to encode firmware images.
    """
    if not isinstance(value, str) or not re.fullmatch(
        r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)", value
    ):
        raise ValueError("Expected major.minor.patch daemon version")
    return tuple(map(int, value.split(".")))


def version_info():
    """Report explicit software, firmware and wire identities without I/O.

    @return JSON-safe identity fields shared by CLI, API and MQTT diagnostics.
    """
    parse_daemon_version(__version__)
    parse_version(FIRMWARE_COMPAT_VERSION)
    return dict(
        daemon_version=__version__,
        firmware_compat_version=FIRMWARE_COMPAT_VERSION,
        api_version=NATIVE_API_VERSION,
        uart_protocol=UART_PROTOCOL_VERSION,
    )


def add_version_arguments(parser, *, software_flag=True):
    """Add immediate, read-only version actions before required CLI arguments.

    @param parser ArgumentParser receiving the common version options.
    @param software_flag False when --version already denotes a firmware artifact.
    """
    if software_flag:
        parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument(
        "--version-info",
        action="version",
        version=json.dumps(version_info(), sort_keys=True),
    )


def parse_version(value):
    """Validate a firmware identifier for bounded two-component numeric ordering.

    @param value Firmware text in major.minor form without leading zeroes.
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
    """Describe running firmware against the explicit radio compatibility target.

    @param caps Adapter capability fields returned by CAPS.
    @return Firmware release, update support, and initial trial-status metadata.
    """
    running = caps.get("fw_version")
    if running is not None:
        parse_version(running)
    return {
        "board": caps.get("board"),
        "running_version": running,
        "target_version": FIRMWARE_COMPAT_VERSION,
        "update_available": (
            parse_version(running) < parse_version(FIRMWARE_COMPAT_VERSION)
            if running
            else None
        ),
        "update_supported": caps.get("update") == "uart_signed_v1",
        "boot_version": caps.get("boot_version"),
        "trial": None,
    }
