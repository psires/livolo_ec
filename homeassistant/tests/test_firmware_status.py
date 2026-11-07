"""Make catalog withdrawal visible without claiming a flash or changing existing entities."""

from copy import deepcopy
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.components.update import UpdateEntityFeature
from homeassistant.helpers import entity_registry as er, issue_registry as ir
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.livolo_ec import async_remove_entry
from custom_components.livolo_ec.firmware_notice import FirmwareOfferNotice, issue_id

from test_update import gateway  # noqa: F401


def diagnostic(hass):
    """Find the one independent firmware diagnostic in this isolated network.

    @param hass Actual HA instance.
    """
    states = [
        state
        for state in hass.states.async_all("sensor")
        if state.entity_id.endswith("_radio_firmware_status")
    ]
    assert len(states) == 1
    return states[0]


def notice(hass, entry):
    """Read the entry-scoped withdrawal issue without changing it.

    @param hass Actual HA instance.
    @param entry Network configuration entry.
    """
    return ir.async_get(hass).async_get_issue("livolo_ec", issue_id(entry))


def withdrawn(reason="artifact_missing", **history):
    """Represent catalog withdrawal while retaining authoritative running and job facts.

    @param reason Native missing, invalid, or unsupported catalog reason.
    @param history Optional last-job metadata from the same authoritative snapshot.
    """
    return dict(
        candidate_id=None,
        latest_version=None,
        install_supported=False,
        update_available=False,
        blocked_reason=reason,
        **history,
    )


@pytest.mark.parametrize(
    "reason,state",
    [
        ("bundle_missing", "catalog_missing"),
        ("artifact_missing", "no_candidate"),
        ("bundle_invalid", "catalog_unusable"),
        ("artifact_invalid", "catalog_unusable"),
        ("signing_key_mismatch", "catalog_unusable"),
        ("artifact_unqualified", "catalog_unusable"),
        ("incompatible_host", "catalog_unusable"),
    ],
)
async def test_withdrawal_explains_unknown_without_installing(
    hass, gateway, reason, state
):
    """A withdrawn offer leaves a visible diagnostic and Repair, never an install or success.

    @param hass Actual HA instance.
    @param gateway Existing gateway and native push fixture.
    @param reason Authoritative catalog failure code.
    @param state Expected distinct enum status for the diagnostic.
    """
    sensor = diagnostic(hass)
    assert sensor.state == "update_available"
    registry = er.async_get(hass)
    sensor_entry = registry.async_get(sensor.entity_id)
    assert sensor_entry.unique_id == "2a992b12:a7:firmware_status"
    assert sensor_entry.entity_category.value == "diagnostic"
    assert sensor_entry.disabled_by is None
    assert sensor_entry.device_id == registry.async_get(gateway.entity_id).device_id
    original = {
        item.entity_id: (item.unique_id, item.device_id)
        for item in er.async_entries_for_config_entry(registry, gateway.entry.entry_id)
    }
    with (
        patch.object(gateway.entry.runtime_data.client, "install_firmware") as install,
        patch.object(gateway.entry.runtime_data.client, "command") as command,
    ):
        await gateway.publish(withdrawn(reason))
        current = diagnostic(hass)
        assert current.state == state
        assert "does not confirm installation" in current.attributes["explanation"]
        assert current.attributes["installed_version"] == "0.12"
        assert current.attributes["status"] is None
        update = hass.states.get(gateway.entity_id)
        assert update.state == "unknown"
        assert update.attributes["latest_version"] is None
        assert update.attributes["installed_version"] == "0.12"
        assert not update.attributes["supported_features"] & UpdateEntityFeature.INSTALL
        issue = notice(hass, gateway.entry)
        assert issue.is_persistent and not issue.is_fixable
        assert issue.translation_placeholders["installed_version"] == "0.12"
        assert issue.translation_placeholders["offered_version"] == "0.13"
        install.assert_not_called()
        command.assert_not_called()
        gateway.preflight.assert_not_called()
    assert original == {
        item.entity_id: (item.unique_id, item.device_id)
        for item in er.async_entries_for_config_entry(registry, gateway.entry.entry_id)
    }


async def test_fresh_empty_catalog_has_diagnostic_without_warning(hass, gateway):
    """Fresh empty catalogs are normal and do not invent an observed withdrawal.

    @param hass Actual HA instance.
    @param gateway Existing fixture reloaded without exposing the withdrawal transition.
    """
    gateway.envelope["data"]["firmware_update"].update(withdrawn())
    assert await hass.config_entries.async_reload(gateway.entry.entry_id)
    await hass.async_block_till_done()
    assert diagnostic(hass).state == "no_candidate"
    assert notice(hass, gateway.entry) is None


async def test_notice_survives_disconnect_reload_and_persistent_storage(hass, gateway):
    """A raised warning persists through reload/storage while disconnected diagnostics go unavailable.

    @param hass Actual HA instance.
    @param gateway Loaded gateway and snapshot fixture.
    """
    await gateway.publish(withdrawn())
    issue = notice(hass, gateway.entry)
    stored = ir.async_get(hass)._data_to_save()
    restored = ir.IssueRegistry(hass)
    with patch.object(restored._store, "async_load", AsyncMock(return_value=stored)):
        await restored._async_load()
    saved = restored.async_get_issue("livolo_ec", issue_id(gateway.entry))
    assert saved.active and saved.is_persistent
    assert saved.data == issue.data
    assert saved.translation_placeholders == issue.translation_placeholders
    coordinator = gateway.entry.runtime_data
    coordinator.connected = False
    coordinator.async_update_listeners()
    await hass.async_block_till_done()
    assert diagnostic(hass).state == "unavailable"
    assert notice(hass, gateway.entry) is issue
    assert await hass.config_entries.async_reload(gateway.entry.entry_id)
    await hass.async_block_till_done()
    assert diagnostic(hass).state == "no_candidate"
    assert (
        notice(hass, gateway.entry).translation_placeholders
        == issue.translation_placeholders
    )
    # Restoring catalog availability resolves the warning without any update job.
    await gateway.publish(
        {
            "candidate_id": "replacement",
            "latest_version": "0.13",
            "update_available": True,
            "install_supported": True,
            "blocked_reason": None,
        }
    )
    assert diagnostic(hass).state == "update_available"
    assert notice(hass, gateway.entry) is None
    assert hass.states.get(gateway.entity_id).attributes["installed_version"] == "0.12"


@pytest.mark.parametrize(
    "fields,state",
    [
        ({"in_progress": True, "status": "pending", "stage": "transfer"}, "installing"),
        ({"status": "unknown", "recovery": "recovery_required"}, "recovery_required"),
        ({"status": "unknown", "recovery": None}, "update_unconfirmed"),
    ],
)
async def test_job_status_takes_precedence_without_false_withdrawal(
    hass, gateway, fields, state
):
    """Maintenance and uncertain results supersede a catalog notice without claiming success.

    @param hass Actual HA instance.
    @param gateway Snapshot and entity fixture.
    @param fields Active or uncertain job metadata.
    @param state Expected diagnostic outcome.
    """
    await gateway.publish(withdrawn())
    assert notice(hass, gateway.entry)
    await gateway.publish(dict(fields, request_id="job-1"), ready=False)
    assert diagnostic(hass).state == state
    assert diagnostic(hass).attributes["request_id"] == "job-1"
    assert notice(hass, gateway.entry) is None
    assert hass.states.get(gateway.entity_id).attributes["installed_version"] == "0.12"
    assert await hass.config_entries.async_reload(gateway.entry.entry_id)
    await hass.async_block_till_done()
    assert diagnostic(hass).state == state
    assert notice(hass, gateway.entry) is None


@pytest.mark.parametrize("status", ["confirmed", "rejected"])
async def test_historical_job_does_not_hide_current_catalog_loss(hass, gateway, status):
    """Past success or rejection remains history while a newer offered candidate disappears.

    @param hass Actual HA instance.
    @param gateway Existing native gateway fixture.
    @param status Terminal result that predates the currently offered candidate.
    """
    await gateway.publish(
        {"status": status, "request_id": "old-job", "stage": "finished"}
    )
    await gateway.publish(withdrawn())
    assert diagnostic(hass).state == "no_candidate"
    assert diagnostic(hass).attributes["status"] == status
    assert diagnostic(hass).attributes["request_id"] == "old-job"
    assert notice(hass, gateway.entry)
    assert await hass.config_entries.async_reload(gateway.entry.entry_id)
    await hass.async_block_till_done()
    assert notice(hass, gateway.entry)


@pytest.mark.parametrize("notice_first", [False, True])
async def test_confirmed_job_does_not_become_a_withdrawal_warning(
    hass, gateway, notice_first
):
    """A new durable confirmed result can explain offer disappearance without a false Repair.

    @param hass Actual HA instance.
    @param gateway Existing native gateway fixture.
    @param notice_first Whether a previous catalog-loss notice already exists.
    """
    if notice_first:
        await gateway.publish(withdrawn())
        assert notice(hass, gateway.entry)
    await gateway.publish(
        withdrawn(status="confirmed", request_id="new-job", installed_version="0.13")
    )
    assert diagnostic(hass).state == "no_candidate"
    assert diagnostic(hass).attributes["status"] == "confirmed"
    assert diagnostic(hass).attributes["installed_version"] == "0.13"
    assert notice(hass, gateway.entry) is None
    await gateway.publish(
        {
            "candidate_id": "candidate-1",
            "latest_version": "0.13",
            "blocked_reason": None,
            "update_available": False,
        }
    )
    assert diagnostic(hass).state == "last_update_confirmed"


@pytest.mark.parametrize(
    "fields,state",
    [
        ({"blocked_reason": "helper_missing"}, "blocked"),
        ({"latest_version": "0.12", "update_available": False}, "no_update_available"),
        (
            {
                "status": "rejected",
                "request_id": "failed-job",
                "error": "Transfer rejected",
            },
            "update_failed",
        ),
    ],
)
async def test_non_successful_statuses_remain_explicit(hass, gateway, fields, state):
    """A blocked offer, equal version or rejected job cannot become a success statement.

    @param hass Actual HA instance.
    @param gateway Existing firmware diagnostic fixture.
    @param fields Current availability or last-job facts.
    @param state Expected honest diagnostic status.
    """
    await gateway.publish(fields)
    assert diagnostic(hass).state == state
    assert diagnostic(hass).attributes["installed_version"] == "0.12"
    assert notice(hass, gateway.entry) is None


async def test_legacy_unknown_and_entry_scoped_cleanup(hass, gateway):
    """Missing legacy metadata is honest, and removing another entry cannot erase this notice.

    @param hass Actual HA instance.
    @param gateway Existing gateway and current snapshot.
    """
    await gateway.publish(withdrawn())
    other = MockConfigEntry(
        domain="livolo_ec", title="Other", unique_id="another:network"
    )
    await async_remove_entry(hass, other)
    assert notice(hass, gateway.entry)
    envelope = deepcopy(gateway.envelope)
    envelope["sequence"] += 1
    envelope["data"].pop("firmware_update")
    gateway.entry.runtime_data.accept(envelope)
    await hass.async_block_till_done()
    assert diagnostic(hass).state == "not_reported"
    assert notice(hass, gateway.entry)
    await async_remove_entry(hass, gateway.entry)
    assert notice(hass, gateway.entry) is None
    # An independent fresh legacy entry never receives a missing-catalog warning.
    legacy = FirmwareOfferNotice(hass, other)
    legacy.observe(None)
    assert notice(hass, other) is None
