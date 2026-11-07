"""Explicit gateway commissioning controls backed by a bounded daemon session."""

from homeassistant.components.button import ButtonEntity
from homeassistant.const import EntityCategory
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN

PARALLEL_UPDATES = 1


async def async_setup_entry(hass, entry, async_add_entities):
    """Register start and cancel controls on the existing logical gateway.

    @param hass Actual HA instance.
    @param entry Config entry with the shared commissioning observer.
    @param async_add_entities Button platform callback.
    """
    async_add_entities(
        [
            LivoloPairingButton(entry.runtime_data, cancel=cancel)
            for cancel in (False, True)
        ]
    )


class LivoloPairingButton(CoordinatorEntity, ButtonEntity):
    """Submit one deliberate start or cancellation, never an automatic retry."""

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.CONFIG
    _attr_should_poll = False

    def __init__(self, coordinator, *, cancel):
        """Assign stable button identity without creating another gateway.

        @param coordinator Existing network push coordinator.
        @param cancel Whether this control closes an active session.
        """
        super().__init__(coordinator)
        self._cancel = cancel
        action = "cancel_pairing" if cancel else "pair_new_device"
        self._attr_translation_key = action
        self._attr_icon = "mdi:stop-circle-outline" if cancel else "mdi:plus-network"
        self._attr_unique_id = f"{coordinator.entry.unique_id}:{action}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, coordinator.entry.unique_id)}
        )

    @property
    def available(self):
        """Keep cancellation available whenever the API can close an active session."""
        manager = self.coordinator.commissioning
        return manager.can_cancel if self._cancel else manager.can_start

    async def async_press(self):
        """Start a two-minute session or close the current one through the native API."""
        await self.coordinator.commissioning.act(cancel=self._cancel)
