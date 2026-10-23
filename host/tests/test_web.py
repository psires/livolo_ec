"""HTTP boundary and actual daemon workflows, with a simulated radio only."""

import asyncio
from dataclasses import replace
import http.client
import json
from pathlib import Path
import tempfile
import time
import unittest

from livolod.config import load
from livolod.protocol import decode
from livolod.service import Service
from livolod.simulator import Simulator
from livolod.storage import Store
from livolod.web import WebServer


class WebTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        """Prepare isolated state and asynchronous collaborators for the next test."""
        self.temp = tempfile.TemporaryDirectory()
        self.config = replace(
            load(Path(__file__).resolve().parents[1] / "config.example.json"),
            database=self.temp.name + "/state.db",
        )
        self.store = Store(self.config.database, "test-network")
        self.service = Service(self.config, self.store, gateway_factory=Simulator)
        self.service.simulated = True
        self.server = WebServer(self.service, port=0)
        await self.server.start()
        self.task = asyncio.create_task(self.service.run())
        await self.wait_ready()

    async def asyncTearDown(self):
        """Stop asynchronous collaborators and release temporary test state."""
        self.service.stop.set()
        await asyncio.wait_for(self.task, 3)
        await self.server.close()
        self.store.close()
        self.temp.cleanup()

    async def wait_ready(self):
        """Wait until the simulated radio session is ready for API requests."""

        async def wait():
            """Wait until the enclosing test's readiness or result condition is satisfied."""
            while not self.service.ready:
                await asyncio.sleep(0.01)

        await asyncio.wait_for(wait(), 3)

    async def http(self, method="GET", path="/api/state", body=None, headers=None):
        """Exchange an HTTP request with the local test server and expose its result.

        @param method HTTP method for the local API request.
        @param path HTTP API path.
        @param body Encoded request or packet payload.
        @param headers Additional request headers.
        """

        def request():
            """Build or submit a command request using this fixture's defaults."""
            conn = http.client.HTTPConnection("127.0.0.1", self.server.port, timeout=5)
            try:
                conn.request(method, path, body=body, headers=headers or {})
                response = conn.getresponse()
                raw = response.read()
                value = (
                    json.loads(raw)
                    if "application/json" in response.headers.get("Content-Type", "")
                    else raw.decode()
                )
                return response.status, dict(response.headers), value
            finally:
                conn.close()

        return await asyncio.to_thread(request)

    async def command(self, action, **payload):
        """Provide the scenario-specific UART response or injected failure.

        @param action Daemon command action.
        @param payload Command parameters supplied by the test.
        """
        status, _, state = await self.http()
        self.assertEqual(status, 200)
        code, _, result = await self.http(
            "POST",
            "/api/command",
            json.dumps({"action": action, "payload": payload}),
            {"Content-Type": "application/json", "X-Livolo-Token": state["csrf_token"]},
        )
        self.assertEqual(code, 200)
        deadline = time.monotonic() + 20
        while result["status"] == "pending" and time.monotonic() < deadline:
            await asyncio.sleep(0.05)
            code, _, result = await self.http(
                path="/api/commands/" + result["request_id"]
            )
            self.assertEqual(code, 200)
        self.assertNotEqual(
            result["status"], "pending", "Command did not finish within 20 seconds"
        )
        return result

    async def test_assets_snapshot_no_cache_and_path_allowlist(self):
        """Verify assets snapshot no cache and path allowlist."""
        for path in ("/", "/app.js", "/style.css", "/api/state"):
            status, headers, value = await self.http(path=path)
            self.assertEqual(status, 200)
            self.assertEqual(headers["Cache-Control"], "no-store")
            self.assertIn("frame-ancestors 'none'", headers["Content-Security-Policy"])
            self.assertNotIn("Access-Control-Allow-Origin", headers)
        status, _, state = await self.http()
        self.assertTrue(state["simulated"])
        self.assertEqual(state["devices"][0]["id"], "46715621")
        self.assertNotIn("database", state)
        for path in (
            "/../config.example.json",
            "/static/../../storage.py",
            "/api/commands/missing",
            "/api/commands/../state",
        ):
            status, _, _ = await self.http(path=path)
            self.assertIn(status, (400, 404))

    async def test_origin_csrf_content_and_host_rejections_never_actuate(self):
        """Verify origin csrf content and host rejections never actuate."""
        _, _, state = await self.http()
        body = json.dumps(
            {"action": "set", "payload": {"device": "46715621", "state": "ON"}}
        )
        base = {
            "Content-Type": "application/json",
            "X-Livolo-Token": state["csrf_token"],
        }
        cases = [
            ({}, 403),
            ({**base, "Origin": "https://attacker.example"}, 403),
            ({**base, "Host": "attacker.example:" + str(self.server.port)}, 403),
            ({**base, "Sec-Fetch-Site": "cross-site"}, 403),
            ({**base, "Content-Type": "text/plain"}, 415),
        ]
        for headers, expected in cases:
            status, _, _ = await self.http("POST", "/api/command", body, headers)
            self.assertEqual(status, expected)
        for bad in (
            "[]",
            '{"action":[],"payload":{}}',
            '{"action":"erase","payload":{}}',
            "bad-json",
        ):
            status, _, _ = await self.http("POST", "/api/command", bad, base)
            self.assertEqual(status, 400)
        status, _, _ = await self.http("POST", "/api/command", "x" * 4097, base)
        self.assertEqual(status, 413)
        # Native startup reports may populate state while requests are rejected.
        # The security boundary must prevent output transmissions regardless.
        sent = [
            decode(bytes.fromhex(command.split()[-1]))
            for command in self.service.gateway.commands
            if command.startswith("SEND ")
        ]
        self.assertFalse(any(packet.opcode == 2 for packet in sent))

    async def test_access_token_required_for_all_api_routes(self):
        """Verify access token required for all api routes."""
        self.server.auth_token = "test-secret"
        for path in ("/api/state", "/api/commands/missing"):
            status, _, _ = await self.http(path=path)
            self.assertEqual(status, 401)
        status, _, _ = await self.http("POST", "/api/command", "{}")
        self.assertEqual(status, 401)
        status, _, _ = await self.http(headers={"Authorization": "Bearer test-secret"})
        self.assertEqual(status, 200)
        self.assertEqual((await self.http(path="/"))[0], 200)
        with self.assertRaises(ValueError):
            WebServer(self.service, host="0.0.0.0", port=0)

    async def test_rename_add_pair_cancel_control_forget_and_restart(self):
        # A literal markup name is returned as data; the browser escapes it on display.
        """Verify rename add pair cancel control forget and restart."""
        name = "<img src=x onerror=alert(1)> Kitchen"
        result = await self.command(
            "rename", device="46715621", name=name, request_id="rename-1"
        )
        self.assertEqual(result["status"], "confirmed")
        self.assertEqual(self.service.devices["46715621"].name, name)
        duplicate = await self.command(
            "rename", device="46715621", name=name, request_id="rename-1"
        )
        for key in ("request_id", "status", "device", "action"):
            self.assertEqual(result[key], duplicate[key])
        self.assertEqual(
            (await self.command("set", device="46715621", mask=1, value=1))["status"],
            "confirmed",
        )
        self.assertEqual(self.service.states["46715621"]["state_bits"], 1)
        result = await self.command("add", device="00112233", name="Bedroom", outputs=1)
        self.assertEqual(result["status"], "confirmed")
        await self.wait_ready()
        # Consume the simulator's startup report before testing an open pairing
        # window and its cancellation; otherwise startup can confirm it first.
        deadline = time.monotonic() + 2
        while "00112233" not in self.service.states and time.monotonic() < deadline:
            await asyncio.sleep(0.01)
        self.assertIn("00112233", self.service.states)
        self.assertEqual(
            (await self.command("pair", device="00112233", seconds=30))["status"],
            "pairing",
        )
        state = (await self.http())[2]
        self.assertEqual(state["pairing"]["device"], "00112233")
        self.assertEqual(
            (await self.command("pair", device="00112233", seconds=0))["status"],
            "confirmed",
        )
        self.assertIsNone((await self.http())[2]["pairing"])
        await self.command("pair", device="00112233", seconds=30)
        self.service.gateway.report("00112233", mask=1, bits=0)
        await asyncio.sleep(0.03)
        self.assertIsNone((await self.http())[2]["pairing"])
        await self.command("remove", device="00112233")
        await self.wait_ready()
        self.assertNotIn("00112233", self.service.devices)
        restored = Service(self.config, self.store, gateway_factory=Simulator)
        self.assertEqual(restored.devices["46715621"].name, name)
        self.assertNotIn("00112233", restored.devices)

    async def test_mqtt_settings_http_security_redaction_and_validation(self):
        """Verify mqtt settings http security redaction and validation."""

        class FakeBroker:
            connected = False

            def __init__(self, config, *_):
                """Prepare the simulated collaborator with the requested test behavior.

                @param config Configuration accepted by the test collaborator.
                @param _ Unused interface arguments.
                """
                self.config = config

            def start(self):
                """Start the test service or broker collaborator for this scenario."""
                pass

            async def close(self):
                """Release resources owned by the simulated collaborator."""
                pass

            def publish(self, *_):
                """Record or deliver a publication for assertions without a production broker.

                @param _ Unused interface arguments.
                """
                pass

            def publish_topic(self, *_, **__):
                """Accept a publication to an explicit topic in the broker test double.

                @param _ Unused interface arguments.
                @param __ Unused interface keyword arguments.
                """
                pass

        self.service.simulated = False
        self.service.mqtt_factory = FakeBroker
        _, _, state = await self.http()
        fields = dict(
            host="broker.home",
            port=1883,
            base_topic="home/livolo",
            username="livolo",
            password_action="replace",
            password="http-fixture-secret",
        )
        body = json.dumps(fields)
        headers = {
            "Content-Type": "application/json",
            "X-Livolo-Token": state["csrf_token"],
        }
        for overrides, status in (
            ({"X-Livolo-Token": ""}, 403),
            ({"Origin": "https://evil.example"}, 403),
            ({"Content-Type": "text/plain"}, 415),
            ({"Sec-Fetch-Site": "cross-site"}, 403),
        ):
            self.assertEqual(
                (
                    await self.http(
                        "POST", "/api/settings/mqtt", body, dict(headers, **overrides)
                    )
                )[0],
                status,
            )
        self.server.auth_token = "web-secret"
        self.assertEqual(
            (await self.http("POST", "/api/settings/mqtt", body, headers))[0], 401
        )
        self.server.auth_token = None
        for bad in ("[]", "{}", json.dumps(dict(fields, password_action="keep"))):
            self.assertEqual(
                (await self.http("POST", "/api/settings/mqtt", bad, headers))[0], 400
            )
        self.assertEqual(self.service.config.mqtt["host"], self.config.mqtt["host"])
        session = self.service.session
        status, _, result = await self.http("POST", "/api/settings/mqtt", body, headers)
        self.assertEqual(status, 200)
        self.assertEqual(result["base_topic"], "home/livolo")
        self.assertTrue(result["password_set"])
        self.assertNotIn("http-fixture-secret", json.dumps(result))
        self.assertNotIn("http-fixture-secret", json.dumps((await self.http())[2]))
        self.assertEqual(self.store.recent_results(), [])
        self.assertEqual(session, self.service.session)
        # Secrets cannot be routed through the durable command endpoint.
        self.assertEqual(
            (
                await self.http(
                    "POST",
                    "/api/command",
                    json.dumps({"action": "mqtt_settings", "payload": fields}),
                    headers,
                )
            )[0],
            400,
        )
        clear = dict(fields, username="", password_action="clear")
        clear.pop("password")
        self.assertFalse(
            (await self.http("POST", "/api/settings/mqtt", json.dumps(clear), headers))[
                2
            ]["password_set"]
        )

    async def test_registry_remains_manageable_without_radio(self):
        """Verify registry remains manageable without radio."""
        self.service.ready = False
        self.assertEqual(
            (await self.command("rename", device="46715621", name="Offline rename"))[
                "status"
            ],
            "confirmed",
        )
        self.assertEqual(
            (await self.command("set", device="46715621", state="ON"))["status"],
            "rejected",
        )
        for fields in (
            {"device": "ffffffff", "name": "Bad", "outputs": 2},
            {"device": "00112233", "name": " ", "outputs": 2},
            {"device": "00112233", "name": "Bad", "outputs": 3},
        ):
            self.assertEqual(
                (await self.command("add", **fields))["status"], "rejected"
            )
        self.assertEqual(len(self.service.devices), 1)


if __name__ == "__main__":
    unittest.main()
