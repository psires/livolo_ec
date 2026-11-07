"""Native HA installs through the actual daemon and worker, with no Web or hardware."""

import asyncio
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import (
    area_registry as ar,
    entity_registry as er,
    issue_registry as ir,
)
from pytest_homeassistant_custom_component.common import MockConfigEntry

from livolod import FIRMWARE_COMPAT_VERSION, __version__ as DAEMON_VERSION
from livolod.config import load
from livolod.native_api import NativeAPI
from livolod.service import Service
from livolod.simulator import Simulator
from livolod.storage import Store

from test_end_to_end import eventually
from custom_components.livolo_ec.firmware_notice import issue_id
from test_version_reporting import version_sensor

ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.usefixtures("socket_enabled")


@pytest.fixture
async def lifecycle(hass, tmp_path, monkeypatch):
    """Provide a real catalog, daemon lifecycle, native transport and HA registries.

    @param hass Actual HA instance.
    @param tmp_path Private fixture state and synthetic artifact directory.
    @param monkeypatch Test-only import path for existing nonexecutable image builders.
    """
    monkeypatch.syspath_prepend(str(ROOT / "host/tests"))
    from test_firmware_bundle import gbl, mg21_image

    catalog = tmp_path / "firmware"
    catalog.mkdir()
    data = gbl(mg21_image(board="zb-gw04-1v2", version=FIRMWARE_COMPAT_VERSION))
    artifact = dict(
        id="offline-test",
        board="zb-gw04-1v2",
        role="gateway",
        version=FIRMWARE_COMPAT_VERSION,
        protocol=3,
        backend="mg21_gecko_gbl_v1",
        path="test.gbl",
        app_baudrate=460800,
        layout="mg21-app-4000-30000",
        qualified=True,
        qualification="qualification.md",
        source_commit="a" * 40,
        notices=["NOTICE"],
        bootloader_baudrate=115200,
        size=len(data),
        sha256=hashlib.sha256(data).hexdigest(),
    )
    (catalog / "test.gbl").write_bytes(data)
    (catalog / "qualification.md").write_text("Synthetic offline fixture only")
    (catalog / "NOTICE").write_text("Not executable firmware; never deploy")
    manifest = json.dumps(
        dict(schema=1, host_version=FIRMWARE_COMPAT_VERSION, artifacts=[artifact])
    )
    (catalog / "manifest.json").write_text(manifest)
    config = replace(
        load(ROOT / "host/config.example.json"),
        database=str(tmp_path / "state.db"),
        serial_port="offline-test-radio",
        serial_baudrate=460800,
        firmware_bundle_dir=str(catalog),
        mqtt={"enabled": False},
        web={"enabled": False},
        output_mode="disabled",
    )
    state = SimpleNamespace(
        root=tmp_path,
        catalog=catalog,
        manifest=manifest,
        scenario="success",
        config=config,
        spawn_count=0,
        service=None,
        api=None,
        task=None,
        store=None,
        entry=None,
        installs=[],
        gateways=[],
    )

    class Radio(Simulator):
        """Hardware boundary with exact identity and normal session/peer commands."""

        async def command(self, text, retries=1):
            """Return fixture identity while preserving real restoration behavior.

            @param text Daemon UART request.
            @param retries Gateway compatibility argument.
            """
            complete = (tmp_path / "transferred").exists()
            version = FIRMWARE_COMPAT_VERSION if complete else "0.12"
            board = "zb-gw04-1v2"
            if complete and state.scenario == "wrong_board":
                board = "sonoff-zbdongle-e"
            if complete and state.scenario == "wrong_version":
                version = "0.11"
            identity = f" board={board} fw_version={version} baud=460800 proto=3"
            if text == "VERSION":
                self.commands.append(text)
                return "OK" + identity
            response = await super().command(text, retries)
            return response + identity if text == "CAPS" else response

    def radio():
        """Create only an isolated fake hardware connection."""
        gateway = Radio()
        state.gateways.append(gateway)
        return gateway

    real_spawn = asyncio.create_subprocess_exec

    async def spawn(*args, **kwargs):
        """Keep real worker main, validation, IPC and process ownership around a fake helper.

        @param args Fixed worker invocation from the actual manager.
        @param kwargs Real pipe, process-group and inherited-guard options.
        """
        assert args[1:3] == ("-m", "livolod.firmware_worker")
        state.spawn_count += 1
        state.installs.append(args[args.index("--job-id") + 1])
        env = dict(
            os.environ,
            PYTHONPATH=str(ROOT / "host"),
            LIVOLO_TEST_ROOT=str(tmp_path),
            LIVOLO_TEST_SCENARIO=state.scenario,
        )
        return await real_spawn(
            sys.executable,
            str(Path(__file__).with_name("firmware_worker_fixture.py")),
            *args[3:],
            env=env,
            **kwargs,
        )

    async def start(port=0):
        """Open the real persisted daemon and native-only listener.

        @param port Optional prior native port for a real reconnect after restart.
        """
        state.store = Store(config.database, config.gateway + ":" + config.tag)
        state.service = Service(config, state.store, gateway_factory=radio)
        state.api = NativeAPI(state.service, token="offline-native-test", port=port)
        await state.api.start()
        state.task = asyncio.create_task(state.service.run())
        await eventually(lambda: state.service.ready)

    async def stop():
        """Close native transport and reap the actual daemon/worker before releasing SQLite."""
        if state.service:
            state.service.stop.set()
            await asyncio.wait_for(state.task, 5)
            await state.api.close()
            state.store.close()
            state.service = None
            # Simulator ARM schedules a delayed report; drain it before HA's leak check.
            await asyncio.sleep(0.06)

    state.start, state.stop = start, stop
    with (
        patch("livolod.firmware_backends.backend_status", return_value=None),
        patch("livolod.firmware_preflight.backend_status", return_value=None),
        patch("asyncio.create_subprocess_exec", side_effect=spawn),
        patch(
            "serial.Serial", side_effect=AssertionError("Unexpected real serial access")
        ),
        patch(
            "livolod.web.start_web", side_effect=AssertionError("Web must remain off")
        ),
    ):
        try:
            await start()
            entry = MockConfigEntry(
                domain="livolo_ec",
                unique_id=config.gateway + ":" + config.tag,
                data={
                    "url": f"http://127.0.0.1:{state.api.port}",
                    "token": "offline-native-test",
                },
            )
            entry.add_to_hass(hass)
            state.entry = entry
            assert await hass.config_entries.async_setup(entry.entry_id)
            await eventually(lambda: bool(hass.states.async_all("update")))
            state.entity = hass.states.async_all("update")[0].entity_id
            state.registry = er.async_get(hass)
            switch = hass.states.async_all("switch")[0].entity_id
            area = ar.async_get(hass).async_create("Test room")
            state.registry.async_update_entity(
                switch, name="Keep this name", area_id=area.id
            )
            state.identities = {
                item.entity_id: (
                    item.unique_id,
                    item.device_id,
                    item.name,
                    item.area_id,
                )
                for item in er.async_entries_for_config_entry(
                    state.registry, entry.entry_id
                )
            }
            state.devices = tuple(state.service.config.devices)
            yield state
        finally:
            if state.entry and state.entry.runtime_data:
                await hass.config_entries.async_unload(state.entry.entry_id)
            await stop()


def assert_identity(state):
    """Check HA customization and daemon network/device preservation.

    @param state Running fixture with baseline registry identities.
    """
    assert state.identities == {
        item.entity_id: (item.unique_id, item.device_id, item.name, item.area_id)
        for item in er.async_entries_for_config_entry(
            state.registry, state.entry.entry_id
        )
    }
    assert state.entry.unique_id == state.config.gateway + ":" + state.config.tag
    assert state.entry.data["token"] == "offline-native-test"
    assert tuple(state.service.config.devices) == state.devices
    assert not state.config.web["enabled"]
    assert state.service.broker is None


@pytest.mark.parametrize(
    "scenario", ["success", "reload", "wrong_board", "wrong_version", "restart"]
)
async def test_native_install_and_reconciliation(hass, lifecycle, scenario):
    """Actual HA install follows durable worker progress, reconnect and exact restoration.

    @param hass Actual HA instance.
    @param lifecycle Native daemon fixture with hardware-boundary substitutions only.
    @param scenario Successful restoration, mismatched identity, or interrupted restart.
    """
    state = lifecycle
    assert DAEMON_VERSION != FIRMWARE_COMPAT_VERSION
    assert version_sensor(hass, "daemon_version").state == DAEMON_VERSION
    assert (
        version_sensor(hass, "firmware_compat_version").state == FIRMWARE_COMPAT_VERSION
    )
    state.scenario = scenario
    initial_session = state.service.session
    task = asyncio.create_task(
        hass.services.async_call(
            "update",
            "install",
            {"entity_id": state.entity},
            blocking=True,
        )
    )
    try:
        await eventually(
            lambda: hass.states.get(state.entity).attributes.get("transfer_percent")
            == 100
        )
        current = hass.states.get(state.entity)
        assert current.attributes["installed_version"] == "0.12"
        assert current.attributes["update_percentage"] is None
        assert current.attributes["in_progress"]
        assert state.store.firmware_job()["last_critical"] == "flash"
        ident = state.store.firmware_job()["request_id"]
        assert state.store.command_result(ident)["status"] == "pending"
        assert not state.service.ready
        # Catalog removal during maintenance must not replace the active job's identity.
        (state.catalog / "manifest.json").unlink()
        await state.service.firmware_manager.refresh_catalog()
        active = state.service.firmware_manager.snapshot()
        assert active["request_id"] == ident
        assert active["latest_version"] == FIRMWARE_COMPAT_VERSION
        assert active["candidate_id"] == state.store.firmware_job()["candidate_id"]
        (state.catalog / "manifest.json").write_text(state.manifest)
        if scenario != "success":
            # Cancelling observation must never cancel or replay the daemon-owned job.
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            assert await hass.config_entries.async_reload(state.entry.entry_id)
            await eventually(
                lambda: hass.states.get(state.entity).attributes.get("request_id")
                == ident
            )
        assert state.store.command_result(ident)["status"] == "pending"
        if scenario == "restart":
            port = state.api.port
            await state.stop()
            await state.start(port)
            assert await hass.config_entries.async_reload(state.entry.entry_id)
        else:
            (state.root / "release-worker").touch()
        expected = "confirmed" if scenario in ("success", "reload") else "unknown"
        await eventually(
            lambda: hass.states.get(state.entity).attributes.get("status") == expected
        )
        if scenario == "success":
            await asyncio.wait_for(task, 5)
        job = state.store.firmware_job()
        assert job["request_id"] == ident
        assert job["status"] == expected
        assert state.spawn_count == 1 and state.installs == [ident]
        assert len(state.store.recent_results()) == 1
        assert_identity(state)
        if expected == "confirmed":
            assert state.service.ready and state.service.session != initial_session
            assert state.service.gateway.armed and state.service.gateway.ack
            assert len(state.service.gateway.peers) == len(state.devices)
            assert (
                hass.states.get(state.entity).attributes["installed_version"]
                == FIRMWARE_COMPAT_VERSION
            )
            assert not state.service.firmware_manager.busy
        else:
            assert job["recovery"] == "recovery_required"
            assert state.service.firmware_manager.busy
            assert not state.service.firmware_manager.requested.is_set()
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.parametrize("scenario", ["before", "after", "death"])
async def test_native_worker_failure_is_durable(hass, lifecycle, scenario):
    """A real worker rejection or death reports recovery accurately without resubmission.

    @param hass Actual HA instance.
    @param lifecycle Native daemon fixture and real persistent manager.
    @param scenario Failure before mutation, after mutation, or abrupt worker exit.
    """
    state = lifecycle
    state.scenario = scenario
    with pytest.raises(HomeAssistantError):
        await hass.services.async_call(
            "update", "install", {"entity_id": state.entity}, blocking=True
        )
    await eventually(lambda: state.service.ready)
    expected = "rejected" if scenario == "before" else "unknown"
    await eventually(
        lambda: hass.states.get(state.entity).attributes.get("status") == expected
    )
    job = state.store.firmware_job()
    assert job["status"] == expected
    assert bool(job["recovery"]) == (scenario != "before")
    assert state.spawn_count == 1
    assert len(state.store.recent_results()) == 1
    assert_identity(state)


async def test_native_catalog_refresh_without_web(hass, lifecycle):
    """Missing and malformed catalogs withdraw eligibility without changing network controls.

    @param hass Actual HA instance.
    @param lifecycle Native daemon fixture with a fixed local catalog.
    """
    state = lifecycle
    session = state.service.session
    empty = json.dumps(
        dict(schema=1, host_version=FIRMWARE_COMPAT_VERSION, artifacts=[])
    )
    for content in (None, "invalid", empty, state.manifest):
        path = state.catalog / "manifest.json"
        if content is None:
            path.unlink()
        else:
            path.write_text(content)
        await state.service.firmware_manager.refresh_catalog()
        available = content == state.manifest
        await eventually(
            lambda: (hass.states.get(state.entity).state == "on") == available
        )
        expected = (
            "update_available"
            if available
            else (
                "catalog_missing"
                if content is None
                else "catalog_unusable" if content == "invalid" else "no_candidate"
            )
        )
        await eventually(
            lambda: version_sensor(hass, "firmware_status").state == expected
        )
        diagnostic = version_sensor(hass, "firmware_status")
        assert diagnostic.state == expected
        assert diagnostic.attributes["installed_version"] == "0.12"
        notice = ir.async_get(hass).async_get_issue("livolo_ec", issue_id(state.entry))
        assert bool(notice) is not available
        if not available:
            assert (
                "does not confirm installation" in diagnostic.attributes["explanation"]
            )
            assert hass.states.get(state.entity).state == "unknown"
        assert state.service.ready and state.service.session == session
        assert state.spawn_count == 0
        assert_identity(state)
