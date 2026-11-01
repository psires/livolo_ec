"""Firmware job transport never replays transfers or mistakes acceptance for success."""

import asyncio

from aiohttp import ClientSession, web
import pytest

from livolo_ec_client import (
    AuthenticationError,
    Client,
    CommandFailed,
    IncompatibleError,
)

pytestmark = pytest.mark.usefixtures("socket_enabled")


@pytest.mark.parametrize("lost_reply", [False, True])
async def test_install_waits_for_same_durable_job(aiohttp_server, lost_reply):
    """A pending or lost submission is resolved by polling its original job ID.

    @param aiohttp_server Isolated loopback server factory.
    @param lost_reply Whether the submission response is lost after acceptance.
    """
    submissions = []
    lookups = []
    candidate_id = "a" * 96 + ":" + "b" * 64

    async def submit(request):
        """Accept exactly the two catalog/job fields without any caller-supplied path.

        @param request Authenticated firmware POST.
        """
        assert request.headers["Authorization"] == "Bearer secret"
        payload = await request.json()
        submissions.append(payload)
        assert payload == {"candidate_id": candidate_id, "request_id": "firmware-1"}
        if lost_reply:
            return web.Response(status=503)
        return web.json_response({"request_id": "firmware-1", "status": "pending"})

    async def result(request):
        """Keep the job pending once, then confirm verified completion.

        @param request Durable result lookup.
        """
        lookups.append(request.match_info["id"])
        return web.json_response(
            {
                "request_id": "firmware-1",
                "status": "pending" if len(lookups) == 1 else "confirmed",
            }
        )

    app = web.Application()
    app.router.add_post("/api/v1/firmware/install", submit)
    app.router.add_get("/api/v1/commands/{id}", result)
    server = await aiohttp_server(app)
    async with ClientSession() as session:
        value = await Client(
            session, str(server.make_url("")), "secret"
        ).install_firmware(candidate_id, request_id="firmware-1")
    assert value["status"] == "confirmed"
    assert len(submissions) == 1 and lookups == ["firmware-1", "firmware-1"]


@pytest.mark.parametrize("status", ["rejected", "unknown"])
async def test_firmware_terminal_failure_is_not_success(aiohttp_server, status):
    """Policy rejection and interrupted-transfer uncertainty propagate to the caller.

    @param aiohttp_server Isolated loopback server factory.
    @param status Durable unsuccessful outcome returned by the daemon.
    """
    calls = []

    async def submit(request):
        """Return the daemon's terminal result without starting any worker.

        @param request Firmware installation POST.
        """
        calls.append(await request.json())
        return web.json_response(
            {
                "request_id": calls[0]["request_id"],
                "status": status,
                "error": "Inspect recovery",
            }
        )

    app = web.Application()
    app.router.add_post("/api/v1/firmware/install", submit)
    server = await aiohttp_server(app)
    async with ClientSession() as session:
        with pytest.raises(CommandFailed) as failure:
            await Client(session, str(server.make_url("")), "secret").install_firmware(
                "candidate-1"
            )
    assert failure.value.result["status"] == status
    assert failure.value.result["error"] == "Inspect recovery"
    assert len(calls) == 1


@pytest.mark.parametrize(
    "http_status,exception", [(401, AuthenticationError), (404, IncompatibleError)]
)
async def test_firmware_auth_or_old_daemon_fails_without_recovery_poll(
    aiohttp_server, http_status, exception
):
    """Rejected credentials and an absent firmware API cannot submit or poll a job.

    @param aiohttp_server Isolated loopback server factory.
    @param http_status Authentication or unsupported-route response code.
    @param exception Client exception expected for the response.
    """
    methods = []

    async def reject(request):
        """Record access attempts and reject the requested route.

        @param request Incoming API request.
        """
        methods.append(request.method)
        return web.Response(status=http_status)

    app = web.Application()
    app.router.add_route("*", "/{tail:.*}", reject)
    server = await aiohttp_server(app)
    async with ClientSession() as session:
        with pytest.raises(exception):
            await Client(session, str(server.make_url("")), "secret").install_firmware(
                "candidate-1"
            )
    assert methods == ["POST"]


async def test_firmware_cancel_only_stops_observation(aiohttp_server):
    """Cancelling an HA waiter neither cancels a daemon job nor creates another transfer.

    @param aiohttp_server Isolated loopback server factory.
    """
    methods = []
    accepted = asyncio.Event()

    async def pending(request):
        """Accept a durable job and expose its pending outcome.

        @param request Installation POST or status GET.
        """
        methods.append(request.method)
        accepted.set()
        return web.json_response({"request_id": "firmware-1", "status": "pending"})

    app = web.Application()
    app.router.add_route("*", "/{tail:.*}", pending)
    server = await aiohttp_server(app)
    async with ClientSession() as session:
        client = Client(session, str(server.make_url("")), "secret")
        task = asyncio.create_task(
            client.install_firmware("candidate-1", request_id="firmware-1")
        )
        await accepted.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert (await client.result("firmware-1"))["status"] == "pending"
    assert methods.count("POST") == 1
    assert set(methods) == {"POST", "GET"}


async def test_firmware_deadline_keeps_original_id(aiohttp_server):
    """A local observation deadline reports uncertainty without restarting the job.

    @param aiohttp_server Isolated loopback server factory.
    """
    calls = []

    async def pending(request):
        """Return an unfinished firmware job for every read.

        @param request Firmware installation POST or status GET.
        """
        calls.append(request.method)
        return web.json_response({"request_id": "firmware-1", "status": "pending"})

    app = web.Application()
    app.router.add_route("*", "/{tail:.*}", pending)
    server = await aiohttp_server(app)
    async with ClientSession() as session:
        with pytest.raises(CommandFailed) as failure:
            await Client(session, str(server.make_url("")), "secret").install_firmware(
                "candidate-1", request_id="firmware-1", timeout=0.02
            )
    assert failure.value.result == {"request_id": "firmware-1", "status": "unknown"}
    assert calls.count("POST") == 1
