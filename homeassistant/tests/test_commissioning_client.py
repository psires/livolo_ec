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


@pytest.mark.parametrize("include_evidence", [False, True])
def test_pairing_evidence_preserves_legacy_and_bounded_new_observations(
    include_evidence,
):
    """Additive diagnostics preserve exact bytes while stripping unrelated packet fields.

    @param include_evidence Whether the daemon supports optional bounded payload evidence.
    """
    observation = {
        "device": "67181844",
        "suffix": "0002",
        "reason": "unknown_pairing_payload",
    }
    if include_evidence:
        observation.update(
            pairing_evidence=[
                {
                    "suffix": "0002",
                    "payload": "0002030534990400",
                    "raw_frame": "secret",
                },
                {"suffix": "0001", "payload": "0001010534010500"},
            ],
            pairing_evidence_truncated=True,
        )
    value = session(observations=[observation])
    result = commissioning_data(value)["observations"][0]
    if include_evidence:
        assert result["pairing_evidence"] == [
            {"suffix": "0002", "payload": "0002030534990400"},
            {"suffix": "0001", "payload": "0001010534010500"},
        ]
        assert result["pairing_evidence_truncated"] is True
        assert "secret" not in str(result)
        assert observation["pairing_evidence"][0]["raw_frame"] == "secret"
    else:
        assert "pairing_evidence" not in result
        assert "pairing_evidence_truncated" not in result
    assert result["outputs"] is None


@pytest.mark.parametrize(
    "extra",
    [
        {"pairing_evidence": None},
        {"pairing_evidence": {}},
        {"pairing_evidence": [{"suffix": "0002", "payload": "0" * 16}] * 3},
        {"pairing_evidence": [None]},
        {"pairing_evidence": [{"suffix": "0002"}]},
        {"pairing_evidence": [{"suffix": "0002", "payload": "0" * 18}]},
        {"pairing_evidence": [{"suffix": "0002", "payload": "A" * 16}]},
        {"pairing_evidence": [{"suffix": "00022", "payload": "0" * 16}]},
        {"pairing_evidence": [{"suffix": 2, "payload": "0" * 16}]},
        {"pairing_evidence_truncated": 1},
        {"pairing_evidence_truncated": "false"},
    ],
)
def test_pairing_evidence_rejects_malformed_or_oversized_fields(extra):
    """Unbounded or malformed diagnostic evidence cannot masquerade as a valid session.

    @param extra Invalid optional evidence fields on an otherwise valid observation.
    """
    observation = dict(device="67181844", suffix="0002", **extra)
    with pytest.raises(IncompatibleError):
        commissioning_data(session(observations=[observation]))
