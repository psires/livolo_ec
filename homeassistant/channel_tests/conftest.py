"""Keep installer validation separate from source-staged integration unit tests."""

import importlib
import os
from pathlib import Path
import socket
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
async def public_github_network(hass, socket_enabled, disable_mock_zeroconf_resolver):
    """Use the same real DNS answers for HA downloads and the socket allowlist.

    @param hass Isolated HA instance owning the download resolver.
    @param socket_enabled Enable sockets before establishing the narrow allowlist.
    @param disable_mock_zeroconf_resolver Keep resolution on this test's event loop.
    """
    from homeassistant.helpers.aiohttp_client import _async_get_or_create_resolver

    resolver = _async_get_or_create_resolver(hass)
    hosts = ("api.github.com", "github.com", "release-assets.githubusercontent.com")
    answers = {host: await resolver.resolve(host, 443) for host in hosts}
    pytest_socket.socket_allow_hosts(
        ["127.0.0.1", *hosts],
        allow_unix_socket=True,
        resolution_cache={
            host: {item["host"] for item in items} for host, items in answers.items()
        },
    )

    async def resolved(host, port=0, family=socket.AF_INET):
        """Reuse actual DNS answers so independently rotating resolvers cannot disagree.

        @param host One of the explicitly allowed GitHub hosts.
        @param port Requested destination port, retained in each resolved address.
        @param family Requested address family, or AF_UNSPEC for either family.
        """
        if host not in answers:
            raise OSError("Unexpected host in public release test")
        return [
            dict(item, port=port)
            for item in answers[host]
            if family in (socket.AF_UNSPEC, item["family"])
        ]

    with patch.object(resolver, "resolve", side_effect=resolved):
        yield
