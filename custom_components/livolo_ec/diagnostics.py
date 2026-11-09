"""Small allowlisted diagnostics; never export credentials or command history."""

import re

from ._client import IncompatibleError, commissioning_data, version_data
from .commissioning import PAIRING_REASONS

RECEIVE_COUNTERS = (
    "invalid_rx",
    "rx_gaps",
    "ack_failures",
)
RECEIVE_REJECTIONS = (
    "envelope",
    "unsupported_frame",
    "unsupported_family",
    "checksum",
    "other",
)


def receive_diagnostics(value):
    """Export only reported numeric counters without inferring radio health or missing zeros.

    @param value Optional daemon diagnostic mapping from the accepted native snapshot.
    @return Nonnegative integer counters and fixed rejection breakdown; absent or malformed fields are omitted.
    """
    source = value if isinstance(value, dict) else {}
    result = {
        key: source[key]
        for key in RECEIVE_COUNTERS
        if type(source.get(key)) is int and source[key] >= 0
    }
    rejected = source.get("rx_rejections")
    if isinstance(rejected, dict):
        result["rx_rejections"] = {
            key: rejected[key]
            for key in RECEIVE_REJECTIONS
            if type(rejected.get(key)) is int and rejected[key] >= 0
        }
    return result


def pairing_diagnostics(value):
    """Retain bounded pairing payload evidence without device identities or command history.

    @param value Optional current commissioning summary from the native snapshot.
    @return Sanitized session outcome and anonymous observations, or None for older daemons.
    """
    try:
        summary = commissioning_data(value)
    except IncompatibleError:
        return {"status": "invalid"}
    if summary is None:
        return None
    observations = []
    for item in summary["observations"]:
        suffix = item.get("suffix")
        safe = {
            "outputs": item["outputs"],
            "suffix": (
                suffix
                if isinstance(suffix, str) and re.fullmatch(r"[0-9a-f]{4}", suffix)
                else None
            ),
            "reason": item["reason"] if item["reason"] in PAIRING_REASONS else None,
        }
        for key in ("pairing_evidence", "pairing_evidence_truncated"):
            if key in item:
                safe[key] = item[key]
        observations.append(safe)
    return {
        "status": summary["status"],
        "registered": summary["registered"],
        "error": summary["error"] if summary["error"] in PAIRING_REASONS else None,
        "observations": observations,
    }


async def async_get_config_entry_diagnostics(hass, entry):
    """Return operational health and bounded pairing evidence without identities or credentials.

    @param hass Home Assistant instance.
    @param entry Loaded network config entry.
    @return A credential-free diagnostic mapping.
    """
    coordinator = entry.runtime_data
    data = coordinator.data
    diagnostics = data.get("diagnostics")
    diagnostics = diagnostics if isinstance(diagnostics, dict) else {}
    firmware = diagnostics.get("firmware")
    firmware = firmware if isinstance(firmware, dict) else {}
    return {
        "connected": coordinator.connected,
        "ready": bool(data.get("ready")),
        "controls_enabled": bool(data.get("controls_enabled")),
        "simulated": bool(data.get("simulated")),
        "device_count": len(data.get("devices", [])),
        "versions": version_data(data.get("diagnostics")),
        "receive_counters": receive_diagnostics(diagnostics),
        "commissioning": pairing_diagnostics(data.get("commissioning")),
        "firmware": {
            key: firmware.get(key)
            for key in (
                "running_version",
                "target_version",
                "update_supported",
                "trial",
            )
        },
    }
