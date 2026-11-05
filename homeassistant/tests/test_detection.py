"""Native HA commissioning reviews daemon evidence before explicit registration."""

from copy import deepcopy
from unittest.mock import AsyncMock, patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.livolo_ec._client import AuthenticationError, CommandFailed


@pytest.fixture
def detection(envelope):
    """Provide one unregistered switch with a daemon-issued profile token.

    @param envelope Valid native snapshot fixture.
    """
    envelope["data"]["discoveries"] = [
        {
            "device": "66780945",
            "configured": False,
            "profile": {
                "status": "detected",
                "outputs": 1,
                "suffix": "0001",
                "token": "review-one",
                "experimental": True,
            },
        }
    ]
    return envelope


async def start_detection(hass):
    """Open the experimental discovery form through the real HA options manager.

    @param hass Actual Home Assistant test instance.
    @return Initial discovery form.
    """
    entry = MockConfigEntry(
        domain="livolo_ec",
        unique_id="2a992b12:a7",
        data={"url": "http://example.invalid:8090", "token": "test-secret"},
    )
    entry.add_to_hass(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    return await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "discover"}
    )


@pytest.mark.parametrize("outputs,suffix", [(1, "0001"), (2, "0002")])
async def test_review_and_explicit_registration(hass, detection, outputs, suffix):
    """Both supported profiles submit only the reviewed token, without pairing.

    @param hass Actual HA test instance.
    @param detection Snapshot with an eligible unregistered switch.
    @param outputs Server-classified output count.
    @param suffix Server-classified wire suffix.
    """
    detection["data"]["discoveries"][0]["profile"].update(
        outputs=outputs, suffix=suffix
    )
    with patch("custom_components.livolo_ec.config_flow.Client") as client:
        client.return_value.snapshot = AsyncMock(return_value=detection)
        client.return_value.command = AsyncMock()
        result = await start_detection(hass)
        assert result["step_id"] == "discover"
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {}
        )
        assert result["step_id"] == "discover"
        client.return_value.command.assert_not_awaited()
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"device": "66780945"}
        )
        assert result["step_id"] == "detected"
        assert result["description_placeholders"] == {
            "device": "66780945",
            "outputs": str(outputs),
            "suffix": suffix,
        }
        client.return_value.command.assert_not_awaited()
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"name": "Hall"}
        )
        assert result["type"] == "create_entry"
        client.return_value.command.assert_awaited_once_with(
            "add",
            {
                "device": "66780945",
                "name": "Hall",
                "auto_detect": True,
                "profile_token": "review-one",
            },
        )


@pytest.mark.parametrize(
    "change",
    [
        {"status": "stale"},
        {"status": "ambiguous"},
        {"status": "unknown"},
        {"token": "review-two"},
        {"outputs": 2},
        {"suffix": "0002"},
        "registered",
        "disconnected",
        "missing",
    ],
)
async def test_changed_evidence_requires_reselection(hass, detection, change):
    """A reviewed profile cannot silently change before its registration is submitted.

    @param hass Actual HA test instance.
    @param detection Snapshot with eligible pairing evidence.
    @param change Evidence or readiness transition after review.
    """
    with patch("custom_components.livolo_ec.config_flow.Client") as client:
        client.return_value.snapshot = AsyncMock(return_value=detection)
        client.return_value.command = AsyncMock()
        result = await start_detection(hass)
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"device": "66780945"}
        )
        candidate = detection["data"]["discoveries"][0]
        if isinstance(change, dict):
            candidate["profile"].update(change)
        elif change == "registered":
            candidate["configured"] = True
        elif change == "disconnected":
            detection["data"]["ready"] = False
        else:
            detection["data"]["discoveries"] = []
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"name": "Hall"}
        )
        assert result["step_id"] == "discover"
        assert result["errors"]["base"] == "detection_changed"
        client.return_value.command.assert_not_awaited()


@pytest.mark.parametrize("status", [None, "unknown", "ambiguous", "stale"])
async def test_unsupported_profiles_and_manual_fallback(hass, detection, status):
    """Unsupported and old-daemon observations stay visible and permit manual setup.

    @param hass Actual HA test instance.
    @param detection Snapshot containing an unregistered observation.
    @param status Unsupported profile status, or None for a legacy daemon.
    """
    candidate = detection["data"]["discoveries"][0]
    if status is None:
        candidate.pop("profile")
    else:
        candidate["profile"]["status"] = status
    with patch("custom_components.livolo_ec.config_flow.Client") as client:
        client.return_value.snapshot = AsyncMock(return_value=detection)
        client.return_value.command = AsyncMock()
        result = await start_detection(hass)
        assert result["description_placeholders"]["count"] == "1"
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"device": "66780945"}
        )
        assert result["errors"]["base"] == "detection_unavailable"
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"manual": True}
        )
        assert result["step_id"] == "add"
        result = await hass.config_entries.options.async_configure(
            result["flow_id"],
            {"device": "66780945", "name": "Manual", "outputs": 1, "suffix": "0001"},
        )
        assert result["type"] == "create_entry"
        client.return_value.command.assert_awaited_once_with(
            "add",
            {
                "device": "66780945",
                "name": "Manual",
                "outputs": 1,
                "suffix": "0001",
            },
        )


async def test_empty_refresh_and_registered_exclusion(hass, detection):
    """An empty list can refresh and a registered device cannot be selected again.

    @param hass Actual HA test instance.
    @param detection Snapshot to populate after opening an empty discovery form.
    """
    empty = deepcopy(detection)
    empty["data"].pop("discoveries")
    with patch("custom_components.livolo_ec.config_flow.Client") as client:
        client.return_value.snapshot = AsyncMock(return_value=empty)
        result = await start_detection(hass)
        assert result["description_placeholders"]["count"] == "0"
        client.return_value.snapshot.return_value = detection
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {}
        )
        assert result["description_placeholders"]["count"] == "1"
        detection["data"]["devices"].append({"id": "66780945", "outputs": 1})
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {}
        )
        assert result["description_placeholders"]["count"] == "0"


@pytest.mark.parametrize("status", ["rejected", "unknown"])
async def test_failed_add_never_pairs_or_retries(hass, detection, status):
    """Failed or uncertain registration requires a new selection and never opens pairing.

    @param hass Actual HA test instance.
    @param detection Snapshot with fresh supported evidence.
    @param status Daemon rejection or uncertain durable command result.
    """
    with patch("custom_components.livolo_ec.config_flow.Client") as client:
        client.return_value.snapshot = AsyncMock(return_value=detection)
        client.return_value.command = AsyncMock(
            side_effect=CommandFailed({"status": status})
        )
        result = await start_detection(hass)
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"device": "66780945"}
        )
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"name": "Hall"}
        )
        assert result["step_id"] == "discover"
        assert result["errors"]["base"] == "detection_failed"
        await hass.config_entries.options.async_configure(result["flow_id"], {})
        assert client.return_value.command.await_count == 1


async def test_auth_failure_still_allows_manual_form(hass):
    """Discovery failure remains recoverable without presenting fabricated candidates.

    @param hass Actual HA test instance.
    """
    with patch("custom_components.livolo_ec.config_flow.Client") as client:
        client.return_value.snapshot = AsyncMock(side_effect=AuthenticationError())
        result = await start_detection(hass)
        assert result["errors"]["base"] == "invalid_auth"
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"manual": True}
        )
        assert result["step_id"] == "add"
