"""Small allowlisted diagnostics; never export credentials or command history."""

from ._client import version_data


async def async_get_config_entry_diagnostics(hass, entry):
    """Return operational health without wire identities, names or raw payloads.

    @param hass Home Assistant instance.
    @param entry Loaded network config entry.
    @return A credential-free diagnostic mapping.
    """
    coordinator = entry.runtime_data
    data = coordinator.data
    firmware = data.get("diagnostics", {}).get("firmware", {})
    return {
        "connected": coordinator.connected,
        "ready": bool(data.get("ready")),
        "controls_enabled": bool(data.get("controls_enabled")),
        "simulated": bool(data.get("simulated")),
        "device_count": len(data.get("devices", [])),
        "versions": version_data(data.get("diagnostics")),
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
