"""Read-only firmware inspection and admission checks never authorize a transfer."""

from copy import deepcopy
from unittest.mock import AsyncMock, patch

from aiohttp import ClientSession, web
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from livolo_ec_client import (
    AuthenticationError,
    Client,
    IncompatibleError,
    preflight_data,
)

pytestmark = pytest.mark.usefixtures("socket_enabled")


@pytest.mark.parametrize("selected", [None, "candidate-1"])
async def test_preflight_is_authenticated_get(
    aiohttp_server, preflight_report, selected
):
    """Dry-run uses only the read route and strips unapproved response fields.

    @param aiohttp_server Isolated HTTP server factory.
    @param preflight_report Valid report fixture.
    @param selected Optional explicit candidate selection.
    """
    calls = []

    async def respond(request):
        """Inspect the exact request and return public evidence plus a private sentinel.

        @param request Incoming preflight HTTP request.
        """
        calls.append((request.method, request.path))
        assert request.headers["Authorization"] == "Bearer secret"
        assert dict(request.query) == (
            {} if selected is None else {"candidate_id": selected}
        )
        assert not await request.read()
        return web.json_response(dict(preflight_report, path="/private/payload"))

    app = web.Application()
    app.router.add_route("*", "/api/v1/firmware/preflight", respond)
    server = await aiohttp_server(app)
    async with ClientSession() as session:
        report = await Client(
            session, str(server.make_url("")), "secret"
        ).firmware_preflight(selected)
    assert report == preflight_report
    assert calls == [("GET", "/api/v1/firmware/preflight")]


@pytest.mark.parametrize(
    "status,error", [(401, AuthenticationError), (404, IncompatibleError)]
)
async def test_preflight_auth_and_old_daemon(aiohttp_server, status, error):
    """An inaccessible read endpoint cannot fall back to an installation request.

    @param aiohttp_server Isolated HTTP server factory.
    @param status HTTP error supplied by the endpoint.
    @param error Expected typed client error.
    """
    calls = []

    async def reject(request):
        """Record all traffic before returning the requested failure.

        @param request Incoming HTTP request.
        """
        calls.append(request.method)
        return web.Response(status=status)

    app = web.Application()
    app.router.add_route("*", "/{tail:.*}", reject)
    server = await aiohttp_server(app)
    async with ClientSession() as session:
        with pytest.raises(error):
            await Client(
                session, str(server.make_url("")), "secret"
            ).firmware_preflight("candidate-1")
    assert calls == ["GET"]


@pytest.mark.parametrize(
    "change",
    [
        {"schema": True},
        {"identity_source": "unknown"},
        {"identity_source": "operator"},
        {"schema": 2},
        {"compatible": "true"},
        {"executable": 1},
        {"candidate_id": "different"},
        {"checks": []},
        {"checks": [{"id": "artifact", "status": "pass", "code": "compatible"}]},
        {"checks": [{"id": "artifact", "status": "unknown", "code": "unknown"}]},
        {"blocked_reason": "radio_busy"},
        {"requires_manual_action": True},
        {"board": "/private/board"},
        {"target_version": None},
    ],
)
def test_invalid_or_conflicting_preflight_is_rejected(preflight_report, change):
    """Malformed schemas and inconsistent executable reports never reach admission.

    @param preflight_report Valid report to mutate.
    @param change One invalid or contradictory field change.
    """
    with pytest.raises(IncompatibleError):
        preflight_data(dict(preflight_report, **change), "candidate-1")


@pytest.mark.parametrize("value", [None, [], "invalid", 1])
def test_preflight_validator_rejects_non_mapping(value):
    """Direct callers receive a typed compatibility error for non-object reports.

    @param value Invalid public helper input.
    """
    with pytest.raises(IncompatibleError):
        preflight_data(value)


async def test_preflight_options_are_read_only_and_refreshable(
    hass, envelope, preflight_report
):
    """HA displays and refreshes the advisory report without any command submission.

    @param hass Actual Home Assistant instance.
    @param envelope Valid native API snapshot.
    @param preflight_report Valid report displayed in the options form.
    """
    envelope["data"]["firmware_update"] = {"preflight_supported": True}
    entry = MockConfigEntry(
        domain="livolo_ec",
        unique_id="2a992b12:a7",
        data={"url": "http://example.invalid:8090", "token": "secret"},
        options={"preserve": True},
    )
    entry.add_to_hass(hass)
    with patch("custom_components.livolo_ec.config_flow.Client") as client:
        client.return_value.snapshot = AsyncMock(return_value=envelope)
        client.return_value.firmware_preflight = AsyncMock(
            return_value=preflight_report
        )
        result = await hass.config_entries.options.async_init(entry.entry_id)
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"next_step_id": "firmware_preflight"}
        )
        assert result["description_placeholders"]["target"] == "0.13"
        assert result["description_placeholders"]["executable"] == "Yes"
        blocked = deepcopy(preflight_report)
        blocked.update(executable=False, blocked_reason="radio_busy")
        client.return_value.firmware_preflight.return_value = blocked
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"refresh": True}
        )
        assert result["description_placeholders"]["reason"] == "radio busy"
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {}
        )
        assert result["type"] == "create_entry" and result["data"] == {"preserve": True}
        assert client.return_value.firmware_preflight.await_count == 2
        client.return_value.command.assert_not_called()
        client.return_value.install_firmware.assert_not_called()


async def test_old_daemon_preflight_form_explains_limitation(hass, envelope):
    """Older daemons get an explicit capability message without a probe or command.

    @param hass Actual Home Assistant instance.
    @param envelope Snapshot without the additive preflight capability.
    """
    entry = MockConfigEntry(
        domain="livolo_ec",
        unique_id="2a992b12:a7",
        data={"url": "http://example.invalid:8090", "token": "secret"},
    )
    entry.add_to_hass(hass)
    with patch("custom_components.livolo_ec.config_flow.Client") as client:
        client.return_value.snapshot = AsyncMock(return_value=envelope)
        result = await hass.config_entries.options.async_init(entry.entry_id)
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"next_step_id": "firmware_preflight"}
        )
        assert result["errors"] == {"base": "preflight_unsupported"}
        client.return_value.firmware_preflight.assert_not_called()
        client.return_value.install_firmware.assert_not_called()
