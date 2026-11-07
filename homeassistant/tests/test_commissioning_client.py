"""Commissioning commands recover durable acceptance without replay or false success."""

from aiohttp import ClientSession, web
import pytest

from livolo_ec_client import (
    Client,
    CommandFailed,
    IncompatibleError,
    commissioning_data,
)

pytestmark = pytest.mark.usefixtures("socket_enabled")


def session(status="listening", session_id="session-1", **changes):
    """Build a complete public session without inventing a selected device.

    @param status Authoritative active or terminal phase.
    @param session_id Durable start command identity.
    @param changes Explicit evidence or result overrides.
    """
    value = dict(
        session_id=session_id,
        status=status,
        seconds_remaining=120,
        device=None,
        name=None,
        outputs=None,
        suffix=None,
        profile=None,
        registered=False,
        error=None,
        observations=[],
    )
    value.update(changes)
    return value


@pytest.mark.parametrize("status", ["listening", "registering", "pairing", "confirmed"])
async def test_lost_start_reply_recovers_same_id(aiohttp_server, status):
    """A lost POST can resolve to any active phase or genuine final result without resend.

    @param aiohttp_server Offline loopback server fixture.
    @param status Current durable status when GET observes the original request.
    """
    submissions, lookups = [], []

    async def submit(request):
        """Model one committed start whose HTTP response is lost.

        @param request Incoming native command request.
        """
        submissions.append(await request.json())
        raise web.HTTPServiceUnavailable()

    async def result(request):
        """Expose the same original command without mutating it.

        @param request Request carrying the stable command ID.
        """
        lookups.append(request.match_info["id"])
        return web.json_response(
            dict(request_id="session-1", session_id="session-1", status=status)
        )

    app = web.Application()
    app.router.add_post("/api/v1/commands", submit)
    app.router.add_get("/api/v1/commands/{id}", result)
    server = await aiohttp_server(app)
    async with ClientSession() as http:
        client = Client(http, str(server.make_url("")), "secret")
        response = await client.command(
            "commission", {"request_id": "session-1", "seconds": 120}
        )
    assert response["status"] == status
    assert len(submissions) == 1 and lookups == ["session-1"]
    assert submissions[0] == {
        "action": "commission",
        "payload": {"request_id": "session-1", "seconds": 120},
    }


@pytest.mark.parametrize(
    "status", ["rejected", "failed", "expired", "cancelled", "conflict", "unknown"]
)
async def test_start_terminal_failures_are_not_success(aiohttp_server, status):
    """Every unconfirmed terminal status raises with its durable identity intact.

    @param aiohttp_server Offline server fixture.
    @param status Non-successful command outcome.
    """

    async def submit(request):
        """Return a durable unsuccessful start result.

        @param request Submitted native command.
        """
        return web.json_response(dict(request_id="session-1", status=status))

    app = web.Application()
    app.router.add_post("/api/v1/commands", submit)
    server = await aiohttp_server(app)
    async with ClientSession() as http:
        with pytest.raises(CommandFailed) as error:
            await Client(http, str(server.make_url("")), "secret").command(
                "commission", {"request_id": "session-1"}
            )
    assert error.value.result["status"] == status


@pytest.mark.parametrize(
    "changes",
    [
        {"status": "confirmed"},
        {"registered": 1},
        {"seconds_remaining": True},
        {"seconds_remaining": 10**400},
        {"seconds_remaining": float("nan")},
        {"device": "not-an-id"},
        {"outputs": 2, "suffix": "0001"},
        {"observations": [{"device": "bad"}]},
    ],
)
def test_invalid_session_cannot_claim_pairing(changes):
    """Malformed summaries and registration-only evidence fail closed.

    @param changes Invalid protocol fields replacing valid idle evidence.
    """
    value = session()
    value.update(changes)
    with pytest.raises(IncompatibleError):
        commissioning_data(value)
