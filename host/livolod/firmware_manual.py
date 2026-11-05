"""Explicit PCA10059 factory-DFU recovery under the normal firmware job owner.

No USB discovery fallback, GPIO control, service control, or application reset
is performed. The operator must identify the board and enter its bootloader.
Automatic daemon updates remain disabled for this unqualified USB handoff.
"""

import json
import os
from pathlib import Path
import subprocess
import tempfile

from .firmware_artifacts import inspect_artifact_bytes
from .firmware_bundle import BundleError

HELPER_CHECK = (
    "import importlib.metadata,json,sys; "
    "print(json.dumps({'python':list(sys.version_info[:2]),"
    "'nrfutil':importlib.metadata.version('nrfutil')}))"
)


def _usb_target(port_name, serial_number, vid, pid):
    """Match an explicit path and unique serial against one exact USB mode.

    @param port_name Exact absolute device path, including a stable by-id symlink if available.
    @param serial_number Explicit expected USB serial number, not a generic product label.
    @param vid Expected vendor ID for the selected application or factory bootloader.
    @param pid Expected product ID for that USB mode.
    @return True only for one matching OS-enumerated identity at that exact path.
    """
    from serial.tools.list_ports import comports

    if (
        not isinstance(port_name, str)
        or not os.path.isabs(port_name)
        or "\0" in port_name
        or not isinstance(serial_number, str)
        or not serial_number
        or len(serial_number) > 256
    ):
        return False
    ports = list(comports())
    selected = [
        p for p in ports if os.path.realpath(p.device) == os.path.realpath(port_name)
    ]
    identities = [
        p
        for p in ports
        if p.vid == vid and p.pid == pid and p.serial_number == serial_number
    ]
    return (
        len(selected) == len(identities) == 1
        and selected[0].vid == vid
        and selected[0].pid == pid
        and selected[0].serial_number == serial_number
    )


def _target(dfu_port, serial_number):
    """Require the explicitly selected PCA10059 factory-DFU USB identity.

    @param dfu_port Absolute selected factory-DFU device path.
    @param serial_number Expected factory-DFU USB serial number.
    @return Whether exactly one matching endpoint is present; no serial commands sent.
    """
    return _usb_target(dfu_port, serial_number, 0x1915, 0x521F)


def application_target_status(port_name, serial_number):
    """Check an explicitly selected gateway application identity after manual DFU.

    @param port_name Exact configured application device path, not a DFU path guess.
    @param serial_number Operator-supplied application USB serial (may differ from DFU).
    @return Unique cafe:4001 identity match; caller must still verify live firmware/session.
    """
    return _usb_target(port_name, serial_number, 0xCAFE, 0x4001)


def _helper(helper_python):
    """Check an explicitly configured isolated legacy runtime without running DFU.

    @param helper_python Absolute trusted Python executable path for nrfutil 6.1.7.
    @return Whether Python 3.10 and the pinned package metadata are present.
    """
    if (
        not isinstance(helper_python, str)
        or not os.path.isabs(helper_python)
        or "\0" in helper_python
        or not os.path.isfile(helper_python)
        or not os.access(helper_python, os.X_OK)
    ):
        return False
    result = subprocess.run(
        [helper_python, "-I", "-c", HELPER_CHECK],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        timeout=10,
        check=False,
        cwd="/",
    )
    if result.returncode or len(result.stdout) > 1024:
        return False
    if json.loads(result.stdout) != {"python": [3, 10], "nrfutil": "6.1.7"}:
        return False
    # Metadata alone cannot establish that the legacy USB dependencies import.
    return (
        subprocess.run(
            [helper_python, "-I", "-m", "nordicsemi", "dfu", "usb-serial", "--help"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=10,
            check=False,
            cwd="/",
        ).returncode
        == 0
    )


def manual_backend_status(candidate, *, helper_python, dfu_port, serial_number):
    """Report explicit recovery readiness without serial commands or flash writes.

    @param candidate Private validated application candidate.
    @param helper_python Operator-configured isolated helper interpreter.
    @param dfu_port Exact target already in factory USB DFU mode.
    @param serial_number Expected factory-DFU USB serial identity.
    @return Sanitized readiness report; no paths, serials, bytes or helper output.
    """
    checks = []
    supported = (
        candidate.get("board") == "nordic-pca10059"
        and candidate.get("backend") == "nrf52840_usb_dfu_v1"
        and candidate.get("operation") == "recovery"
    )
    checks.append(
        dict(
            id="manual_backend",
            status="pass" if supported else "fail",
            code="supported" if supported else "unsupported_operation",
        )
    )
    if supported:
        for ident, check, code in (
            (
                "dfu_identity",
                lambda: _target(dfu_port, serial_number),
                "dfu_identity_unavailable",
            ),
            ("helper", lambda: _helper(helper_python), "backend_unavailable"),
        ):
            try:
                valid = check()
            except (OSError, ValueError, subprocess.SubprocessError, ImportError):
                valid = False
            checks.append(
                dict(
                    id=ident,
                    status="pass" if valid else "fail",
                    code="compatible" if valid else code,
                )
            )
    reason = next((c["code"] for c in checks if c["status"] != "pass"), None)
    return dict(
        compatible=supported,
        executable=reason is None,
        identity_source="explicit_operator",
        checks=checks,
        blocked_reason=reason,
        requires_manual_action=reason is not None,
    )


def install_manual(
    candidate, *, port_name, helper_python, serial_number, lock_fd, before, progress
):
    """Upload a revalidated application to the explicitly selected factory bootloader.

    @param candidate Private validated recovery candidate with immutable package bytes.
    @param port_name Exact device path currently in USB DFU mode.
    @param helper_python Trusted isolated Python 3.10 executable with nrfutil 6.1.7.
    @param serial_number Explicit expected bootloader USB serial number.
    @param lock_fd Inherited exclusive lifetime lock held by the durable job owner.
    @param before Durable phase callback; must acknowledge flash before mutation.
    @param progress Event callback; receives transfer-complete, never network confirmation.
    """
    from .firmware_worker import validate_guard

    validate_guard(lock_fd)
    inspection = inspect_artifact_bytes(
        candidate["_data"], board=candidate["board"], version=candidate["version"]
    )
    if inspection["sha256"] != candidate["sha256"]:
        raise BundleError(
            "artifact_invalid", "Recovery artifact changed after inspection"
        )
    status = manual_backend_status(
        candidate,
        helper_python=helper_python,
        dfu_port=port_name,
        serial_number=serial_number,
    )
    if not status["executable"]:
        raise BundleError(
            status["blocked_reason"], "Manual recovery prerequisites are not satisfied"
        )
    with tempfile.TemporaryDirectory(prefix="livolo-dfu-") as directory:
        payload = Path(directory) / "application.zip"
        payload.write_bytes(candidate["_data"])
        before("flash")
        # The parent handshake may take time; refuse a replacement USB device.
        if not _target(port_name, serial_number):
            raise BundleError(
                "dfu_identity_changed", "DFU target changed before transfer"
            )
        child = subprocess.Popen(
            [
                helper_python,
                "-I",
                "-m",
                "nordicsemi",
                "dfu",
                "usb-serial",
                "--package",
                str(payload),
                "--port",
                port_name,
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
                "flash_failed",
                "Nordic DFU did not complete; manual recovery may be required",
            )
    progress(dict(stage="transferred"))
