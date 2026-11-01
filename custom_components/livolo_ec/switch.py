"""Reported-state switch entities, with one selected-bit command per output."""

from functools import partial

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from ._client import CommandFailed, LivoloError
from .const import DOMAIN

PARALLEL_UPDATES = 0


async def async_setup_entry(hass, entry, async_add_entities):
    """Discover channels dynamically from authoritative daemon snapshots.

    @param hass Home Assistant instance.
    @param entry Network config entry.
    @param async_add_entities HA entity registration callback.
    """
    coordinator = entry.runtime_data
    known = set()

    @callback
    def channel_removed(key):
        """Allow a forgotten/deleted channel to be rediscovered later.

        @param key Stable device ID and output tuple removed by HA.
        """
        known.discard(key)

    @callback
    def add_channels():
        """Register new channels once while preserving existing HA identities."""
        entities = []
        for device in coordinator.data["devices"]:
            for output in range(1, device["outputs"] + 1):
                key = (device["id"], output)
                if key not in known:
                    known.add(key)
                    entities.append(
                        LivoloSwitch(
                            coordinator, device, output, partial(channel_removed, key)
                        )
                    )
        async_add_entities(entities)

    entry.async_on_unload(coordinator.async_add_listener(add_channels))
    add_channels()


class LivoloSwitch(CoordinatorEntity, SwitchEntity):
    """Expose validated radio reports without optimistic state changes."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, coordinator, device, output, on_remove=None):
        """Assign stable logical-network and channel identifiers.

        @param coordinator Shared network push coordinator.
        @param device Initial authoritative device profile.
        @param output One-based output number.
        @param on_remove Optional callback releasing discovery ownership on removal.
        """
        super().__init__(coordinator)
        self.device_id = device["id"]
        self.output = output
        self._release_channel = on_remove
        self._attr_unique_id = (
            f"{coordinator.entry.unique_id}:{self.device_id}:{output}"
        )
        self._attr_name = f"Output {output}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{coordinator.entry.unique_id}:{self.device_id}")},
            via_device_id=coordinator.gateway_device_id,
            manufacturer="Livolo",
            model=f"EC {device['outputs']}-gang switch",
            name=device.get("name", self.device_id),
        )

    async def async_will_remove_from_hass(self):
        """Release the channel discovery slot when HA removes this entity."""
        await super().async_will_remove_from_hass()
        if self._release_channel:
            self._release_channel()

    @property
    def device(self):
        """Return the current profile, or None after it was forgotten."""
        return next(
            (d for d in self.coordinator.data["devices"] if d["id"] == self.device_id),
            None,
        )

    @property
    def available(self):
        """Require API connection, radio readiness and an available configured channel."""
        device = self.device
        return bool(
            self.coordinator.connected
            and self.coordinator.data.get("ready")
            and self.coordinator.data.get("controls_enabled")
            and device
            and self.output <= device["outputs"]
            and device.get("availability") == "online"
        )

    @property
    def is_on(self):
        """Read the validated report; absent state remains unknown."""
        device = self.device
        state = device.get("state") if device else None
        if not isinstance(state, dict):
            return None
        value = state.get("outputs", {}).get(str(self.output))
        if value in ("ON", "OFF"):
            return value == "ON"
        bits = state.get("bits")
        return bool(bits & (1 << (self.output - 1))) if type(bits) is int else None

    async def _set(self, on):
        """Submit an explicit selected-bit command without local state speculation.

        @param on Desired state of this output only.
        """
        if not self.available:
            raise HomeAssistantError("Livolo output is unavailable")
        mask = 1 << (self.output - 1)
        try:
            await self.coordinator.client.command(
                "set",
                {"device": self.device_id, "mask": mask, "value": mask if on else 0},
            )
        except CommandFailed as error:
            raise HomeAssistantError(
                f"Livolo command {error.result.get('request_id')}: {error.result.get('status')}. "
                "The physical state may differ; check its reported state before retrying."
            ) from error
        except LivoloError as error:
            raise HomeAssistantError(
                "Livolo API failed; command outcome may be unknown"
            ) from error

    async def async_turn_on(self, **kwargs):
        """Turn on this output through the daemon's confirmation policy.

        @param kwargs Unused standard HA service parameters.
        """
        await self._set(True)

    async def async_turn_off(self, **kwargs):
        """Turn off this output through the daemon's confirmation policy.

        @param kwargs Unused standard HA service parameters.
        """
        await self._set(False)
