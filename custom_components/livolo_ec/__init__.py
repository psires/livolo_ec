"""Native Home Assistant entities backed by a local Livolo EC daemon."""

import re

from homeassistant.const import Platform
from homeassistant.exceptions import (
    ConfigEntryAuthFailed,
    ConfigEntryNotReady,
    HomeAssistantError,
)
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.storage import Store

from ._client import AuthenticationError, Client, LivoloError
from .const import CONF_TOKEN, CONF_URL, DOMAIN
from .coordinator import LivoloCoordinator
from .firmware_notice import issue_id

PLATFORMS = [Platform.SWITCH, Platform.UPDATE, Platform.SENSOR, Platform.BUTTON]


async def async_setup_entry(hass, entry):
    """Validate the API, register the gateway and load native entity platforms.

    @param hass Home Assistant instance.
    @param entry Persisted network/API configuration.
    @return True after successful setup.
    """
    client = Client(
        async_get_clientsession(hass), entry.data[CONF_URL], entry.data[CONF_TOKEN]
    )
    coordinator = LivoloCoordinator(hass, entry, client)
    await coordinator.commissioning.restore()
    try:
        await client.info()
        coordinator.accept(await client.snapshot())
    except AuthenticationError as error:
        raise ConfigEntryAuthFailed("Livolo API credential rejected") from error
    except LivoloError as error:
        raise ConfigEntryNotReady(
            "Livolo native API is unavailable or incompatible"
        ) from error
    entry.runtime_data = coordinator
    gateway_device = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, entry.unique_id)},
        manufacturer="Livolo EC project",
        model="EC gateway",
        name="Livolo EC gateway",
    )
    coordinator.gateway_device_id = gateway_device.id
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    coordinator.start()
    return True


async def async_unload_entry(hass, entry):
    """Release entities and API subscriptions, without stopping radio service.

    @param hass Home Assistant instance.
    @param entry Loaded config entry.
    @return Whether all platforms unloaded successfully.
    """
    # Closing the stream notifies listeners. Do that while the existing channels
    # are still registered, before removal releases their discovery slots.
    coordinator = entry.runtime_data
    await coordinator.close()
    if await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        return True
    coordinator.start()
    return False


async def async_remove_config_entry_device(hass, entry, device_entry):
    """Forget the selected daemon peer before allowing HA to remove its registry entry.

    @param hass Home Assistant instance.
    @param entry Network config entry.
    @param device_entry HA device proposed for deletion.
    @return True for a verified absent child; False for unsupported device identities.
    """
    coordinator = getattr(entry, "runtime_data", None)
    if coordinator is None or not entry.unique_id:
        return False
    identifiers = [
        identifier
        for domain, identifier in device_entry.identifiers
        if domain == DOMAIN
    ]
    if len(identifiers) != 1:
        return False
    match = re.fullmatch(re.escape(entry.unique_id) + r":([0-9a-f]{8})", identifiers[0])
    if match is None:
        return False
    device_id = match[1]
    try:
        coordinator.accept(await coordinator.client.snapshot())
        if any(device["id"] == device_id for device in coordinator.data["devices"]):
            await coordinator.client.command("remove", {"device": device_id})
            coordinator.accept(await coordinator.client.snapshot())
    except LivoloError as error:
        raise HomeAssistantError(
            "Could not verify removal from the Livolo daemon. "
            "The HA device has been kept; check the daemon and retry."
        ) from error
    if any(device["id"] == device_id for device in coordinator.data["devices"]):
        raise HomeAssistantError(
            "The Livolo daemon still reports this device as registered. "
            "The HA device has been kept."
        )
    return True


async def async_remove_entry(hass, entry):
    """Remove this entry's persistent notice only when the configuration is deleted.

    @param hass Home Assistant instance owning the issue registry.
    @param entry Permanently removed network configuration.
    """
    ir.async_delete_issue(hass, DOMAIN, issue_id(entry))
    await Store(hass, 1, f"{DOMAIN}.commissioning.{entry.entry_id}").async_remove()
