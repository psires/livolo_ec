"""Standard HA firmware updates delegated to durable daemon maintenance jobs."""

import math
import uuid

from homeassistant.components.update import (
    UpdateDeviceClass,
    UpdateEntity,
    UpdateEntityFeature,
)
from homeassistant.const import EntityCategory
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from ._client import CommandFailed, LivoloError
from .const import DOMAIN

PARALLEL_UPDATES = 0


async def async_setup_entry(hass, entry, async_add_entities):
    """Attach one firmware entity to the existing logical gateway device.

    @param hass Home Assistant instance.
    @param entry Config entry containing the shared push coordinator.
    @param async_add_entities HA entity registration callback.
    """
    async_add_entities([LivoloFirmwareUpdate(entry.runtime_data)])


class LivoloFirmwareUpdate(CoordinatorEntity, UpdateEntity):
    """Observe gateway updates without owning the radio, images or recovery policy."""

    _attr_has_entity_name = True
    _attr_name = None
    _attr_device_class = UpdateDeviceClass.FIRMWARE
    _attr_entity_category = EntityCategory.CONFIG
    _attr_should_poll = False
    _attr_title = "Livolo EC radio firmware"

    def __init__(self, coordinator):
        """Retain the gateway identity across adapter replacement and HA reloads.

        @param coordinator Shared authenticated network state coordinator.
        """
        super().__init__(coordinator)
        self._installing = False
        self._attr_unique_id = f"{coordinator.entry.unique_id}:firmware"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, coordinator.entry.unique_id)}
        )

    @property
    def firmware(self):
        """Return optional update metadata; old daemons remain usable for switches."""
        value = self.coordinator.data.get("firmware_update")
        return value if isinstance(value, dict) else {}

    @property
    def available(self):
        """Keep maintenance observable without requiring a running radio application."""
        return self.coordinator.connected and bool(self.firmware)

    @property
    def installed_version(self):
        """Display the daemon's verified running version, never the requested target."""
        value = self.firmware.get("installed_version")
        return value if isinstance(value, str) and value else None

    @property
    def latest_version(self):
        """Offer only the version of the daemon's currently validated candidate."""
        value = self.firmware.get("latest_version")
        return value if isinstance(value, str) and value else None

    @property
    def supported_features(self):
        """Advertise only implemented job observation and candidate installation."""
        features = UpdateEntityFeature(0)
        if type(self.firmware.get("in_progress")) is bool:
            features |= UpdateEntityFeature.PROGRESS
        if (
            self.firmware.get("install_supported") is True
            and not self.firmware.get("blocked_reason")
            and self.firmware.get("candidate_id")
            and self.latest_version
        ):
            features |= UpdateEntityFeature.INSTALL
        if isinstance(self.firmware.get("release_notes"), str):
            features |= UpdateEntityFeature.RELEASE_NOTES
        return features

    @property
    def in_progress(self):
        """Track local submission and authoritative jobs started through any client."""
        return self._installing or self.firmware.get("in_progress") is True

    @property
    def update_percentage(self):
        """Expose measured overall progress only, with no stage-based estimates."""
        value = self.firmware.get("progress_percent")
        if type(value) in (int, float) and math.isfinite(value) and 0 <= value <= 100:
            return value
        return None

    @property
    def release_summary(self):
        """Show a bounded summary of the candidate's release notes."""
        value = self.firmware.get("release_notes")
        return value[:255] if isinstance(value, str) else None

    @property
    def release_url(self):
        """Expose an optional public release link advertised by the daemon."""
        value = self.firmware.get("release_url")
        return (
            value
            if isinstance(value, str) and value.startswith(("https://", "http://"))
            else None
        )

    @property
    def extra_state_attributes(self):
        """Keep safe job status and recovery guidance visible when installation is blocked."""
        fields = (
            "board",
            "blocked_reason",
            "stage",
            "request_id",
            "status",
            "error",
            "recovery",
            "rollback_supported",
        )
        value = {
            key: self.firmware[key]
            for key in fields
            if key in self.firmware
            and isinstance(self.firmware[key], (str, bool, type(None)))
        }
        if not self.firmware:
            value["blocked_reason"] = "Daemon does not advertise firmware updates"
        return value

    async def async_release_notes(self):
        """Return supplied candidate notes without downloading artifacts in HA Core."""
        value = self.firmware.get("release_notes")
        return value if isinstance(value, str) else None

    async def async_install(self, version, backup, **kwargs):
        """Await one daemon-owned job through verified success or a durable failure.

        @param version Must be None; arbitrary version selection is not supported.
        @param backup Must be False; no user-selectable backup capability is advertised.
        @param kwargs Unused standard Home Assistant service options.
        """
        if version is not None or backup:
            raise HomeAssistantError(
                "Version selection and optional backups are unsupported"
            )
        if self.in_progress:
            raise HomeAssistantError("A Livolo firmware update is already in progress")
        if not self.available or not (
            self.supported_features & UpdateEntityFeature.INSTALL
        ):
            raise HomeAssistantError(
                self.firmware.get("blocked_reason")
                or "Livolo firmware install is unavailable"
            )
        candidate = self.firmware["candidate_id"]
        request_id = str(uuid.uuid4())
        self._installing = True
        self.async_write_ha_state()
        try:
            await self.coordinator.client.install_firmware(
                candidate, request_id=request_id
            )
            # A durable confirmation proves completion. Refresh displayed versions
            # immediately when possible; the push stream remains authoritative.
            try:
                self.coordinator.accept(await self.coordinator.client.snapshot())
            except LivoloError:
                pass
        except CommandFailed as error:
            result = error.result
            raise HomeAssistantError(
                f"Livolo firmware job {request_id}: {result.get('status')}. "
                f"{result.get('error') or 'Inspect the job status and recovery guidance before retrying.'}"
            ) from error
        except (LivoloError, ValueError) as error:
            raise HomeAssistantError(
                f"Livolo firmware job {request_id} could not be observed. "
                "It may still be running; inspect its status before retrying."
            ) from error
        finally:
            self._installing = False
            self.async_write_ha_state()
