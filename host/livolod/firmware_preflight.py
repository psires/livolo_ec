"""Shared advisory firmware checks without radio commands or persistent writes."""

import re

from . import __version__
from .firmware_backends import backend_status
from .firmware_bundle import BOARDS, BundleError, load_candidate, select_candidate
from .version import parse_version


def candidate_identity(value):
    """Validate an optional opaque candidate identifier without accepting paths.

    @param value Candidate ID or None to select by identified board.
    @return Unchanged valid value; raises ValueError for malformed input.
    """
    if value is not None and (
        not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,256}", value)
    ):
        raise ValueError("Invalid candidate_id")
    return value


def build_report(
    config,
    candidate_id=None,
    *,
    board=None,
    caps=None,
    version_info=None,
    update_info=None,
    live=False,
    simulated=False,
    blocked=None,
    identity_source="unknown",
    candidate=None,
):
    """Validate artifact, runtime helper and available live identity evidence.

    @param config Local validated configuration, never supplied by an API client.
    @param candidate_id Optional hash-bound catalog selection.
    @param board Exact expected board, or the currently identified board.
    @param caps Fresh session capabilities, absent for offline inspection.
    @param version_info VERSION fields from that same session or explicit probe.
    @param update_info TI UPDATE_STATUS fields from the same session or probe.
    @param live Whether identity evidence belongs to a currently owned live target.
    @param simulated Whether this is an isolated simulator, which cannot flash.
    @param blocked Optional lifecycle/ownership blocking reason.
    @param identity_source Live session, explicit probe, explicit operator, or unknown.
    @param candidate Optional already revalidated private candidate for install admission.
    @return Sanitized schema-1 advisory report, never an authorization to flash.
    """
    candidate_identity(candidate_id)
    checks = []

    def check(ident, passed, code):
        """Append one stable public result without exception details.

        @param ident Stable check identifier.
        @param passed True, False or None for unavailable evidence.
        @param code Stable outcome code for failed or unknown checks.
        """
        checks.append(
            dict(
                id=ident,
                status="unknown" if passed is None else "pass" if passed else "fail",
                code="compatible" if passed else code,
            )
        )

    caps, version_info, update_info = caps or {}, version_info or {}, update_info or {}
    installed_version = None
    if (
        live
        and board in BOARDS
        and caps.get("board") == version_info.get("board") == board
        and caps.get("fw_version") == version_info.get("fw_version")
    ):
        try:
            parse_version(caps.get("fw_version"))
            installed_version = caps["fw_version"]
        except (TypeError, ValueError):
            pass
    result = dict(
        schema=1,
        operation="update",
        candidate_id=None,
        board=board if board in BOARDS else None,
        installed_version=installed_version,
        target_version=None,
        compatible=None,
        executable=False,
        identity_source=identity_source,
        checks=checks,
        blocked_reason=None,
        requires_manual_action=False,
        rollback_supported=False,
    )
    try:
        if candidate is None:
            if candidate_id is None:
                candidate = select_candidate(
                    config.firmware_bundle_dir,
                    board=board,
                    host_version=__version__,
                    app_baudrate=config.serial_baudrate,
                )
            else:
                candidate = load_candidate(
                    config.firmware_bundle_dir,
                    candidate_id,
                    host_version=__version__,
                    app_baudrate=config.serial_baudrate,
                )
        check("artifact", True, "artifact_invalid")
    except BundleError as error:
        check("artifact", False, error.code)
        result.update(
            compatible=False,
            blocked_reason=error.code,
            requires_manual_action=error.code
            in ("backend_unavailable", "unsupported_board"),
        )
        return result
    result.update(
        candidate_id=candidate["id"],
        board=candidate["board"],
        target_version=candidate["version"],
        rollback_supported=candidate["rollback_supported"],
    )
    check(
        "expected_board",
        board == candidate["board"] if board else None,
        "board_identity_unknown" if not board else "board_mismatch",
    )
    helper = backend_status(candidate, config.firmware_nordic)
    check("helper", not helper, helper)
    check(
        "live_identity",
        live and not simulated,
        "simulation" if simulated else "live_identity_required",
    )
    if live:
        check(
            "board",
            caps.get("board") == version_info.get("board") == candidate["board"],
            "board_identity_changed",
        )
        check(
            "protocol",
            caps.get("proto") == version_info.get("proto") == "3",
            "incompatible_protocol",
        )
        check(
            "baudrate",
            caps.get("baud") == str(config.serial_baudrate),
            "incompatible_baudrate",
        )
        try:
            installed = caps.get("fw_version")
            newer = parse_version(candidate["version"]) > parse_version(installed)
            same = installed == version_info.get("fw_version")
            result["installed_version"] = installed if same else None
            check(
                "version",
                newer and same,
                "version_not_newer" if same else "board_identity_changed",
            )
        except (ValueError, TypeError):
            check("version", False, "board_identity_unknown")
        if candidate["backend"] == "ti_uart_signed_v1":
            check(
                "bootloader",
                version_info.get("boot_version") == "0.2"
                and version_info.get("update") == "uart_signed_v1",
                "bootloader_install_required",
            )
            check(
                "signing_key",
                update_info.get("key") == candidate.get("public_key")
                and bool(candidate.get("public_key")),
                "signing_key_mismatch",
            )
            check(
                "previous_update",
                update_info.get("trial") == "0"
                and update_info.get("state") in ("ffffffff", "fffffff0", "ffffffc8"),
                "recovery_required",
            )
    if blocked:
        check("lifecycle", False, blocked)
    compatibility = [
        c for c in checks if c["id"] not in ("helper", "lifecycle", "live_identity")
    ]
    result["compatible"] = (
        False
        if any(c["status"] == "fail" for c in compatibility)
        else (True if live else None)
    )
    reason = next((c["code"] for c in checks if c["status"] != "pass"), None)
    result.update(
        executable=reason is None,
        blocked_reason=reason,
        requires_manual_action=reason
        in ("backend_unavailable", "bootloader_install_required", "recovery_required"),
    )
    return result
