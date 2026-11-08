"""Independent guided-setup acceptance across real HA flows and loopback HTTP.

The adversarial HTTP fixture supplies results only; it never starts a worker or
claims physical qualification. Handoff must preserve the existing HA identity
even when a successful bootstrap result conflicts with the normal native API.
"""

from copy import deepcopy
import asyncio
from dataclasses import replace
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from aiohttp import ClientSession, web
import pytest
from homeassistant.helpers import area_registry as ar, device_registry as dr
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from livolod import FIRMWARE_COMPAT_VERSION
from livolod.bootstrap_api import BootstrapAPI
from livolod.bootstrap_controller import BootstrapController
from livolod.bootstrap_runtime import BootstrapRuntime
from livolod.bootstrap_store import BootstrapStore
from livolod.config import load
from livolod.native_api import NativeAPI
from livolod.preserved_gecko import GeckoTarget
from livolod.bootstrap_worker import target_json
from livolod.simulator import Simulator
from livolod.storage import Store

from test_end_to_end import eventually


pytestmark = pytest.mark.usefixtures("socket_enabled")


@pytest.fixture
async def handoff_endpoint(aiohttp_server):
    """Serve independent bootstrap and normal responses without a physical backend.

    @param aiohttp_server Isolated loopback HTTP server factory.
    """
    state = SimpleNamespace(
        calls=[],
        bootstrap_simulated=False,
        bootstrap_network_ready=True,
        info=dict(api_version=1, gateway="2a992b12", tag="a7", simulated=False),
        snapshot=dict(
            type="snapshot",
            api_version=1,
            epoch="acceptance",
            sequence=1,
            data=dict(
                gateway="2a992b12",
                tag="a7",
                ready=True,
                controls_enabled=True,
                simulated=False,
                devices=[],
            ),
        ),
    )

    async def request_handler(request):
        """Return durable handoff evidence while recording every attempted operation.

        @param request Authenticated request from the assembled HA client.
        """
        state.calls.append((request.method, request.path))
        assert request.headers.get("Authorization") == "Bearer acceptance-token"
        assert request.method == "GET", "Resuming a handoff must never submit work"
        if request.path == "/api/v1/info":
            return web.json_response(state.info)
        if request.path == "/api/v1/snapshot":
            return web.json_response(state.snapshot)
        if request.path == "/api/v1/provisioning/info":
            data = dict(
                mode="gateway",
                physical_install_supported=False,
                prepare_supported=False,
                enter_supported=True,
                initialize_supported=False,
                profile="zb-gw04-1v2",
                expected_network_id="2a992b12:a7",
                active_request_id=None,
                latest_request_id="handoff-one",
                blocked_reason=None,
            )
        elif request.path == "/api/v1/provisioning/operations/handoff-one":
            data = dict(
                request_id="handoff-one",
                instance_id="service-one",
                kind="handoff",
                status="confirmed",
                stage="network_ready",
                error=None,
                recovery=None,
                install_request_id="install-one",
                expected_network_id="2a992b12:a7",
                application_verified=True,
                network_ready=state.bootstrap_network_ready,
                setup_required=False,
            )
        else:
            raise AssertionError(f"Unexpected acceptance request: {request.path}")
        return web.json_response(
            dict(
                schema=1,
                instance_id="service-one",
                operation="provision",
                simulated=state.bootstrap_simulated,
                ok=True,
                data=data,
            )
        )

    app = web.Application()
    app.router.add_route("*", "/{path:.*}", request_handler)
    server = await aiohttp_server(app)
    state.url = str(server.make_url(""))
    return state


@pytest.mark.parametrize(
    "disagreement",
    [
        None,
        "info_identity",
        "snapshot_identity",
        "not_ready",
        "simulated_info",
        "simulated_snapshot",
        "simulated_bootstrap",
        "unready_handoff",
    ],
)
async def test_resumed_handoff_requires_normal_identity_and_preserves_entry(
    hass, handoff_endpoint, disagreement
):
    """A resumed setup cannot replace a configured hub or trust bootstrap success alone.

    @param hass Actual HA instance with isolated registries and persistent storage.
    @param handoff_endpoint Independent HTTP responses, without any mocked client methods.
    @param disagreement Fault injected after a claimed successful handoff, or None.
    """
    endpoint = handoff_endpoint
    if disagreement == "info_identity":
        endpoint.info["gateway"] = "2a992b13"
    elif disagreement == "snapshot_identity":
        endpoint.snapshot["data"]["tag"] = "a8"
    elif disagreement == "not_ready":
        endpoint.snapshot["data"]["ready"] = False
    elif disagreement == "simulated_info":
        endpoint.info["simulated"] = True
    elif disagreement == "simulated_snapshot":
        endpoint.snapshot["data"]["simulated"] = True
    elif disagreement == "simulated_bootstrap":
        endpoint.bootstrap_simulated = True
    elif disagreement == "unready_handoff":
        endpoint.bootstrap_network_ready = False
    entry = MockConfigEntry(
        domain="livolo_ec",
        unique_id="2a992b12:a7",
        title="Existing hub",
        data={"url": endpoint.url, "token": "acceptance-token"},
        options={"development_enabled": False, "preserved_option": "keep"},
    )
    entry.add_to_hass(hass)
    areas = ar.async_get(hass)
    area = areas.async_create("Preserved room")
    devices = dr.async_get(hass)
    device = devices.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={("livolo_ec", "2a992b12:a7:46715621")},
        name="Existing switch",
    )
    registry = er.async_get(hass)
    entity = registry.async_get_or_create(
        "switch",
        "livolo_ec",
        "2a992b12:a7:46715621:1",
        config_entry=entry,
        device_id=device.id,
    )
    registry.async_update_entity(entity.entity_id, name="Keep my name", area_id=area.id)
    before = deepcopy(dict(data=dict(entry.data), options=dict(entry.options)))
    before_entity = registry.async_get(entity.entity_id)
    before_device = devices.async_get(device.id)
    flow = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        flow["flow_id"], {"next_step_id": "radio_setup"}
    )
    if disagreement is None:
        assert result["type"] == "create_entry"
    else:
        assert result["type"] == "form"
        assert result["errors"]["base"] == "bootstrap_incompatible"
    assert dict(entry.data) == before["data"]
    assert dict(entry.options) == before["options"]
    assert entry.unique_id == "2a992b12:a7"
    assert registry.async_get(entity.entity_id) == before_entity
    assert devices.async_get(device.id) == before_device
    assert len(hass.config_entries.async_entries("livolo_ec")) == 1
    assert endpoint.calls
    assert all(method == "GET" for method, _ in endpoint.calls)


@pytest.fixture
async def controller_endpoint(tmp_path, monkeypatch):
    """Run real controller/journal/API/runtime with only physical worker and UART injected.

    @param tmp_path Isolated persistent state, catalog and nondeployable artifact directory.
    @param monkeypatch Scoped fixture imports and a lost-response HTTP fault.
    """
    root = Path(__file__).resolve().parents[2]
    monkeypatch.syspath_prepend(str(root / "host/tests"))
    from test_firmware_bundle import gbl, mg21_image

    catalog = tmp_path / "firmware"
    catalog.mkdir()
    data = gbl(mg21_image("zb-gw04-1v2", FIRMWARE_COMPAT_VERSION))
    artifact = dict(
        id="acceptance-only",
        board="zb-gw04-1v2",
        role="gateway",
        version=FIRMWARE_COMPAT_VERSION,
        protocol=3,
        backend="mg21_gecko_gbl_v1",
        path="fixture.gbl",
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
    (catalog / "fixture.gbl").write_bytes(data)
    for name in ("qualification.md", "NOTICE"):
        (catalog / name).write_text("Synthetic nondeployable acceptance fixture only")
    (catalog / "manifest.json").write_text(
        json.dumps(
            dict(
                schema=1,
                host_version=FIRMWARE_COMPAT_VERSION,
                artifacts=[artifact],
            )
        )
    )
    config = replace(
        load(root / "host/config.example.json"),
        database=str(tmp_path / "gateway.db"),
        serial_port="/dev/acceptance-no-radio",
        serial_baudrate=460800,
        firmware_bundle_dir=str(catalog),
        mqtt={"enabled": False},
        web={"enabled": False},
        output_mode="disabled",
    )
    state = SimpleNamespace(
        api=None,
        controller=None,
        store=None,
        runtime=None,
        lost=None,
        posts=[],
        worker_calls=[],
        transfers=0,
        block_install=False,
        radio_version=FIRMWARE_COMPAT_VERSION,
        flash_reached=asyncio.Event(),
    )
    baseline = Store(config.database, config.gateway + ":" + config.tag)
    baseline.devices(config.devices)
    state.baseline_devices = baseline.db.execute(
        "SELECT id,value FROM devices ORDER BY id"
    ).fetchall()
    baseline.close()
    target = GeckoTarget(
        port=config.serial_port,
        usb_path="devices/acceptance/usb1/1-1",
        attachment="offline-one",
        device_number=(20, 0),
        interface="00",
        loader_version="1.9.2",
    )

    class Radio(Simulator):
        """Test-only radio boundary; production controller/API/store remain unmodified."""

        async def command(self, text, retries=1):
            """Report exact fixture identity while exercising actual normal-service restoration.

            @param text Service command sent to the injected simulator.
            @param retries Simulator compatibility argument.
            """
            identity = f" board=zb-gw04-1v2 fw_version={state.radio_version} proto=3 baud=460800"
            if text == "VERSION":
                self.commands.append(text)
                return "OK" + identity
            result = await super().command(text, retries)
            return result + identity if text == "CAPS" else result

    class Worker:
        """Inject only nonexecutable physical results, with a controllable write boundary."""

        async def run(self, kind, job, candidate, binding, checkpoint):
            """Exercise actual durable checkpoints around synthetic transfer and inspection.

            @param kind Explicit prepare or install operation.
            @param job Admitted durable operation.
            @param candidate Real validator-selected immutable fixture bytes.
            @param binding Previously prepared private plan, or None.
            @param checkpoint Real controller durable checkpoint callback.
            """
            state.worker_calls.append((kind, job["request_id"]))
            if kind == "cancel":
                await checkpoint("run_intact")
                version = binding["baseline"]["version"]
                identity = dict(board="zb-gw04-1v2", fw_version=version, proto="3")
                return dict(
                    version=identity,
                    caps=dict(identity, baud="460800"),
                    status=dict(armed="0", ack="0"),
                )
            assert candidate["_data"] == data
            if kind == "prepare":
                return {"target": target_json(target), "loader": "fixture"}
            assert (
                kind == "install"
                and binding["inspection"]["target"]["attachment"] == "offline-one"
            )
            await checkpoint("flash")
            state.transfers += 1
            state.flash_reached.set()
            if state.block_install:
                await asyncio.Event().wait()
            return dict(
                application_verified=True,
                board="zb-gw04-1v2",
                version=FIRMWARE_COMPAT_VERSION,
                artifact_sha256=candidate["sha256"],
                network_ready=False,
            )

    original_submit = BootstrapAPI.submit

    async def lose_response(api, request):
        """Drop one accepted HTTP response after the production handler admits its job.

        @param api Production provisioning route owner.
        @param request Actual authenticated loopback request.
        """
        kind = request.path.rsplit("/", 1)[1]
        state.posts.append(kind)
        response = await original_submit(api, request)
        if state.lost == kind:
            state.lost = None
            return web.Response(status=503)
        return response

    monkeypatch.setattr(BootstrapAPI, "submit", lose_response)
    settings = dict(
        serial_port=config.serial_port,
        catalog=str(catalog),
        network={"gateway": config.gateway, "tag": config.tag},
        mode="bootstrap",
        database=config.database,
        lease_directory=str(tmp_path / "leases"),
    )
    raw = json.loads((root / "host/config.example.json").read_text())
    raw.update(
        database=config.database,
        serial_port=config.serial_port,
        serial_baudrate=460800,
        firmware_bundle_dir=str(catalog),
        mqtt={"enabled": False},
        web={"enabled": False},
        output_mode="disabled",
    )

    async def start(port=0):
        """Reopen the real journal and authenticated listener without replaying work.

        @param port Previous loopback port after an explicit test restart, or zero.
        """
        state.store = BootstrapStore(tmp_path / "bootstrap.db")
        state.runtime = BootstrapRuntime(
            raw, settings, gateway_factory=Radio, ready_timeout=3
        )
        state.runtime.ownership.target = target
        state.controller = BootstrapController(
            state.store, settings, Worker(), state.runtime
        )
        state.api = NativeAPI(
            None, token="acceptance-token", port=port, provisioning=state.controller
        )
        state.runtime.api = state.api
        await state.api.start()
        state.url = f"http://127.0.0.1:{state.api.port}"

    async def stop():
        """Reap controller work and normal service before closing their independent journals."""
        if state.controller:
            await state.controller.close()
            await state.runtime.enter()
            await state.api.close()
            state.store.close()
            state.controller = None
            await asyncio.sleep(0.06)

    state.start, state.stop = start, stop
    state.network = settings["network"]
    with patch(
        "serial.Serial",
        side_effect=AssertionError("No physical serial in acceptance tests"),
    ):
        await start()
        try:
            yield state
        finally:
            await stop()


async def review_fixture_image(hass, endpoint):
    """Drive actual HA options through real prepare admission and return its review form.

    @param hass Actual Home Assistant instance.
    @param endpoint Running production controller/API fixture.
    @return Existing entry and prepared review flow result.
    """
    entry = MockConfigEntry(
        domain="livolo_ec",
        unique_id="2a992b12:a7",
        data={"url": endpoint.url, "token": "acceptance-token"},
        options={"keep_this": "unchanged"},
    )
    entry.add_to_hass(hass)
    flow = await hass.config_entries.options.async_init(entry.entry_id)
    flow = await hass.config_entries.options.async_configure(
        flow["flow_id"], {"next_step_id": "radio_setup"}
    )
    if flow["step_id"] == "radio_enter":
        flow = await hass.config_entries.options.async_configure(
            flow["flow_id"], {"confirmed": True}
        )
        await eventually(lambda: not endpoint.controller.tasks)
        flow = await hass.config_entries.options.async_configure(
            flow["flow_id"], {"refresh": True}
        )
        if flow["step_id"] == "radio_progress":
            flow = await hass.config_entries.options.async_configure(
                flow["flow_id"], {"refresh": True}
            )
    assert flow["step_id"] == "radio_select", flow
    flow = await hass.config_entries.options.async_configure(
        flow["flow_id"],
        dict(
            target_id=endpoint.controller.target_id,
            board_confirmed=True,
            dedicated_radio=True,
            bootloader_ready=True,
        ),
    )
    await eventually(lambda: not endpoint.controller.tasks)
    if flow["step_id"] != "radio_review":
        flow = await hass.config_entries.options.async_configure(
            flow["flow_id"], {"refresh": True}
        )
    assert flow["step_id"] == "radio_review", flow
    return entry, flow


@pytest.mark.parametrize("lost", [None, "install", "handoff"])
async def test_real_controller_flow_reconciles_lost_replies(
    hass, controller_endpoint, lost
):
    """Real HA/client/API/journal agree on one install and preserve network through handoff.

    @param hass Actual HA instance.
    @param controller_endpoint Real controller/listener/store with an injected physical boundary.
    @param lost Response to drop after actual admission, or None.
    """
    endpoint = controller_endpoint
    entry, flow = await review_fixture_image(hass, endpoint)
    before = deepcopy(dict(data=dict(entry.data), options=dict(entry.options)))
    endpoint.lost = lost
    flow = await hass.config_entries.options.async_configure(
        flow["flow_id"], {"confirmed": True, "dedicated_radio": True}
    )
    await eventually(lambda: not endpoint.controller.tasks)
    if flow.get("step_id") != "radio_handoff":
        flow = await hass.config_entries.options.async_configure(
            flow["flow_id"], {"refresh": True}
        )
    assert flow["step_id"] == "radio_handoff", flow
    assert endpoint.transfers == 1 and endpoint.runtime.service is None
    flow = await hass.config_entries.options.async_configure(
        flow["flow_id"], {"confirmed": True}
    )
    await eventually(lambda: not endpoint.controller.tasks)
    if flow["type"] != "create_entry":
        flow = await hass.config_entries.options.async_configure(
            flow["flow_id"], {"refresh": True}
        )
    assert flow["type"] == "create_entry", flow
    assert endpoint.posts == ["prepare", "install", "handoff"]
    assert endpoint.transfers == 1
    assert endpoint.runtime.service.ready
    assert (
        dict(entry.data) == before["data"] and dict(entry.options) == before["options"]
    )
    assert (
        endpoint.runtime.store.db.execute(
            "SELECT id,value FROM devices ORDER BY id"
        ).fetchall()
        == endpoint.baseline_devices
    )


async def test_real_direct_cancel_keeps_013_without_inspection_or_install(
    hass, controller_endpoint
):
    """An existing 0.13 hub can leave setup directly using actual readiness, without a flash.

    @param hass Actual HA instance.
    @param controller_endpoint Production runtime/API/controller with a synthetic radio.
    """
    endpoint = controller_endpoint
    endpoint.radio_version = "0.13"
    await endpoint.runtime.start_gateway(endpoint.network)
    await eventually(lambda: endpoint.runtime.service.ready)
    endpoint.controller.mode = "gateway"
    endpoint.store.set_meta("mode", "gateway")
    entry = MockConfigEntry(
        domain="livolo_ec",
        unique_id="2a992b12:a7",
        data={"url": endpoint.url, "token": "acceptance-token"},
    )
    entry.add_to_hass(hass)
    flow = await hass.config_entries.options.async_init(entry.entry_id)
    flow = await hass.config_entries.options.async_configure(
        flow["flow_id"], {"next_step_id": "radio_setup"}
    )
    assert flow["step_id"] == "radio_enter"
    flow = await hass.config_entries.options.async_configure(
        flow["flow_id"], {"confirmed": True}
    )
    await eventually(lambda: not endpoint.controller.tasks)
    if flow["step_id"] == "radio_progress":
        flow = await hass.config_entries.options.async_configure(
            flow["flow_id"], {"refresh": True}
        )
    assert flow["step_id"] == "radio_select", flow
    flow = await hass.config_entries.options.async_configure(
        flow["flow_id"], {"cancel_setup": True}
    )
    assert flow["step_id"] == "radio_cancel", flow
    flow = await hass.config_entries.options.async_configure(
        flow["flow_id"], {"confirmed": True}
    )
    await eventually(lambda: not endpoint.controller.tasks)
    if flow["type"] != "create_entry":
        flow = await hass.config_entries.options.async_configure(
            flow["flow_id"], {"refresh": True}
        )
    assert flow["type"] == "create_entry", flow
    assert endpoint.posts == ["enter", "cancel"] and endpoint.transfers == 0
    assert endpoint.runtime.service.ready
    assert endpoint.store.meta("baseline")["version"] == "0.13"
    assert (
        endpoint.runtime.service.firmware_manager.version_info["fw_version"] == "0.13"
    )


async def test_real_controller_restart_fences_install_without_replay(
    hass, controller_endpoint
):
    """Restart after the possible-write checkpoint shows recovery and never starts a new job.

    @param hass Actual HA instance with durable observer state.
    @param controller_endpoint Production journal/controller/API fixture.
    """
    endpoint = controller_endpoint
    entry, flow = await review_fixture_image(hass, endpoint)
    endpoint.block_install = True
    flow = await hass.config_entries.options.async_configure(
        flow["flow_id"], {"confirmed": True, "dedicated_radio": True}
    )
    await asyncio.wait_for(endpoint.flash_reached.wait(), 5)
    instance = endpoint.store.instance_id
    port = endpoint.api.port
    await endpoint.stop()
    await endpoint.start(port)
    assert endpoint.store.instance_id == instance
    hass.config_entries.options.async_abort(flow["flow_id"])
    hass.data.pop("livolo_ec_bootstrap_observers", None)
    flow = await hass.config_entries.options.async_init(entry.entry_id)
    flow = await hass.config_entries.options.async_configure(
        flow["flow_id"], {"next_step_id": "radio_setup"}
    )
    assert flow["step_id"] == "radio_progress", flow
    assert flow["description_placeholders"]["status"] == "unknown"
    assert "recovery" in flow["description_placeholders"]["reason"]
    assert endpoint.posts == ["prepare", "install"] and endpoint.transfers == 1
    assert endpoint.runtime.service is None
    assert entry.unique_id == "2a992b12:a7"


async def test_two_actual_dialogs_confirm_one_install(hass, controller_endpoint):
    """Simultaneous confirmations from separate options dialogs share one durable install.

    @param hass Actual HA instance with two live options flows.
    @param controller_endpoint Production controller/listener/store with injected physical work.
    """
    endpoint = controller_endpoint
    entry, first = await review_fixture_image(hass, endpoint)
    second = await hass.config_entries.options.async_init(entry.entry_id)
    second = await hass.config_entries.options.async_configure(
        second["flow_id"], {"next_step_id": "radio_setup"}
    )
    assert second["step_id"] == "radio_review"
    await asyncio.gather(
        *(
            hass.config_entries.options.async_configure(
                flow["flow_id"], {"confirmed": True, "dedicated_radio": True}
            )
            for flow in (first, second)
        )
    )
    await eventually(lambda: not endpoint.controller.tasks)
    assert endpoint.posts == ["prepare", "install"]
    assert endpoint.transfers == 1
    assert (
        endpoint.store.db.execute("SELECT count(*) FROM operations").fetchone()[0] == 2
    )


async def test_expired_plan_allows_explicit_fresh_inspection(hass, controller_endpoint):
    """A durable expired-plan refusal permits a new explicit review without auto-retry.

    @param hass Actual HA instance.
    @param controller_endpoint Real controller/journal and synthetic physical worker.
    """
    endpoint = controller_endpoint
    _, flow = await review_fixture_image(hass, endpoint)
    original_prepare = endpoint.controller.result(
        endpoint.store.meta("latest_request_id")
    )
    original_plan = original_prepare["plan"]["plan_id"]
    normal_clock = endpoint.controller.clock
    endpoint.controller.clock = lambda: normal_clock() + 121
    flow = await hass.config_entries.options.async_configure(
        flow["flow_id"], {"confirmed": True, "dedicated_radio": True}
    )
    assert flow["step_id"] == "radio_progress", flow
    assert flow["description_placeholders"]["status"] == "rejected", flow
    assert endpoint.transfers == 0 and endpoint.posts == ["prepare", "install"]
    endpoint.controller.clock = normal_clock
    flow = await hass.config_entries.options.async_configure(
        flow["flow_id"], {"review_again": True}
    )
    assert flow["step_id"] == "radio_select", flow
    assert endpoint.posts == ["prepare", "install"]
    flow = await hass.config_entries.options.async_configure(
        flow["flow_id"],
        dict(
            target_id=endpoint.controller.target_id,
            board_confirmed=True,
            dedicated_radio=True,
            bootloader_ready=True,
        ),
    )
    await eventually(lambda: not endpoint.controller.tasks)
    if flow.get("step_id") != "radio_review":
        flow = await hass.config_entries.options.async_configure(
            flow["flow_id"], {"refresh": True}
        )
    assert flow["step_id"] == "radio_review", flow
    latest = endpoint.controller.result(endpoint.store.meta("latest_request_id"))
    assert latest["kind"] == "prepare" and latest["plan"]["plan_id"] != original_plan
    assert endpoint.posts == ["prepare", "install", "prepare"]
    assert endpoint.transfers == 0


async def test_bootstrap_observation_and_rejected_auth_never_operate(
    controller_endpoint,
):
    """Real read routes and rejected credentials cannot admit jobs or touch the worker.

    @param controller_endpoint Production API/controller and independent physical journal.
    """
    endpoint = controller_endpoint
    before = tuple(endpoint.store.db.iterdump())
    async with ClientSession() as session:
        for path, status in (
            ("/api/v1/provisioning/info", 200),
            ("/api/v1/provisioning/targets", 200),
            ("/api/v1/provisioning/operations/never-submitted", 404),
            ("/api/v1/info", 503),
        ):
            async with session.get(
                endpoint.url + path,
                headers={"Authorization": "Bearer acceptance-token"},
            ) as response:
                assert response.status == status
                await response.read()
        async with session.post(
            endpoint.url + "/api/v1/provisioning/prepare",
            json={"request_id": "unauthorized"},
            headers={"Authorization": "Bearer wrong-token"},
        ) as response:
            assert response.status in (401, 403)
            await response.read()
    assert tuple(endpoint.store.db.iterdump()) == before
    assert endpoint.worker_calls == [] and endpoint.posts == []
    assert endpoint.runtime.service is None


@pytest.mark.parametrize("lost", [False, True])
async def test_real_cancel_after_review_restores_existing_hub_without_flash(
    hass, controller_endpoint, lost
):
    """Declining a reviewed image resumes the existing hub, including after a lost cancel reply.

    @param hass Actual HA instance.
    @param controller_endpoint Real setup runtime and journal with injected physical boundary.
    @param lost Whether the accepted cancellation response is lost.
    """
    endpoint = controller_endpoint
    await endpoint.runtime.start_gateway(endpoint.network)
    await eventually(lambda: endpoint.runtime.service.ready)
    endpoint.controller.mode = "gateway"
    endpoint.store.set_meta("mode", "gateway")
    entry, flow = await review_fixture_image(hass, endpoint)
    before = deepcopy(dict(data=dict(entry.data), options=dict(entry.options)))
    assert endpoint.runtime.quiesced and endpoint.runtime.service is None
    assert endpoint.store.meta("baseline")["version"] == FIRMWARE_COMPAT_VERSION
    flow = await hass.config_entries.options.async_configure(
        flow["flow_id"], {"cancel_setup": True}
    )
    assert flow["step_id"] == "radio_cancel", flow
    endpoint.lost = "cancel" if lost else None
    flow = await hass.config_entries.options.async_configure(
        flow["flow_id"], {"confirmed": True}
    )
    await eventually(lambda: not endpoint.controller.tasks)
    if flow["type"] != "create_entry":
        flow = await hass.config_entries.options.async_configure(
            flow["flow_id"], {"refresh": True}
        )
    assert flow["type"] == "create_entry", flow
    assert endpoint.posts == ["enter", "prepare", "cancel"]
    assert endpoint.transfers == 0 and endpoint.runtime.service.ready
    assert (
        dict(entry.data) == before["data"] and dict(entry.options) == before["options"]
    )
    assert (
        endpoint.runtime.store.db.execute(
            "SELECT id,value FROM devices ORDER BY id"
        ).fetchall()
        == endpoint.baseline_devices
    )
