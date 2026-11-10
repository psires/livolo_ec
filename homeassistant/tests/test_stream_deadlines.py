"""Exercise event-stream startup deadlines and cleanup with local HTTP servers."""

import asyncio
import logging

from aiohttp import ClientSession, web
import pytest

import livolo_ec_client as client_module
from livolo_ec_client import AuthenticationError, Client, IncompatibleError, LivoloError

pytestmark = pytest.mark.usefixtures("socket_enabled")


async def test_stalled_handshake(aiohttp_server, monkeypatch, caplog):
    """A stalled upgrade times out and logs its phase without exposing secrets.

    @param aiohttp_server Local server factory.
    @param monkeypatch Fixture to shorten test deadline.
    @param caplog Captured log messages.
    """
    release = asyncio.Event()

    async def handler(request):
        """Hold the upgrade until the test releases it.

        @param request Incoming HTTP request.
        """
        await release.wait()
        return web.Response()

    app = web.Application()
    app.router.add_get("/private-secret/api/v1/events", handler)
    server = await aiohttp_server(app)
    monkeypatch.setattr(client_module, "STREAM_HANDSHAKE_TIMEOUT", 0.05)
    caplog.set_level(logging.DEBUG, logger=client_module.__name__)
    try:
        async with ClientSession() as session:
            with pytest.raises(LivoloError):
                await anext(
                    Client(
                        session, str(server.make_url("/private-secret")), "secret-token"
                    ).events()
                )
        assert "phase=handshake" in caplog.text
        assert "TimeoutError" in caplog.text
        assert "secret-token" not in caplog.text
        assert "private-secret" not in caplog.text
    finally:
        release.set()


async def test_first_snapshot_timeout_and_cancel_close_socket(
    aiohttp_server, monkeypatch
):
    """An idle responsive socket times out or is cancelled and is closed.

    @param aiohttp_server Local server factory.
    @param monkeypatch Fixture to shorten test deadline.
    """
    closed = asyncio.Event()

    async def handler(request):
        """Keep the socket open and observe closure.

        @param request WebSocket upgrade.
        """
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        async for _ in ws:
            pass
        closed.set()
        return ws

    app = web.Application()
    app.router.add_get("/api/v1/events", handler)
    server = await aiohttp_server(app)
    monkeypatch.setattr(client_module, "STREAM_FIRST_SNAPSHOT_TIMEOUT", 0.08)
    async with ClientSession() as session:
        with pytest.raises(LivoloError):
            await anext(Client(session, str(server.make_url("")), "secret").events())
        await asyncio.wait_for(closed.wait(), 1)
        closed.clear()
        monkeypatch.setattr(client_module, "STREAM_FIRST_SNAPSHOT_TIMEOUT", 5)
        stream = Client(session, str(server.make_url("")), "secret").events()
        task = asyncio.create_task(anext(stream))
        await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await asyncio.wait_for(closed.wait(), 1)


async def test_healthy_stream_outlives_start_deadlines(
    aiohttp_server, monkeypatch, envelope
):
    """A valid stream stays alive past both startup deadlines.

    @param aiohttp_server Local server factory.
    @param monkeypatch Fixture to shorten test deadlines.
    @param envelope Valid event snapshot.
    """

    async def handler(request):
        """Send a first snapshot, pause, then send another.

        @param request WebSocket upgrade.
        """
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        await ws.send_json(envelope)
        await asyncio.sleep(0.18)
        await ws.send_json(envelope)
        await ws.close()
        return ws

    app = web.Application()
    app.router.add_get("/api/v1/events", handler)
    server = await aiohttp_server(app)
    monkeypatch.setattr(client_module, "STREAM_HANDSHAKE_TIMEOUT", 0.08)
    monkeypatch.setattr(client_module, "STREAM_FIRST_SNAPSHOT_TIMEOUT", 0.08)
    async with ClientSession() as session:
        assert (
            len(
                [
                    e
                    async for e in Client(
                        session, str(server.make_url("")), "secret"
                    ).events()
                ]
            )
            == 2
        )


@pytest.mark.parametrize("kind", ["auth", "invalid"])
async def test_stream_preserves_specific_failures(aiohttp_server, kind):
    """Authentication and invalid snapshots retain their distinct error types.

    @param aiohttp_server Local server factory.
    @param kind Error scenario to exercise.
    """

    async def handler(request):
        """Reject authentication or send an invalid first snapshot.

        @param request Incoming request.
        """
        if kind == "auth":
            return web.Response(status=401)
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        await ws.send_json([])
        await ws.close()
        return ws

    app = web.Application()
    app.router.add_get("/api/v1/events", handler)
    server = await aiohttp_server(app)
    async with ClientSession() as session:
        with pytest.raises(
            AuthenticationError if kind == "auth" else IncompatibleError
        ):
            await anext(Client(session, str(server.make_url("")), "secret").events())


async def test_generator_close_releases_socket(aiohttp_server, envelope, monkeypatch):
    """Closing a stream after its first yield releases its WebSocket.

    aiohttp 3.14.5 can retain a deferred heartbeat timer after close; the
    peer-observed close is checked here independently of that library timer.

    @param aiohttp_server Local server factory.
    @param envelope Valid event snapshot.
    @param monkeypatch Fixture to shorten heartbeat timer.
    """
    monkeypatch.setattr(client_module, "STREAM_HEARTBEAT", 0.05)
    closed = asyncio.Event()

    async def handler(request):
        """Send a snapshot and observe client closure.

        @param request WebSocket upgrade.
        """
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        await ws.send_json(envelope)
        async for _ in ws:
            pass
        closed.set()
        return ws

    app = web.Application()
    app.router.add_get("/api/v1/events", handler)
    server = await aiohttp_server(app)
    async with ClientSession() as session:
        stream = Client(session, str(server.make_url("")), "secret").events()
        assert await anext(stream) == envelope
        await stream.aclose()
        await asyncio.wait_for(closed.wait(), 1)
        await asyncio.sleep(0.2)
