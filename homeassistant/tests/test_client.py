"""Native API transport and uncertain-command behavior with real local HTTP."""

import asyncio
import copy
import time

from aiohttp import ClientSession, web
import pytest

from livolo_ec_client import (
    AuthenticationError,
    Client,
    CommandFailed,
    IncompatibleError,
    normalize_url,
    snapshot_data,
)

pytestmark = pytest.mark.usefixtures("socket_enabled")


@pytest.mark.parametrize(
    "url",
    [
        "ftp://host",
        "http://user:secret@host",
        "http://host?token=x",
        "http://host/#fragment",
    ],
)
def test_unsafe_urls(url):
    """Reject credentials and unsupported endpoint URL forms.

    @param url Unsafe API base URL.
    """
    with pytest.raises(ValueError):
        normalize_url(url)


async def test_lost_submission_is_never_replayed(aiohttp_server):
    """Recover an accepted command after HTTP failure without a second POST.

    @param aiohttp_server Local aiohttp server factory.
    """
    submitted = []

    async def submit(request):
        """Simulate a committed request whose HTTP response fails.

        @param request Incoming command POST.
        """
        submitted.append(await request.json())
        return web.Response(status=503)

    async def result(request):
        """Expose the durable outcome after submission response loss.

        @param request Command result GET.
        """
        return web.json_response(
            {"request_id": request.match_info["id"], "status": "confirmed"}
        )

    app = web.Application()
    app.router.add_post("/api/v1/commands", submit)
    app.router.add_get("/api/v1/commands/{id}", result)
    server = await aiohttp_server(app)
    async with ClientSession() as session:
        client = Client(session, str(server.make_url("")), "secret")
        result_value = await client.command(
            "set", {"device": "46715621", "mask": 1, "value": 1}
        )
    assert len(submitted) == 1
    assert result_value["request_id"] == submitted[0]["payload"]["request_id"]


async def test_rejected_auth_is_not_retried(aiohttp_server):
    """Authentication failure does not attempt result recovery or a fresh command.

    @param aiohttp_server Local aiohttp server factory.
    """
    calls = []

    async def reject(request):
        """Reject all requests while recording attempted methods.

        @param request Incoming API request.
        """
        calls.append(request.method)
        return web.Response(status=401)

    app = web.Application()
    app.router.add_route("*", "/{tail:.*}", reject)
    server = await aiohttp_server(app)
    async with ClientSession() as session:
        with pytest.raises(AuthenticationError):
            await Client(session, str(server.make_url("")), "bad").command("set", {})
    assert calls == ["POST"]


async def test_stream_auth_and_epoch_reset(aiohttp_server, envelope):
    """Read full snapshots across sequence resets while sending auth only in headers.

    @param aiohttp_server Local aiohttp server factory.
    @param envelope Valid snapshot fixture.
    """

    async def events(request):
        """Send snapshots from two daemon epochs.

        @param request WebSocket upgrade.
        """
        assert request.headers["Authorization"] == "Bearer secret"
        assert not request.query
        socket = web.WebSocketResponse()
        await socket.prepare(request)
        await socket.send_json(envelope)
        newer = copy.deepcopy(envelope)
        newer.update(epoch="epoch-two", sequence=0)
        await socket.send_json(newer)
        await socket.close()
        return socket

    app = web.Application()
    app.router.add_get("/api/v1/events", events)
    server = await aiohttp_server(app)
    async with ClientSession() as session:
        values = [
            event
            async for event in Client(
                session, str(server.make_url("")), "secret"
            ).events()
        ]
    assert [(v["epoch"], v["sequence"]) for v in values] == [
        ("epoch-one", 1),
        ("epoch-two", 0),
    ]


def test_duplicate_profiles_rejected(envelope):
    """Malformed registries cannot create duplicate or conflicting HA entities.

    @param envelope Valid snapshot fixture.
    """
    envelope["data"]["devices"].append(copy.deepcopy(envelope["data"]["devices"][0]))
    with pytest.raises(IncompatibleError):
        snapshot_data(envelope)


async def test_unconfirmed_is_not_success(aiohttp_server):
    """A completed HTTP response cannot turn an unconfirmed radio result into success.

    @param aiohttp_server Local aiohttp server factory.
    """

    async def submit(request):
        """Return a device-level uncertain outcome.

        @param request Command POST.
        """
        payload = (await request.json())["payload"]
        return web.json_response(
            {"request_id": payload["request_id"], "status": "unconfirmed"}
        )

    app = web.Application()
    app.router.add_post("/api/v1/commands", submit)
    server = await aiohttp_server(app)
    async with ClientSession() as session:
        with pytest.raises(CommandFailed) as error:
            await Client(session, str(server.make_url("")), "secret").command("set", {})
    assert error.value.result["status"] == "unconfirmed"


async def test_total_deadline_includes_submission(aiohttp_server):
    """A slow initial POST respects the total timeout and is never replayed.

    @param aiohttp_server Local server factory.
    """
    submitted = []

    async def submit(request):
        """Delay after accepting the request to model a lost reply.

        @param request Incoming command POST.
        """
        submitted.append(await request.json())
        await asyncio.sleep(0.2)
        return web.json_response({"status": "pending"})

    app = web.Application()
    app.router.add_post("/api/v1/commands", submit)
    server = await aiohttp_server(app)
    started = time.monotonic()
    async with ClientSession() as session:
        with pytest.raises(CommandFailed) as error:
            await Client(session, str(server.make_url("")), "secret").command(
                "set", {}, timeout=0.02
            )
    assert time.monotonic() - started < 0.15
    assert len(submitted) == 1 and error.value.result["status"] == "unknown"
