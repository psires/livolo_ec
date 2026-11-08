"""Verify daemon-first native HA deletion without orphaning registered switches."""

from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr, entity_registry as er

from custom_components.livolo_ec import async_remove_config_entry_device
from custom_components.livolo_ec._client import CommandFailed, LivoloError

from test_update import gateway  # noqa: F401


def child(hass, gateway):
    """Find the switch device separately from its network gateway.

    @param hass Isolated real HA instance.
    @param gateway Loaded integration fixture.
    """
    output = next(
        e
        for e in er.async_get(hass).entities.values()
        if e.unique_id == "2a992b12:a7:46715621:1"
    )
    return dr.async_get(hass).async_get(output.device_id)


async def test_removal_refreshes_stale_absence_and_verifies_daemon(hass, gateway):
    """A stale HA cache cannot skip removal of a peer re-added in the daemon.

    @param hass Isolated real HA instance.
    @param gateway Snapshot stream representing a registered switch.
    """
    device = child(hass, gateway)
    coordinator = gateway.entry.runtime_data
    stale = deepcopy(gateway.envelope)
    stale["data"]["devices"] = []
    coordinator.accept(stale)
    registered = deepcopy(gateway.envelope)
    registered["sequence"] += 1
    removed = deepcopy(registered)
    removed["sequence"] += 1
    removed["data"]["devices"] = []
    with (
        patch.object(coordinator.client, "snapshot", side_effect=[registered, removed]),
        patch.object(coordinator.client, "command") as command,
    ):
        assert await async_remove_config_entry_device(hass, gateway.entry, device)
        command.assert_awaited_once_with("remove", {"device": "46715621"})
    assert coordinator.data["devices"] == []


async def test_web_forgotten_child_needs_no_second_remove(hass, gateway):
    """Clean up a Web/options removal using a fresh snapshot, not another remove command.

    @param hass Isolated real HA instance.
    @param gateway Existing HA registration whose daemon record was forgotten.
    """
    device = child(hass, gateway)
    gateway.envelope["sequence"] += 1
    gateway.envelope["data"]["devices"] = []
    with patch.object(gateway.entry.runtime_data.client, "command") as command:
        assert await async_remove_config_entry_device(hass, gateway.entry, device)
        command.assert_not_called()


@pytest.mark.parametrize("status", ["rejected", "unknown"])
async def test_unconfirmed_removal_keeps_ha_device(hass, gateway, status):
    """A rejected or uncertain command retains the HA record and is never replayed.

    @param hass Isolated real HA instance.
    @param gateway Registered peer fixture.
    @param status Daemon rejection or unresolved outcome returned by the client.
    """
    device = child(hass, gateway)
    with patch.object(
        gateway.entry.runtime_data.client,
        "command",
        side_effect=CommandFailed({"status": status}),
    ) as command:
        with pytest.raises(HomeAssistantError, match="HA device has been kept"):
            await async_remove_config_entry_device(hass, gateway.entry, device)
        command.assert_awaited_once_with("remove", {"device": "46715621"})
    assert dr.async_get(hass).async_get(device.id) is device
    assert len(hass.states.async_all("switch")) == 2


@pytest.mark.parametrize("failure_at", ["before", "after"])
async def test_snapshot_failure_keeps_ha_device(hass, gateway, failure_at):
    """Both the admission read and post-removal read must succeed before HA deletion.

    @param hass Isolated real HA instance.
    @param gateway Registered peer fixture.
    @param failure_at Whether the daemon read fails before or after command submission.
    """
    device = child(hass, gateway)
    reads = {
        "before": [LivoloError("offline")],
        "after": [deepcopy(gateway.envelope), LivoloError("offline")],
    }
    with (
        patch.object(
            gateway.entry.runtime_data.client, "snapshot", side_effect=reads[failure_at]
        ),
        patch.object(gateway.entry.runtime_data.client, "command") as command,
    ):
        with pytest.raises(HomeAssistantError, match="HA device has been kept"):
            await async_remove_config_entry_device(hass, gateway.entry, device)
        assert command.await_count == {"before": 0, "after": 1}[failure_at]
    assert dr.async_get(hass).async_get(device.id) is device


async def test_still_registered_after_ack_is_not_removed(hass, gateway):
    """An acknowledgement cannot hide a peer present in the latest authoritative snapshot.

    @param hass Isolated real HA instance.
    @param gateway Snapshot retains the target, for example after concurrent re-addition.
    """
    with patch.object(gateway.entry.runtime_data.client, "command") as command:
        with pytest.raises(HomeAssistantError, match="still reports this device"):
            await async_remove_config_entry_device(
                hass, gateway.entry, child(hass, gateway)
            )
        command.assert_awaited_once()


async def test_stale_absent_snapshot_cannot_override_newer_registration(hass, gateway):
    """A delayed absence cannot authorize deletion against a newer registry snapshot.

    @param hass Isolated real HA instance.
    @param gateway Registered peer whose latest snapshot must remain authoritative.
    """
    registered = deepcopy(gateway.envelope)
    registered["sequence"] += 2
    stale = deepcopy(registered)
    stale["sequence"] -= 1
    stale["data"]["devices"] = []
    with (
        patch.object(
            gateway.entry.runtime_data.client,
            "snapshot",
            side_effect=[registered, stale],
        ),
        patch.object(gateway.entry.runtime_data.client, "command") as command,
    ):
        with pytest.raises(HomeAssistantError, match="still reports this device"):
            await async_remove_config_entry_device(
                hass, gateway.entry, child(hass, gateway)
            )
        command.assert_awaited_once()


async def test_cached_absence_cannot_allow_cleanup_while_daemon_is_unreachable(
    hass, gateway
):
    """Offline cleanup cannot trust an old empty registry snapshot.

    @param hass Isolated real HA instance.
    @param gateway HA still retains the child but cached daemon data has no devices.
    """
    device = child(hass, gateway)
    await gateway.publish({})
    gateway.entry.runtime_data.data["devices"] = []
    with (
        patch.object(
            gateway.entry.runtime_data.client,
            "snapshot",
            side_effect=LivoloError("offline"),
        ),
        patch.object(gateway.entry.runtime_data.client, "command") as command,
    ):
        with pytest.raises(HomeAssistantError, match="HA device has been kept"):
            await async_remove_config_entry_device(hass, gateway.entry, device)
        command.assert_not_called()


@pytest.mark.parametrize(
    "identifiers",
    [
        {("livolo_ec", "2a992b12:a7")},
        {("livolo_ec", "other-network:46715621")},
        {("livolo_ec", "2a992b12:a7:invalid")},
        {("unrelated", "2a992b12:a7:46715621")},
        {("livolo_ec", "2a992b12:a7:46715621"), ("livolo_ec", "2a992b12:a7:66780945")},
    ],
)
async def test_gateway_and_unscoped_identities_cannot_remove_peers(
    hass, gateway, identifiers
):
    """Only one exact child identity in this network may reach the daemon.

    @param hass Isolated real HA instance.
    @param gateway Loaded network.
    @param identifiers Gateway, unrelated, malformed or ambiguous device identifiers.
    """
    with (
        patch.object(gateway.entry.runtime_data.client, "snapshot") as snapshot,
        patch.object(gateway.entry.runtime_data.client, "command") as command,
    ):
        assert not await async_remove_config_entry_device(
            hass, gateway.entry, SimpleNamespace(identifiers=identifiers)
        )
        snapshot.assert_not_called()
        command.assert_not_called()


async def test_changed_network_cannot_remove_original_peer(hass, gateway):
    """An endpoint serving a different network cannot authorize a destructive command.

    @param hass Isolated real HA instance.
    @param gateway Network whose configured endpoint has changed identity.
    """
    wrong = deepcopy(gateway.envelope)
    wrong["data"]["tag"] = "b1"
    with (
        patch.object(gateway.entry.runtime_data.client, "snapshot", return_value=wrong),
        patch.object(gateway.entry.runtime_data.client, "command") as command,
    ):
        with pytest.raises(HomeAssistantError, match="HA device has been kept"):
            await async_remove_config_entry_device(
                hass, gateway.entry, child(hass, gateway)
            )
        command.assert_not_called()
