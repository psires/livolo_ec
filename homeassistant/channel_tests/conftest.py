"""Keep installer validation separate from source-staged integration unit tests."""

import importlib
import os
from pathlib import Path
import sys
from types import ModuleType
from unittest.mock import patch

import pytest_socket

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "host"))
if os.environ.get("LIVOLO_INSTALLER", "hacs") == "hacs":
    sys.path.insert(
        0, os.environ.get("LIVOLO_HACS_SOURCE", "/tmp/livolo-hacs-channel-upstream")
    )
    importlib.import_module("custom_components.hacs")
else:
    namespace = ModuleType("custom_components")
    namespace.__path__ = []
    sys.modules["custom_components"] = namespace


@pytest.fixture(autouse=True)
def custom_integrations(enable_custom_integrations):
    """Allow the real installer to populate the isolated HA configuration.

    @param enable_custom_integrations HA fixture enabling filesystem integrations.
    """


@pytest.fixture
def public_github_network():
    """Allow public release traffic while preserving HA's isolated test filesystem.

    The upstream HA fixtures block DNS globally; permit resolution only while
    establishing the explicit GitHub and loopback connection allowlist.
    """
    package = (
        "pytest_homeassistant_custom_component"
        if os.environ.get("LIVOLO_INSTALLER", "hacs") == "hacs"
        else "pytest_homeassistant"
    )
    plugins = importlib.import_module(package + ".plugins")
    with patch("socket.getaddrinfo", plugins._real_getaddrinfo):
        pytest_socket.socket_allow_hosts(
            [
                "127.0.0.1",
                "api.github.com",
                "github.com",
                "release-assets.githubusercontent.com",
            ],
            allow_unix_socket=True,
        )
