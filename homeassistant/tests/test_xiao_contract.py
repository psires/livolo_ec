"""Exercise proposed opaque XIAO identities, not firmware or migration qualification."""

from copy import deepcopy
from unittest.mock import patch

import pytest
from homeassistant.components.update import UpdateEntityFeature
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import area_registry as ar, device_registry as dr
from homeassistant.helpers import entity_registry as er

from livolo_ec_client import IncompatibleError, preflight_data

from test_firmware_status import diagnostic, notice, withdrawn
from test_update import gateway  # noqa: F401


@pytest.fixture(params=["seeed-xiao-mg24", "seeed-xiao-esp32c6"])
def proposed_board(request):
    """Keep experimental board names in fixtures without admitting production targets.

    @param request Parameterized proposed board identity, pending hardware review.
    """
    return request.param


def identities(hass, entry):
    """Capture logical identities and user customizations for one configured network.

    @param hass Real isolated Home Assistant instance.
    @param entry Existing logical network configuration.
    """
    entities = er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)
    devices = dr.async_get(hass)
    return {
        entity.entity_id: (
            entity.unique_id,
            entity.device_id,
            entity.name,
            entity.area_id,
            devices.async_get(entity.device_id).identifiers,
            devices.async_get(entity.device_id).name_by_user,
            devices.async_get(entity.device_id).area_id,
        )
        for entity in entities
    }


async def test_opaque_board_preserves_control_and_identity_without_updater(
    hass, gateway, proposed_board
):
    """Board metadata cannot replace logical identities or gate qualified switch control.

    @param hass Real isolated HA instance.
    @param gateway Existing shared HA fixture with authoritative snapshot updates.
    @param proposed_board Experimental board name, not proof of working hardware.
    """
    registry = er.async_get(hass)
    output = next(
        e for e in registry.entities.values() if e.unique_id == "2a992b12:a7:46715621:2"
    )
    area = ar.async_get(hass).async_create("Fixture room")
    registry.async_update_entity(output.entity_id, name="Keep output", area_id=area.id)
    dr.async_get(hass).async_update_device(
        output.device_id, name_by_user="Keep switch", area_id=area.id
    )
    hass.config_entries.async_update_entry(gateway.entry, options={"preserve": True})
    original = identities(hass, gateway.entry)
    original_data = dict(gateway.entry.data)
    original_entry_id = gateway.entry.entry_id

    # A fabricated stale candidate deliberately tests a disabled backend with an offer.
    await gateway.publish(
        dict(
            board=proposed_board,
            install_supported=False,
            blocked_reason="unsupported_board",
        )
    )
    assert await hass.config_entries.async_reload(gateway.entry.entry_id)
    await hass.async_block_till_done()
    assert identities(hass, gateway.entry) == original
    assert gateway.entry.entry_id == original_entry_id
    assert gateway.entry.unique_id == "2a992b12:a7"
    assert gateway.entry.data == original_data
    assert gateway.entry.options == {"preserve": True}
    update = hass.states.get(gateway.entity_id)
    assert update.attributes["board"] == proposed_board
    assert not update.attributes["supported_features"] & UpdateEntityFeature.INSTALL
    assert diagnostic(hass).state == "blocked"
    client = gateway.entry.runtime_data.client
    with (
        patch.object(client, "install_firmware") as install,
        patch.object(client, "command") as command,
    ):
        with pytest.raises(HomeAssistantError):
            await hass.services.async_call(
                "update", "install", {"entity_id": gateway.entity_id}, blocking=True
            )
        await hass.services.async_call(
            "switch", "turn_on", {"entity_id": output.entity_id}, blocking=True
        )
        command.assert_awaited_once_with(
            "set", {"device": "46715621", "mask": 2, "value": 2}
        )
        assert hass.states.get(output.entity_id).state == "off"
        gateway.envelope["data"]["devices"][0]["state"]["bits"] = 3
        await gateway.publish({})
        assert hass.states.get(output.entity_id).state == "on"
        install.assert_not_called()
        gateway.preflight.assert_not_called()


async def test_xiao_catalog_withdrawal_is_not_firmware_success(
    hass, gateway, proposed_board
):
    """Removing a synthetic offer leaves unknown firmware state and visible evidence.

    @param hass Real isolated HA instance.
    @param gateway Shared gateway fixture; its offer is synthetic, not qualified firmware.
    @param proposed_board Opaque board identity retained across catalog withdrawal.
    """
    await gateway.publish({"board": proposed_board})
    with patch.object(gateway.entry.runtime_data.client, "install_firmware") as install:
        await gateway.publish(withdrawn("bundle_missing"))
        update = hass.states.get(gateway.entity_id)
        assert update.state == "unknown"
        assert update.attributes["board"] == proposed_board
        assert update.attributes["installed_version"] == "0.12"
        assert update.attributes["latest_version"] is None
        assert not update.attributes["supported_features"] & UpdateEntityFeature.INSTALL
        status = diagnostic(hass)
        assert status.state == "catalog_missing"
        assert status.attributes["status"] is None
        assert notice(hass, gateway.entry).active
        install.assert_not_called()
        gateway.preflight.assert_not_called()


def test_xiao_manual_preflight_retains_identity_without_admission(
    preflight_report, proposed_board
):
    """A known board with no updater remains observable but cannot claim executable status.

    @param preflight_report Existing schema-1 report used by the native client harness.
    @param proposed_board Proposed identity; no production allowlist is modified.
    """
    report = deepcopy(preflight_report)
    report.update(
        board=proposed_board,
        compatible=False,
        executable=False,
        requires_manual_action=True,
        blocked_reason="backend_unavailable",
    )
    helper = next(check for check in report["checks"] if check["id"] == "helper")
    helper.update(status="fail", code="backend_unavailable")
    assert preflight_data(report, "candidate-1") == report
    with pytest.raises(IncompatibleError):
        preflight_data(dict(report, executable=True), "candidate-1")
