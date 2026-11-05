"""Exercise standard HA firmware services against authoritative daemon job state."""

import asyncio
import copy
from types import SimpleNamespace
from unittest.mock import patch

from homeassistant.components.update import UpdateEntityFeature
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr, entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry
import pytest

from custom_components.livolo_ec._client import CommandFailed, IncompatibleError


@pytest.fixture
async def gateway(hass, envelope, preflight_report):
    """Load a real HA update entity with a controllable firmware snapshot stream.

    @param hass Home Assistant test instance.
    @param envelope Valid native snapshot fixture.
    @param preflight_report Valid advisory compatibility response.
    """
    envelope["data"]["firmware_update"] = {
        "board": "zb-gw04-1v2",
        "installed_version": "0.12",
        "latest_version": "0.13",
        "candidate_id": "candidate-1",
        "update_available": True,
        "install_supported": True,
        "preflight_supported": True,
        "blocked_reason": None,
        "in_progress": False,
        "stage": "idle",
        "progress_percent": None,
        "request_id": None,
        "status": None,
        "error": None,
        "recovery": None,
        "rollback_supported": False,
        "release_notes": "Firmware release notes",
        "release_url": "https://example.invalid/release/0.13",
    }
    queue = asyncio.Queue()

    async def events(client):
        """Reconnect from current authoritative state, then forward changes.

        @param client Native client whose transport is replaced for this test.
        """
        yield copy.deepcopy(envelope)
        while True:
            yield await queue.get()

    async def publish(fields, ready=None):
        """Publish a firmware transition without requiring radio readiness.

        @param fields Firmware fields to merge into the next snapshot.
        @param ready Optional application-radio readiness override.
        """
        envelope["sequence"] += 1
        envelope["data"]["firmware_update"].update(fields)
        if ready is not None:
            envelope["data"]["ready"] = ready
        await queue.put(copy.deepcopy(envelope))
        await hass.async_block_till_done()

    entry = MockConfigEntry(
        domain="livolo_ec",
        title="Livolo",
        unique_id="2a992b12:a7",
        data={"url": "http://example.invalid:8090", "token": "test-secret"},
    )
    entry.add_to_hass(hass)
    with (
        patch("custom_components.livolo_ec._client.Client.info", return_value={}),
        patch(
            "custom_components.livolo_ec._client.Client.snapshot",
            side_effect=lambda: copy.deepcopy(envelope),
        ),
        patch("custom_components.livolo_ec._client.Client.events", events),
        patch(
            "custom_components.livolo_ec._client.Client.firmware_preflight",
            return_value=preflight_report,
        ) as preflight,
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        states = hass.states.async_all("update")
        assert len(states) == 1
        yield SimpleNamespace(
            entry=entry,
            entity_id=states[0].entity_id,
            envelope=envelope,
            publish=publish,
            preflight=preflight,
            report=preflight_report,
        )
        await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()


async def test_update_identity_features_and_missing_candidates(hass, gateway):
    """Gateway update identity is stable and install capability requires a candidate.

    @param hass Home Assistant test instance.
    @param gateway Loaded gateway and controllable firmware state.
    """
    state = hass.states.get(gateway.entity_id)
    assert state.state == "on"
    assert state.attributes["installed_version"] == "0.12"
    assert state.attributes["latest_version"] == "0.13"
    assert state.attributes["release_summary"] == "Firmware release notes"
    features = UpdateEntityFeature(state.attributes["supported_features"])
    assert features == (
        UpdateEntityFeature.INSTALL
        | UpdateEntityFeature.PROGRESS
        | UpdateEntityFeature.RELEASE_NOTES
    )
    registry = er.async_get(hass)
    entity = registry.async_get(gateway.entity_id)
    assert entity.unique_id == "2a992b12:a7:firmware"
    device = dr.async_get(hass).async_get(entity.device_id)
    assert ("livolo_ec", "2a992b12:a7") in device.identifiers
    await gateway.publish(
        {
            "latest_version": None,
            "candidate_id": None,
            "blocked_reason": "No compatible payload",
        }
    )
    state = hass.states.get(gateway.entity_id)
    assert state.state == "unknown"
    assert state.attributes["installed_version"] == "0.12"
    assert state.attributes["blocked_reason"] == "No compatible payload"
    assert not state.attributes["supported_features"] & UpdateEntityFeature.INSTALL


async def test_install_service_waits_for_verified_result(hass, gateway):
    """The standard update action stays pending until the daemon verifies completion.

    @param hass Home Assistant test instance.
    @param gateway Loaded gateway and controllable firmware state.
    """
    started, complete = asyncio.Event(), asyncio.Event()

    async def install(candidate_id, request_id=None):
        """Hold an accepted daemon job until the test supplies final verification.

        @param candidate_id Selected catalog entry, never a local path or URL.
        @param request_id Durable ID created once by the integration.
        """
        assert candidate_id == "candidate-1" and request_id
        started.set()
        await complete.wait()
        gateway.envelope["sequence"] += 1
        gateway.envelope["data"]["firmware_update"].update(
            installed_version="0.13",
            in_progress=False,
            status="confirmed",
            stage="confirmed",
        )
        return {"request_id": request_id, "status": "confirmed"}

    with patch.object(
        gateway.entry.runtime_data.client, "install_firmware", side_effect=install
    ) as submit:
        task = asyncio.create_task(
            hass.services.async_call(
                "update", "install", {"entity_id": gateway.entity_id}, blocking=True
            )
        )
        await started.wait()
        assert not task.done()
        assert hass.states.get(gateway.entity_id).attributes["in_progress"]
        assert (
            hass.states.get(gateway.entity_id).attributes["installed_version"] == "0.12"
        )
        with pytest.raises(HomeAssistantError):
            await hass.services.async_call(
                "update", "install", {"entity_id": gateway.entity_id}, blocking=True
            )
        complete.set()
        await task
        await hass.async_block_till_done()
        submit.assert_awaited_once()
        gateway.preflight.assert_awaited_once_with("candidate-1")
    state = hass.states.get(gateway.entity_id)
    assert state.state == "off"
    assert state.attributes["installed_version"] == "0.13"
    assert not state.attributes["in_progress"]


async def test_external_job_survives_reload_and_radio_unavailability(hass, gateway):
    """Web-started maintenance stays visible through radio downtime and HA reload.

    @param hass Home Assistant test instance.
    @param gateway Loaded gateway and controllable firmware state.
    """
    await gateway.publish(
        {
            "request_id": "web-job-1",
            "status": "pending",
            "in_progress": True,
            "stage": "transfer",
            "progress_percent": None,
        },
        ready=False,
    )
    assert all(
        state.state == "unavailable" for state in hass.states.async_all("switch")
    )
    assert await hass.config_entries.async_reload(gateway.entry.entry_id)
    await hass.async_block_till_done()
    state = hass.states.get(gateway.entity_id)
    assert state.state == "on"
    assert state.attributes["in_progress"]
    assert state.attributes["request_id"] == "web-job-1"
    assert state.attributes["update_percentage"] is None
    assert state.attributes["stage"] == "transfer"
    await gateway.publish({"progress_percent": 41.5})
    assert hass.states.get(gateway.entity_id).attributes["update_percentage"] == 41.5
    await gateway.publish({"progress_percent": 150})
    assert hass.states.get(gateway.entity_id).attributes["update_percentage"] is None


async def test_install_failure_preserves_recovery_guidance(hass, gateway):
    """Uncertain firmware results raise service errors without inventing a running version.

    @param hass Home Assistant test instance.
    @param gateway Loaded gateway and controllable firmware state.
    """
    with patch.object(
        gateway.entry.runtime_data.client,
        "install_firmware",
        side_effect=CommandFailed(
            {"status": "unknown", "error": "Manual recovery required"}
        ),
    ) as submit:
        with pytest.raises(
            HomeAssistantError, match="unknown.*Manual recovery required"
        ):
            await hass.services.async_call(
                "update", "install", {"entity_id": gateway.entity_id}, blocking=True
            )
        submit.assert_awaited_once()
    assert hass.states.get(gateway.entity_id).attributes["installed_version"] == "0.12"
    assert not hass.states.get(gateway.entity_id).attributes["in_progress"]
    await gateway.publish(
        {
            "install_supported": False,
            "blocked_reason": "Recovery required",
            "error": "Manual recovery required",
            "recovery": "Follow the board recovery procedure",
        }
    )
    state = hass.states.get(gateway.entity_id)
    assert state.attributes["recovery"] == "Follow the board recovery procedure"
    assert not state.attributes["supported_features"] & UpdateEntityFeature.INSTALL


async def test_update_rejects_unimplemented_options(hass, gateway):
    """HA rejects arbitrary versions and optional backups before calling the client.

    @param hass Home Assistant test instance.
    @param gateway Loaded gateway and controllable firmware state.
    """
    with patch.object(gateway.entry.runtime_data.client, "install_firmware") as submit:
        for options in ({"version": "0.11"}, {"backup": True}):
            with pytest.raises(HomeAssistantError):
                await hass.services.async_call(
                    "update",
                    "install",
                    dict(options, entity_id=gateway.entity_id),
                    blocking=True,
                )
        submit.assert_not_called()


async def test_old_daemon_keeps_switches_and_unavailable_update(hass, gateway):
    """Missing optional firmware metadata preserves normal switch integration setup.

    @param hass Home Assistant test instance.
    @param gateway Loaded gateway and controllable firmware state.
    """
    del gateway.envelope["data"]["firmware_update"]
    assert await hass.config_entries.async_reload(gateway.entry.entry_id)
    await hass.async_block_till_done()
    assert len(hass.states.async_all("switch")) == 2
    state = hass.states.get(gateway.entity_id)
    assert state.state == "unavailable"
    assert state.attributes["supported_features"] == 0


@pytest.mark.parametrize(
    "change",
    [
        {"executable": False, "blocked_reason": "radio_busy"},
        {
            "compatible": None,
            "executable": False,
            "blocked_reason": "live_identity_required",
        },
        {"candidate_id": "changed"},
        {"board": "other-board"},
        {"target_version": "0.14"},
    ],
)
async def test_preflight_blocks_standard_install(hass, gateway, change):
    """Unusable or changed advisory evidence prevents any installation submission.

    @param hass Actual Home Assistant instance.
    @param gateway Loaded update entity and mutable preflight response.
    @param change Blocking or changed candidate evidence.
    """
    gateway.report.update(change)
    with patch.object(gateway.entry.runtime_data.client, "install_firmware") as submit:
        with pytest.raises(HomeAssistantError, match="preflight"):
            await hass.services.async_call(
                "update", "install", {"entity_id": gateway.entity_id}, blocking=True
            )
        submit.assert_not_called()
    assert not hass.states.get(gateway.entity_id).attributes["in_progress"]


async def test_preflight_transport_failure_is_not_an_uncertain_job(hass, gateway):
    """A failed read reports that no installation was requested and clears busy state.

    @param hass Actual Home Assistant instance.
    @param gateway Loaded entity with a controllable preflight client.
    """
    gateway.preflight.side_effect = IncompatibleError("Old daemon")
    with patch.object(gateway.entry.runtime_data.client, "install_firmware") as submit:
        with pytest.raises(HomeAssistantError, match="No installation was requested"):
            await hass.services.async_call(
                "update", "install", {"entity_id": gateway.entity_id}, blocking=True
            )
        submit.assert_not_called()
    assert not hass.states.get(gateway.entity_id).attributes["in_progress"]


async def test_candidate_push_change_during_preflight_blocks_install(hass, gateway):
    """A fresh snapshot superseding the checked candidate prevents a stale request.

    @param hass Actual Home Assistant instance.
    @param gateway Loaded entity and push stream control.
    """
    started, complete = asyncio.Event(), asyncio.Event()

    async def preflight(candidate_id):
        """Delay a valid report while a newer snapshot arrives.

        @param candidate_id Requested candidate before the push change.
        """
        assert candidate_id == "candidate-1"
        started.set()
        await complete.wait()
        return gateway.report

    gateway.preflight.side_effect = preflight
    with patch.object(gateway.entry.runtime_data.client, "install_firmware") as submit:
        task = asyncio.create_task(
            hass.services.async_call(
                "update", "install", {"entity_id": gateway.entity_id}, blocking=True
            )
        )
        await asyncio.wait_for(started.wait(), 2)
        gateway.envelope["sequence"] += 1
        gateway.envelope["data"]["firmware_update"]["candidate_id"] = "new-candidate"
        gateway.entry.runtime_data.accept(copy.deepcopy(gateway.envelope))
        complete.set()
        with pytest.raises(HomeAssistantError, match="changed during preflight"):
            await task
        submit.assert_not_called()


async def test_old_firmware_api_has_no_install_capability(hass, gateway):
    """An older firmware API keeps observations available but cannot bypass preflight.

    @param hass Actual Home Assistant instance.
    @param gateway Existing update entity and authoritative state stream.
    """
    await gateway.publish({"preflight_supported": False})
    state = hass.states.get(gateway.entity_id)
    assert not state.attributes["supported_features"] & UpdateEntityFeature.INSTALL
    assert "preflight" in state.attributes["blocked_reason"]
    assert len(hass.states.async_all("switch")) == 2
