"""Real daemon simulator -> authenticated API -> assembled HA integration."""

import asyncio
from dataclasses import replace
from pathlib import Path
import time

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from livolod.config import load
from livolod.native_api import NativeAPI
from livolod.service import Service
from livolod.simulator import Simulator
from livolod.storage import Store

pytestmark = pytest.mark.usefixtures("socket_enabled")


async def eventually(predicate, timeout=5):
    """Wait for asynchronous work with a bounded real-time deadline.

    @param predicate Side-effect-free completion condition.
    @param timeout Maximum seconds to wait.
    """
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() >= deadline:
            raise AssertionError("Condition did not become true")
        await asyncio.sleep(0.01)


async def test_broker_free_native_control(hass, tmp_path):
    """Native entities control/report independently while HA unload leaves the daemon ready.

    @param hass Actual HA instance.
    @param tmp_path Isolated database directory.
    """
    root = Path(__file__).resolve().parents[2]
    config = replace(
        load(root / "host/config.example.json"),
        database=str(tmp_path / "state.db"),
        mqtt={"enabled": False},
        output_mode="experimental_phase",
    )
    store = Store(config.database, config.gateway + ":" + config.tag)
    service = Service(config, store, gateway_factory=Simulator, simulated=True)
    api = NativeAPI(service, token="local-test-secret", port=0)
    await api.start()
    radio_task = asyncio.create_task(service.run())
    config_entry = MockConfigEntry(
        domain="livolo_ec",
        title="Simulated Livolo",
        unique_id=config.gateway + ":" + config.tag,
        data={
            "url": f"http://127.0.0.1:{api.port}",
            "token": "local-test-secret",
            "allow_simulated": True,
        },
    )
    config_entry.add_to_hass(hass)
    loaded = False
    try:
        await eventually(lambda: service.ready)
        assert await hass.config_entries.async_setup(config_entry.entry_id)
        loaded = True
        await eventually(
            lambda: len(hass.states.async_all("switch")) == 2
            and all(s.state == "off" for s in hass.states.async_all("switch"))
        )
        first = next(
            s for s in hass.states.async_all("switch") if s.name.endswith("Output 1")
        )
        second = next(
            s for s in hass.states.async_all("switch") if s.name.endswith("Output 2")
        )
        await hass.services.async_call(
            "switch", "turn_on", {"entity_id": second.entity_id}, blocking=True
        )
        await eventually(lambda: hass.states.get(second.entity_id).state == "on")
        assert hass.states.get(first.entity_id).state == "off"
        assert service.states["46715621"]["state_bits"] == 2
        # Model a physical press using a native simulator report, with no API command.
        service.gateway.report("46715621", bits=3, mask=1)
        await eventually(lambda: hass.states.get(first.entity_id).state == "on")
        assert service.broker is None
        assert await hass.config_entries.async_unload(config_entry.entry_id)
        loaded = False
        assert service.ready and not radio_task.done()
    finally:
        if loaded:
            await hass.config_entries.async_unload(config_entry.entry_id)
        service.stop.set()
        await asyncio.wait_for(radio_task, 3)
        await api.close()
        store.close()
