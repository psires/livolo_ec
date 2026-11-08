"""Persist credential-free bootstrap intent before sending any physical operation."""

import asyncio
from copy import deepcopy
import hashlib
import uuid

from homeassistant.helpers.storage import Store

from ._client import IncompatibleError, normalize_url
from .const import DOMAIN


class BootstrapObserver:
    """Share one durable observer across dialogs without owning a radio or retry policy."""

    def __init__(self, hass, url, entry_id=None):
        """Scope local intent to an existing entry or a new connection endpoint.

        @param hass Home Assistant instance owning persistent storage.
        @param url Credential-free normalized native API endpoint.
        @param entry_id Existing HA entry ID, or None before initial configuration.
        """
        self.endpoint = hashlib.sha256(normalize_url(url).encode()).hexdigest()
        self._store = Store(hass, 1, f"{DOMAIN}.bootstrap.{entry_id or self.endpoint}")
        self.lock = asyncio.Lock()
        self.state = None

    async def restore(self):
        """Restore observation only; never reconstruct or submit an operation."""
        if self.state is None:
            saved = await self._store.async_load()
            self.state = saved if isinstance(saved, dict) else {}
        if self.state and self.state.get("endpoint") != self.endpoint:
            raise IncompatibleError(
                "Bootstrap endpoint changed; reconcile existing intent"
            )

    async def bind(self, instance_id, expected_network_id):
        """Reject service or network replacement before adopting observed status.

        @param instance_id Validated persistent physical service identity.
        @param expected_network_id Existing or explicitly selected network identity.
        """
        await self.restore()
        previous = self.state.get("instance_id")
        if previous is not None and previous != instance_id:
            raise IncompatibleError("Bootstrap service instance changed")
        previous_network = self.state.get("expected_network_id")
        if previous_network is not None and expected_network_id != previous_network:
            raise IncompatibleError("Bootstrap network identity changed")
        await self.save(
            instance_id=instance_id, expected_network_id=expected_network_id
        )

    async def save(self, **fields):
        """Commit public observation state without storing credentials or helper paths.

        @param fields Validated public response/intent fields supplied by the flow.
        """
        updated = dict(self.state or {}, endpoint=self.endpoint, **deepcopy(fields))
        await self._store.async_save(updated)
        self.state = updated

    async def intent(self, operation, payload, confirmation_id=None):
        """Durably reserve an explicit operation before its one outbound POST.

        @param operation Physical API operation selected explicitly by the user.
        @param payload Validated operation inputs, excluding credentials.
        @param confirmation_id Local review generation, never sent to the service.
        @return New request body; an unresolved request blocks another intent.
        """
        if self.state.get("pending"):
            raise IncompatibleError("Observe the existing bootstrap request first")
        body = dict(payload, request_id=uuid.uuid4().hex)
        await self.save(
            pending={
                "operation": operation,
                "body": body,
                "confirmation_id": confirmation_id,
            }
        )
        return body


def observer_for(hass, url, entry_id=None):
    """Reuse the same lock and state for simultaneous dialogs on one endpoint/entry.

    @param hass Home Assistant instance.
    @param url Native API URL without embedded credentials.
    @param entry_id Existing entry, or None before network verification.
    """
    key = entry_id or hashlib.sha256(normalize_url(url).encode()).hexdigest()
    observers = hass.data.setdefault(f"{DOMAIN}_bootstrap_observers", {})
    if key not in observers:
        observers[key] = BootstrapObserver(hass, url, entry_id)
    observer = observers[key]
    if observer.endpoint != hashlib.sha256(normalize_url(url).encode()).hexdigest():
        raise IncompatibleError("Bootstrap endpoint changed; reconcile existing intent")
    return observer
