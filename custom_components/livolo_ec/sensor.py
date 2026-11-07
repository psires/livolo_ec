"""Expose firmware eligibility and native pairing progress on the logical gateway."""

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.const import EntityCategory
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from ._client import version_data
from .const import DOMAIN
from .commissioning import PAIRING_STATES

PARALLEL_UPDATES = 0

EXPLANATIONS = {
    "not_reported": "The daemon has not reported enough firmware information. This does not confirm an installation. Switch control is independent of firmware update information.",
    "catalog_missing": "The configured firmware catalog is missing. A disappearing update offer does not confirm installation. Check the reported running version and last job result. Restore or select a compatible catalog, then run Check firmware update (read-only) in the integration's Configure dialog.",
    "catalog_unusable": "The configured firmware catalog cannot be used. A disappearing update offer does not confirm installation. Check the reported running version and last job result. Correct the catalog, then run Check firmware update (read-only) in the integration's Configure dialog.",
    "no_candidate": "The catalog has no compatible firmware available for this radio. An empty bundled catalog can be intentional. A disappearing update offer does not confirm installation. Check the reported running version and last job result. To offer an update, select a compatible catalog and run Check firmware update (read-only) in the integration's Configure dialog.",
    "installing": "A firmware job is in progress. Transfer reaching 100% does not confirm installation; wait for the daemon to verify the running firmware and restored network. Do not submit another installation.",
    "recovery_required": "The daemon reports that firmware recovery is required. Check the job result and recovery guidance before taking further action. A missing update offer does not confirm installation; do not retry automatically.",
    "update_unconfirmed": "The last firmware job has an unknown outcome. Check its request ID and daemon recovery guidance before retrying. This is not a confirmed installation.",
    "update_failed": "The last firmware job was rejected or did not complete. Check its error and the reported running version. A disappearing update offer does not confirm installation.",
    "blocked": "Firmware installation is currently unavailable. Check the blocking reason and run Check firmware update (read-only) in the integration's Configure dialog. This does not confirm an installation.",
    "update_available": "A compatible firmware update is available. Installation requires an explicit action on the gateway's Firmware update entity.",
    "last_update_confirmed": "The daemon confirmed the last firmware job. The reported running version and last job result remain separate from the availability of future updates.",
    "no_update_available": "The daemon does not offer a newer version from the current compatible catalog. This state alone does not confirm that an installation took place.",
}


async def async_setup_entry(hass, entry, async_add_entities):
    """Add enabled diagnostics to the existing gateway without changing its identity.

    @param hass Actual Home Assistant instance.
    @param entry Network configuration with the shared native coordinator.
    @param async_add_entities Sensor platform registration callback.
    """
    async_add_entities(
        [
            LivoloFirmwareStatus(entry.runtime_data),
            LivoloPairingStatus(entry.runtime_data),
            LivoloVersionSensor(entry.runtime_data, "daemon_version"),
            LivoloVersionSensor(entry.runtime_data, "firmware_compat_version"),
        ]
    )


class LivoloVersionSensor(CoordinatorEntity, SensorEntity):
    """Display software and radio compatibility as distinct read-only identities."""

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_should_poll = False

    def __init__(self, coordinator, field):
        """Attach one explicit version field to the existing logical gateway.

        @param coordinator Shared native push coordinator.
        @param field Daemon software or firmware compatibility field to display.
        """
        super().__init__(coordinator)
        self._field = field
        self._attr_translation_key = field
        self._attr_unique_id = f"{coordinator.entry.unique_id}:{field}"
        self._attr_icon = (
            "mdi:application-cog" if field == "daemon_version" else "mdi:chip"
        )
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, coordinator.entry.unique_id)}
        )

    @property
    def available(self):
        """Mark disconnected version metadata unavailable without blocking switch control."""
        return self.coordinator.connected

    @property
    def native_value(self):
        """Leave legacy software identity unknown rather than relabelling its compatibility alias."""
        return version_data(self.coordinator.data.get("diagnostics"))[self._field]

    @property
    def extra_state_attributes(self):
        """Explain the version domain and expose only explicit wire revisions."""
        versions = version_data(self.coordinator.data.get("diagnostics"))
        if self._field == "daemon_version":
            return {
                "api_version": versions["api_version"],
                "uart_protocol": versions["uart_protocol"],
                "explanation": "Daemon software package version. Older daemons may not report it separately. This is not the installed radio firmware version.",
            }
        return {
            "explanation": "Exact radio firmware compatibility target reported by the daemon. This is not an installed version or an available update; see the radio Firmware update entity.",
        }


class LivoloFirmwareStatus(CoordinatorEntity, SensorEntity):
    """Keep a plain explanation visible even when the update entity becomes unknown."""

    _attr_has_entity_name = True
    _attr_translation_key = "firmware_status"
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_should_poll = False
    _attr_icon = "mdi:chip"
    _attr_options = list(EXPLANATIONS)

    def __init__(self, coordinator):
        """Observe the existing logical gateway without initiating any operation.

        @param coordinator Shared authenticated push coordinator.
        """
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.entry.unique_id}:firmware_status"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, coordinator.entry.unique_id)}
        )

    @property
    def firmware(self):
        """Accept optional metadata without excluding older daemons from diagnostics."""
        value = self.coordinator.data.get("firmware_update")
        return value if isinstance(value, dict) else {}

    @property
    def available(self):
        """Require a live API stream, while retaining visibility during radio maintenance."""
        return self.coordinator.connected

    @property
    def native_value(self):
        """Prioritize real job outcomes and describe absent candidates without inferring success."""
        firmware = self.firmware
        if not firmware:
            return "not_reported"
        if firmware.get("recovery"):
            return "recovery_required"
        if firmware.get("in_progress") is True or firmware.get("status") == "pending":
            return "installing"
        if firmware.get("status") == "unknown":
            return "update_unconfirmed"
        reason = firmware.get("blocked_reason")
        if reason == "bundle_missing":
            return "catalog_missing"
        if reason in (
            "bundle_invalid",
            "artifact_unqualified",
            "incompatible_host",
            "artifact_invalid",
            "signing_key_mismatch",
        ):
            return "catalog_unusable"
        if reason == "artifact_missing":
            return "no_candidate"
        if firmware.get("status") == "rejected":
            return "update_failed"
        if reason:
            return "blocked"
        if firmware.get("candidate_id") and firmware.get("update_available") is True:
            return "update_available"
        if firmware.get("status") == "confirmed":
            return "last_update_confirmed"
        if (
            firmware.get("candidate_id")
            and firmware.get("installed_version")
            and firmware.get("latest_version")
            and firmware.get("update_available") is False
        ):
            return "no_update_available"
        return "not_reported"

    @property
    def extra_state_attributes(self):
        """Keep the explanation and authoritative version/job facts separate and bounded."""
        attributes = {"explanation": EXPLANATIONS[self.native_value]}
        for key in (
            "installed_version",
            "latest_version",
            "blocked_reason",
            "stage",
            "request_id",
            "status",
            "error",
            "recovery",
        ):
            value = self.firmware.get(key)
            attributes[key] = value[:512] if isinstance(value, str) else None
        return attributes


class LivoloPairingStatus(CoordinatorEntity, SensorEntity):
    """Keep native commissioning progress and outcomes visible across UI entry points."""

    _attr_has_entity_name = True
    _attr_translation_key = "pairing_status"
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_should_poll = False
    _attr_icon = "mdi:access-point-network"
    _attr_options = list(PAIRING_STATES)

    def __init__(self, coordinator):
        """Use a stable status identity on the existing gateway device.

        @param coordinator Shared native push coordinator.
        """
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.entry.unique_id}:pairing_status"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, coordinator.entry.unique_id)}
        )

    @property
    def available(self):
        """Show radio interruptions through the live API, but never hide API disconnects."""
        return self.coordinator.connected

    @property
    def native_value(self):
        """Use only authoritative session status or explicit legacy capability absence."""
        return self.coordinator.commissioning.status

    @property
    def extra_state_attributes(self):
        """Describe detection, registration and physical confirmation separately."""
        return self.coordinator.commissioning.attributes
