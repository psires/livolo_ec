"""Test the assembled distribution against real Home Assistant APIs."""

import atexit
import importlib.util
from pathlib import Path
import shutil
import sys
import tempfile

import pytest

ROOT = Path(__file__).resolve().parents[2]
STAGE = Path(tempfile.mkdtemp(prefix="livolo-ha-test-"))
spec = importlib.util.spec_from_file_location(
    "livolo_build", ROOT / "homeassistant/build_release.py"
)
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)
builder.stage_integration(STAGE)
sys.path.insert(0, str(STAGE))
sys.path.insert(0, str(ROOT / "client"))
sys.path.insert(0, str(ROOT / "host"))
atexit.register(shutil.rmtree, STAGE, ignore_errors=True)
# Load the assembled namespace before HA's test import mocks are installed.
# Standalone end-to-end runs must not depend on another test module importing it.
importlib.import_module("custom_components.livolo_ec")


@pytest.fixture(autouse=True)
def custom_integrations(enable_custom_integrations):
    """Enable loading of the assembled custom integration.

    @param enable_custom_integrations HA plugin fixture enabling custom code.
    """


@pytest.fixture
def preflight_report():
    """Supply a sanitized successful advisory report for transport and HA tests."""
    return {
        "schema": 1,
        "operation": "update",
        "candidate_id": "candidate-1",
        "board": "zb-gw04-1v2",
        "installed_version": "0.12",
        "target_version": "0.13",
        "compatible": True,
        "executable": True,
        "identity_source": "live_session",
        "checks": [
            {"id": ident, "status": "pass", "code": "compatible"}
            for ident in (
                "artifact",
                "expected_board",
                "helper",
                "live_identity",
                "board",
                "protocol",
                "baudrate",
                "version",
            )
        ],
        "blocked_reason": None,
        "requires_manual_action": False,
        "rollback_supported": False,
    }


@pytest.fixture
def envelope():
    """Supply a two-channel authoritative snapshot with no fabricated reports."""
    return {
        "type": "snapshot",
        "api_version": 1,
        "epoch": "epoch-one",
        "sequence": 1,
        "data": {
            "gateway": "2a992b12",
            "tag": "a7",
            "ready": True,
            "controls_enabled": True,
            "simulated": False,
            "diagnostics": {},
            "devices": [
                {
                    "id": "46715621",
                    "name": "Kitchen",
                    "outputs": 2,
                    "availability": "online",
                    "state": {"bits": 1},
                }
            ],
        },
    }
