"""Protect manual commissioning boundaries while preserving normal HA management."""

import asyncio
from copy import deepcopy
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.helpers import area_registry as ar, device_registry as dr
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.livolo_ec.config_flow import LivoloOptionsFlow
from custom_components.livolo_ec._client import (
    AuthenticationError,
    CommandFailed,
    IncompatibleError,
    LivoloError,
)

from test_detection import detection  # noqa: F401
from test_update import gateway  # noqa: F401


@pytest.fixture
def configured(hass):
    """Create one hub without Development or any running radio connection.

    @param hass Isolated Home Assistant instance.
    """
    entry = MockConfigEntry(
        domain="livolo_ec",
        unique_id="2a992b12:a7",
        data={"url": "http://example.invalid:8090", "token": "test-secret"},
        options={"preserve": "original"},
    )
    entry.add_to_hass(hass)
    return entry


async def set_development(hass, entry, enabled):
    """Change the option through a separate real HA options flow.

    @param hass Isolated Home Assistant instance.
    @param entry Hub whose preference should change.
    @param enabled Explicit Development value to persist.
    """
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "settings"}
    )
    return await hass.config_entries.options.async_configure(
        result["flow_id"], {"development": enabled}
    )


async def open_manual(hass, entry, step):
    """Traverse the actual Development submenu to a manual action.

    @param hass Isolated Home Assistant instance.
    @param entry Hub with Development explicitly enabled.
    @param step Manual action selected by the user.
    """
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "development"}
    )
    assert set(result["menu_options"]) == {"add", "discover", "pair"}
    return await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": step}
    )


async def test_default_and_per_hub_persistence_preserve_latest_options(
    hass, configured
):
    """Settings default off, remain per hub and merge options changed while open.

    @param hass Isolated Home Assistant instance.
    @param configured Hub without an existing Development preference.
    """
    other = MockConfigEntry(domain="livolo_ec", unique_id="other:a7", data={})
    other.add_to_hass(hass)
    result = await hass.config_entries.options.async_init(configured.entry_id)
    assert set(result["menu_options"]) == {
        "pair_new",
        "refresh",
        "forget",
        "firmware_preflight",
        "settings",
    }
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "settings"}
    )
    assert result["data_schema"]({}) == {"development": False}
    hass.config_entries.async_update_entry(configured, options={"preserve": "new"})
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"development": True}
    )
    assert result["type"] == "create_entry"
    assert configured.options == {"preserve": "new", "development": True}
    result = await hass.config_entries.options.async_init(other.entry_id)
    assert "development" not in result["menu_options"]
    await open_manual(hass, configured, "add")
    await set_development(hass, configured, False)
    result = await hass.config_entries.options.async_init(configured.entry_id)
    assert "development" not in result["menu_options"]
    assert configured.options == {"preserve": "new", "development": False}


@pytest.mark.parametrize(
    "step,payload",
    [
        ("development", None),
        ("add", {"device": "66780945", "name": "Test", "outputs": 1, "suffix": "0001"}),
        ("pair", {"device": "46715621", "seconds": 120}),
        ("discover", {"manual": True}),
        ("detected", {"name": "Test"}),
        ("detected", {"rescan": True}),
    ],
)
async def test_direct_manual_steps_fail_closed(hass, configured, step, payload):
    """Bypassing the displayed menu cannot read discoveries or submit manual commands.

    @param hass Isolated Home Assistant instance.
    @param configured Hub with Development disabled.
    @param step Directly invoked manual flow handler.
    @param payload Submitted action or fallback selection.
    """
    flow = LivoloOptionsFlow()
    flow.hass = hass
    flow.handler = configured.entry_id
    flow._review = {"device": "66780945"}
    with patch("custom_components.livolo_ec.config_flow.Client") as client:
        result = await getattr(flow, "async_step_" + step)(payload)
    assert result["type"] == "abort" and result["reason"] == "development_disabled"
    assert flow._review is None
    client.assert_not_called()


@pytest.mark.parametrize(
    "step,payload",
    [
        ("add", {"device": "66780945", "name": "Test", "outputs": 1, "suffix": "0001"}),
        ("pair", {"device": "46715621", "seconds": 120}),
        ("discover", {"manual": True}),
        ("detected", {"name": "Test"}),
        ("detected", {"rescan": True}),
    ],
)
async def test_open_manual_forms_stop_after_setting_disabled(
    hass, configured, detection, step, payload
):
    """An already open form cannot use a stale Development preference after disabling it.

    @param hass Isolated Home Assistant instance.
    @param configured Hub whose preference changes in another options flow.
    @param detection Daemon-classified profile used only for reviewed discovery.
    @param step Previously opened manual action.
    @param payload Form submission after Development was disabled.
    """
    await set_development(hass, configured, True)
    with patch("custom_components.livolo_ec.config_flow.Client") as client:
        client.return_value.snapshot = AsyncMock(return_value=detection)
        client.return_value.command = AsyncMock()
        result = await open_manual(
            hass, configured, "discover" if step == "detected" else step
        )
        if step == "detected":
            result = await hass.config_entries.options.async_configure(
                result["flow_id"], {"device": "66780945"}
            )
        await set_development(hass, configured, False)
        client.return_value.snapshot.reset_mock()
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], payload
        )
        assert result["type"] == "abort" and result["reason"] == "development_disabled"
        client.return_value.snapshot.assert_not_awaited()
        client.return_value.command.assert_not_awaited()


@pytest.mark.parametrize("reviewed", [False, True])
@pytest.mark.parametrize(
    "failure", [None, AuthenticationError, IncompatibleError, LivoloError]
)
async def test_disable_during_awaited_discovery_blocks_form_and_registration(
    hass, configured, detection, reviewed, failure
):
    """The latest preference wins even when disabled while the daemon lookup is pending.

    @param hass Isolated Home Assistant instance.
    @param configured Hub changed by a concurrent settings flow.
    @param detection Still-valid profile that would otherwise permit registration.
    @param reviewed Whether the suspended read validates a reviewed registration.
    @param failure Optional lookup failure after the setting changes.
    """
    await set_development(hass, configured, True)
    entered, release = asyncio.Event(), asyncio.Event()

    async def delayed_snapshot():
        """Hold the API response until the other settings flow has completed."""
        entered.set()
        await release.wait()
        if failure:
            raise failure("lookup failed")
        return deepcopy(detection)

    with patch("custom_components.livolo_ec.config_flow.Client") as client:
        client.return_value.snapshot = AsyncMock(return_value=detection)
        client.return_value.command = AsyncMock()
        result = await open_manual(hass, configured, "discover")
        if reviewed:
            result = await hass.config_entries.options.async_configure(
                result["flow_id"], {"device": "66780945"}
            )
        client.return_value.snapshot.side_effect = delayed_snapshot
        task = asyncio.create_task(
            hass.config_entries.options.async_configure(
                result["flow_id"], {"name": "Test"} if reviewed else {}
            )
        )
        try:
            await asyncio.wait_for(entered.wait(), 1)
            await set_development(hass, configured, False)
            release.set()
            result = await asyncio.wait_for(task, 1)
        finally:
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        assert result["type"] == "abort" and result["reason"] == "development_disabled"
        client.return_value.command.assert_not_awaited()


@pytest.mark.parametrize("step", ["add", "pair", "detected"])
@pytest.mark.parametrize("failure", ["rejected", "unknown", "transport"])
async def test_disable_during_failed_manual_command_closes_form_without_replay(
    hass, configured, detection, step, failure
):
    """A failed in-flight manual action cannot reopen its disabled UI or trigger a retry.

    @param hass Isolated Home Assistant instance.
    @param configured Hub changed by a concurrent settings flow.
    @param detection Valid reviewed profile for automatic classification within Development.
    @param step Manual registration, reviewed registration or targeted pairing already submitted.
    @param failure Command outcome error or API observation failure.
    """
    await set_development(hass, configured, True)
    entered, release = asyncio.Event(), asyncio.Event()
    payload = (
        {"device": "66780945", "name": "Test", "outputs": 1, "suffix": "0001"}
        if step == "add"
        else {"device": "46715621", "seconds": 120}
    )
    if step == "detected":
        payload = {"name": "Test"}
    action = "add" if step == "detected" else step
    expected = (
        {
            "device": "66780945",
            "name": "Test",
            "auto_detect": True,
            "profile_token": "review-one",
        }
        if step == "detected"
        else payload
    )

    async def delayed_command(operation, data):
        """Report a failed observation only after the other settings flow disables the UI.

        @param operation Already submitted manual operation.
        @param data Original operation arguments, never replayed.
        """
        assert operation == action and data == expected
        entered.set()
        await release.wait()
        if failure != "transport":
            raise CommandFailed({"status": failure})
        raise LivoloError("observation lost")

    with patch("custom_components.livolo_ec.config_flow.Client") as client:
        client.return_value.command = AsyncMock(side_effect=delayed_command)
        client.return_value.snapshot = AsyncMock(return_value=detection)
        result = await open_manual(
            hass, configured, "discover" if step == "detected" else step
        )
        if step == "detected":
            result = await hass.config_entries.options.async_configure(
                result["flow_id"], {"device": "66780945"}
            )
        task = asyncio.create_task(
            hass.config_entries.options.async_configure(result["flow_id"], payload)
        )
        try:
            await asyncio.wait_for(entered.wait(), 1)
            await set_development(hass, configured, False)
            release.set()
            result = await asyncio.wait_for(task, 1)
        finally:
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        assert (
            result["type"] == "abort"
            and result["reason"] == "development_disabled_after_command"
        )
        assert result["description_placeholders"] == {
            "outcome": "unknown" if failure == "transport" else failure
        }
        client.return_value.command.assert_awaited_once_with(action, expected)
        assert configured.options == {"preserve": "original", "development": False}


@pytest.mark.parametrize("step", ["add", "pair"])
async def test_enabled_manual_actions_submit_to_selected_hub(hass, configured, step):
    """Explicit Development opt-in retains working manual registration and pairing.

    @param hass Isolated Home Assistant instance.
    @param configured Selected hub with preserved connection settings.
    @param step Manual command to execute once.
    """
    payload = (
        {"device": "66780945", "name": "Test", "outputs": 1, "suffix": "0001"}
        if step == "add"
        else {"device": "46715621", "seconds": 120}
    )
    await set_development(hass, configured, True)
    with patch("custom_components.livolo_ec.config_flow.Client") as client:
        client.return_value.command = AsyncMock()
        result = await open_manual(hass, configured, step)
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], payload
        )
        assert result["type"] == "create_entry"
        client.return_value.command.assert_awaited_once_with(step, payload)
        assert client.call_args.args[1:] == (
            configured.data["url"],
            configured.data["token"],
        )
        assert configured.options == {"preserve": "original", "development": True}


@pytest.mark.parametrize("step", ["refresh", "forget", "firmware_preflight"])
async def test_ordinary_management_available_without_development(
    hass, configured, envelope, preflight_report, step
):
    """Normal management keeps its behavior without enabling manual commissioning.

    @param hass Isolated Home Assistant instance.
    @param configured Hub with Development absent and therefore disabled.
    @param envelope Authoritative native snapshot.
    @param preflight_report Non-mutating firmware advisory report.
    @param step Ordinary management action under test.
    """
    envelope["data"]["firmware_update"] = {"preflight_supported": True}
    with patch("custom_components.livolo_ec.config_flow.Client") as client:
        client.return_value.command = AsyncMock()
        client.return_value.snapshot = AsyncMock(return_value=envelope)
        client.return_value.firmware_preflight = AsyncMock(
            return_value=preflight_report
        )
        result = await hass.config_entries.options.async_init(configured.entry_id)
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"next_step_id": step}
        )
        payload = {} if step == "firmware_preflight" else {"device": "46715621"}
        if step == "forget":
            payload["confirm"] = True
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], payload
        )
        assert result["type"] == "create_entry"
        if step == "firmware_preflight":
            client.return_value.command.assert_not_awaited()
            client.return_value.firmware_preflight.assert_awaited_once()
        else:
            client.return_value.command.assert_awaited_once_with(
                "remove" if step == "forget" else "refresh", {"device": "46715621"}
            )
        assert configured.options == {"preserve": "original"}


async def test_setting_changes_preserve_loaded_network_and_registry(hass, gateway):
    """Toggling Development preserves loaded entities, device metadata and network identity.

    @param hass Isolated Home Assistant instance.
    @param gateway Loaded integration with real device and entity registries.
    """
    entry = gateway.entry
    area = ar.async_get(hass).async_create("Keep room")
    entity = next(
        iter(er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id))
    )
    er.async_get(hass).async_update_entity(
        entity.entity_id, name="Keep output", area_id=area.id
    )
    dr.async_get(hass).async_update_device(
        entity.device_id, name_by_user="Keep device", area_id=area.id
    )
    original_data, unique_id = dict(entry.data), entry.unique_id
    devices = list(
        dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id)
    )
    entities = dict(er.async_get(hass).entities)
    coordinator = entry.runtime_data
    await set_development(hass, entry, True)
    await set_development(hass, entry, False)
    assert entry.data == original_data and entry.unique_id == unique_id
    assert entry.runtime_data is coordinator
    assert (
        list(dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id))
        == devices
    )
    assert dict(er.async_get(hass).entities) == entities
