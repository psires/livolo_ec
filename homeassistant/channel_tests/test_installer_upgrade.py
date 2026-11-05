"""Exercise upstream installers, actual HTTP ZIP transfers and existing HA registries."""

import asyncio
from dataclasses import replace
import importlib
import json
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from aiogithubapi import GitHubAPI
import pytest

if os.environ.get("LIVOLO_INSTALLER", "hacs") == "hacs":
    from pytest_homeassistant_custom_component.common import MockConfigEntry
else:
    from pytest_homeassistant.common import MockConfigEntry
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers import area_registry as ar, entity_registry as er

from livolod.config import load
from livolod.native_api import NativeAPI
from livolod.service import Service
from livolod.simulator import Simulator
from livolod.storage import Store

ROOT = Path(__file__).resolve().parents[2]
BASELINE = os.environ.get("LIVOLO_BASELINE_VERSION", "0.2.1")
CURRENT = os.environ.get("LIVOLO_CURRENT_VERSION", "0.3.0")
pytestmark = pytest.mark.usefixtures(
    "socket_enabled", "disable_mock_zeroconf_resolver", "public_github_network"
)


async def eventually(predicate):
    """Await an asynchronous state transition with a short deterministic limit.

    @param predicate Side-effect-free readiness condition.
    """
    async with asyncio.timeout(5):
        while not predicate():
            await asyncio.sleep(0.01)


async def test_real_installer_preserves_config_entities_and_daemon(hass, tmp_path):
    """An upstream ZIP install and upgrade retain configured entities and daemon state.

    @param hass Isolated real Home Assistant instance.
    @param tmp_path Private registry and installation directory.
    """
    hass.config.config_dir = str(tmp_path / "ha_config")
    Path(hass.config.path("custom_components")).mkdir(parents=True)
    backend = os.environ.get("LIVOLO_INSTALLER", "hacs")
    if backend == "hacs":
        from custom_components.hacs.base import HacsBase
        from custom_components.hacs.repositories.base import HacsManifest
        from custom_components.hacs.repositories.integration import (
            HacsIntegrationRepository,
        )

        manager = HacsBase()
        manager.hass = hass
        manager.core.config_path = hass.config.path()
        manager.session = async_get_clientsession(hass)
        manager.githubapi = GitHubAPI(token="", session=manager.session)
        repository = HacsIntegrationRepository(manager, "psires/ha-livolo-ec")
        repository.repository_manifest = HacsManifest.from_dict(
            {"name": "Livolo EC", "zip_release": True, "filename": "livolo_ec.zip"}
        )
    else:
        from homeassistant.components.lovelace import LOVELACE_DATA
        from homeassistant.components.marketplace.base import MarketplaceManager
        from homeassistant.components.marketplace.repositories.base import (
            RepositoryManifest,
        )
        from homeassistant.components.marketplace.repositories.integration import (
            IntegrationRepository,
        )

        hass.data[LOVELACE_DATA] = SimpleNamespace(resource_mode="storage")
        manager = MarketplaceManager(
            hass, MockConfigEntry(domain="marketplace", data={})
        )
        repository = IntegrationRepository(manager, "psires/ha-livolo-ec")
        repository.repository_manifest = RepositoryManifest.from_dict(
            {"name": "Livolo EC", "zip_release": True, "filename": "livolo_ec.zip"}
        )
    repository.data.domain = "livolo_ec"
    repository.data.id = "channel-validation"
    repository.data.default_branch = "main"
    repository.data.config_flow = True
    releases = await repository.async_get_releases()
    stable = [item for item in releases if not item.draft and not item.prerelease]
    assert [item.tag_name for item in stable[:2]] == [CURRENT, BASELINE]
    repository.data.last_version = stable[0].tag_name
    repository.content.path.local = repository.localpath
    repository.content.path.remote = "custom_components/livolo_ec"
    config = replace(
        load(ROOT / "host/config.example.json"),
        database=str(tmp_path / "state.sqlite3"),
        mqtt={"enabled": False},
        output_mode="experimental_phase",
    )
    store = Store(config.database, config.gateway + ":" + config.tag)
    service = Service(config, store, gateway_factory=Simulator, simulated=True)
    api = NativeAPI(service, token="isolated-installer-test-token", port=0)
    await api.start()
    radio_task = asyncio.create_task(service.run())
    entry = MockConfigEntry(
        domain="livolo_ec",
        unique_id=config.gateway + ":" + config.tag,
        data={
            "url": f"http://127.0.0.1:{api.port}",
            "token": "isolated-installer-test-token",
            "allow_simulated": True,
        },
    )
    entry.add_to_hass(hass)
    original_entry_id = entry.entry_id
    original_data = dict(entry.data)
    loaded = False
    namespace = importlib.import_module("custom_components")
    original_paths = list(namespace.__path__)
    namespace.__path__ = [
        hass.config.path("custom_components"),
        *[
            path
            for path in namespace.__path__
            if Path(path).resolve() != ROOT / "custom_components"
        ],
    ]
    try:
        await eventually(lambda: service.ready)
        from homeassistant.loader import async_get_custom_components

        await async_get_custom_components(hass)
        with patch.object(repository, "update_repository", new=AsyncMock()):
            for version in (BASELINE, CURRENT):
                if backend == "hacs":
                    await repository.async_install(version=version)
                else:
                    await repository._async_install_via_github_api(version=version)
                assert repository.data.installed_version == version
                manifest = json.loads(
                    (Path(repository.localpath) / "manifest.json").read_text()
                )
                assert manifest["version"] == version
                assert not (Path(repository.localpath) / "custom_components").exists()
                if version == BASELINE:
                    assert repository.pending_update
                    importlib.import_module("custom_components.livolo_ec")
                    assert await hass.config_entries.async_setup(entry.entry_id)
                    loaded = True
                    await eventually(
                        lambda: len(hass.states.async_all("switch")) == 2
                        and all(
                            s.state == "off" for s in hass.states.async_all("switch")
                        )
                    )
                    registry = er.async_get(hass)
                    switches = hass.states.async_all("switch")
                    target = switches[0].entity_id
                    area = ar.async_get(hass).async_create("Keep my area")
                    registry.async_update_entity(
                        target, name="Keep my name", area_id=area.id
                    )
                    identities = {
                        s.entity_id: registry.async_get(s.entity_id).unique_id
                        for s in switches
                    }
                    await hass.services.async_call(
                        "switch", "turn_on", {"entity_id": target}, blocking=True
                    )
                    await eventually(lambda: hass.states.get(target).state == "on")
                    bits = service.states["46715621"]["state_bits"]
                else:
                    assert (
                        entry.entry_id == original_entry_id
                        and dict(entry.data) == original_data
                    )
                    assert {
                        s.entity_id: registry.async_get(s.entity_id).unique_id
                        for s in hass.states.async_all("switch")
                    } == identities
                    assert registry.async_get(target).name == "Keep my name"
                    assert registry.async_get(target).area_id == area.id
                    assert service.states["46715621"]["state_bits"] == bits
                    assert service.ready and service.broker is None
                    assert repository.pending_restart
                    assert await hass.config_entries.async_reload(entry.entry_id)
                    await eventually(lambda: hass.states.get(target).state == "on")
        assert not repository.pending_update
    finally:
        if loaded:
            await hass.config_entries.async_unload(entry.entry_id)
        service.stop.set()
        await asyncio.wait_for(radio_task, 3)
        await api.close()
        store.close()
        namespace.__path__ = original_paths
