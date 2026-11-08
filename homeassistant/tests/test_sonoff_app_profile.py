"""Check the explicit experimental Sonoff bootstrap wrapper configuration."""

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location(
    "livolo_sonoff_app", ROOT / "homeassistant/app/run.py"
)
app = importlib.util.module_from_spec(spec)
spec.loader.exec_module(app)


def test_sonoff_bootstrap_is_explicit_and_unconfigured(tmp_path):
    """First-use Sonoff setup remains disarmed and preserves the exact serial selection.

    @param tmp_path Isolated app data directory.
    """
    options = {
        "gateway": "",
        "tag": "",
        "serial_port": "/dev/serial/by-id/sonoff-test",
        "serial_baudrate": 460800,
        "api_token": "s" * 32,
        "web_enabled": False,
        "radio_setup_profile": "sonoff-zbdongle-e",
        "radio_mode": "bootstrap",
    }
    raw = app.configuration(options, tmp_path)
    settings = app.bootstrap_settings(options, raw, tmp_path)
    assert raw["network"] is None and raw["output_mode"] == "disabled"
    assert settings["profile"] == "sonoff-zbdongle-e"
    assert settings["mode"] == "bootstrap" and settings["network"] is None
    assert "/dev/serial/by-id/sonoff-test" in settings["port_aliases"]
    assert settings["loader_policy"] is None


def test_sonoff_requires_explicit_bootstrap_for_empty_network(tmp_path):
    """An unconfigured dongle cannot accidentally start as an existing gateway.

    @param tmp_path Isolated app data directory.
    """
    with pytest.raises(ValueError, match="requires bootstrap"):
        app.configuration(
            {
                "gateway": "",
                "tag": "",
                "serial_port": "/dev/test",
                "serial_baudrate": 460800,
                "api_token": "s" * 32,
                "radio_setup_profile": "sonoff-zbdongle-e",
                "radio_mode": "gateway",
            },
            tmp_path,
        )
