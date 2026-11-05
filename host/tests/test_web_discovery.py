"""Exercise experimental discovery through the real browser HTTP contract."""

import asyncio
from dataclasses import replace
import http.client
import json
from pathlib import Path
import tempfile
import time
import unittest

from livolod.config import load
from livolod.service import Service
from livolod.storage import Store
from livolod.web import WebServer
from test_device_profile import ONE, TWO


class DiscoveryWebTests(unittest.IsolatedAsyncioTestCase):
    """Use captured announcements and an isolated registry without a radio worker."""

    async def asyncSetUp(self):
        """Start only the web listener with a ready mock session and an empty registry."""
        self.temp = tempfile.TemporaryDirectory()
        config = replace(
            load(Path(__file__).parents[1] / "config.example.json"),
            devices=(),
            database=self.temp.name + "/state.db",
        )
        self.store = Store(config.database, "web-discovery")
        self.service = Service(config, self.store)
        self.service.ready = True
        self.service.session = "web-test"
        self.server = WebServer(self.service, port=0)
        await self.server.start()

    async def asyncTearDown(self):
        """Release the listener and temporary registry after each HTTP scenario."""
        await self.server.close()
        self.store.close()
        self.temp.cleanup()

    async def exchange(self, payload=None):
        """Read the snapshot or submit one CSRF-protected add command.

        @param payload Optional add fields; None reads the browser snapshot.
        @return HTTP status and decoded JSON response.
        """

        def request():
            """Perform blocking loopback HTTP outside the daemon owner event loop."""
            connection = http.client.HTTPConnection("127.0.0.1", self.server.port)
            try:
                if payload is None:
                    connection.request("GET", "/api/state")
                else:
                    connection.request(
                        "POST",
                        "/api/command",
                        json.dumps({"action": "add", "payload": payload}),
                        {
                            "Content-Type": "application/json",
                            "X-Livolo-Token": self.server.token,
                        },
                    )
                response = connection.getresponse()
                return response.status, json.loads(response.read())
            finally:
                connection.close()

        return await asyncio.to_thread(request)

    def announce(self, raw):
        """Inject a captured frame through the same decoder used by UART reception.

        @param raw CRC-valid one- or two-gang pairing announcement.
        """
        self.service.handle_rx(
            time.monotonic(), f"RX 1 0 -42 99 {len(raw)} {raw.hex()}"
        )

    async def test_detected_profiles_cross_http_and_persist_named_devices(self):
        """Both gang counts survive snapshot serialization and token-bound web addition."""
        for raw, count in ((ONE, 1), (TWO, 2)):
            self.service.ready = True
            self.announce(raw)
            status, snapshot = await self.exchange()
            self.assertEqual(status, 200)
            candidate = next(
                item for item in snapshot["discoveries"] if not item["configured"]
            )
            profile = candidate["profile"]
            self.assertEqual(profile["outputs"], count)
            status, result = await self.exchange(
                dict(
                    request_id=f"web-auto-{count}",
                    device=candidate["device"],
                    name=f"Room {count}",
                    auto_detect=True,
                    profile_token=profile["token"],
                )
            )
            self.assertEqual(status, 200)
            self.assertEqual(result["status"], "confirmed")
            device = self.service.devices[candidate["device"]]
            self.assertEqual(device.outputs, count)
            self.assertEqual(device.name, f"Room {count}")
            self.assertIsNone(self.service.pairing)

    async def test_invalidated_review_rejects_and_manual_fallback_still_works(self):
        """Session drift cannot authorize addition; the explicit manual API stays usable."""
        self.announce(ONE)
        _, snapshot = await self.exchange()
        candidate = snapshot["discoveries"][0]
        self.service.session = "replacement-session"
        _, result = await self.exchange(
            dict(
                request_id="expired-review",
                device=candidate["device"],
                name="Hall",
                auto_detect=True,
                profile_token=candidate["profile"]["token"],
            )
        )
        self.assertEqual(result["status"], "rejected")
        self.assertFalse(self.service.devices)
        _, result = await self.exchange(
            dict(
                request_id="manual-fallback",
                device=candidate["device"],
                name="Hall",
                outputs=1,
                suffix="0001",
            )
        )
        self.assertEqual(result["status"], "confirmed")
        self.assertEqual(self.service.devices[candidate["device"]].outputs, 1)
