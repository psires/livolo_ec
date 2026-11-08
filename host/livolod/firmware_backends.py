"""Fixed board-specific installation operations, without service or database access.

The daemon remains responsible for quiescence, durable phase acknowledgement,
worker lifetime and post-install verification/restore. The separate pinned GPL
Silicon Labs helper is invoked as a CLI; its implementation is not copied here.
Nordic USB handoff requires an explicit operator-selected identity/runtime profile.
"""

import importlib.metadata
import subprocess
import sys
import tempfile
from pathlib import Path

from .firmware_bundle import BundleError
from .firmware_update import UpdatePort, transfer
from .protocol import key_values
from .version import parse_version

SILABS_VERSION = "0.0.31"


def backend_status(candidate, nordic=None):
    """Check local helper availability without touching a radio.

    @param candidate Validated bundle candidate dictionary.
    @param nordic Optional operator-configured Nordic USB/helper profile.
    @return None when installed, otherwise a stable blocking reason code.
    """
    try:
        if candidate["backend"] == "nrf52840_usb_dfu_v1":
            from .firmware_nordic import backend_status as nordic_status

            return nordic_status(nordic or {})
        if candidate["backend"] == "ti_uart_signed_v1":
            importlib.metadata.version("cryptography")
            return None
        if candidate["backend"] == "mg21_gecko_gbl_v1":
            if importlib.metadata.version("universal-silabs-flasher") == SILABS_VERSION:
                return None
    except importlib.metadata.PackageNotFoundError:
        pass
    return "backend_unavailable"


def _preflight(port, candidate):
    """Recheck live identity and disarmed ownership before allowing any mutation.

    @param port Exclusively owned UpdatePort transport.
    @param candidate Verified desired board, version, rate and package.
    @return Current firmware VERSION fields; raises on mismatch or armed state.
    """
    version = key_values(port.command("VERSION", timeout=3, attempts=1))
    caps = key_values(port.command("CAPS", timeout=3, attempts=1))
    if (
        version.get("board") != candidate["board"]
        or caps.get("board") != candidate["board"]
        or version.get("proto") != "3"
        or caps.get("proto") != "3"
        or version.get("fw_version") != caps.get("fw_version")
    ):
        raise BundleError(
            "board_identity_changed", "Radio identity changed before update"
        )
    try:
        newer = parse_version(candidate["version"]) > parse_version(
            version.get("fw_version")
        )
    except ValueError as error:
        raise BundleError(
            "board_identity_unknown", "Radio firmware version is unknown"
        ) from error
    if not newer:
        raise BundleError(
            "version_not_newer", "Candidate must be newer than running firmware"
        )
    if caps.get("baud") != str(candidate["app_baudrate"]):
        raise BundleError(
            "incompatible_baudrate", "Radio rate does not match the selected update"
        )
    if candidate["backend"] == "ti_uart_signed_v1":
        if (
            version.get("update") != "uart_signed_v1"
            or version.get("boot_version") != "0.2"
        ):
            raise BundleError(
                "bootloader_install_required",
                "TI needs the supported signed-update bootloader",
            )
        status = key_values(port.command("UPDATE_STATUS", timeout=3, attempts=1))
        if status.get("key") != candidate["public_key"]:
            raise BundleError(
                "signing_key_mismatch",
                "Update key does not match installed TI firmware",
            )
        # Integrated recovery never silently resumes a previous durable update.
        if status.get("trial") != "0" or status.get("state") not in (
            "ffffffff",
            "fffffff0",
            "ffffffc8",
        ):
            raise BundleError(
                "recovery_required", "An earlier TI update requires reconciliation"
            )
    status = key_values(port.command("STATUS", timeout=3, attempts=1))
    if status.get("armed") != "0" or status.get("ack") != "0":
        raise BundleError(
            "radio_busy", "Radio must be disarmed before handing off to the updater"
        )
    return version


def _run_flasher(path, port, lock_fd, guard_fds=()):
    """Run the pinned Gecko helper with fixed arguments and a bounded lifetime.

    @param path Private staged application-only GBL path, already validated.
    @param port Exact configured serial device path; never a discovered first match.
    @param lock_fd Held radio guard inherited by the helper until it exits.
    @param guard_fds Additional held deployment guards inherited by a setup helper.
    """
    command = [
        sys.executable,
        "-m",
        "universal_silabs_flasher",
        "--device",
        port,
        "--bootloader-baudrate",
        "115200",
        "--probe-method",
        "bootloader",
        "flash",
        "--firmware",
        str(path),
    ]
    # No shell, detached session, generic reset profile, remote URL or flash override.
    child = subprocess.Popen(
        command,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        pass_fds=tuple(dict.fromkeys((lock_fd,) + tuple(guard_fds))),
    )
    try:
        result = child.wait(timeout=120)
    except BaseException:
        child.terminate()
        try:
            child.wait(timeout=3)
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait(timeout=3)
        raise
    if result:
        raise BundleError(
            "flash_failed", "Gecko upload failed; recovery may be required"
        )


def install(candidate, port_name, lock_fd, before, progress, nordic=None):
    """Transfer a validated newer application under exclusive worker ownership.

    @param candidate Verified private candidate, including immutable payload bytes.
    @param port_name Exact local serial device path selected by daemon configuration.
    @param lock_fd Open inherited lifetime guard; pass unchanged to external helpers.
    @param before Blocking durable phase handshake; failure prevents the next mutation.
    @param progress Callback for genuine transfer-stage measurements only.
    @param nordic Optional explicit Nordic identity/runtime profile from local configuration.
    @return Nothing; successful return requires daemon reconnect/restore to confirm.
    """
    reason = backend_status(candidate, nordic)
    if reason:
        raise BundleError(
            reason, "Required firmware installation backend is unavailable"
        )
    if candidate["backend"] == "nrf52840_usb_dfu_v1":
        from .firmware_nordic import install as nordic_install

        return nordic_install(candidate, port_name, lock_fd, before, progress, nordic)
    if (
        not isinstance(port_name, str)
        or not port_name.startswith("/")
        or "\0" in port_name
    ):
        raise BundleError(
            "device_access_unavailable", "An exact local serial path is required"
        )
    port = UpdatePort(port_name, candidate["app_baudrate"])
    try:
        _preflight(port, candidate)
        if candidate["backend"] == "ti_uart_signed_v1":
            from cryptography.hazmat.primitives.asymmetric.ed25519 import (
                Ed25519PublicKey,
            )

            transfer(
                port,
                candidate["_data"],
                Ed25519PublicKey.from_public_bytes(
                    bytes.fromhex(candidate["public_key"])
                ),
                progress=progress,
                before=before,
            )
            return
        if candidate["backend"] != "mg21_gecko_gbl_v1":
            raise BundleError(
                "backend_unavailable", "Unsupported firmware installation backend"
            )
        # Stage immutable bytes before downtime; do not hand the helper a mutable
        # source path that was only validated earlier in the parent process.
        with tempfile.TemporaryDirectory(prefix="livolo-gbl-") as directory:
            payload = Path(directory) / "application.gbl"
            payload.write_bytes(candidate["_data"])
            before("disarm")
            port.command("DISARM", timeout=3, attempts=1)
            before("bootloader_entry")
            try:
                port.command("BOOTLOADER", timeout=3, attempts=1)
            except TimeoutError:
                pass  # The reply may be lost during reset; probe only this device.
            port.close()
            port = None
            before("flash")
            _run_flasher(payload, port_name, lock_fd)
    finally:
        if port:
            port.close()
