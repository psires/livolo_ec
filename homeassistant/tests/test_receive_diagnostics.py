"""Download receive counters without raw traffic, identities or inferred radio health."""

from copy import deepcopy
import json
from types import SimpleNamespace

import pytest

from custom_components.livolo_ec.diagnostics import async_get_config_entry_diagnostics


async def download(hass, diagnostics):
    """Exercise HA's download entry point using a snapshot without any network client.

    @param hass Actual HA test instance.
    @param diagnostics Optional native diagnostic mapping, including malformed fixtures.
    @return Downloaded diagnostic data, after checking the input snapshot is unchanged.
    """
    data = dict(
        ready=True,
        controls_enabled=False,
        simulated=False,
        devices=[],
        diagnostics=diagnostics,
    )
    before = deepcopy(data)
    entry = SimpleNamespace(runtime_data=SimpleNamespace(connected=True, data=data))
    result = await async_get_config_entry_diagnostics(hass, entry)
    assert data == before
    return result


async def test_legacy_receive_counters_are_not_reclassified(hass):
    """Old aggregates survive unchanged without invented category counts or a radio-health verdict.

    @param hass Actual HA test instance.
    """
    counters = dict(invalid_rx=146, rx_gaps=0, ack_failures=0)
    result = await download(hass, counters)
    assert result["receive_counters"] == counters
    assert result["ready"] is True
    assert result["controls_enabled"] is False


async def test_receive_breakdown_preserves_reported_counts_without_recomputing(hass):
    """The fixed reason breakdown survives download independently of the legacy aggregate.

    @param hass Actual HA test instance.
    """
    counters = dict(
        invalid_rx=150,
        rx_gaps=0,
        ack_failures=0,
        rx_rejections=dict(
            envelope=0, unsupported_frame=146, unsupported_family=0, checksum=0, other=0
        ),
    )
    result = await download(hass, counters)
    assert result["receive_counters"] == counters
    # A diagnostic export cannot repair or reinterpret an inconsistent upstream snapshot.
    assert result["receive_counters"]["invalid_rx"] == 150
    assert result["ready"] is True and result["controls_enabled"] is False


@pytest.mark.parametrize(
    "invalid", [True, -1, 1.5, "146", None, [146], {"raw": "private-frame"}]
)
async def test_receive_breakdown_rejects_non_counters_and_private_values(hass, invalid):
    """Every category is filtered without coercion or leaking arbitrary nested metadata.

    @param hass Actual HA test instance.
    @param invalid Invalid value used for each public rejection category.
    """
    result = await download(
        hass,
        dict(
            invalid_rx=7,
            rx_rejections={
                "envelope": invalid,
                "unsupported_frame": invalid,
                "unsupported_family": invalid,
                "checksum": invalid,
                "other": invalid,
                "raw": "private-frame",
                "device": "67181844",
                "unexpected_numeric": 888,
            },
        ),
    )
    assert result["receive_counters"] == {"invalid_rx": 7, "rx_rejections": {}}
    assert "private" not in json.dumps(result) and "67181844" not in json.dumps(result)


@pytest.mark.parametrize("group", [None, [], "private-message", 146])
async def test_malformed_rejection_group_is_omitted(hass, group):
    """An invalid optional breakdown cannot replace the valid legacy aggregate.

    @param hass Actual HA test instance.
    @param group Non-mapping value supplied for the optional category group.
    """
    result = await download(hass, dict(invalid_rx=146, rx_rejections=group))
    assert result["receive_counters"] == {"invalid_rx": 146}


async def test_receive_breakdown_does_not_fill_missing_categories(hass):
    """Partial reported categories are preserved without fabricating zero for missing evidence.

    @param hass Actual HA test instance.
    """
    result = await download(
        hass, dict(rx_rejections={"unsupported_frame": 146, "raw": "private-frame"})
    )
    assert result["receive_counters"] == {"rx_rejections": {"unsupported_frame": 146}}


@pytest.mark.parametrize("diagnostics", [None, {}, [], "private-parser-message"])
async def test_missing_or_malformed_diagnostics_do_not_claim_zero(hass, diagnostics):
    """Absent legacy counters and malformed optional groups stay absent instead of claiming success.

    @param hass Actual HA test instance.
    @param diagnostics Missing or malformed optional diagnostic mapping.
    """
    result = await download(hass, diagnostics)
    assert result["receive_counters"] == {}
    assert "private-parser-message" not in json.dumps(result)


@pytest.mark.parametrize(
    "invalid", [True, False, -1, 1.5, "146", None, {"raw": "private-frame"}]
)
async def test_malformed_counter_is_omitted_without_dropping_valid_counters(
    hass, invalid
):
    """Counter values must be actual nonnegative integers, without coercion or nested data exports.

    @param hass Actual HA test instance.
    @param invalid Non-counter value under an allowlisted key.
    """
    result = await download(hass, dict(invalid_rx=invalid, rx_gaps=0, ack_failures=3))
    assert result["receive_counters"] == {"rx_gaps": 0, "ack_failures": 3}
    assert "private-frame" not in json.dumps(result)


async def test_receive_download_omits_unknown_fields_and_private_traffic(hass):
    """A numeric prefix or arbitrary native field cannot bypass the diagnostic export allowlist.

    @param hass Actual HA test instance.
    """
    result = await download(
        hass,
        dict(
            invalid_rx=7,
            rx_gaps=0,
            ack_failures=0,
            rx_private_counter=999,
            invalid_rx_private_counter=888,
            token="private-token",
            raw="private-frame",
            error="private-parser-message",
            device="67181844",
            request_id="private-request",
            serial_path="private-path",
            firmware={"raw": "private-firmware-frame"},
        ),
    )
    assert result["receive_counters"] == {
        "invalid_rx": 7,
        "rx_gaps": 0,
        "ack_failures": 0,
    }
    serialized = json.dumps(result)
    assert "private" not in serialized
    assert "67181844" not in serialized
    assert "999" not in serialized and "888" not in serialized
