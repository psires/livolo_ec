"""Durable HA bootstrap intent survives dialog loss without retrying physical work."""

from unittest.mock import AsyncMock, patch

import pytest

from custom_components.livolo_ec._client import IncompatibleError
from custom_components.livolo_ec.bootstrap_observer import (
    BootstrapObserver,
    observer_for,
)


async def test_intent_is_saved_before_it_can_be_submitted(hass):
    """Persistence failure prevents a caller from receiving a submit-ready request.

    @param hass Actual HA instance with isolated storage.
    """
    observer = BootstrapObserver(hass, "http://example.invalid:8090")
    await observer.bind("instance-1", "2a992b12:a7")
    with patch.object(observer._store, "async_save", AsyncMock(side_effect=OSError)):
        with pytest.raises(OSError):
            await observer.intent("install", {"plan_id": "plan-1"})
    assert observer.state.get("pending") is None


async def test_restore_retains_one_unresolved_request(hass):
    """Closing a dialog retains its exact request and blocks another physical operation.

    @param hass Actual HA instance with isolated persistent storage.
    """
    observer = BootstrapObserver(hass, "http://example.invalid:8090")
    await observer.bind("instance-1", "2a992b12:a7")
    request = await observer.intent("prepare", {"target_id": "radio-1"})
    restored = BootstrapObserver(hass, "http://example.invalid:8090/")
    await restored.restore()
    assert restored.state["pending"]["body"] == request
    with pytest.raises(IncompatibleError, match="existing bootstrap request"):
        await restored.intent("prepare", {"target_id": "radio-1"})


async def test_replacement_service_network_or_endpoint_cannot_adopt_intent(hass):
    """Saved observer identity cannot be silently reassigned after configuration changes.

    @param hass Actual HA instance.
    """
    observer = observer_for(hass, "http://example.invalid:8090", "entry-1")
    await observer.bind("instance-1", "2a992b12:a7")
    assert observer_for(hass, "http://example.invalid:8090/", "entry-1") is observer
    with pytest.raises(IncompatibleError, match="instance changed"):
        await observer.bind("instance-2", "2a992b12:a7")
    with pytest.raises(IncompatibleError, match="network identity changed"):
        await observer.bind("instance-1", "2a992b13:a7")
    with pytest.raises(IncompatibleError, match="endpoint changed"):
        observer_for(hass, "http://replacement.invalid:8090", "entry-1")
    restored = BootstrapObserver(hass, "http://replacement.invalid:8090", "entry-1")
    with pytest.raises(IncompatibleError, match="endpoint changed"):
        await restored.restore()
