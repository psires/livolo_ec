"""Push-only network state and reconnect lifecycle."""

import asyncio
from contextlib import suppress
import logging

from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from ._client import AuthenticationError, IncompatibleError, LivoloError, network_id
from .const import CONF_ALLOW_SIMULATED, DOMAIN
from .firmware_notice import FirmwareOfferNotice
from .commissioning import LivoloCommissioning

LOGGER = logging.getLogger(__name__)


class LivoloCoordinator(DataUpdateCoordinator):
    """Keep a single shared API connection for all network entities."""

    def __init__(self, hass, entry, client):
        """Bind a client without giving the integration ownership of serial.

        @param hass Home Assistant instance.
        @param entry Config entry for the logical EC network.
        @param client Async native API client.
        """
        super().__init__(hass, LOGGER, name=DOMAIN, config_entry=entry)
        self.entry = entry
        self.client = client
        self.connected = False
        self.gateway_device_id = None
        self._task = None
        self._epoch = None
        self._sequence = -1
        self.firmware_notice = FirmwareOfferNotice(hass, entry)
        self.commissioning = LivoloCommissioning(self)

    def accept(self, envelope):
        """Apply an authoritative snapshot, rejecting identity changes and stale data.

        @param envelope Validated full API snapshot.
        """
        data = envelope["data"]
        if network_id(data) != self.entry.unique_id:
            raise IncompatibleError("Endpoint now serves a different network")
        if data.get("simulated") and not self.entry.data.get(
            CONF_ALLOW_SIMULATED, False
        ):
            raise IncompatibleError("Simulation was not enabled for this entry")
        epoch, sequence = envelope["epoch"], envelope["sequence"]
        if epoch == self._epoch and sequence < self._sequence:
            return
        self._epoch, self._sequence = epoch, sequence
        self.firmware_notice.observe(data.get("firmware_update"))
        self.commissioning.observe(data)
        self.async_set_updated_data(data)

    def start(self):
        """Start a reconnecting stream after entity platforms have subscribed."""
        self._task = self.entry.async_create_background_task(
            self.hass, self._listen(), "Livolo EC event stream"
        )

    async def _listen(self):
        """Reconnect with bounded backoff and mark stale transport unavailable."""
        delay = 1
        while True:
            try:
                async for envelope in self.client.events():
                    self.connected = True
                    self.accept(envelope)
                    delay = 1
            except AuthenticationError:
                self.entry.async_start_reauth(self.hass)
                return
            except IncompatibleError as error:
                LOGGER.error("Livolo endpoint is incompatible: %s", error)
                return
            except LivoloError:
                LOGGER.debug("Livolo API disconnected; reconnecting")
            finally:
                self.connected = False
                self.async_update_listeners()
            await asyncio.sleep(delay)
            delay = min(30, delay * 2)

    async def close(self):
        """Cancel the event connection while leaving the daemon running."""
        if self._task:
            self._task.cancel()
            with suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        await self.commissioning.close()
