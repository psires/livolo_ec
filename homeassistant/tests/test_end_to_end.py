"""Real daemon simulator -> authenticated API -> assembled HA integration."""

import asyncio
from dataclasses import replace
from pathlib import Path
import time
from unittest.mock import patch

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


async def test_firmware_job_through_native_api_and_ha(hass, tmp_path):
    """HA awaits a durable firmware result through real HTTP and push snapshots.

    @param hass Actual HA instance.
    @param tmp_path Isolated command ledger; no firmware payload or radio is opened.
    """
    root = Path(__file__).resolve().parents[2]
    config = replace(
        load(root / "host/config.example.json"),
        database=str(tmp_path / "firmware.db"),
        mqtt={"enabled": False},
    )
    store = Store(config.database, config.gateway + ":" + config.tag)
    service = Service(config, store, simulated=True)
    firmware = {
        "board": "zb-gw04-1v2",
        "installed_version": "0.12",
        "latest_version": "0.13",
        "candidate_id": "a" * 96 + ":" + "b" * 64,
        "install_supported": True,
        "in_progress": False,
        "progress_percent": None,
    }
    submissions = []
    accepted = asyncio.Event()

    def submit(payload):
        """Model worker acceptance using the real durable ledger, without flashing.

        @param payload Exact candidate and request identifiers from the native route.
        @return Persisted pending outcome for the same job ID.
        """
        assert payload["candidate_id"] == firmware["candidate_id"]
        submissions.append(payload)
        request_id = payload["request_id"]
        store.begin(request_id, dict(action="firmware_update", **payload))
        firmware.update(request_id=request_id, in_progress=True, stage="transfer")
        accepted.set()
        return service.result(request_id, "pending", action="firmware_update")

    api = NativeAPI(service, token="firmware-test-token", port=0)
    loaded = False
    task = None
    with (
        patch.object(service, "start_firmware_update", side_effect=submit),
        patch.object(
            service, "firmware_update_snapshot", side_effect=lambda: dict(firmware)
        ),
    ):
        await api.start()
        entry = MockConfigEntry(
            domain="livolo_ec",
            title="Firmware test",
            unique_id=config.gateway + ":" + config.tag,
            data={
                "url": f"http://127.0.0.1:{api.port}",
                "token": "firmware-test-token",
                "allow_simulated": True,
            },
        )
        entry.add_to_hass(hass)
        try:
            assert await hass.config_entries.async_setup(entry.entry_id)
            loaded = True
            await eventually(
                lambda: hass.states.async_all("update")
                and hass.states.async_all("update")[0].state == "on"
            )
            entity_id = hass.states.async_all("update")[0].entity_id
            task = asyncio.create_task(
                hass.services.async_call(
                    "update", "install", {"entity_id": entity_id}, blocking=True
                )
            )
            await asyncio.wait_for(accepted.wait(), 3)
            await eventually(
                lambda: hass.states.get(entity_id).attributes.get("stage") == "transfer"
            )
            assert not task.done()
            assert hass.states.get(entity_id).attributes["in_progress"]
            assert not service.ready
            firmware.update(
                installed_version="0.13", in_progress=False, stage="confirmed"
            )
            service.result(
                submissions[0]["request_id"], "confirmed", action="firmware_update"
            )
            await asyncio.wait_for(task, 3)
            await eventually(lambda: hass.states.get(entity_id).state == "off")
            assert len(submissions) == 1
            assert (
                store.command_result(submissions[0]["request_id"])["status"]
                == "confirmed"
            )
        finally:
            if task and not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            if loaded:
                await hass.config_entries.async_unload(entry.entry_id)
            await api.close()
            store.close()


@pytest.mark.parametrize(
    "device,outputs,raw",
    [
        (
            "66780945",
            1,
            "018825ffffffff667809450000000000000000ff009e01667809450001000101053401040014ad",
        ),
        (
            "46715621",
            2,
            "01880effffffff467156210000000000000000ff009e014671562100020002030534010400ffc6",
        ),
    ],
)
async def test_detected_registration_through_native_api(
    hass, tmp_path, device, outputs, raw
):
    """Captured pairing evidence reaches HA review and creates the correct native entities.

    @param hass Actual HA instance.
    @param tmp_path Isolated registry and command ledger directory.
    @param device Wire identity from the embedded CRC-valid observation.
    @param outputs Expected physical output count from daemon classification.
    @param raw Captured native pairing frame; no radio is opened or transmitted to.
    """
    root = Path(__file__).resolve().parents[2]
    config = replace(
        load(root / "host/config.example.json"),
        devices=(),
        database=str(tmp_path / "detection.db"),
        mqtt={"enabled": False},
    )
    store = Store(config.database, config.gateway + ":" + config.tag)
    service = Service(config, store, simulated=True)
    service.ready = True
    service.session = "detection-test-session"
    service.handle_rx(time.monotonic(), f"RX 1 0 -42 99 {len(raw) // 2} {raw}")
    api = NativeAPI(service, token="detection-test-token", port=0)
    await api.start()
    entry = MockConfigEntry(
        domain="livolo_ec",
        unique_id=config.gateway + ":" + config.tag,
        data={
            "url": f"http://127.0.0.1:{api.port}",
            "token": "detection-test-token",
            "allow_simulated": True,
        },
    )
    entry.add_to_hass(hass)
    loaded = False
    try:
        assert await hass.config_entries.async_setup(entry.entry_id)
        loaded = True
        result = await hass.config_entries.options.async_init(entry.entry_id)
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"next_step_id": "discover"}
        )
        assert result["description_placeholders"]["count"] == "1"
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"device": device}
        )
        assert result["description_placeholders"]["outputs"] == str(outputs)
        assert not service.devices
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"name": "Detected switch"}
        )
        assert result["type"] == "create_entry"
        assert service.devices[device].outputs == outputs
        assert service.devices[device].suffix == f"{outputs:04x}"
        assert service.devices[device].name == "Detected switch"
        await eventually(lambda: len(hass.states.async_all("switch")) == outputs)
        assert all(s.state == "unavailable" for s in hass.states.async_all("switch"))
        assert service.pairing is None
        assert service.gateway is None
        assert service.broker is None
        results = store.recent_results()
        assert len(results) == 1 and results[0]["status"] == "confirmed"
    finally:
        if loaded:
            await hass.config_entries.async_unload(entry.entry_id)
        await api.close()
        store.close()
