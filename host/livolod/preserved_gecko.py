"""Internal reviewed-Gecko application-only adapter with injected physical I/O.

The opt-in bootstrap worker supplies the physical provider, exact-unit policy,
durable permissions and inherited guards. This adapter alone does not authorize
physical installation or implement a stock-reset workflow. Normal update and
standalone eligibility remain separate from the guided engineering setup path.
"""

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import tempfile

from . import FIRMWARE_COMPAT_VERSION
from .firmware_artifacts import inspect_artifact_bytes
from .firmware_backends import _run_flasher, backend_status
from .firmware_bundle import BundleError
from .firmware_worker import validate_guard
from .gecko_profiles import gecko_profile

BOARD = "zb-gw04-1v2"
APP_BAUD = 460800
LOADER_BAUD = 115200
# This module alone makes no physical eligibility declaration.
PHYSICAL_INSTALL_SUPPORTED = False


@dataclass(frozen=True)
class GeckoTarget:
    """Parent-reviewed endpoint/profile, never populated directly from HTTP input."""

    port: str
    usb_path: str
    attachment: str
    device_number: tuple
    interface: str
    loader_version: str
    board: str = BOARD
    vid: int = 0x1A86
    pid: int = 0x7523
    serial: str = None

    def identity(self):
        """Bind the exact bridge to topology/generation and its serial when required."""
        value = dict(
            port=self.port,
            usb_path=self.usb_path,
            attachment=self.attachment,
            device_number=list(self.device_number),
            interface=self.interface,
            vid=self.vid,
            pid=self.pid,
        )
        if self.serial is not None:
            value["serial"] = self.serial
        return value

    def validate(self):
        """Reject unsupported profile shapes without claiming to inspect physical hardware."""
        profile = gecko_profile(self.board)
        if (
            self.vid != profile.vid
            or self.pid != profile.pid
            or self.loader_version != profile.loader_version
            or (
                profile.serial_required
                and (
                    not isinstance(self.serial, str)
                    or not 1 <= len(self.serial) <= 128
                    or not self.serial.isascii()
                    or any(ord(c) < 33 or ord(c) > 126 for c in self.serial)
                )
            )
            or (not profile.serial_required and self.serial is not None)
            or not isinstance(self.port, str)
            or not self.port.startswith("/dev/")
            or "\0" in self.port
            or ".." in Path(self.port).parts
            or any(
                not isinstance(value, str) or not value or len(value) > 128
                for value in (
                    self.usb_path,
                    self.attachment,
                    self.interface,
                    self.loader_version,
                )
            )
            or not isinstance(self.device_number, tuple)
            or len(self.device_number) != 2
            or any(type(value) is not int or value < 0 for value in self.device_number)
        ):
            raise BundleError(
                "unsupported_profile", "An exact reviewed Gecko endpoint is required"
            )


class GeckoInstallError(BundleError):
    """Sanitized worker failure with a conservative durable-recovery classification."""

    def __init__(self, code, stage, mutation_possible):
        """Retain phase facts without leaking helper output or inventing rollback.

        @param code Stable failure identifier.
        @param stage Last worker phase reached.
        @param mutation_possible Whether helper invocation could have changed the application.
        """
        super().__init__(code, "Preserved-Gecko installation did not verify")
        self.stage = stage
        self.mutation_possible = mutation_possible
        self.recovery = "recovery_required" if mutation_possible else None


def check_endpoint(target, observe):
    """Require fresh exact identity and parent-provided ownership evidence.

    @param target Frozen reviewed endpoint identity, without inferred board identification.
    @param observe Bounded no-reset provider returning identity, unique, owner and board evidence.
    """
    observed = observe()
    if (
        not isinstance(observed, dict)
        or set(observed) != {"identity", "unique", "owner", "board_evidence"}
        or observed["identity"] != target.identity()
        or observed["unique"] is not True
    ):
        raise BundleError("target_changed", "Selected endpoint is absent or ambiguous")
    if observed["owner"] != "exclusive":
        raise BundleError(
            "ownership_unresolved", "Physical ownership is not established"
        )
    if observed["board_evidence"] != gecko_profile(target.board).board_evidence:
        raise BundleError(
            "board_evidence_required", "USB bridge identity is insufficient"
        )


def check_loader(target, evidence):
    """Require the exact parent-approved Gecko loader and unsigned application upload mode.

    @param target Server-owned profile with a separately reviewed loader version.
    @param evidence Fresh exact loader probe projection from the held endpoint.
    """
    target.validate()
    if (
        not isinstance(evidence, dict)
        or any(
            type(evidence.get(key)) is not bool
            for key in ("upload_supported", "requires_signed", "requires_encrypted")
        )
        or evidence
        != dict(
            kind="gecko",
            version=target.loader_version,
            baud=LOADER_BAUD,
            upload_supported=True,
            requires_signed=False,
            requires_encrypted=False,
        )
    ):
        raise BundleError(
            "loader_unverified", "Loader does not match the reviewed profile"
        )


def check_application(version, evidence, board=BOARD):
    """Verify independent application replies, keeping RF disabled and network unconfigured.

    @param version Expected compiled version of the transferred immutable artifact.
    @param evidence Bounded projection with separate VERSION, CAPS and STATUS mappings.
    @param board Exact immutable profile expected by the admitted operation.
    """
    gecko_profile(board)
    if not isinstance(evidence, dict) or set(evidence) != {"version", "caps", "status"}:
        raise BundleError(
            "application_mismatch", "Application verification is incomplete"
        )
    reported, caps, status = (evidence[key] for key in ("version", "caps", "status"))
    if (
        not all(isinstance(value, dict) for value in (reported, caps, status))
        or any(
            reply.get("board") != board
            or reply.get("fw_version") != version
            or reply.get("proto") != "3"
            for reply in (reported, caps)
        )
        or caps.get("baud") != str(APP_BAUD)
        or status.get("armed") != "0"
        or status.get("ack") != "0"
    ):
        raise BundleError(
            "application_mismatch", "Application identity or disarmed state differs"
        )


def install_preserved_gecko(
    *,
    target,
    data,
    sha256,
    version,
    lease_fd,
    before,
    progress,
    observe,
    probe_loader,
    verify_application,
    helper_status=backend_status,
    flash=_run_flasher,
):
    """Transfer one application under a parent's durable journal and physical lease.

    @param target Exact reviewed GeckoTarget, already manually in the preserved loader.
    @param data Immutable application-only GBL bytes, never a filename or download URL.
    @param sha256 Expected trusted artifact SHA-256 from the admitted job.
    @param version Expected compiled version, matching this runtime's compatibility target.
    @param lease_fd Parent-held regular-file guard inherited until worker/helper exit.
    @param before Durable phase acknowledgement; returning grants the next possible mutation.
    @param progress Stage callback; no transfer percentage is invented for the Gecko helper.
    @param observe Bounded endpoint/owner evidence provider, with no reset or fallback scan.
    @param probe_loader Provider taking exact port, baud and timeout; no reset or flash writes.
    @param verify_application Provider returning separate VERSION/CAPS/STATUS with no RF writes.
    @param helper_status Local fixed helper availability check, injected in tests only.
    @param flash Fixed bounded helper launcher, injected in tests only; never HTTP-selectable.
    @return Firmware-only verification receipt; parent must commit it before later handoff.
    """
    stage, mutation_possible = "validating", False
    try:
        if not isinstance(target, GeckoTarget):
            raise BundleError("unsupported_profile", "Expected reviewed target profile")
        target.validate()
        if (
            not isinstance(data, bytes)
            or version != FIRMWARE_COMPAT_VERSION
            or hashlib.sha256(data).hexdigest() != sha256
        ):
            raise BundleError("artifact_invalid", "Artifact identity changed")
        metadata = inspect_artifact_bytes(data, board=target.board, version=version)
        # Reuse the production helper version check, not an arbitrary executable.
        reason = helper_status({"backend": "mg21_gecko_gbl_v1"})
        if reason:
            raise BundleError(reason, "Pinned Gecko helper is unavailable")
        validate_guard(lease_fd)
        stage = "verifying_target"
        progress({"stage": stage})
        check_endpoint(target, observe)
        evidence = probe_loader(target.port, LOADER_BAUD, timeout=3)
        check_loader(target, evidence)
        check_endpoint(target, observe)
        with tempfile.TemporaryDirectory(prefix="livolo-preserved-gecko-") as directory:
            payload = Path(directory) / "application.gbl"
            payload.write_bytes(data)
            os.chmod(payload, 0o600)
            stage = "flash"
            before(stage)
            # Recheck after the parent acknowledgement and immediately before helper open.
            validate_guard(lease_fd)
            check_endpoint(target, observe)
            mutation_possible = True
            flash(payload, target.port, lease_fd)
        stage = "verifying_application"
        progress({"stage": stage})
        check_endpoint(target, observe)
        evidence = verify_application(target.port, APP_BAUD, timeout=3)
        check_endpoint(target, observe)
        check_application(version, evidence, board=target.board)
        return dict(
            operation="provision",
            board=target.board,
            version=version,
            artifact_sha256=metadata["sha256"],
            stage="application_verified",
            application_verified=True,
            network_ready=False,
            setup_required=True,
            image_readback_verified=False,
        )
    except Exception as error:
        code = error.code if isinstance(error, BundleError) else "worker_failed"
        raise GeckoInstallError(code, stage, mutation_possible) from error
