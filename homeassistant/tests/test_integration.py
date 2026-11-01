"""Actual HA setup, entities, discovery, reconfiguration and shutdown behavior."""

import asyncio
import copy
from unittest.mock import AsyncMock, patch

from homeassistant.config_entries import SOURCE_USER
from homeassistant.const import STATE_OFF, STATE_ON, STATE_UNAVAILABLE
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry
import pytest

from custom_components.livolo_ec._client import AuthenticationError, IncompatibleError
from custom_components.livolo_ec.coordinator import LivoloCoordinator
from custom_components.livolo_ec.switch import LivoloSwitch


def entry_data():
    """Return credential settings used only by simulated test clients."""
    return {
        "url": "http://example.invalid:8090",
        "token": "secret",
        "allow_simulated": False,
    }


def entry():
    """Construct a uniquely identified network config entry."""
    return MockConfigEntry(
        domain="livolo_ec", title="Livolo", unique_id="2a992b12:a7", data=entry_data()
    )


async def test_setup_push_dynamic_and_unload(hass, envelope):
    """HA creates channels, receives reports/additions and closes its stream on unload.

    @param hass Real HA test instance.
    @param envelope Valid snapshot fixture.
    """
    queue = asyncio.Queue()
    closed = asyncio.Event()

    async def events(client):
        """Feed a deterministic real async event stream.

        @param client Patched native client instance.
        """
        try:
            while True:
                yield await queue.get()
        finally:
            closed.set()

    config_entry = entry()
    config_entry.add_to_hass(hass)
    with (
        patch(
            "custom_components.livolo_ec._client.Client.info",
            return_value={"api_version": 1, "gateway": "2a992b12", "tag": "a7"},
        ),
        patch(
            "custom_components.livolo_ec._client.Client.snapshot", return_value=envelope
        ),
        patch("custom_components.livolo_ec._client.Client.events", events),
        patch(
            "custom_components.livolo_ec._client.Client.command", new_callable=AsyncMock
        ) as command,
    ):
        assert await hass.config_entries.async_setup(config_entry.entry_id)
        await queue.put(envelope)
        await hass.async_block_till_done()
        states = hass.states.async_all("switch")
        assert len(states) == 2
        first = next(s for s in states if s.name.endswith("Output 1"))
        second = next(s for s in states if s.name.endswith("Output 2"))
        assert first.state == STATE_ON and second.state == STATE_OFF
        await hass.services.async_call(
            "switch", "turn_on", {"entity_id": second.entity_id}, blocking=True
        )
        command.assert_awaited_once_with(
            "set", {"device": "46715621", "mask": 2, "value": 2}
        )
        assert hass.states.get(second.entity_id).state == STATE_OFF
        update = copy.deepcopy(envelope)
        update["sequence"] = 2
        update["data"]["devices"][0]["state"]["bits"] = 3
        update["data"]["devices"].append(
            {
                "id": "66780945",
                "name": "Hall",
                "outputs": 1,
                "availability": "online",
                "state": None,
            }
        )
        await queue.put(update)
        await hass.async_block_till_done()
        assert len(hass.states.async_all("switch")) == 3
        assert hass.states.get(second.entity_id).state == STATE_ON
        kitchen = copy.deepcopy(update["data"]["devices"][0])
        update = copy.deepcopy(update)
        update["sequence"] = 3
        update["data"]["devices"] = [update["data"]["devices"][1]]
        await queue.put(update)
        await hass.async_block_till_done()
        registry = er.async_get(hass)
        registry.async_remove(first.entity_id)
        registry.async_remove(second.entity_id)
        await hass.async_block_till_done()
        assert len(hass.states.async_all("switch")) == 1
        update = copy.deepcopy(update)
        update["sequence"] = 4
        update["data"]["devices"].append(kitchen)
        await queue.put(update)
        await hass.async_block_till_done()
        assert len(hass.states.async_all("switch")) == 3
        update = copy.deepcopy(update)
        update["sequence"] = 5
        update["data"]["ready"] = False
        await queue.put(update)
        await hass.async_block_till_done()
        assert all(
            s.state == STATE_UNAVAILABLE for s in hass.states.async_all("switch")
        )
        assert await hass.config_entries.async_unload(config_entry.entry_id)
        assert closed.is_set()


async def test_flow_validates_unique_network(hass, envelope):
    """The UI creates one entry per logical network and prevents duplicates.

    @param hass Real HA test instance.
    @param envelope Valid snapshot fixture.
    """
    with (
        patch(
            "custom_components.livolo_ec.config_flow.Client.info",
            return_value={"api_version": 1, "gateway": "2a992b12", "tag": "a7"},
        ),
        patch(
            "custom_components.livolo_ec.config_flow.Client.snapshot",
            return_value=envelope,
        ),
        patch("custom_components.livolo_ec.async_setup_entry", return_value=True),
    ):
        result = await hass.config_entries.flow.async_init(
            "livolo_ec", context={"source": SOURCE_USER}, data=entry_data()
        )
        assert result["type"] == "create_entry"
        assert result["result"].unique_id == "2a992b12:a7"
        duplicate = await hass.config_entries.flow.async_init(
            "livolo_ec", context={"source": SOURCE_USER}, data=entry_data()
        )
        assert (
            duplicate["type"] == "abort" and duplicate["reason"] == "already_configured"
        )


async def test_reload_does_not_rediscover_unloading_entities(hass, envelope, caplog):
    """Reload retains channels without registering replacements during teardown.

    @param hass Real HA test instance.
    @param envelope Valid snapshot fixture.
    @param caplog Captured runtime logs, including duplicate entity registration.
    """

    async def events(client):
        """Keep a connected push stream alive until the integration closes it.

        @param client Patched native client instance.
        """
        yield envelope
        await asyncio.Future()

    config_entry = entry()
    config_entry.add_to_hass(hass)
    with (
        patch(
            "custom_components.livolo_ec._client.Client.info",
            return_value={"api_version": 1, "gateway": "2a992b12", "tag": "a7"},
        ),
        patch(
            "custom_components.livolo_ec._client.Client.snapshot", return_value=envelope
        ),
        patch("custom_components.livolo_ec._client.Client.events", events),
    ):
        assert await hass.config_entries.async_setup(config_entry.entry_id)
        await hass.async_block_till_done()
        original_ids = {s.entity_id for s in hass.states.async_all("switch")}
        assert len(original_ids) == 2
        assert await hass.config_entries.async_reload(config_entry.entry_id)
        await hass.async_block_till_done()
        assert {s.entity_id for s in hass.states.async_all("switch")} == original_ids
        assert "does not generate unique IDs" not in caplog.text
        assert await hass.config_entries.async_unload(config_entry.entry_id)
        await hass.async_block_till_done()
        assert all(
            state.state == STATE_UNAVAILABLE and state.attributes.get("restored")
            for state in hass.states.async_all("switch")
        )
        assert "does not generate unique IDs" not in caplog.text


async def test_flow_auth_and_simulation(hass, envelope):
    """Reject bad credentials and accidental simulator connections in the UI.

    @param hass Real HA test instance.
    @param envelope Valid snapshot fixture.
    """
    with patch(
        "custom_components.livolo_ec.config_flow.Client.info",
        side_effect=AuthenticationError(),
    ):
        result = await hass.config_entries.flow.async_init(
            "livolo_ec", context={"source": SOURCE_USER}, data=entry_data()
        )
        assert result["errors"]["base"] == "invalid_auth"
    envelope["data"]["simulated"] = True
    with (
        patch(
            "custom_components.livolo_ec.config_flow.Client.info",
            return_value={"gateway": "2a992b12", "tag": "a7", "simulated": True},
        ),
        patch(
            "custom_components.livolo_ec.config_flow.Client.snapshot",
            return_value=envelope,
        ),
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], entry_data()
        )
        assert result["errors"]["base"] == "simulation_disabled"


async def test_state_identity_and_unavailability(hass, envelope):
    """Unknown state stays unknown, forgotten devices cannot be operated, and IDs persist.

    @param hass Real HA test instance.
    @param envelope Valid snapshot fixture.
    """
    config_entry = entry()
    coordinator = LivoloCoordinator(hass, config_entry, AsyncMock())
    coordinator.accept(envelope)
    coordinator.connected = True
    switch = LivoloSwitch(coordinator, envelope["data"]["devices"][0], 2)
    identity = switch.unique_id
    envelope["data"]["devices"][0]["name"] = "Renamed"
    envelope["data"]["devices"][0]["state"] = None
    assert switch.unique_id == identity and switch.is_on is None
    envelope["data"]["devices"] = []
    assert not switch.available
    with pytest.raises(HomeAssistantError):
        await switch.async_turn_on()
    envelope["data"]["tag"] = "b1"
    with pytest.raises(IncompatibleError):
        coordinator.accept(envelope)


async def test_stale_sequence_and_restart(hass, envelope):
    """Old messages cannot roll back state, but a fresh daemon epoch can reset sequence.

    @param hass Real HA test instance.
    @param envelope Valid snapshot fixture.
    """
    coordinator = LivoloCoordinator(hass, entry(), AsyncMock())
    coordinator.accept(envelope)
    old = copy.deepcopy(envelope)
    old["sequence"] = 0
    old["data"]["ready"] = False
    coordinator.accept(old)
    assert coordinator.data["ready"]
    old["epoch"] = "new-process"
    coordinator.accept(old)
    assert not coordinator.data["ready"]


async def test_reconfigure_rejects_different_network(hass, envelope):
    """Editing an endpoint cannot quietly replace the configured logical network.

    @param hass Real HA test instance.
    @param envelope Valid snapshot fixture.
    """
    config_entry = entry()
    config_entry.add_to_hass(hass)
    envelope["data"]["tag"] = "b1"
    with (
        patch(
            "custom_components.livolo_ec.config_flow.Client.info",
            return_value={"gateway": "2a992b12", "tag": "b1"},
        ),
        patch(
            "custom_components.livolo_ec.config_flow.Client.snapshot",
            return_value=envelope,
        ),
    ):
        result = await hass.config_entries.flow.async_init(
            "livolo_ec",
            context={"source": "reconfigure", "entry_id": config_entry.entry_id},
            data=entry_data(),
        )
        assert result["type"] == "abort" and result["reason"] == "wrong_network"


async def test_options_targeted_pairing_and_forget_confirmation(hass):
    """Pairing uses a bounded target and forgetting requires an explicit checkbox.

    @param hass Real HA test instance.
    """
    config_entry = entry()
    config_entry.add_to_hass(hass)
    with patch(
        "custom_components.livolo_ec.config_flow.Client.command", new_callable=AsyncMock
    ) as command:
        result = await hass.config_entries.options.async_init(config_entry.entry_id)
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"next_step_id": "pair"}
        )
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"device": "46715621", "seconds": 120}
        )
        assert result["type"] == "create_entry"
        command.assert_awaited_once_with("pair", {"device": "46715621", "seconds": 120})
        command.reset_mock()
        result = await hass.config_entries.options.async_init(config_entry.entry_id)
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"next_step_id": "forget"}
        )
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"device": "46715621", "confirm": False}
        )
        assert result["errors"]["base"] == "confirm_required"
        command.assert_not_awaited()


async def test_diagnostics_omit_secrets_and_history(hass, envelope):
    """Diagnostics cannot export a stored token, names, raw frames or command payloads.

    @param hass Real HA test instance.
    @param envelope Valid snapshot fixture.
    """
    from custom_components.livolo_ec.diagnostics import (
        async_get_config_entry_diagnostics,
    )

    config_entry = entry()
    coordinator = LivoloCoordinator(hass, config_entry, AsyncMock())
    coordinator.accept(envelope)
    config_entry.runtime_data = coordinator
    envelope["data"]["diagnostics"]["raw"] = "secret-wire-payload"
    envelope["data"]["diagnostics"]["firmware"] = {
        "running_version": "0.12",
        "target_version": "0.12",
        "update_supported": True,
        "trial": False,
        "raw": "secret-firmware-payload",
    }
    value = await async_get_config_entry_diagnostics(hass, config_entry)
    assert "secret" not in str(value) and "Kitchen" not in str(value)
    assert value["device_count"] == 1
    assert value["firmware"] == {
        "running_version": "0.12",
        "target_version": "0.12",
        "update_supported": True,
        "trial": False,
    }
