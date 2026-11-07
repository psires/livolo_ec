"""Version identities remain explicit across daemon upgrades and legacy API consumers."""

from copy import deepcopy
from unittest.mock import patch

from aiohttp import ClientSession, web
from homeassistant.helpers import entity_registry as er
import pytest

from livolo_ec_client import Client, version_data
from custom_components.livolo_ec.diagnostics import async_get_config_entry_diagnostics

from test_update import gateway  # noqa: F401

pytestmark = pytest.mark.usefixtures("socket_enabled")

VERSIONS = {
    "daemon_version": "0.14.0",
    "firmware_compat_version": "0.13",
    "host_version": "0.13",
    "api_version": 1,
    "uart_protocol": 3,
}


def version_sensor(hass, key):
    """Locate a distinct version diagnostic without relying on entity insertion order.

    @param hass Actual HA instance.
    @param key Translation key identifying the diagnostic.
    """
    registry = er.async_get(hass)
    return next(
        state
        for state in hass.states.async_all("sensor")
        if registry.async_get(state.entity_id).unique_id.endswith(":" + key)
    )


@pytest.mark.parametrize("modern", [False, True])
async def test_new_client_reads_legacy_and_explicit_info(aiohttp_server, modern):
    """Optional new fields do not alter the API 1 identity or legacy compatibility alias.

    @param aiohttp_server Offline native HTTP fixture factory.
    @param modern Whether the daemon reports explicit software/compatibility identities.
    """
    payload = {
        "gateway": "2a992b12",
        "tag": "a7",
        "api_version": 1,
        "uart_protocol": 3,
        "host_version": "0.13",
    }
    if modern:
        payload.update(VERSIONS)

    async def info(request):
        """Serve the unchanged API 1 identity with optional additive fields.

        @param request Authenticated read-only native info request.
        """
        assert request.headers["Authorization"] == "Bearer offline-token"
        return web.json_response(payload)

    app = web.Application()
    app.router.add_get("/api/v1/info", info)
    server = await aiohttp_server(app)
    async with ClientSession() as session:
        result = await Client(session, str(server.make_url("")), "offline-token").info()
    assert result["api_version"] == 1
    assert result["host_version"] == "0.13"
    versions = version_data(result)
    assert versions["daemon_version"] == ("0.14.0" if modern else None)
    assert versions["firmware_compat_version"] == ("0.13" if modern else None)
    assert versions["api_version"] == 1 and versions["uart_protocol"] == 3


@pytest.mark.parametrize(
    "fields",
    [
        None,
        {"daemon_version": "0.13", "firmware_compat_version": "0.14.0"},
        {"daemon_version": "secret-token", "firmware_compat_version": "65536.0"},
        {"daemon_version": "0.014.0", "firmware_compat_version": "00.13"},
        {"daemon_version": "9" * 1000, "firmware_compat_version": []},
        {"api_version": True, "uart_protocol": "3", "host_version": "secret"},
    ],
)
def test_untrusted_optional_versions_remain_unknown(fields):
    """Malformed display metadata is omitted without denying switch compatibility.

    @param fields Missing, malformed or non-version diagnostic values.
    """
    assert all(value is None for value in version_data(fields).values())


async def test_software_upgrade_does_not_offer_radio_firmware_or_change_identity(
    hass, gateway
):
    """Independent software changes update diagnostics only, preserving entities and firmware state.

    @param hass Actual Home Assistant instance.
    @param gateway Real loaded update platform and controllable push stream.
    """
    data = gateway.envelope["data"]
    data["diagnostics"] = deepcopy(VERSIONS)
    data["diagnostics"]["firmware"] = {
        "running_version": "0.13",
        "target_version": "0.13",
        "update_supported": True,
        "trial": False,
        "raw": "secret-radio-payload",
    }
    await gateway.publish({"installed_version": "0.13", "update_available": False})
    assert version_sensor(hass, "daemon_version").state == "0.14.0"
    assert version_sensor(hass, "firmware_compat_version").state == "0.13"
    firmware_before = hass.states.get(gateway.entity_id)
    assert firmware_before.state == "off"
    registry = er.async_get(hass)
    identities = {
        item.entity_id: (item.unique_id, item.device_id)
        for item in er.async_entries_for_config_entry(registry, gateway.entry.entry_id)
    }
    with patch.object(gateway.entry.runtime_data.client, "install_firmware") as install:
        data["diagnostics"]["daemon_version"] = "8.4.2"
        await gateway.publish({})
        assert version_sensor(hass, "daemon_version").state == "8.4.2"
        current = hass.states.get(gateway.entity_id)
        assert (
            current.state == "off" and current.attributes == firmware_before.attributes
        )
        assert await hass.config_entries.async_reload(gateway.entry.entry_id)
        await hass.async_block_till_done()
        install.assert_not_called()
    assert identities == {
        item.entity_id: (item.unique_id, item.device_id)
        for item in er.async_entries_for_config_entry(registry, gateway.entry.entry_id)
    }
    diagnostic = await async_get_config_entry_diagnostics(hass, gateway.entry)
    assert diagnostic["versions"] == dict(VERSIONS, daemon_version="8.4.2")
    assert diagnostic["firmware"]["running_version"] == "0.13"
    assert "secret" not in str(diagnostic)
    # Rollback to old daemon metadata does not reinterpret host_version as software.
    data["diagnostics"] = {"host_version": "0.13"}
    await gateway.publish({})
    assert version_sensor(hass, "daemon_version").state == "unknown"
    assert version_sensor(hass, "firmware_compat_version").state == "unknown"
    assert hass.states.get(gateway.entity_id).state == "off"
    assert all(
        state.state != "unavailable" for state in hass.states.async_all("switch")
    )


async def test_missing_radio_candidate_is_not_filled_from_software_version(
    hass, gateway
):
    """Even a newer daemon and explicit target cannot create an absent firmware candidate.

    @param hass Actual HA instance.
    @param gateway Loaded gateway with a controlled catalog snapshot.
    """
    gateway.envelope["data"]["diagnostics"] = dict(VERSIONS, daemon_version="99.0.0")
    await gateway.publish(
        {"latest_version": None, "candidate_id": None, "update_available": False}
    )
    state = hass.states.get(gateway.entity_id)
    assert state.state == "unknown"
    assert state.attributes["installed_version"] == "0.12"
    assert state.attributes["latest_version"] is None
    assert version_sensor(hass, "daemon_version").state == "99.0.0"
    coordinator = gateway.entry.runtime_data
    coordinator.connected = False
    coordinator.async_update_listeners()
    await hass.async_block_till_done()
    assert version_sensor(hass, "daemon_version").state == "unavailable"
    assert version_sensor(hass, "firmware_compat_version").state == "unavailable"
