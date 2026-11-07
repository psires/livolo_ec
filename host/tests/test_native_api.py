"""Native API boundaries and durable policy using isolated state and fake radios."""

import asyncio
from dataclasses import replace
import json
import os
from pathlib import Path
import socket
import tempfile
import time
import unittest
from unittest.mock import patch

import aiohttp

from livolod import __version__, FIRMWARE_COMPAT_VERSION
from livolod.config import load, parse
from livolod.native_api import NativeAPI, start_api
from livolod.service import Service
from livolod.simulator import Simulator
from livolod.storage import Store

EXAMPLE = Path(__file__).parents[1] / "config.example.json"


class NativeConfigurationTests(unittest.TestCase):
    def test_optional_mqtt_and_api_validation(self):
        """Accept explicit broker-free forms and reject ambiguous listener secrets."""
        for mqtt in (None, {"enabled": False}):
            raw = json.loads(EXAMPLE.read_text())
            raw["mqtt"] = mqtt
            self.assertEqual(parse(raw).mqtt, {"enabled": False})
        del raw["mqtt"]
        self.assertEqual(parse(raw).mqtt, {"enabled": False})
        self.assertFalse(parse(raw).api["enabled"])
        raw["api"] = dict(enabled=True, token_env="TEST_API_TOKEN")
        self.assertEqual(parse(raw).api["port"], 8090)
        for api in (
            None,
            [],
            dict(enabled=True),
            dict(enabled=1),
            dict(port=True),
            dict(port=65536),
            dict(host=""),
            dict(token="secret"),
            dict(token_env="bad name"),
        ):
            with self.subTest(api=api), self.assertRaises((ValueError, TypeError)):
                parse(dict(raw, api=api))
        for mqtt in (False, [], {"enabled": 0}, {"enabled": True}):
            with self.subTest(mqtt=mqtt), self.assertRaises(ValueError):
                parse(dict(raw, mqtt=mqtt))


class NativeAPITests(unittest.IsolatedAsyncioTestCase):
    async def test_commissioning_start_cancel_and_reload_snapshot(self):
        """Native clients observe one durable session without replaying enrollment."""
        self.service.ready = True
        self.service.session = "api-test"
        result = await self.command("commission", request_id="native-window")
        self.assertEqual(result["status"], "listening")
        status, snapshot = await self.get("/api/v1/snapshot")
        self.assertIs(snapshot["data"]["commissioning_supported"], True)
        self.assertEqual(
            snapshot["data"]["commissioning"]["session_id"], "native-window"
        )
        result = await self.command(
            "commission_cancel", request_id="native-cancel", session_id="native-window"
        )
        self.assertEqual(result["status"], "confirmed")
        _, result = await self.get("/api/v1/commands/native-window")
        self.assertEqual(result["status"], "cancelled")
        result = await self.command("commission", request_id="native-window")
        self.assertEqual(result["status"], "cancelled")

    async def test_firmware_preflight_is_read_only_and_accepts_only_candidate_id(self):
        """Preflight uses normal bearer authentication without starting a firmware job."""
        changes = self.store.db.total_changes
        status, value = await self.get("/api/v1/firmware/preflight")
        self.assertEqual(status, 200)
        self.assertEqual(value["schema"], 1)
        self.assertFalse(value["executable"])
        self.assertIsNone(self.store.firmware_job())
        self.assertEqual(self.store.db.total_changes, changes)
        status, snapshot = await self.get("/api/v1/snapshot")
        self.assertEqual(status, 200)
        self.assertIs(snapshot["data"]["firmware_update"]["preflight_supported"], True)
        for query in (
            "?path=/tmp/file",
            "?candidate_id=../file",
            "?candidate_id=a&candidate_id=b",
        ):
            status, _ = await self.get("/api/v1/firmware/preflight" + query)
            self.assertEqual(status, 400)
        async with self.client.get(
            self.base + "/api/v1/firmware/preflight",
            headers={"Authorization": "Bearer wrong"},
        ) as response:
            self.assertEqual(response.status, 401)

    async def asyncSetUp(self):
        """Start only the machine API, with the radio initially offline."""
        self.temp = tempfile.TemporaryDirectory()
        self.config = replace(
            load(EXAMPLE),
            database=self.temp.name + "/state.db",
            mqtt={"enabled": False},
        )
        self.store = Store(self.config.database, "test")
        self.service = Service(
            self.config, self.store, gateway_factory=Simulator, simulated=True
        )
        self.api = NativeAPI(self.service, token="test-secret", port=0)
        await self.api.start()
        self.client = aiohttp.ClientSession(
            headers={"Authorization": "Bearer test-secret"}
        )
        self.base = f"http://127.0.0.1:{self.api.port}"
        self.task = None

    async def asyncTearDown(self):
        """Close all sockets and pending radio work before releasing the store."""
        if self.task:
            self.service.stop.set()
            await asyncio.wait_for(self.task, 3)
        await self.client.close()
        await self.api.close()
        self.store.close()
        self.temp.cleanup()

    async def get(self, path):
        """Fetch authenticated JSON from the isolated listener.

        @param path API endpoint to read.
        @return HTTP status and decoded JSON.
        """
        async with self.client.get(self.base + path) as response:
            return response.status, await response.json()

    async def command(self, action, **payload):
        """Submit a command through the machine API.

        @param action Service action name.
        @param payload Command policy fields.
        @return Service result after checking the HTTP envelope succeeded.
        """
        async with self.client.post(
            self.base + "/api/v1/commands", json=dict(action=action, payload=payload)
        ) as response:
            self.assertEqual(response.status, 200)
            return await response.json()

    async def next_matching(self, ws, predicate, timeout=4):
        """Read snapshots until a state condition holds under a bounded deadline.

        @param ws Connected event socket.
        @param predicate Condition on the next authoritative envelope.
        @param timeout Maximum wait, including existing radio heartbeat policy.
        @return Matching snapshot envelope.
        """

        async def receive():
            """Consume full snapshots until the caller's condition is satisfied."""
            while True:
                message = await ws.receive_json()
                if predicate(message):
                    return message

        return await asyncio.wait_for(receive(), timeout)

    async def test_authentication_all_routes_and_websocket(self):
        """Every route rejects missing/wrong/URL credentials before doing work."""
        async with aiohttp.ClientSession() as client:
            for path in [
                "/health",
                "/api/v1/info",
                "/api/v1/snapshot",
                "/api/v1/commands/missing",
                "/api/v1/firmware/install",
                "/api/v1/events",
                "/missing",
            ]:
                for headers in ({}, {"Authorization": "Bearer wrong"}):
                    async with client.get(
                        self.base + path, headers=headers
                    ) as response:
                        self.assertEqual(response.status, 401)
            async with client.post(self.base + "/api/v1/commands", json={}) as response:
                self.assertEqual(response.status, 401)
            with self.assertRaises(aiohttp.WSServerHandshakeError) as error:
                await client.ws_connect(self.base + "/api/v1/events?token=test-secret")
            self.assertEqual(error.exception.status, 401)
        async with self.client.get(
            self.base + "/api/v1/snapshot?token=test-secret"
        ) as response:
            self.assertEqual(response.status, 400)
        self.assertEqual(self.store.recent_results(), [])

    async def test_firmware_install_envelope_and_policy_delegation(self):
        """Firmware accepts only opaque candidate/job IDs and delegates policy once."""
        endpoint = self.base + "/api/v1/firmware/install"
        result = {
            "request_id": "fw-1",
            "candidate_id": "candidate-1",
            "action": "firmware_update",
            "status": "rejected",
            "error": "No qualified candidate",
        }
        with patch.object(
            self.service, "start_firmware_update", return_value=result, create=True
        ) as start:
            for value in (
                [],
                {},
                {"request_id": "fw-1"},
                {"request_id": "fw-1", "candidate_id": 1},
                {"request_id": "fw-1", "candidate_id": "c" * 257},
                {"request_id": "r" * 129, "candidate_id": "candidate-1"},
                {"request_id": "fw-1", "candidate_id": "/tmp/image.gbl"},
                {"request_id": "fw-1", "candidate_id": "https://example/image"},
                {
                    "request_id": "fw-1",
                    "candidate_id": "candidate-1",
                    "version": "0.13",
                },
            ):
                async with self.client.post(endpoint, json=value) as response:
                    self.assertEqual(response.status, 400)
            async with self.client.post(
                endpoint,
                data=b"x" * 4097,
                headers={"Content-Type": "application/json"},
            ) as response:
                self.assertEqual(response.status, 413)
            async with self.client.post(endpoint, data="{}") as response:
                self.assertEqual(response.status, 400)
            async with aiohttp.ClientSession() as unauthenticated:
                async with unauthenticated.post(
                    endpoint, json={"request_id": "fw-1", "candidate_id": "candidate-1"}
                ) as response:
                    self.assertEqual(response.status, 401)
            start.assert_not_called()
            payload = {"request_id": "fw-1", "candidate_id": "candidate-1"}
            async with self.client.post(endpoint, json=payload) as response:
                self.assertEqual(response.status, 200)
                self.assertEqual(await response.json(), result)
            start.assert_called_once_with(payload)
            long_candidate = "a" * 96 + ":" + "b" * 64
            payload = {"request_id": "fw-2", "candidate_id": long_candidate}
            async with self.client.post(endpoint, json=payload) as response:
                self.assertEqual(response.status, 200)
            start.assert_called_with(payload)

    async def test_catalog_refresh_pushes_availability_without_web_or_reconnect(self):
        """Native subscribers see publication and withdrawal from the background watcher."""
        from test_firmware_catalog import fixture
        from livolod.firmware_catalog import provision

        root = Path(self.temp.name)
        source, catalog = root / "source", root / "catalog"
        fixture(source)
        self.service.config = replace(
            self.service.config,
            firmware_bundle_dir=str(catalog),
            serial_baudrate=460800,
            web={"enabled": False},
        )
        self.service.ready = True
        self.service.simulated = False
        manager = self.service.firmware_manager
        manager.observe(
            dict(board="zb-gw04-1v2", fw_version="0.12", proto="3", baud="460800")
        )
        session = self.service.session
        changes = self.store.db.total_changes
        with (
            patch("livolod.firmware_manager.CATALOG_REFRESH_SECONDS", 0.02),
            patch("livolod.firmware_backends.backend_status", return_value=None),
        ):
            watcher = asyncio.create_task(manager.watch_catalog())
            try:
                async with self.client.ws_connect(self.base + "/api/v1/events") as ws:
                    await ws.receive_json()
                    provision(source, catalog, FIRMWARE_COMPAT_VERSION)
                    event = await self.next_matching(
                        ws, lambda e: e["data"]["firmware_update"]["update_available"]
                    )
                    self.assertTrue(
                        event["data"]["firmware_update"]["install_supported"]
                    )
                    (catalog / "manifest.json").unlink()
                    event = await self.next_matching(
                        ws,
                        lambda e: not e["data"]["firmware_update"]["install_supported"],
                    )
                    self.assertEqual(
                        event["data"]["firmware_update"]["blocked_reason"],
                        "bundle_missing",
                    )
            finally:
                watcher.cancel()
                await asyncio.gather(watcher, return_exceptions=True)
        self.assertEqual(self.service.session, session)
        self.assertEqual(self.store.db.total_changes, changes)
        self.assertIsNone(self.store.firmware_job())

    async def test_transfer_progress_is_additive_bounded_and_never_overall(self):
        """Expose measured transfer detail while rejecting nonfinite or invalid values."""
        firmware = dict(stage="transfer", in_progress=True, progress_percent=None)
        with patch.object(
            self.service, "firmware_update_snapshot", side_effect=lambda: dict(firmware)
        ):
            for value in (
                0,
                42.5,
                100,
                None,
                -1,
                101,
                True,
                "50",
                float("nan"),
                float("inf"),
                10**400,
            ):
                firmware["transfer_percent"] = value
                async with self.client.get(self.base + "/api/v1/snapshot") as response:
                    data = (await response.json())["data"]["firmware_update"]
                expected = (
                    value if type(value) in (int, float) and 0 <= value <= 100 else None
                )
                self.assertEqual(data["transfer_percent"], expected)
                self.assertIsNone(data["progress_percent"])
                self.assertEqual(data["stage"], "transfer")

    async def test_firmware_snapshots_keep_status_during_radio_maintenance(self):
        """Firmware job changes reach subscribers while raw worker data stays private."""
        firmware = {
            "board": "zb-gw04-1v2",
            "installed_version": "0.12",
            "latest_version": "0.13",
            "candidate_id": "candidate-1",
            "install_supported": True,
            "in_progress": False,
            "stage": "idle",
            "progress_percent": None,
            "raw": "secret-worker-output",
            "path": "/private/firmware.gbl",
        }
        with patch.object(
            self.service,
            "firmware_update_snapshot",
            side_effect=lambda: dict(firmware),
            create=True,
        ):
            async with self.client.ws_connect(self.base + "/api/v1/events") as ws:
                initial = await ws.receive_json()
                self.assertEqual(initial["data"]["firmware_update"]["stage"], "idle")
                firmware.update(in_progress=True, stage="transfer", request_id="fw-1")
                self.service.notify_changed()
                event = await self.next_matching(
                    ws, lambda value: value["data"]["firmware_update"]["in_progress"]
                )
                self.assertFalse(event["data"]["ready"])
                self.assertFalse(event["data"]["controls_enabled"])
                self.assertIsNone(event["data"]["firmware_update"]["progress_percent"])
                self.assertNotIn("secret-worker-output", json.dumps(event))
                self.assertNotIn("/private/firmware.gbl", json.dumps(event))
                self.assertEqual((await self.get("/health"))[0], 200)
                firmware.update(
                    in_progress=False, status="unknown", recovery="recovery_required"
                )
                self.service.notify_changed()
                event = await self.next_matching(
                    ws,
                    lambda value: value["data"]["firmware_update"].get("recovery"),
                )
                self.assertFalse(event["data"]["controls_enabled"])
                self.assertFalse(event["data"]["firmware_update"]["in_progress"])

    async def test_offline_health_info_and_secret_free_snapshot(self):
        """Liveness works before radio readiness and snapshots expose only public fields."""
        self.store.save_mqtt_settings(dict(self.config.mqtt, password="saved-secret"))
        self.service.diagnostics.update(
            raw="raw-packet",
            password="diagnostic-secret",
            query_transaction={"report": "raw-report"},
        )
        self.service.states["46715621"] = dict(
            state_bits=1, outputs={"1": "ON", "2": "OFF"}, raw="packet"
        )
        self.assertEqual((await self.get("/health"))[0], 200)
        _, info = await self.get("/api/v1/info")
        self.assertEqual(info["host_version"], FIRMWARE_COMPAT_VERSION)
        self.assertEqual(info["daemon_version"], __version__)
        self.assertEqual(info["firmware_compat_version"], FIRMWARE_COMPAT_VERSION)
        self.assertEqual(info["uart_protocol"], 3)
        self.assertEqual(info["api_version"], 1)
        self.assertTrue(info["simulated"])
        _, envelope = await self.get("/api/v1/snapshot")
        self.assertFalse(envelope["data"]["ready"])
        for key in (
            "daemon_version",
            "firmware_compat_version",
            "api_version",
            "uart_protocol",
            "host_version",
        ):
            self.assertEqual(envelope["data"]["diagnostics"][key], info[key])
        self.assertEqual(envelope["data"]["devices"][0]["state"]["bits"], 1)
        self.assertEqual(envelope["data"]["devices"][0]["availability"], "offline")
        encoded = json.dumps(envelope)
        for forbidden in (
            "test-secret",
            "saved-secret",
            "diagnostic-secret",
            "raw-packet",
            "raw-report",
            "csrf",
            "mqtt",
            "packet",
        ):
            self.assertNotIn(forbidden, encoded)

    async def test_envelopes_size_and_policy_rejection(self):
        """Bound request bodies while keeping policy rejection in HTTP 200 results."""
        for value in (
            [],
            {},
            {"action": [], "payload": {}},
            {"action": "set", "payload": []},
            {"action": "snapshot", "payload": {}},
            {"action": "set", "payload": {}, "extra": 1},
        ):
            async with self.client.post(
                self.base + "/api/v1/commands", json=value
            ) as response:
                self.assertEqual(response.status, 400)
        async with self.client.post(
            self.base + "/api/v1/commands",
            data=b"x" * 4097,
            headers={"Content-Type": "application/json"},
        ) as response:
            self.assertEqual(response.status, 413)
        result = await self.command("refresh", request_id="offline", device="46715621")
        self.assertEqual(result["status"], "rejected")
        self.assertEqual(result["error"], "radio is offline")
        self.assertEqual(
            (await self.get("/api/v1/commands/offline"))[1]["status"], "rejected"
        )
        self.assertEqual((await self.get("/api/v1/commands/unknown"))[0], 404)

    async def test_registry_dedup_and_reconnect_snapshot(self):
        """Registry/result events are authoritative and retrying an ID never replays work."""
        async with self.client.ws_connect(self.base + "/api/v1/events") as ws:
            initial = await ws.receive_json()
            result = await self.command(
                "rename", request_id="rename-1", device="46715621", name="Kitchen"
            )
            event = await self.next_matching(
                ws, lambda e: e["data"]["devices"][0]["name"] == "Kitchen"
            )
            self.assertGreater(event["sequence"], initial["sequence"])
            self.assertEqual(event["epoch"], initial["epoch"])
            self.assertTrue(
                any(r["request_id"] == "rename-1" for r in event["data"]["results"])
            )
            duplicate = await self.command(
                "rename", request_id="rename-1", device="46715621", name="Kitchen"
            )
            self.assertEqual(result, duplicate)
            changed = await self.command(
                "rename", request_id="rename-1", device="46715621", name="Other"
            )
            self.assertEqual(changed["status"], "rejected")
        async with self.client.ws_connect(self.base + "/api/v1/events") as ws:
            restored = await ws.receive_json()
            self.assertEqual(restored["data"]["devices"][0]["name"], "Kitchen")
            await self.command("remove", request_id="remove-1", device="46715621")
            empty = await self.next_matching(ws, lambda e: not e["data"]["devices"])
            self.assertGreater(empty["sequence"], restored["sequence"])

    async def test_pending_notifications_and_restart_reconciliation(self):
        """Pending commands are visible, survive retry, and become unknown after restart."""
        self.service.ready = True
        async with self.client.ws_connect(self.base + "/api/v1/events") as ws:
            await ws.receive_json()
            result = await self.command(
                "refresh", request_id="pending-1", device="46715621"
            )
            self.assertEqual(result["status"], "pending")
            event = await self.next_matching(
                ws,
                lambda e: any(
                    r["request_id"] == "pending-1" for r in e["data"]["results"]
                ),
            )
            self.assertEqual(event["data"]["results"][0]["status"], "pending")
            await self.command("refresh", request_id="pending-1", device="46715621")
            self.assertEqual(self.service.commands.qsize(), 1)
        old_epoch = self.api.epoch
        await self.api.close()
        self.store.close()
        self.store = Store(self.config.database, "test")
        self.service = Service(self.config, self.store, simulated=True)
        self.api = NativeAPI(self.service, token="test-secret", port=0)
        await self.api.start()
        self.base = f"http://127.0.0.1:{self.api.port}"
        status, result = await self.get("/api/v1/commands/pending-1")
        self.assertEqual(status, 200)
        self.assertEqual(result["status"], "unknown")
        self.assertNotEqual(self.api.epoch, old_epoch)
        result = await self.command(
            "refresh", request_id="pending-1", device="46715621"
        )
        self.assertEqual(result["status"], "unknown")
        self.assertTrue(self.service.commands.empty())

    async def test_state_availability_pairing_and_readiness_events_without_mqtt(self):
        """Native clients observe each state category with no Broker instance."""
        async with self.client.ws_connect(self.base + "/api/v1/events") as ws:
            previous = await ws.receive_json()
            self.service.ready = True
            self.service.snapshot()
            ready = await self.next_matching(ws, lambda e: e["data"]["ready"])
            self.assertGreater(ready["sequence"], previous["sequence"])
            self.service.states["46715621"] = dict(
                state_bits=2, outputs={"1": "OFF", "2": "ON"}
            )
            self.service.seen["46715621"] = time.monotonic()
            self.service.publish("46715621/state", self.service.states["46715621"])
            online = await self.next_matching(
                ws, lambda e: e["data"]["devices"][0]["availability"] == "online"
            )
            self.assertEqual(online["data"]["devices"][0]["state"]["bits"], 2)
            self.service.seen["46715621"] = (
                time.monotonic() - self.config.offline_after - 1
            )
            offline = await self.next_matching(
                ws, lambda e: e["data"]["devices"][0]["availability"] == "offline"
            )
            self.assertGreater(offline["sequence"], online["sequence"])
            self.service.pairing = dict(
                device="46715621", request_id="pair-1", deadline=time.monotonic() + 20
            )
            self.service.snapshot()
            await self.next_matching(ws, lambda e: e["data"]["pairing"] is not None)
            self.service.finish_pairing("cancelled")
            await self.next_matching(ws, lambda e: e["data"]["pairing"] is None)
            self.service.ready = False
            self.service.snapshot()
            await self.next_matching(ws, lambda e: not e["data"]["ready"])
        self.assertIsNone(self.service.broker)

    async def test_snapshot_registration_race_and_coalescing(self):
        """A mutation at subscription registration is present initially or follows it."""
        original = self.api.flush
        fired = False

        def racing_flush():
            """Schedule a change at the exact point the initial snapshot is taken."""
            nonlocal fired
            original()
            if not fired:
                fired = True
                asyncio.get_running_loop().call_soon(
                    self.service.submit,
                    "rename",
                    dict(request_id="race", device="46715621", name="Raced"),
                )

        with patch.object(self.api, "flush", racing_flush):
            async with self.client.ws_connect(self.base + "/api/v1/events") as ws:
                await self.next_matching(
                    ws, lambda e: e["data"]["devices"][0]["name"] == "Raced"
                )
        initial = self.api.sequence
        self.service.ready = True
        for _ in range(10):
            self.service.notify_changed()
        await asyncio.sleep(0)
        self.assertEqual(self.api.sequence, initial + 1)

    async def test_simulated_commands_publish_confirmed_results_and_output_state(self):
        """Use actual service workers and simulator reports through the machine API."""
        self.task = asyncio.create_task(self.service.run())
        async with self.client.ws_connect(self.base + "/api/v1/events") as ws:
            await self.next_matching(ws, lambda e: e["data"]["ready"])
            for action, request_id, extra in (
                ("set", "sim-set", {"mask": 1, "value": 1}),
                ("refresh", "sim-refresh", {}),
            ):
                accepted = await self.command(
                    action, request_id=request_id, device="46715621", **extra
                )
                self.assertEqual(accepted["status"], "pending")
                event = await self.next_matching(
                    ws,
                    lambda e: any(
                        r["request_id"] == request_id and r["status"] != "pending"
                        for r in e["data"]["results"]
                    ),
                    timeout=20,
                )
                result = next(
                    r for r in event["data"]["results"] if r["request_id"] == request_id
                )
                self.assertEqual(result["status"], "confirmed")
                self.assertEqual(event["data"]["devices"][0]["state"]["bits"] & 1, 1)
                self.assertEqual(
                    (await self.get("/api/v1/commands/" + request_id))[1]["status"],
                    "confirmed",
                )

    async def test_event_connection_limit_and_send_timeout(self):
        """Reject excess subscriptions and disconnect a writer stalled on one snapshot."""
        self.api.max_clients = 1
        async with self.client.ws_connect(self.base + "/api/v1/events") as ws:
            await ws.receive_json()
            with self.assertRaises(aiohttp.WSServerHandshakeError) as error:
                await self.client.ws_connect(self.base + "/api/v1/events")
            self.assertEqual(error.exception.status, 503)
        while self.api.clients:
            await asyncio.sleep(0)

        async def stalled_send(ws, data, **kwargs):
            """Simulate TCP backpressure even when no subsequent state changes arrive.

            @param ws Unused stalled WebSocket.
            @param data Unused snapshot text.
            @param kwargs Unused WebSocket send options.
            """
            await asyncio.Event().wait()

        self.api.send_timeout = 0.02
        with patch("livolod.native_api.web.WebSocketResponse.send_str", stalled_send):
            async with self.client.ws_connect(self.base + "/api/v1/events") as ws:
                message = await asyncio.wait_for(ws.receive(), 2)
                self.assertEqual(message.type, aiohttp.WSMsgType.CLOSE)
                self.assertEqual(message.data, 1013)

    async def test_slow_client_queue_is_bounded_and_other_clients_progress(self):
        """A blocked writer is closed without blocking service changes or healthy clients."""
        blocked = asyncio.Event()
        original = self.api.send_events
        calls = 0

        async def slow_sender(ws, queue):
            """Hold the first subscriber writer while allowing a normal second one.

            @param ws Subscriber socket.
            @param queue Bounded outgoing snapshot queue.
            """
            nonlocal calls
            calls += 1
            if calls == 1:
                await blocked.wait()
            else:
                await original(ws, queue)

        with patch.object(self.api, "send_events", slow_sender):
            async with self.client.ws_connect(self.base + "/api/v1/events") as slow:
                async with self.client.ws_connect(
                    self.base + "/api/v1/events"
                ) as healthy:
                    await healthy.receive_json()
                    for n in range(self.api.queue_size + 1):
                        self.service.diagnostics["rx_gaps"] = n + 1
                        self.service.notify_changed()
                        await self.next_matching(
                            healthy,
                            lambda e: e["data"]["diagnostics"]["rx_gaps"] == n + 1,
                        )
                    message = await asyncio.wait_for(slow.receive(), 2)
                    self.assertEqual(message.type, aiohttp.WSMsgType.CLOSE)
                    self.assertEqual(message.data, 1013)
                    self.assertEqual(len(self.api.clients), 1)

    async def test_disabled_mqtt_does_not_restore_credentials_or_enable_editor(self):
        """Saved broker overrides cannot turn MQTT back on in broker-free mode."""
        self.store.save_mqtt_settings(
            dict(host="broker", username="user", password="secret")
        )
        service = Service(self.config, self.store, simulated=True)
        self.assertEqual(service.config.mqtt, {"enabled": False})
        self.assertFalse(service.mqtt_summary()["enabled"])
        self.assertFalse(service.mqtt_summary()["editable"])
        service.mqtt_factory = object
        with self.assertRaises(ValueError):
            await service.configure_mqtt({})
        self.assertEqual(self.store.mqtt_settings({})["password"], "secret")

    async def test_missing_api_secret_fails_before_binding(self):
        """An enabled machine API cannot accidentally become unauthenticated."""
        config = replace(
            self.config,
            api=dict(
                enabled=True,
                host="127.0.0.1",
                port=8090,
                token_env="NATIVE_API_TEST_SECRET",
            ),
        )
        with patch.dict(os.environ, {}, clear=True), self.assertRaises(ValueError):
            await start_api(self.service, config)


class BrokerFreeStartupTests(unittest.IsolatedAsyncioTestCase):
    async def test_entrypoint_starts_api_before_unavailable_radio_without_broker(self):
        """Exercise startup/shutdown wiring while the serial adapter cannot open."""
        from livolod.__main__ import run

        with tempfile.TemporaryDirectory() as directory:
            with socket.socket() as sock:
                sock.bind(("127.0.0.1", 0))
                port = sock.getsockname()[1]
            config = replace(
                load(EXAMPLE),
                database=directory + "/state.db",
                mqtt={"enabled": False},
                api=dict(
                    enabled=True,
                    host="127.0.0.1",
                    port=port,
                    token_env="NATIVE_API_TEST_SECRET",
                ),
            )
            captured = []
            original = Service.run

            async def capture(service):
                """Capture the service so the fixture can request normal shutdown.

                @param service Running service under test.
                """
                captured.append(service)
                await original(service)

            with (
                patch.dict(os.environ, {"NATIVE_API_TEST_SECRET": "test-secret"}),
                patch(
                    "livolod.__main__.Broker",
                    side_effect=AssertionError("Broker constructed"),
                ),
                patch(
                    "livolod.service.Gateway.open", side_effect=OSError("radio absent")
                ),
                patch.object(Service, "run", capture),
            ):
                task = asyncio.create_task(run(config))
                try:

                    async def wait_service():
                        """Wait for startup to reach radio opening or fail promptly."""
                        while not captured:
                            if task.done():
                                task.result()
                            await asyncio.sleep(0.01)

                    await asyncio.wait_for(wait_service(), 3)
                    async with aiohttp.ClientSession(
                        headers={"Authorization": "Bearer test-secret"}
                    ) as client:
                        async with client.get(
                            f"http://127.0.0.1:{port}/health"
                        ) as response:
                            self.assertEqual(response.status, 200)
                        async with client.get(
                            f"http://127.0.0.1:{port}/api/v1/snapshot"
                        ) as response:
                            self.assertFalse((await response.json())["data"]["ready"])
                finally:
                    if captured:
                        captured[0].stop.set()
                    else:
                        task.cancel()
                    await asyncio.wait_for(task, 3)
