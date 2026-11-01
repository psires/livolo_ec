"""Firmware HTTP authorization and delegation with no serial or update worker."""

import asyncio
import http.client
import json
import unittest

from livolod.web import WebServer


class FirmwareService:
    """Record owner-loop calls and model durable results without running an update."""

    def __init__(self):
        """Create an isolated candidate and result ledger for each HTTP test."""
        self.calls = []
        self.results = {}
        self.loop = asyncio.get_running_loop()
        self.firmware = {
            "board": "codm-cc2652p",
            "installed_version": "0.12",
            "latest_version": "0.13",
            "candidate_id": "ti-test-artifact",
            "update_available": True,
            "install_supported": True,
            "blocked_reason": None,
            "in_progress": False,
            "stage": None,
            "progress_percent": None,
            "request_id": None,
            "status": None,
            "error": None,
            "recovery": None,
            "rollback_supported": True,
        }

    def api_snapshot(self):
        """Expose maintenance status even while the simulated radio is unavailable."""
        return {"ready": False, "firmware_update": self.firmware, "devices": []}

    def start_firmware_update(self, payload):
        """Record the install request without transferring or flashing anything.

        @param payload Exact request and candidate identifiers from the browser.
        @return Previously stored result or a newly accepted pending result.
        """
        assert asyncio.get_running_loop() is self.loop
        self.calls.append(dict(payload))
        ident = payload["request_id"]
        if ident not in self.results:
            status = (
                "pending"
                if payload["candidate_id"] == self.firmware["candidate_id"]
                else "rejected"
            )
            self.results[ident] = {"request_id": ident, "status": status}
        return self.results[ident]

    def command_result(self, request_id):
        """Resolve an existing identity without invoking the install operation.

        @param request_id Previously submitted firmware request identity.
        @return Durable result or None when the request was never accepted.
        """
        return self.results.get(request_id)


class FirmwareWebTests(unittest.IsolatedAsyncioTestCase):
    """Exercise the browser firmware route against an isolated HTTP server."""

    async def asyncSetUp(self):
        """Bind a local server and a fake service, without any radio connection."""
        self.service = FirmwareService()
        self.server = WebServer(self.service, port=0, token="test-access")
        await self.server.start()

    async def asyncTearDown(self):
        """Release the local server after all requests finish."""
        await self.server.close()

    async def request(self, method, path, payload=None, headers=None):
        """Send one local HTTP request with overridable browser authorization.

        @param method HTTP method such as GET or POST.
        @param path Browser endpoint to exercise.
        @param payload JSON-serializable request body, or None for a read.
        @param headers Header overrides, including intentionally invalid values.
        @return HTTP status and decoded JSON or asset text.
        """
        request_headers = {
            "Host": f"127.0.0.1:{self.server.port}",
            "Authorization": "Bearer test-access",
            "Origin": f"http://127.0.0.1:{self.server.port}",
            "Content-Type": "application/json",
            "X-Livolo-Token": self.server.token,
        }
        request_headers.update(headers or {})

        def exchange():
            """Perform blocking HTTP I/O outside the service event loop."""
            connection = http.client.HTTPConnection(
                "127.0.0.1", self.server.port, timeout=5
            )
            try:
                body = None if payload is None else json.dumps(payload)
                connection.request(method, path, body, request_headers)
                response = connection.getresponse()
                data = response.read().decode()
                if response.getheader("Content-Type", "").startswith(
                    "application/json"
                ):
                    data = json.loads(data)
                return response.status, data
            finally:
                connection.close()

        return await asyncio.to_thread(exchange)

    async def test_install_and_lookup_share_durable_identity(self):
        """An accepted install delegates once; status reads do not execute it again."""
        payload = {"request_id": "web-update-1", "candidate_id": "ti-test-artifact"}
        status, result = await self.request("POST", "/api/firmware/install", payload)
        self.assertEqual(status, 200)
        self.assertEqual(result, {"request_id": "web-update-1", "status": "pending"})
        self.assertEqual(self.service.calls, [payload])
        self.service.results[payload["request_id"]]["status"] = "confirmed"
        status, result = await self.request("GET", "/api/commands/web-update-1")
        self.assertEqual((status, result["status"]), (200, "confirmed"))
        self.assertEqual(self.service.calls, [payload])
        status, snapshot = await self.request("GET", "/api/state")
        self.assertEqual(status, 200)
        self.assertFalse(snapshot["ready"])
        self.assertIn("firmware_update", snapshot)

    async def test_rejections_and_duplicate_requests_are_service_owned(self):
        """The bridge returns backend rejections and preserves its deduplication key."""
        payload = {"request_id": "web-update-2", "candidate_id": "stale-artifact"}
        first = await self.request("POST", "/api/firmware/install", payload)
        second = await self.request("POST", "/api/firmware/install", payload)
        self.assertEqual(first, second)
        self.assertEqual(first[1]["status"], "rejected")
        self.assertEqual(len(self.service.results), 1)
        self.assertEqual(self.service.calls, [payload, payload])

    async def test_install_requires_existing_http_security_guards(self):
        """Invalid bearer, origin, host or CSRF never reaches firmware maintenance."""
        payload = {"request_id": "web-denied", "candidate_id": "ti-test-artifact"}
        for headers, expected in (
            ({"Authorization": "Bearer wrong"}, 401),
            ({"Origin": "https://untrusted.example"}, 403),
            ({"Host": "untrusted.example"}, 403),
            ({"X-Livolo-Token": "wrong"}, 403),
            ({"Sec-Fetch-Site": "cross-site"}, 403),
            ({"Content-Type": "text/plain"}, 415),
        ):
            with self.subTest(headers=headers):
                status, _ = await self.request(
                    "POST", "/api/firmware/install", payload, headers
                )
                self.assertEqual(status, expected)
        self.assertEqual(self.service.calls, [])

    async def test_install_accepts_only_identifier_envelope(self):
        """Paths, URLs, extra fields and command-route aliases cannot select firmware."""
        valid = {"request_id": "web-bad-body", "candidate_id": "ti-test-artifact"}
        for body in (
            None,
            [],
            {},
            {"request_id": "missing-candidate"},
            dict(valid, path="/tmp/firmware.bin"),
            dict(valid, url="https://untrusted.example/firmware"),
            dict(valid, version="0.99"),
            dict(valid, candidate_id={"path": "/tmp/firmware.bin"}),
            dict(valid, request_id=""),
            dict(valid, request_id="x" * 129),
            dict(valid, candidate_id="x" * 257),
            dict(valid, candidate_id="/tmp/firmware.bin"),
            dict(valid, candidate_id="https://untrusted.example/firmware"),
        ):
            with self.subTest(body=body):
                status, _ = await self.request("POST", "/api/firmware/install", body)
                self.assertIn(status, (400, 413))
        status, _ = await self.request(
            "POST", "/api/command", {"action": "firmware_install", "payload": valid}
        )
        self.assertEqual(status, 400)
        self.assertEqual(self.service.calls, [])

    async def test_candidate_id_accepts_full_artifact_name_and_digest(self):
        """A 96-character artifact name plus a full digest fits the candidate contract."""
        candidate_id = "a" * 96 + ":" + "b" * 64
        self.assertEqual(len(candidate_id), 161)
        self.service.firmware["candidate_id"] = candidate_id
        payload = {"request_id": "web-full-digest", "candidate_id": candidate_id}
        status, result = await self.request("POST", "/api/firmware/install", payload)
        self.assertEqual((status, result["status"]), (200, "pending"))
        self.assertEqual(self.service.calls, [payload])

    async def test_ingress_install_uses_context_bound_csrf(self):
        """Trusted ingress can install only with the origin/path-bound session token."""
        await self.server.close()
        self.server = WebServer(
            self.service,
            port=0,
            ingress={
                "enabled": True,
                "trusted_proxies": ["127.0.0.1/32"],
                "allowed_origins": ["https://ha.example"],
            },
        )
        await self.server.start()
        headers = {
            "Origin": "https://ha.example",
            "X-Ingress-Path": "/api/hassio_ingress/test-session",
            "X-Forwarded-Host": "ha.example",
            "X-Forwarded-Proto": "https",
        }
        status, snapshot = await self.request("GET", "/api/state", headers=headers)
        self.assertEqual(status, 200)
        payload = {"request_id": "web-ingress", "candidate_id": "ti-test-artifact"}
        status, _ = await self.request(
            "POST", "/api/firmware/install", payload, headers
        )
        self.assertEqual(status, 403)
        headers["X-Livolo-Token"] = snapshot["csrf_token"]
        status, result = await self.request(
            "POST", "/api/firmware/install", payload, headers
        )
        self.assertEqual((status, result["status"]), (200, "pending"))
        self.assertEqual(self.service.calls, [payload])
