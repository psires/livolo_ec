"""Actual HA buttons and native transport commission a synthetic switch with Web disabled."""

import asyncio
from dataclasses import replace
import importlib.util
from pathlib import Path
import time
from unittest.mock import patch

import pytest
from homeassistant.helpers import device_registry as dr, entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from livolod.config import load
from livolod.native_api import NativeAPI
from livolod.protocol import decode
from livolod.service import Service
from livolod.storage import Store

from test_end_to_end import eventually

pytestmark = pytest.mark.usefixtures("socket_enabled")


@pytest.mark.parametrize(
    "origin,outputs", [("button", 1), ("button", 2), ("external", 2)]
)
async def test_native_pairing_creates_devices_and_notifies_only_after_confirmation(
    hass, tmp_path, monkeypatch, origin, outputs
):
    """Run the real owner loop, native API, HA button and physical-report confirmation.

    @param hass Actual HA instance.
    @param tmp_path Isolated daemon database.
    @param monkeypatch Temporary path for shared offline radio/profile fixtures.
    @param origin HA gateway button or another client using the same daemon session.
    @param outputs Supported captured profile's output count.
    """
    root = Path(__file__).resolve().parents[2]
    monkeypatch.syspath_prepend(str(root / "host/tests"))
    from test_discovery_profiles import announcement

    spec = importlib.util.spec_from_file_location(
        "livolo_host_pairing_fixture", root / "host/tests/test_commissioning.py"
    )
    fixtures = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fixtures)
    config = replace(
        load(root / "host/config.example.json"),
        devices=(),
        database=str(tmp_path / "pairing.db"),
        mqtt={"enabled": False},
        web={"enabled": False},
    )
    store = Store(config.database, config.gateway + ":" + config.tag)
    radios = []

    def radio():
        """Use the strict offline UART fixture, including disarmed peer setup and native reports."""
        gateway = fixtures.StrictPairingSimulator()
        gateway.suffix = f"{outputs:04x}"
        radios.append(gateway)
        return gateway

    service = Service(config, store, gateway_factory=radio, simulated=True)
    api = NativeAPI(service, token="offline-pairing-token", port=0)
    await api.start()
    task = asyncio.create_task(service.run())
    entry = MockConfigEntry(
        domain="livolo_ec",
        unique_id=config.gateway + ":" + config.tag,
        data={
            "url": f"http://127.0.0.1:{api.port}",
            "token": "offline-pairing-token",
            "allow_simulated": True,
        },
    )
    entry.add_to_hass(hass)
    loaded = False
    with (
        patch(
            "custom_components.livolo_ec.commissioning.persistent_notification.async_create"
        ) as notify,
        patch("serial.Serial", side_effect=AssertionError("No hardware access")),
    ):
        try:
            await eventually(lambda: service.ready)
            assert await hass.config_entries.async_setup(entry.entry_id)
            loaded = True
            await eventually(lambda: entry.runtime_data.connected)
            button = next(
                s.entity_id
                for s in hass.states.async_all("button")
                if s.entity_id.endswith("_pair_new_device")
            )
            status = next(
                s.entity_id
                for s in hass.states.async_all("sensor")
                if s.entity_id.endswith("_pairing_status")
            )
            if origin == "button":
                await hass.services.async_call(
                    "button", "press", {"entity_id": button}, blocking=True
                )
            else:
                await entry.runtime_data.client.command(
                    "commission", {"request_id": "external-session", "seconds": 120}
                )
            await eventually(lambda: hass.states.get(status).state == "listening")
            raw = announcement(
                suffix=f"{outputs:04x}",
                body="0001010534010400" if outputs == 1 else "0002030534010400",
            )
            radios[-1].events.put_nowait(
                (time.monotonic(), f"RX 1 0 -42 99 {len(raw)} {raw.hex()}")
            )
            await eventually(lambda: len(radios) == 2 and service.ready)
            await eventually(lambda: hass.states.get(status).state == "pairing")
            await eventually(lambda: len(hass.states.async_all("switch")) == outputs)
            assert service.commissioning.state["registered"]
            await asyncio.sleep(0.1)
            assert service.commissioning.state["status"] == "pairing"
            notify.assert_not_called()
            # Only another fresh request triggers admission; the earlier ARM report is insufficient.
            radios[-1].events.put_nowait(
                (time.monotonic(), f"RX 2 0 -42 99 {len(raw)} {raw.hex()}")
            )
            await eventually(lambda: hass.states.get(status).state == "confirmed")
            await eventually(lambda: notify.call_count == 1)
            assert radios[-1].admission.is_set()
            device = dr.async_get(hass).async_get_device_by_identifier(
                ("livolo_ec", entry.unique_id + ":66780945"), entry.entry_id
            )
            assert (
                device and device.via_device_id == entry.runtime_data.gateway_device_id
            )
            assert f"/config/devices/device/{device.id}" in notify.call_args.args[1]
            registry = er.async_get(hass)
            identities = {
                item.entity_id: item.unique_id
                for item in er.async_entries_for_config_entry(registry, entry.entry_id)
            }
            assert await hass.config_entries.async_reload(entry.entry_id)
            await hass.async_block_till_done()
            assert identities == {
                item.entity_id: item.unique_id
                for item in er.async_entries_for_config_entry(registry, entry.entry_id)
            }
            notify.assert_called_once()
            for gateway in radios:
                for text in gateway.commands:
                    if text.startswith("SEND "):
                        assert decode(bytes.fromhex(text.split()[2])).opcode != 2
            assert service.broker is None and not config.web["enabled"]
        finally:
            if loaded:
                await hass.config_entries.async_unload(entry.entry_id)
            service.stop.set()
            await asyncio.wait_for(task, 5)
            await api.close()
            store.close()
            await asyncio.sleep(0.06)
