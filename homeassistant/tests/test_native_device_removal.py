"""Exercise HA's actual Delete endpoint against a durable simulator-backed daemon."""

import asyncio
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import pytest
from homeassistant.components.config import device_registry as device_api
from homeassistant.helpers import device_registry as dr, entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from livolod.config import load
from livolod.native_api import NativeAPI
from livolod.service import Service
from livolod.simulator import Simulator
from livolod.storage import Store

from test_end_to_end import eventually

pytestmark = pytest.mark.usefixtures("socket_enabled")


async def test_native_delete_removes_only_selected_peer_and_survives_reload(
    hass, hass_ws_client, tmp_path
):
    """Delete traverses HA, the authenticated API and durable registry before unlinking.

    @param hass Isolated real HA instance.
    @param hass_ws_client Authenticated HA WebSocket test client factory.
    @param tmp_path Private database directory; the radio is always a simulator.
    """
    base = load(Path(__file__).resolve().parents[2] / "host/config.example.json")
    survivor = replace(
        base.devices[0],
        id="66780945",
        name="Keep switch",
        slot=1,
        outputs=1,
        suffix="0001",
    )
    config = replace(
        base,
        database=str(tmp_path / "remove.db"),
        devices=(*base.devices, survivor),
        mqtt={"enabled": False},
        web={"enabled": False},
    )
    store = Store(config.database, config.gateway + ":" + config.tag)
    service = Service(config, store, gateway_factory=Simulator, simulated=True)
    api = NativeAPI(service, token="offline-removal-token", port=0)
    await api.start()
    task = asyncio.create_task(service.run())
    entry = MockConfigEntry(
        domain="livolo_ec",
        unique_id=config.gateway + ":" + config.tag,
        data={
            "url": f"http://127.0.0.1:{api.port}",
            "token": "offline-removal-token",
            "allow_simulated": True,
        },
        options={"preserve": True},
    )
    entry.add_to_hass(hass)
    loaded = False
    try:
        await eventually(lambda: service.ready)
        assert await hass.config_entries.async_setup(entry.entry_id)
        loaded = True
        await eventually(lambda: len(hass.states.async_all("switch")) == 3)
        registry = er.async_get(hass)
        devices = dr.async_get(hass)
        target = devices.async_get_device_by_identifier(
            ("livolo_ec", entry.unique_id + ":46715621"), entry.entry_id
        )
        keep = devices.async_get_device_by_identifier(
            ("livolo_ec", entry.unique_id + ":66780945"), entry.entry_id
        )
        gateway_id = entry.runtime_data.gateway_device_id
        original_data = dict(entry.data)
        kept_entities = {
            entity.entity_id: entity.unique_id
            for entity in registry.entities.values()
            if entity.device_id == keep.id
        }
        client = await hass_ws_client(hass)
        device_api.async_setup(hass)
        with patch.object(
            service, "manage_registry", wraps=service.manage_registry
        ) as manage:
            result = await client.remove_device(target.id)
            assert result["success"] is True
            await hass.async_block_till_done()
            manage.assert_called_once()
            assert manage.call_args.args[1:] == ("remove", {"device": "46715621"})
        assert devices.async_get(target.id) is None
        assert not any(
            entity.device_id == target.id for entity in registry.entities.values()
        )
        assert devices.async_get(gateway_id) is not None
        assert devices.async_get(keep.id) is not None
        assert set(service.devices) == {"66780945"}
        assert store.removed_devices() == ["46715621"]
        assert "46715621" not in store.states()
        assert entry.runtime_data.data["devices"][0]["id"] == "66780945"
        await eventually(lambda: service.ready)
        assert await hass.config_entries.async_reload(entry.entry_id)
        await hass.async_block_till_done()
        assert len(hass.states.async_all("switch")) == 1
        assert {
            entity.entity_id: entity.unique_id
            for entity in registry.entities.values()
            if entity.device_id == keep.id
        } == kept_entities
        assert entry.data == original_data and entry.options == {"preserve": True}
        assert service.firmware_manager.job is None and service.broker is None
    finally:
        if loaded:
            await hass.config_entries.async_unload(entry.entry_id)
        service.stop.set()
        await asyncio.wait_for(task, 3)
        # ARM schedules reports after 50 ms; drain them after the simulator disarms.
        await asyncio.sleep(0.06)
        await api.close()
        store.close()
    # Original config still names the removed peer; reopening must not seed it again.
    reopened = Store(config.database, config.gateway + ":" + config.tag)
    try:
        assert {device.id for device in reopened.devices(config.devices)} == {
            "66780945"
        }
        assert reopened.removed_devices() == ["46715621"]
    finally:
        reopened.close()
