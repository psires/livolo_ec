"""Native Home Assistant entities backed by a local Livolo EC daemon."""

from homeassistant.const import Platform
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers import device_registry as dr

from ._client import AuthenticationError, Client, LivoloError
from .const import CONF_TOKEN, CONF_URL, DOMAIN
from .coordinator import LivoloCoordinator

PLATFORMS = [Platform.SWITCH, Platform.UPDATE]


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
    """Allow registry removal only after a physical device was forgotten by the daemon.

    @param hass Home Assistant instance.
    @param entry Network config entry.
    @param device_entry HA device proposed for deletion.
    @return True for a forgotten child; False for the gateway or active peers.
    """
    if not hasattr(entry, "runtime_data"):
        return False
    active = {(DOMAIN, entry.unique_id)}
    active.update(
        (DOMAIN, f"{entry.unique_id}:{device['id']}")
        for device in entry.runtime_data.data["devices"]
    )
    return not bool(device_entry.identifiers & active)
