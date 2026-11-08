"""Bound and validate physical-bootstrap HTTP messages without retrying operations."""

from copy import deepcopy
import json
from unittest.mock import AsyncMock

from aiohttp import ClientSession, web
import pytest

from livolo_ec_client import Client, IncompatibleError
from livolo_ec_client.bootstrap import (
    BootstrapClient,
    BootstrapError,
    MAX_RESPONSE_BYTES,
    operation_data,
    reconciliation_data,
)


def reconciliation():
    """Return the exact authority for one unknown Install and one current setup session."""
    return dict(
        reconciliation_id="reconciliation-1",
        request_id="install-1",
        kind="install",
        plan_id="plan-1",
        target_id="radio-1",
        resolution="verified_no_flash",
        permits="prepare_only",
        setup_session="session-2",
    )


@pytest.mark.parametrize("record", [None, reconciliation()])
async def test_info_reconciliation_is_optional_and_bound(record):
    """Legacy metadata stays compatible and explicit authority survives strict parsing.

    @param record Absent legacy authority or an exact session-bound record.
    """
    metadata = info()
    if record:
        metadata.update(setup_session="session-2", reconciliation=record)
    async with ClientSession() as session:
        client = BootstrapClient(Client(session, "http://example.invalid", "secret"))
        client._request = AsyncMock(return_value=("instance-1", metadata))
        result = await client.info()
    assert result["reconciliation"] == record
    assert result["setup_session"] == ("session-2" if record else None)
    client._request.assert_awaited_once_with(
        "GET", "info", instance_id=None, optional=True
    )


@pytest.mark.parametrize(
    "change",
    [
        {"kind": "prepare"},
        {"resolution": "verified"},
        {"permits": "install"},
        {"setup_session": "old-session"},
        {"request_id": None},
        {"plan_id": ""},
        {"target_id": True},
        {"reconciliation_id": "x" * 513},
        {"extra": True},
    ],
)
def test_reconciliation_rejects_ambiguous_or_stale_authority(change):
    """No malformed, expanded or differently scoped authority can release local intent.

    @param change Invalid field or unsupported authority extension.
    """
    with pytest.raises(IncompatibleError):
        reconciliation_data(dict(reconciliation(), **change), "session-2")


@pytest.mark.parametrize("field", list(reconciliation()))
def test_reconciliation_requires_every_binding(field):
    """Every declared binding is mandatory before the flow can acknowledge an unknown result.

    @param field Required record field omitted by the service.
    """
    record = reconciliation()
    record.pop(field)
    with pytest.raises(IncompatibleError):
        reconciliation_data(record, "session-2")


pytestmark = pytest.mark.usefixtures("socket_enabled")


def envelope(data=None, **changes):
    """Create a physical-service protocol envelope for isolated HTTP tests.

    @param data Public response fields.
    @param changes Envelope modifications for negative cases.
    """
    return dict(
        schema=1,
        instance_id="instance-1",
        simulated=False,
        operation="provision",
        ok=True,
        data=data or {},
        **changes,
    )


def info():
    """Provide explicit capabilities without claiming a ready normal network."""
    return dict(
        mode="bootstrap",
        profile="zb-gw04-1v2",
        physical_install_supported=True,
        prepare_supported=True,
        enter_supported=False,
        initialize_supported=False,
        cancel_supported=True,
        baseline_available=True,
        expected_network_id="2a992b12:a7",
        active_request_id=None,
        latest_request_id=None,
        blocked_reason=None,
    )


@pytest.mark.parametrize("chunked", [False, True])
async def test_oversized_http_body_is_bounded_without_content_length(
    aiohttp_server, chunked
):
    """Declared and streamed oversized JSON stop at a fixed response limit.

    @param aiohttp_server Local HTTP server factory.
    @param chunked Whether the server omits Content-Length.
    """
    body = json.dumps(envelope(dict(info(), extra="x" * MAX_RESPONSE_BYTES))).encode()

    async def respond(request):
        """Stream or return the oversized test body without touching a device.

        @param request Local HTTP request.
        """
        if not chunked:
            return web.Response(body=body, content_type="application/json")
        response = web.StreamResponse(headers={"Content-Type": "application/json"})
        await response.prepare(request)
        await response.write(body)
        await response.write_eof()
        return response

    app = web.Application()
    app.router.add_get("/api/v1/provisioning/info", respond)
    server = await aiohttp_server(app)
    async with ClientSession() as session:
        with pytest.raises(IncompatibleError, match="too large"):
            await BootstrapClient(
                Client(session, str(server.make_url("")), "secret")
            ).info()


@pytest.mark.parametrize(
    "change",
    [
        {"simulated": True},
        {"schema": True},
        {"instance_id": "replacement"},
        {"operation": "update"},
    ],
)
async def test_fake_or_replaced_service_never_supplies_capability(
    aiohttp_server, change
):
    """A physical client rejects simulated metadata and changed service identity.

    @param aiohttp_server Local HTTP server factory.
    @param change Invalid physical envelope field.
    """

    async def respond(request):
        """Return one deliberately incompatible capability envelope.

        @param request Local read request.
        """
        return web.json_response(dict(envelope(info()), **change))

    app = web.Application()
    app.router.add_get("/api/v1/provisioning/info", respond)
    server = await aiohttp_server(app)
    async with ClientSession() as session:
        with pytest.raises(IncompatibleError):
            await BootstrapClient(
                Client(session, str(server.make_url("")), "secret")
            ).info("instance-1")


async def test_missing_result_stays_uncertain_and_never_reposts(aiohttp_server):
    """A durable lookup miss after a connection failure cannot trigger a new physical request.

    @param aiohttp_server Isolated HTTP server factory.
    """
    calls = []

    async def respond(request):
        """Record the exact method and return the stable missing-result envelope.

        @param request Incoming local request.
        """
        calls.append((request.method, request.path))
        assert request.headers["Authorization"] == "Bearer secret"
        return web.json_response(
            dict(envelope(), ok=False, error="unknown_operation"), status=404
        )

    app = web.Application()
    app.router.add_route("*", "/{tail:.*}", respond)
    server = await aiohttp_server(app)
    async with ClientSession() as session:
        with pytest.raises(BootstrapError, match="unknown_operation"):
            await BootstrapClient(
                Client(session, str(server.make_url("")), "secret")
            ).operation("request-1", "instance-1")
    assert calls == [("GET", "/api/v1/provisioning/operations/request-1")]


@pytest.mark.parametrize(
    "chosen",
    [
        {"gateway": 12345678, "tag": "a7"},
        {"gateway": "2a992b12", "tag": 12},
        {"gateway": "00000000", "tag": "a7"},
        {"gateway": "2a992b12", "tag": "ff"},
    ],
)
async def test_network_fields_cannot_be_coerced_or_reserved(chosen):
    """Invalid initialization identities fail before any HTTP request.

    @param chosen Invalid new-network configuration.
    """
    async with ClientSession() as session:
        client = BootstrapClient(Client(session, "http://example.invalid", "secret"))
        with pytest.raises((ValueError, IncompatibleError)):
            await client.submit(
                "handoff",
                dict(
                    request_id="request-1",
                    instance_id="instance-1",
                    install_request_id="install-1",
                    confirmed=True,
                    network=chosen,
                ),
                "instance-1",
            )
    result = dict(
        request_id="request-1",
        kind="initialize",
        status="confirmed",
        stage="network_initialized",
        network=chosen,
    )
    with pytest.raises(IncompatibleError):
        operation_data(result, "instance-1")


async def test_prepare_requires_dedication_and_exact_board_before_http():
    """Missing installation consent cannot reach the inspection endpoint."""
    async with ClientSession() as session:
        client = BootstrapClient(Client(session, "http://example.invalid", "secret"))
        with pytest.raises(ValueError, match="confirmation"):
            await client.submit(
                "prepare",
                dict(
                    request_id="request-1",
                    instance_id="instance-1",
                    target_id="radio-1",
                    candidate_id="image-1",
                    dedicated=False,
                    board_confirmed=True,
                ),
                "instance-1",
            )


@pytest.mark.parametrize("flag", [True, False, "true"])
async def test_engineering_annotation_survives_all_read_models(aiohttp_server, flag):
    """Engineering qualification survives every response model and rejects non-boolean flags.

    @param aiohttp_server Isolated HTTP server factory.
    @param flag Valid boolean qualification or invalid truthy substitute.
    """
    plan = dict(
        plan_id="plan-1",
        instance_id="instance-1",
        target_id="target-1",
        candidate_id="image-1",
        profile="zb-gw04-1v2",
        version="0.14",
        artifact_sha256="a" * 64,
        expires_at=2_000_000_000,
        requires_confirmation=True,
        engineering_only=flag,
    )
    target = dict(
        target_id="target-1",
        board="zb-gw04-1v2",
        candidate_id="image-1",
        version="0.14",
        artifact_sha256="a" * 64,
        label="Test radio",
        requires_bootloader=True,
        engineering_only=flag,
    )
    operation = dict(
        request_id="request-1",
        kind="prepare",
        status="confirmed",
        stage="prepared",
        plan=plan,
        engineering_only=flag,
    )
    responses = {
        "info": dict(info(), engineering_only=flag),
        "targets": {"targets": [target]},
        "operations/request-1": operation,
    }

    async def respond(request):
        """Return the annotated physical API model requested by the client.

        @param request Read-only local HTTP request.
        """
        return web.json_response(envelope(responses[request.match_info["tail"]]))

    app = web.Application()
    app.router.add_get("/api/v1/provisioning/{tail:.*}", respond)
    server = await aiohttp_server(app)
    async with ClientSession() as session:
        client = BootstrapClient(Client(session, str(server.make_url("")), "secret"))
        if type(flag) is not bool:
            for call, args in (
                (client.info, ()),
                (client.targets, ("instance-1",)),
                (client.operation, ("request-1", "instance-1")),
            ):
                with pytest.raises(IncompatibleError, match="qualification"):
                    await call(*args)
        else:
            assert (await client.info())["engineering_only"] is flag
            assert (await client.targets("instance-1"))["targets"][0][
                "engineering_only"
            ] is flag
            result = await client.operation("request-1", "instance-1")
            assert result["engineering_only"] is flag
            assert result["plan"]["engineering_only"] is flag


@pytest.mark.parametrize(
    "board,methods,valid",
    [
        ("sonoff-zbdongle-e", None, True),
        ("sonoff-zbdongle-e", ["manual"], True),
        ("sonoff-zbdongle-e", ["manual", "sonoff_sniffer_0_12"], True),
        ("zb-gw04-1v2", ["manual", "sonoff_sniffer_0_12"], False),
        ("sonoff-zbdongle-e", ["sonoff_sniffer_0_12"], False),
        ("sonoff-zbdongle-e", ["manual", "manual"], False),
        ("sonoff-zbdongle-e", "sonoff_sniffer_0_12", False),
        ("sonoff-zbdongle-e", ["manual", "unreviewed"], False),
    ],
)
async def test_remote_entry_advertisement_is_explicit_and_exact_board(
    board, methods, valid
):
    """Legacy targets stay manual while invalid or cross-board remote capabilities fail closed.

    @param board Exact advertised board profile.
    @param methods Optional capability list; None omits the legacy field.
    @param valid Whether the target has a permitted capability contract.
    """
    target = dict(
        target_id="radio-1",
        board=board,
        candidate_id="image-1",
        version="0.14",
        artifact_sha256="a" * 64,
        label="Test radio",
        requires_bootloader=True,
    )
    if methods is not None:
        target["entry_methods"] = methods
    async with ClientSession() as session:
        client = BootstrapClient(Client(session, "http://example.invalid", "secret"))
        client._request = AsyncMock(return_value=("instance-1", {"targets": [target]}))
        if valid:
            result = await client.targets("instance-1")
            assert result["targets"][0]["entry_methods"] == (methods or ["manual"])
        else:
            with pytest.raises(IncompatibleError, match="entry methods"):
                await client.targets("instance-1")


@pytest.mark.parametrize(
    "extra",
    [
        {"entry_method": "sonoff_sniffer_0_12"},
        {"entry_confirmed": True},
        {"entry_method": "sonoff_sniffer_0_12", "entry_confirmed": False},
        {"entry_method": "sonoff_sniffer_0_12", "entry_confirmed": 1},
        {"entry_method": "unreviewed", "entry_confirmed": True},
    ],
)
async def test_remote_entry_missing_or_invalid_consent_never_sends_http(extra):
    """A legacy acknowledgment or truthy substitute cannot authorize a software entry command.

    @param extra Missing, unconfirmed or unknown optional entry fields.
    """
    body = dict(
        request_id="request-1",
        instance_id="instance-1",
        target_id="radio-1",
        candidate_id="image-1",
        dedicated=True,
        board_confirmed=True,
        **extra,
    )
    async with ClientSession() as session:
        client = BootstrapClient(Client(session, "http://example.invalid", "secret"))
        client._request = AsyncMock()
        with pytest.raises(ValueError):
            await client.submit("prepare", body, "instance-1")
        client._request.assert_not_awaited()


@pytest.mark.parametrize("automatic", [False, True])
async def test_prepare_transport_keeps_remote_consent_separate(
    aiohttp_server, automatic
):
    """The real HTTP request sends optional entry consent only for explicit automatic preparation.

    @param aiohttp_server Isolated loopback HTTP server factory.
    @param automatic Whether this request explicitly authorizes known-sniffer entry.
    """
    bodies = []
    plan = dict(
        plan_id="plan-1",
        instance_id="instance-1",
        target_id="radio-1",
        candidate_id="image-1",
        profile="sonoff-zbdongle-e",
        version="0.14",
        artifact_sha256="a" * 64,
        expires_at=2_000_000_000,
        requires_confirmation=True,
    )

    async def respond(request):
        """Record one preparation and return a distinct reviewed plan without installing.

        @param request Authenticated local preparation POST.
        """
        assert request.headers["Authorization"] == "Bearer secret"
        bodies.append(await request.json())
        return web.json_response(
            envelope(
                dict(
                    request_id="request-1",
                    kind="prepare",
                    status="confirmed",
                    stage="prepared",
                    plan=plan,
                    entry_attempted=automatic,
                )
            )
        )

    app = web.Application()
    app.router.add_post("/api/v1/provisioning/prepare", respond)
    server = await aiohttp_server(app)
    body = dict(
        request_id="request-1",
        instance_id="instance-1",
        target_id="radio-1",
        candidate_id="image-1",
        dedicated=True,
        board_confirmed=True,
    )
    if automatic:
        body.update(entry_method="sonoff_sniffer_0_12", entry_confirmed=True)
    async with ClientSession() as session:
        result = await BootstrapClient(
            Client(session, str(server.make_url("")), "secret")
        ).submit("prepare", body, "instance-1")
    assert bodies == [body]
    assert result["plan"]["plan_id"] == "plan-1"
    assert result["entry_attempted"] is automatic
    assert not result["application_verified"] and not result["network_ready"]


@pytest.mark.parametrize("flag", [1, "true", None])
async def test_entry_recovery_capabilities_require_real_booleans(flag):
    """Malformed safety flags cannot unlock manual recovery or claim an attempted entry.

    @param flag Non-boolean value substituted into the server safety contract.
    """
    async with ClientSession() as session:
        client = BootstrapClient(Client(session, "http://example.invalid", "secret"))
        client._request = AsyncMock(
            return_value=(
                "instance-1",
                dict(info(), manual_reinspection_supported=flag),
            )
        )
        with pytest.raises(IncompatibleError):
            await client.info()
    with pytest.raises(IncompatibleError):
        operation_data(
            dict(
                request_id="request-1",
                kind="prepare",
                status="unknown",
                stage="entry_unknown",
                recovery="manual_bootloader_inspection_required",
                entry_attempted=flag,
            ),
            "instance-1",
        )


def test_bootloader_observation_cannot_replace_prepared_plan():
    """Even a claimed confirmed preparation requires an independently bound firmware plan."""
    with pytest.raises(IncompatibleError, match="review plan"):
        operation_data(
            dict(
                request_id="request-1",
                kind="prepare",
                status="confirmed",
                stage="bootloader_observed",
                entry_attempted=True,
                menu_verified=True,
            ),
            "instance-1",
        )
