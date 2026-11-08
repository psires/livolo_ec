"""Keep native firmware guidance useful without turning a report into an installer."""

from copy import deepcopy
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.helpers import device_registry as dr
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.livolo_ec._client import LivoloError
from custom_components.livolo_ec.firmware_presentation import guidance, job_summary


@pytest.mark.parametrize(
    "fields,expected",
    [
        (
            {"status": "pending", "stage": "transfer", "transfer_percent": 100},
            "verification",
        ),
        ({"status": "unknown", "stage": "interrupted"}, "outcome is unknown"),
        (
            {"status": "pending", "recovery": "recovery_required"},
            "Recovery is required",
        ),
        (
            {"status": "confirmed", "stage": "confirmed"},
            "verified the last installation",
        ),
        (
            {"status": "rejected", "stage": "restored_previous"},
            "Previous firmware restored",
        ),
    ],
)
def test_job_outcomes_never_infer_success_from_transfer(fields, expected):
    """Progress, recovery and previous-image restoration retain distinct meanings.

    @param fields Authoritative job fields to render.
    @param expected Required user-facing outcome text.
    """
    text = job_summary(fields)
    assert expected in text
    if fields.get("transfer_percent") == 100:
        assert "Image transfer: 100%" in text
        assert "100% transfer is not confirmation" in text
        assert "verified the last installation" not in text
        assert "Measured overall progress" not in text


@pytest.mark.parametrize("percent", [True, float("nan"), float("inf"), -1, 101, "100"])
def test_unmeasured_progress_and_untrusted_text_are_not_rendered(percent):
    """Invalid measurements and daemon text cannot create links or successful progress.

    @param percent Invalid overall and transfer percentage value.
    """
    text = job_summary(
        {
            "status": "unknown",
            "stage": ["malformed"],
            "request_id": "[private](https://untrusted.invalid)",
            "error": "/private/secret",
            "progress_percent": percent,
            "transfer_percent": percent,
        }
    )
    assert "Measured overall progress" not in text
    assert "Image transfer:" not in text
    assert "https://" not in text and "/private/" not in text
    assert "outcome is unknown" in text


@pytest.mark.parametrize(
    "reason,expected",
    [
        ("artifact_missing", "empty catalog does not mean"),
        ("backend_unavailable", "required flashing helper"),
        ("bootloader_install_required", "separate board-specific procedure"),
        ("signing_key_mismatch", "does not match"),
        ("live_identity_required", "cannot convert a stock adapter"),
        ("unexpected_new_code", "daemon blocked this update"),
    ],
)
def test_blockers_give_a_bounded_next_action(reason, expected):
    """Known prerequisites explain a next action; unknown blockers never offer a bypass.

    @param reason Public daemon blocking code.
    @param expected Useful explanation fragment.
    """
    assert expected in guidance(reason)


async def test_report_links_existing_gateway_and_preserves_job_and_options(
    hass, envelope, preflight_report
):
    """Refresh displays pending and recovery outcomes without mutation or identity changes.

    @param hass Actual HA instance.
    @param envelope Native network snapshot fixture.
    @param preflight_report Advisory compatibility report fixture.
    """
    entry = MockConfigEntry(
        domain="livolo_ec",
        unique_id="2a992b12:a7",
        data={"url": "http://example.invalid:8090", "token": "secret"},
        options={"development": False, "preserve": "existing"},
    )
    entry.add_to_hass(hass)
    registry = dr.async_get(hass)
    device = registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={("livolo_ec", entry.unique_id)},
        name="Existing gateway",
    )
    envelope["data"]["firmware_update"] = {
        "preflight_supported": True,
        "installed_version": "0.12",
        "status": "pending",
        "stage": "transfer",
        "request_id": "existing-job-1",
        "transfer_percent": 100,
    }
    report = deepcopy(preflight_report)
    report.update(executable=False, blocked_reason="update_in_progress")
    with patch("custom_components.livolo_ec.config_flow.Client") as client:
        client.return_value.snapshot = AsyncMock(return_value=envelope)
        client.return_value.firmware_preflight = AsyncMock(return_value=report)
        flow = await hass.config_entries.options.async_init(entry.entry_id)
        result = await hass.config_entries.options.async_configure(
            flow["flow_id"], {"next_step_id": "firmware_preflight"}
        )
        values = result["description_placeholders"]
        assert f"/config/devices/device/{device.id}" in values["gateway_link"]
        assert "Image transfer: 100%" in values["job"]
        assert "existing-job-1" in values["job"]
        assert "do not start another" in values["guidance"]
        envelope["data"]["firmware_update"].update(
            status="unknown", recovery="recovery_required", stage="interrupted"
        )
        report.update(blocked_reason="recovery_required", installed_version=None)
        result = await hass.config_entries.options.async_configure(
            flow["flow_id"], {"refresh": True}
        )
        values = result["description_placeholders"]
        assert "Recovery is required" in values["job"]
        assert values["last_installed"] == "0.12"
        assert values["installed"] == "Unknown"
        assert "reviewed recovery procedure" in values["guidance"]
        result = await hass.config_entries.options.async_configure(flow["flow_id"], {})
        assert result["data"] == {"development": False, "preserve": "existing"}
        assert registry.async_get(device.id) is device
        client.return_value.command.assert_not_called()
        client.return_value.install_firmware.assert_not_called()


@pytest.mark.parametrize("failure", ["snapshot", "firmware_preflight"])
async def test_failed_refresh_does_not_reuse_successful_preflight(
    hass, envelope, preflight_report, failure
):
    """Connection loss replaces prior eligibility with unknown without submitting work.

    @param hass Actual HA instance.
    @param envelope Native snapshot fixture.
    @param preflight_report Successful report before the connection fails.
    @param failure Read operation that loses its response.
    """
    entry = MockConfigEntry(
        domain="livolo_ec",
        unique_id="2a992b12:a7",
        data={"url": "http://example.invalid:8090", "token": "secret"},
    )
    entry.add_to_hass(hass)
    envelope["data"]["firmware_update"] = {"preflight_supported": True}
    with patch("custom_components.livolo_ec.config_flow.Client") as client:
        client.return_value.snapshot = AsyncMock(return_value=envelope)
        client.return_value.firmware_preflight = AsyncMock(
            return_value=preflight_report
        )
        flow = await hass.config_entries.options.async_init(entry.entry_id)
        result = await hass.config_entries.options.async_configure(
            flow["flow_id"], {"next_step_id": "firmware_preflight"}
        )
        assert result["description_placeholders"]["executable"] == "Yes"
        assert (
            "/config/devices/device/"
            not in result["description_placeholders"]["gateway_link"]
        )
        getattr(client.return_value, failure).side_effect = LivoloError("offline")
        result = await hass.config_entries.options.async_configure(
            flow["flow_id"], {"refresh": True}
        )
        assert result["errors"] == {"base": "cannot_connect"}
        assert result["description_placeholders"]["executable"] == "Unknown"
        assert result["description_placeholders"]["target"] == "Unknown"
        client.return_value.command.assert_not_called()
        client.return_value.install_firmware.assert_not_called()
