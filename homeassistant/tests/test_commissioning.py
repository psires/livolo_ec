"""Real HA gateway controls observe one shared pairing session without automatic retries."""

from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.exceptions import HomeAssistantError

from custom_components.livolo_ec._client import CommandFailed

from test_update import gateway  # noqa: F401
from test_commissioning_client import session
from test_development_options import set_development


@pytest.fixture
async def pairing(hass, gateway):
    """Add native commissioning capability to an already loaded HA gateway.

    @param hass Actual Home Assistant instance.
    @param gateway Existing integration with controlled snapshot and push stream.
    """
    data = gateway.envelope["data"]
    data.update(commissioning_supported=True, commissioning=None)

    async def publish(value, ready=True):
        """Publish session state through the actual coordinator event stream.

        @param value New full session summary, or None.
        @param ready Current radio readiness, independent of native API availability.
        """
        data["commissioning"] = value
        await gateway.publish({}, ready=ready)

    await publish(None)
    yield SimpleNamespace(
        gateway=gateway,
        data=data,
        publish=publish,
        start=next(
            s.entity_id
            for s in hass.states.async_all("button")
            if s.entity_id.endswith("_pair_new_device")
        ),
        cancel=next(
            s.entity_id
            for s in hass.states.async_all("button")
            if s.entity_id.endswith("_cancel_pairing")
        ),
        sensor=next(
            s.entity_id
            for s in hass.states.async_all("sensor")
            if s.entity_id.endswith("_pairing_status")
        ),
    )


async def press(hass, entity):
    """Invoke the actual standard HA button service.

    @param hass Actual Home Assistant instance.
    @param entity Start or cancel entity ID.
    """
    await hass.services.async_call(
        "button", "press", {"entity_id": entity}, blocking=True
    )


async def test_buttons_submit_once_and_cancel_is_not_pairing(hass, pairing):
    """Start uses a stable ID and 120 seconds; cancel confirmation never becomes paired.

    @param hass Actual HA instance.
    @param pairing Controllable native commissioning fixture.
    """

    async def command(action, payload):
        """Apply accepted command effects without producing a physical confirmation.

        @param action Explicit commission or cancellation action.
        @param payload Stable command/session IDs.
        """
        if action == "commission":
            assert payload["seconds"] == 120
            pairing.data["commissioning"] = session(session_id=payload["request_id"])
        else:
            assert action == "commission_cancel"
            assert payload["session_id"] == pairing.data["commissioning"]["session_id"]
            pairing.data["commissioning"]["status"] = "cancelled"
        return {"status": "confirmed", "request_id": payload["request_id"]}

    with (
        patch.object(
            pairing.gateway.entry.runtime_data.client, "command", side_effect=command
        ) as submit,
        patch(
            "custom_components.livolo_ec.commissioning.persistent_notification.async_create"
        ) as notify,
    ):
        await press(hass, pairing.start)
        assert hass.states.get(pairing.sensor).state == "listening"
        assert hass.states.get(pairing.start).state == "unavailable"
        await press(hass, pairing.cancel)
        assert hass.states.get(pairing.sensor).state == "cancelled"
        assert submit.await_count == 2
        notify.assert_not_called()


async def test_uncertain_start_survives_reload_and_explicit_cancel_fences_id(
    hass, pairing
):
    """A lost request blocks new starts across reload and can be cancelled even before acceptance.

    @param hass Actual HA instance.
    @param pairing Loaded native controls and snapshots.
    """
    submitted = []

    async def lost(action, payload):
        """Lose observation of the one submitted start.

        @param action Commission action being submitted.
        @param payload Original durable ID.
        """
        submitted.append((action, dict(payload)))
        raise CommandFailed({"request_id": payload["request_id"], "status": "unknown"})

    with (
        patch("custom_components.livolo_ec._client.Client.command", side_effect=lost),
        patch("custom_components.livolo_ec._client.Client.result", return_value=None),
    ):
        with pytest.raises(HomeAssistantError):
            await press(hass, pairing.start)
        ident = submitted[0][1]["request_id"]
        assert hass.states.get(pairing.sensor).state == "uncertain"
        assert hass.states.get(pairing.start).state == "unavailable"
        assert await hass.config_entries.async_reload(pairing.gateway.entry.entry_id)
        await hass.async_block_till_done()
        assert hass.states.get(pairing.sensor).state == "uncertain"
        assert (
            hass.states.get(pairing.sensor).attributes["unresolved_request_id"] == ident
        )
        assert len(submitted) == 1
        with patch(
            "custom_components.livolo_ec._client.Client.command",
            return_value={"status": "confirmed"},
        ) as cancel:
            await press(hass, pairing.cancel)
            cancel.assert_awaited_once()
            assert cancel.call_args.args[0] == "commission_cancel"
            assert cancel.call_args.args[1]["session_id"] == ident
        assert hass.states.get(pairing.sensor).state == "idle"


async def test_external_session_notification_only_after_physical_confirmation(
    hass, pairing
):
    """Web/native sessions use the same entities and notify once, never on registration or reload.

    @param hass Actual HA instance.
    @param pairing Existing device registry and shared session push.
    """
    value = session()
    with patch(
        "custom_components.livolo_ec.commissioning.persistent_notification.async_create"
    ) as notify:
        await pairing.publish(value)
        value = dict(
            value,
            status="registering",
            device="46715621",
            name="Kitchen",
            outputs=2,
            suffix="0002",
            profile="livolo-ec-suffix-0002",
            registered=True,
        )
        await pairing.publish(value, ready=False)
        assert hass.states.get(pairing.sensor).state == "registering"
        assert hass.states.get(pairing.cancel).state != "unavailable"
        notify.assert_not_called()
        await pairing.publish(dict(value, status="confirmed", seconds_remaining=0))
        await hass.async_block_till_done()
        notify.assert_called_once()
        assert "/config/devices/device/" in notify.call_args.args[1]
        await pairing.publish(dict(value, status="confirmed", seconds_remaining=0))
        assert await hass.config_entries.async_reload(pairing.gateway.entry.entry_id)
        await hass.async_block_till_done()
        notify.assert_called_once()


async def test_disappearance_conflict_unsupported_and_legacy_are_not_success(
    hass, pairing
):
    """Missing or conflicting evidence remains actionable and never creates a paired notification.

    @param hass Actual HA instance.
    @param pairing Native capability and session fixture.
    """
    with patch(
        "custom_components.livolo_ec.commissioning.persistent_notification.async_create"
    ) as notify:
        await pairing.publish(session())
        await pairing.publish(None)
        assert hass.states.get(pairing.sensor).state == "unknown"
        assert (
            hass.states.get(pairing.sensor).attributes["error"]
            == "session_not_reported"
        )
        value = session(session_id="second")
        value["observations"] = [
            {
                "device": "66780945",
                "outputs": None,
                "suffix": "0009",
                "profile": None,
                "reason": "unknown_pairing_suffix",
            }
        ]
        await pairing.publish(value)
        assert (
            "advanced manual setup"
            in hass.states.get(pairing.sensor).attributes["observations"][0][
                "explanation"
            ]
        )
        await pairing.publish(dict(value, status="conflict", error="multiple_devices"))
        assert hass.states.get(pairing.sensor).state == "conflict"
        pairing.data["commissioning_supported"] = False
        await pairing.publish(None)
        assert hass.states.get(pairing.sensor).state == "unsupported"
        assert hass.states.get(pairing.start).state == "unavailable"
        notify.assert_not_called()


async def test_existing_targeted_pairing_and_disconnect_disable_start(hass, pairing):
    """Do not offer overlapping work or pretend disconnected progress is current.

    @param hass Actual HA instance.
    @param pairing Existing native gateway fixture.
    """
    pairing.data["pairing"] = {"device": "46715621", "seconds_remaining": 30}
    await pairing.publish(None)
    assert hass.states.get(pairing.start).state == "unavailable"
    coordinator = pairing.gateway.entry.runtime_data
    coordinator.connected = False
    coordinator.async_update_listeners()
    await hass.async_block_till_done()
    assert hass.states.get(pairing.sensor).state == "unavailable"
    assert hass.states.get(pairing.cancel).state == "unavailable"


async def test_options_attach_without_second_start_with_development_disabled(
    hass, pairing
):
    """Opening the secondary flow observes the current session without creating another one.

    @param hass Actual HA instance.
    @param pairing Active session and loaded options flow.
    """
    await pairing.publish(session())
    with patch.object(pairing.gateway.entry.runtime_data.client, "command") as submit:
        result = await hass.config_entries.options.async_init(
            pairing.gateway.entry.entry_id
        )
        assert "pair_new" in result["menu_options"]
        assert not {"pair", "add", "discover", "development"} & set(
            result["menu_options"]
        )
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"next_step_id": "pair_new"}
        )
        assert result["step_id"] == "pairing_status"
        assert result["description_placeholders"]["session"] == "session-1"
        submit.assert_not_called()


async def test_legacy_daemon_explains_upgrade_without_manual_options(hass, pairing):
    """An older daemon cannot silently reveal manual commissioning in the normal UI.

    @param hass Actual HA instance.
    @param pairing Loaded gateway with its automatic commissioning capability withdrawn.
    """
    pairing.data["commissioning_supported"] = False
    await pairing.publish(None)
    with patch.object(pairing.gateway.entry.runtime_data.client, "command") as submit:
        result = await hass.config_entries.options.async_init(
            pairing.gateway.entry.entry_id
        )
        assert not {"development", "add", "pair", "discover"} & set(
            result["menu_options"]
        )
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"next_step_id": "pair_new"}
        )
        assert result["errors"] == {"base": "commissioning_unsupported"}
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {}
        )
        assert result["errors"] == {"base": "commissioning_unsupported"}
        submit.assert_not_called()


@pytest.mark.parametrize("status", ["listening", "expired"])
async def test_options_distinguish_observed_unknown_profile_from_session_outcome(
    hass, pairing, status
):
    """Unknown announcements explain rejection without claiming an active window has failed.

    @param hass Actual HA instance.
    @param pairing Native commissioning fixture with no registration.
    @param status Listening observation or its later expired session outcome.
    """
    value = session(
        status=status,
        error="unsupported_device_seen" if status == "expired" else None,
        observations=[
            {
                "device": "67181844",
                "outputs": None,
                "suffix": "0002",
                "profile": None,
                "reason": "unknown_pairing_payload",
            }
        ],
    )
    await pairing.publish(session())
    with patch.object(pairing.gateway.entry.runtime_data.client, "command") as submit:
        result = await hass.config_entries.options.async_init(
            pairing.gateway.entry.entry_id
        )
        pairing.data["commissioning"] = value
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"next_step_id": "pair_new"}
        )
        fields = result["description_placeholders"]
        assert fields["status"] == status
        assert fields["registered"] == "No" and fields["outputs"] == "Not detected"
        assert "Unrecognized pairing signature" in fields["observations"]
        if status == "listening":
            assert fields["reason"] == "Still in progress; no session error reported."
        else:
            assert fields["reason"].startswith("Only unsupported announcements")
        strings = json.loads(
            (
                Path(__file__).resolve().parents[2]
                / "custom_components/livolo_ec/strings.json"
            ).read_text()
        )
        description = strings["options"]["step"]["pairing_status"][
            "description"
        ].format(**fields)
        assert "None" not in description and ".." not in description
        assert (
            "Session result:" in description
            and "Observed announcements:" in description
        )
        submit.assert_not_called()


async def test_options_explicit_start_refresh_and_cancel(hass, pairing):
    """Disabling Development during automatic pairing preserves observation and cancellation.

    @param hass Actual HA instance.
    @param pairing Loaded controls with controllable daemon replies.
    """
    await set_development(hass, pairing.gateway.entry, True)

    async def command(action, payload):
        """Publish the daemon state corresponding to the submitted action.

        @param action Explicit commission or cancellation action.
        @param payload Stable request and session identities.
        """
        if action == "commission":
            pairing.data["commissioning"] = session(session_id=payload["request_id"])
        else:
            pairing.data["commissioning"]["status"] = "cancelled"
        return {"status": "confirmed", "request_id": payload["request_id"]}

    with patch.object(
        pairing.gateway.entry.runtime_data.client, "command", side_effect=command
    ) as submit:
        result = await hass.config_entries.options.async_init(
            pairing.gateway.entry.entry_id
        )
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"next_step_id": "pair_new"}
        )
        assert result["step_id"] == "pair_new"
        submit.assert_not_called()
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {}
        )
        assert result["description_placeholders"]["status"] == "listening"
        assert submit.await_count == 1
        await set_development(hass, pairing.gateway.entry, False)
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {}
        )
        assert submit.await_count == 1
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"stop": True}
        )
        assert result["description_placeholders"]["status"] == "cancelled"
        assert submit.await_count == 2
        assert submit.call_args.args[0] == "commission_cancel"


async def test_replaced_and_malformed_sessions_never_notify(hass, pairing):
    """Replacement records unknown prior outcome; malformed confirmation fails closed.

    @param hass Actual HA instance.
    @param pairing Shared session push fixture.
    """
    with patch(
        "custom_components.livolo_ec.commissioning.persistent_notification.async_create"
    ) as notify:
        await pairing.publish(session(session_id="first"))
        await pairing.publish(session(session_id="second"))
        state = hass.states.get(pairing.sensor)
        assert state.state == "listening"
        assert state.attributes["previous_session_id"] == "first"
        assert state.attributes["previous_outcome"] == "unknown"
        await pairing.publish(session(status="confirmed", session_id="second"))
        assert hass.states.get(pairing.sensor).state == "invalid"
        assert hass.states.get(pairing.start).state == "unavailable"
        notify.assert_not_called()


async def test_unresolved_start_can_be_resolved_by_read_only_result(hass, pairing):
    """An original terminal result releases uncertainty without replay or false notification.

    @param hass Actual HA instance.
    @param pairing Native controls and command client fixture.
    """
    client = pairing.gateway.entry.runtime_data.client
    with (
        patch.object(
            client, "command", side_effect=CommandFailed({"status": "unknown"})
        ) as submit,
        patch.object(client, "result", return_value=None) as lookup,
        patch(
            "custom_components.livolo_ec.commissioning.persistent_notification.async_create"
        ) as notify,
    ):
        with pytest.raises(HomeAssistantError):
            await press(hass, pairing.start)
        ident = submit.call_args.args[1]["request_id"]
        lookup.return_value = {"request_id": ident, "status": "confirmed"}
        await pairing.gateway.entry.runtime_data.commissioning.refresh()
        await hass.async_block_till_done()
        assert hass.states.get(pairing.sensor).state == "unknown"
        assert hass.states.get(pairing.start).state != "unavailable"
        assert submit.await_count == 1
        lookup.assert_awaited_once_with(ident)
        notify.assert_not_called()


@pytest.mark.parametrize("include_evidence", [False, True])
async def test_pairing_evidence_attributes_and_anonymous_download(
    hass, pairing, include_evidence
):
    """Diagnostic evidence survives push without entering the main form or revealing identities.

    @param hass Actual HA instance.
    @param pairing Native session fixture and actual gateway status sensor.
    @param include_evidence Legacy observation or newer bounded diagnostic evidence.
    """
    from custom_components.livolo_ec.diagnostics import (
        async_get_config_entry_diagnostics,
    )

    observation = {
        "device": "67181844",
        "suffix": "0002",
        "reason": "unknown_pairing_payload",
        "raw": "secret-frame",
    }
    if include_evidence:
        observation.update(
            pairing_evidence=[
                {
                    "suffix": "0002",
                    "payload": "0002030534990400",
                    "raw": "secret-packet",
                }
            ],
            pairing_evidence_truncated=False,
        )
    value = session(
        session_id="private-session", name="Private kitchen", observations=[observation]
    )
    await pairing.publish(value)
    attributes = hass.states.get(pairing.sensor).attributes["observations"][0]
    download = await async_get_config_entry_diagnostics(hass, pairing.gateway.entry)
    sanitized = download["commissioning"]["observations"][0]
    assert download["commissioning"]["status"] == "listening"
    assert download["commissioning"]["registered"] is False
    assert (
        sanitized["outputs"] is None
        and sanitized["reason"] == "unknown_pairing_payload"
    )
    if include_evidence:
        assert (
            attributes["pairing_evidence"]
            == sanitized["pairing_evidence"]
            == [{"suffix": "0002", "payload": "0002030534990400"}]
        )
        assert sanitized["pairing_evidence_truncated"] is False
    else:
        assert (
            "pairing_evidence" not in sanitized and "pairing_evidence" not in attributes
        )
    for private in ("67181844", "private-session", "Private kitchen", "secret", "raw"):
        assert private not in json.dumps(download)
    result = await hass.config_entries.options.async_init(
        pairing.gateway.entry.entry_id
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "pair_new"}
    )
    assert "0002030534990400" not in str(result["description_placeholders"])


async def test_malformed_pairing_evidence_download_fails_closed(hass, pairing):
    """Invalid evidence is excluded from diagnostic exports without fabricating success.

    @param hass Actual HA instance.
    @param pairing Session stream carrying untrusted optional metadata.
    """
    from custom_components.livolo_ec.diagnostics import (
        async_get_config_entry_diagnostics,
    )

    await pairing.publish(
        session(
            observations=[
                {
                    "device": "67181844",
                    "pairing_evidence": [{"suffix": "0002", "payload": "secret"}],
                }
            ]
        )
    )
    download = await async_get_config_entry_diagnostics(hass, pairing.gateway.entry)
    assert download["commissioning"] == {"status": "invalid"}
    assert "secret" not in json.dumps(download)
