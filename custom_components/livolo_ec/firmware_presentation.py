"""Present advisory firmware evidence without requesting or authorizing maintenance."""

import math
import re


REASONS = {
    "bundle_missing": "The firmware catalog is missing. Restore a reviewed catalog in the Livolo app, then refresh this report.",
    "artifact_missing": "No compatible image is available in the configured catalog. An empty catalog does not mean the radio is up to date.",
    "bundle_invalid": "The firmware catalog could not be validated. Correct the catalog in the Livolo app, then refresh.",
    "artifact_invalid": "The firmware image failed validation. Use a reviewed catalog; do not bypass this check.",
    "artifact_unqualified": "This image has not been qualified for installation on this board.",
    "incompatible_host": "The image is not compatible with this daemon. Use matching reviewed app and firmware releases.",
    "backend_unavailable": "The Livolo app is missing the required flashing helper. Use an app release that supports this exact board.",
    "unsupported_board": "This exact radio board is not supported by the selected update method.",
    "board_identity_unknown": "The exact radio board could not be verified. Check the selected adapter and its connection; a USB bridge name alone does not identify the board.",
    "board_identity_changed": "Radio identity changed. Check the selected adapter before requesting any installation.",
    "board_mismatch": "The image targets a different radio board. Select a reviewed catalog for this exact board.",
    "live_identity_required": "A running Livolo radio must be identified before an update. This update screen cannot convert a stock adapter.",
    "radio_offline": "The radio is not connected to the daemon. Check its USB connection and the selected adapter in the Livolo app.",
    "radio_busy": "The radio is busy. Wait for pairing or other commands to finish, then refresh.",
    "update_in_progress": "A firmware job is already running. Follow its status and wait; do not start another installation.",
    "recovery_required": "The radio needs recovery. Follow the exact board's reviewed recovery procedure and inspect the existing job before attempting another installation.",
    "bootloader_install_required": "The required Livolo bootloader is not installed. Initial setup needs a separate board-specific procedure; this update cannot install it.",
    "signing_key_mismatch": "The image signing key does not match the radio. Use the matching reviewed release; do not override verification.",
    "version_not_newer": "The catalog does not offer a newer version. No installation is needed from this candidate.",
    "incompatible_protocol": "The radio protocol does not match this update method. Check the supported app and firmware combination.",
    "incompatible_baudrate": "The image and configured radio speed do not match. Check the exact board's supported app settings.",
    "simulation": "This is a simulator. It cannot flash a physical radio.",
}

STAGES = {
    "idle": "No active job",
    "preparing": "Preparing the update",
    "preflight": "Checking compatibility",
    "disarm": "Pausing radio control",
    "begin": "Starting the transfer",
    "bootloader_entry": "Entering the bootloader",
    "flash": "Writing firmware",
    "transfer": "Transferring firmware",
    "transferred": "Transfer complete; verification still required",
    "commit": "Committing the image",
    "committed": "Image committed; verification still required",
    "activate": "Starting the new firmware",
    "restoring": "Verifying the radio and restoring the network",
    "confirmed": "Installation verified",
    "restored_previous": "Previous firmware restored",
    "recovery": "Recovery required",
    "interrupted": "Job interrupted",
    "failed": "Job did not complete",
}


def public_code(value, fallback="Not reported"):
    """Allow only bounded identifiers in Markdown, excluding paths and arbitrary messages.

    @param value Optional public code, version or request identifier.
    @param fallback Text used for absent or unsafe values.
    """
    return (
        value
        if isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9.:-]{1,128}", value)
        else fallback
    )


def guidance(reason, firmware=None):
    """Translate known blockers while leaving unknown outcomes explicitly unsupported.

    @param reason Optional stable daemon preflight blocking code.
    @param firmware Current snapshot; unresolved jobs take precedence over catalog advice.
    """
    if isinstance(firmware, dict):
        if firmware.get("recovery"):
            return REASONS["recovery_required"]
        if firmware.get("status") == "unknown":
            return "The existing job has an unknown outcome. Inspect its status and recovery guidance before requesting another installation."
        if firmware.get("in_progress") is True or firmware.get("status") == "pending":
            return REASONS["update_in_progress"]
    if not reason:
        return "Review the versions and compatibility checks before using the gateway's standard firmware update control. Installation checks compatibility again."
    return REASONS.get(
        reason if isinstance(reason, str) else "",
        "The daemon blocked this update. Inspect the gateway's Radio firmware status and daemon diagnostics before taking further action.",
    )


def job_summary(firmware):
    """Describe only the observed job, never interpreting transfer completion as success.

    @param firmware Optional current firmware snapshot mapping from the daemon.
    """
    if not isinstance(firmware, dict) or not firmware:
        return "No firmware job information was reported."
    status = firmware.get("status")
    if firmware.get("recovery"):
        outcome = "Recovery is required. Do not retry installation automatically."
    elif status == "unknown":
        outcome = "The last job's outcome is unknown. It may have changed the radio; inspect its status before retrying."
    elif firmware.get("in_progress") is True or status == "pending":
        outcome = "Installation is in progress. Keep the adapter connected and wait for verification. Do not start another installation."
    elif status == "confirmed":
        outcome = "The daemon verified the last installation. This result is separate from the currently offered update."
    elif status == "rejected":
        outcome = "The last installation did not complete. Check its result and the reported running version before retrying."
    else:
        outcome = "No confirmed installation result was reported."
    stage_code = firmware.get("stage")
    stage = STAGES.get(
        stage_code if isinstance(stage_code, str) else "",
        "Stage not reported or not recognized",
    )
    lines = [outcome, f"Stage: {stage}."]
    request = public_code(firmware.get("request_id"), "")
    if request:
        lines.append(f"Job: {request}.")
    for field, label in (
        ("progress_percent", "Measured overall progress"),
        ("transfer_percent", "Image transfer"),
    ):
        value = firmware.get(field)
        if type(value) in (int, float) and math.isfinite(value) and 0 <= value <= 100:
            lines.append(f"{label}: {value:g}%.")
    if firmware.get("transfer_percent") is not None:
        lines.append(
            "100% transfer is not confirmation; radio and network verification must finish."
        )
    if firmware.get("error"):
        lines.append(
            "The daemon reported an error. Inspect Radio firmware status on the gateway for job details."
        )
    return "\n\n".join(lines)
