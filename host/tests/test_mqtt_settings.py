"""Settings persistence, secret handling and real MQTT client replacement."""

import asyncio
from dataclasses import replace
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from livolod.config import load
from livolod.mqtt import Broker
from livolod.mqtt_settings import updated_settings
from livolod.service import Service
from livolod.simulator import Simulator
from livolod.storage import Store
from test_integrations import TestBroker, mqtt_string


class SettingsTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        """Prepare isolated state and asynchronous collaborators for the next test."""
        self.temp = tempfile.TemporaryDirectory()
        self.config = replace(
            load(Path(__file__).resolve().parents[1] / "config.example.json"),
            database=self.temp.name + "/state.db",
        )
        self.store = Store(self.config.database, "test")
        self.service = Service(self.config, self.store, gateway_factory=Simulator)
        self.service.mqtt_factory = Broker

    async def asyncTearDown(self):
        """Stop asynchronous collaborators and release temporary test state."""
        if self.service.broker:
            await self.service.broker.close()
        self.store.close()
        self.temp.cleanup()

    def request(self, **values):
        """Build or submit a command request using this fixture's defaults.

        @param values Request fields overriding the fixture defaults.
        """
        return dict(
            host="127.0.0.1",
            port=1883,
            base_topic="home/livolo",
            username="tester",
            password_action="replace",
            password="fixture-secret",
            **values,
        )

    async def test_simulation_cannot_publish_saved_production_namespace(self):
        """Verify simulation cannot publish saved production namespace."""
        self.store.save_mqtt_settings(dict(self.config.mqtt, base_topic="production"))
        service = Service(
            self.config, self.store, gateway_factory=Simulator, simulated=True
        )
        self.assertEqual(service.config.mqtt["base_topic"], "production/simulated")
        self.assertTrue(service.config.mqtt["client_id"].endswith("-simulated"))
        service.mqtt_factory = Broker
        self.assertFalse(service.mqtt_summary()["editable"])
        with self.assertRaises(ValueError):
            await service.configure_mqtt(self.request())

    async def test_validation_and_environment_password_semantics(self):
        """Verify validation and environment password semantics."""
        base = dict(
            self.config.mqtt, username="original", password_env="FIXTURE_PASSWORD"
        )
        request = self.request()
        for update in (
            {"host": "mqtt://broker"},
            {"host": "broker:1883"},
            {"port": True},
            {"port": 0},
            {"base_topic": "bad/#"},
            {"base_topic": "/bad"},
            {"username": ""},
            {"password_action": "other"},
            {"password": ""},
            {"password": "\0"},
            {"password_action": "keep"},
            {"tls_ca": "untrusted-path"},
            {"password": "x" * 1025},
        ):
            with self.subTest(update=update), self.assertRaises(ValueError):
                updated_settings(base, dict(request, **update), self.config.gateway)
        keep = dict(request, password_action="keep")
        keep.pop("password")
        saved = updated_settings(base, keep, self.config.gateway)
        self.assertEqual(saved["password_env"], "FIXTURE_PASSWORD")
        saved = updated_settings(saved, request, self.config.gateway)
        self.assertNotIn("password_env", saved)
        saved = updated_settings(
            saved, dict(keep, username="", password_action="clear"), self.config.gateway
        )
        self.assertNotIn("password", saved)
        self.assertNotIn("username", saved)
        tls = dict(
            base, tls_ca="/cert/ca", tls_cert="/cert/client", tls_key="/cert/key"
        )
        self.assertEqual(
            updated_settings(tls, keep, self.config.gateway)["tls_key"], "/cert/key"
        )

    async def test_persistence_redaction_and_start_failure(self):
        """Verify persistence redaction and start failure."""

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

        self.service.mqtt_factory = FakeBroker
        summary = await self.service.configure_mqtt(self.request())
        self.assertTrue(summary["password_set"])
        self.assertNotIn("fixture-secret", json.dumps(self.service.api_snapshot()))
        self.assertEqual(self.store.recent_results(), [])
        self.assertEqual(os.stat(self.config.database).st_mode & 0o777, 0o600)
        self.store.close()
        self.store = Store(self.config.database, "test")
        restored = Service(self.config, self.store)
        self.assertEqual(restored.config.mqtt["password"], "fixture-secret")
        self.assertEqual(restored.config.mqtt["base_topic"], "home/livolo")
        self.service.store = self.store
        old = dict(self.service.config.mqtt)
        with patch.object(FakeBroker, "start", side_effect=RuntimeError("failure")):
            with self.assertRaises(RuntimeError):
                await self.service.configure_mqtt(
                    dict(self.request(), host="another-host")
                )
        self.assertEqual(self.store.mqtt_settings({}), old)
        self.assertEqual(self.service.config.mqtt, old)

    async def test_real_reconnect_topic_credentials_and_radio_preserved(self):
        """Verify real reconnect topic credentials and radio preserved."""
        peer = TestBroker()
        server = await asyncio.start_server(peer.connection, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        self.service.config = replace(
            self.service.config, mqtt=dict(self.config.mqtt, port=port)
        )
        old = Broker(
            self.service.config.mqtt, self.service.receive, self.service.snapshot
        )
        self.service.broker = old
        old.start()
        task = asyncio.create_task(self.service.run())
        try:
            await peer.wait_for(lambda: old.connected and self.service.ready)
            session, gateway = self.service.session, self.service.gateway
            await self.service.configure_mqtt(dict(self.request(), port=port))
            await peer.wait_for(
                lambda: self.service.broker.connected
                and any(t == "home/livolo/bridge/devices" for t, _, _ in peer.messages)
            )
            self.assertEqual(peer.connections, 2)
            self.assertTrue(
                any(
                    t == "livolo/bridge/state" and p == b"offline"
                    for t, p, _ in peer.messages
                )
            )
            self.assertIs(self.service.gateway, gateway)
            self.assertEqual(self.service.session, session)
            self.assertFalse(self.service.reconfigure.is_set())
            self.assertTrue(
                peer.connect_packets[-1].endswith(
                    mqtt_string("tester") + mqtt_string("fixture-secret")
                )
            )
            self.assertEqual(peer.connect_packets[-1][7] & 0xC0, 0xC0)
            await peer.publish(
                "home/livolo/bridge/request/rename",
                {
                    "device": "46715621",
                    "name": "MQTT renamed",
                    "request_id": "new-prefix",
                },
            )
            await peer.wait_for(
                lambda: self.service.devices["46715621"].name == "MQTT renamed"
            )
            old._deliver(
                "livolo/bridge/request/rename",
                b'{"device":"46715621","name":"Late old request"}',
                False,
            )
            self.assertEqual(self.service.devices["46715621"].name, "MQTT renamed")
            # Credential changes must not leak into retained snapshots or ledger.
            self.assertFalse(
                any(b"fixture-secret" in payload for _, payload, _ in peer.messages)
            )
            self.assertNotIn("fixture-secret", json.dumps(self.store.recent_results()))
        finally:
            self.service.stop.set()
            await task
            await self.service.broker.close()
            await peer.wait_for(lambda: not peer.tasks)
            server.close()
            await server.wait_closed()


if __name__ == "__main__":
    unittest.main()
