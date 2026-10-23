"""Discovery lifecycle and HA-generated commands over a real MQTT connection."""

import asyncio
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest

from livolod.config import load
from livolod.homeassistant import discovery_messages, STATUS_TOPIC
from livolod.mqtt import Broker
from livolod.service import Service
from livolod.simulator import Simulator
from livolod.storage import Store
from test_daemon import Broker as RecordingBroker
from test_integrations import TestBroker, mqtt_packet, mqtt_string


class DiscoveryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        """Prepare isolated state and asynchronous collaborators for the next test."""
        self.temp = tempfile.TemporaryDirectory()
        self.config = replace(
            load(Path(__file__).resolve().parents[1] / "config.example.json"),
            database=self.temp.name + "/state.db",
            output_mode="experimental_phase",
        )
        self.store = Store(self.config.database, "test")

    async def asyncTearDown(self):
        """Stop asynchronous collaborators and release temporary test state."""
        self.store.close()
        self.temp.cleanup()

    async def test_identity_rename_prefix_and_one_gang_cleanup(self):
        """Verify identity rename prefix and one gang cleanup."""
        before = dict(discovery_messages(self.config, []))
        changed = replace(
            self.config,
            devices=(replace(self.config.devices[0], name="Kitchen", outputs=1),),
            mqtt=dict(self.config.mqtt, base_topic="new/prefix"),
        )
        after = dict(discovery_messages(changed, []))
        self.assertEqual(before.keys(), after.keys())
        first, second = list(after)
        self.assertEqual(after[second], "")
        entity = after[first]
        self.assertEqual(entity["unique_id"], before[first]["unique_id"])
        self.assertEqual(entity["device"]["name"], "Kitchen")
        self.assertEqual(entity["command_topic"], "new/prefix/46715621/set")
        self.assertEqual(entity["availability_mode"], "all")
        self.assertEqual(len(entity["availability"]), 3)
        self.assertFalse(entity["optimistic"])
        self.assertFalse(entity["retain"])

    async def test_removed_device_tombstones_survive_restart(self):
        """Verify removed device tombstones survive restart."""
        broker = RecordingBroker()
        service = Service(self.config, self.store, broker)
        result = service.submit("remove", {"device": "46715621"})
        self.assertEqual(result["status"], "confirmed")
        expected = [
            (topic, "", True) for topic, _ in discovery_messages(self.config, [])
        ]
        for message in expected:
            self.assertIn(message, broker.messages)
        self.store.close()
        self.store = Store(self.config.database, "test")
        broker.messages.clear()
        restarted = Service(self.config, self.store, broker)
        restarted.snapshot()
        for message in expected:
            self.assertIn(message, broker.messages)

    async def test_simulation_never_touches_ha_and_disabled_controls_are_unavailable(
        self,
    ):
        """Verify simulation never touches ha and disabled controls are unavailable."""
        broker = RecordingBroker()
        simulated = Service(self.config, self.store, broker, simulated=True)
        simulated.snapshot()
        self.assertFalse(
            any(topic.startswith("homeassistant/") for topic, _, _ in broker.messages)
        )
        broker.messages.clear()
        disabled = Service(
            replace(self.config, output_mode="disabled"), self.store, broker
        )
        disabled.snapshot()
        self.assertIn(("bridge/controls", "offline", True), broker.messages)
        entity = next(
            value for topic, value, _ in broker.messages if topic.endswith("/config")
        )
        disabled.ready = True
        disabled.receive(entity["command_topic"], entity["payload_on"].encode(), False)
        self.assertTrue(disabled.commands.empty())
        self.assertEqual(broker.messages[-1][1]["status"], "rejected")

    async def test_tcp_discovery_commands_birth_reconnect_and_registry(self):
        """Verify tcp discovery commands birth reconnect and registry."""
        peer = TestBroker()
        server = await asyncio.start_server(peer.connection, "127.0.0.1", 0)
        config = replace(
            self.config,
            mqtt=dict(self.config.mqtt, port=server.sockets[0].getsockname()[1]),
        )
        service = Service(config, self.store, gateway_factory=Simulator)
        broker = Broker(config.mqtt, service.receive, service.snapshot)
        service.broker = broker
        task = asyncio.create_task(service.run())
        broker.start()

        def configs():
            """Collect retained Home Assistant discovery configurations from test traffic."""
            return {
                topic: json.loads(payload)
                for topic, payload, _ in peer.messages
                if topic.startswith("homeassistant/") and payload
            }

        try:
            await peer.wait_for(lambda: service.ready and len(configs()) == 2)
            entities = sorted(
                configs().values(), key=lambda entity: entity["unique_id"]
            )
            self.assertTrue(
                all(
                    retained
                    for topic, _, retained in peer.messages
                    if topic.endswith("/config")
                )
            )
            # Use the exact payloads HA will send. Each channel must preserve the other.
            for entity, action, bits in (
                (entities[0], "on", 1),
                (entities[1], "on", 3),
                (entities[0], "off", 2),
                (entities[1], "off", 0),
            ):
                await peer.publish(
                    entity["command_topic"], json.loads(entity["payload_" + action])
                )
                await peer.wait_for(
                    lambda: service.states.get("46715621", {}).get("state_bits") == bits
                )
            # A retained ON command must not switch an output on.
            peer.messages.clear()
            await peer.publish(
                entities[0]["command_topic"],
                json.loads(entities[0]["payload_on"]),
                retained=True,
            )
            await peer.wait_for(
                lambda: any(
                    topic.endswith("bridge/result")
                    and json.loads(payload).get("status") == "rejected"
                    for topic, payload, _ in peer.messages
                )
            )
            self.assertEqual(service.states["46715621"]["state_bits"], 0)
            # HA birth is a raw string, not JSON, and must only republish snapshots.
            peer.messages.clear()
            results_before = self.store.recent_results()
            peer.writers[-1].write(
                mqtt_packet(0x31, mqtt_string(STATUS_TOPIC) + b"online")
            )
            await peer.writers[-1].drain()
            await peer.wait_for(
                lambda: len(configs()) == 2
                and any(topic.endswith("/state") for topic, _, _ in peer.messages)
            )
            self.assertEqual(results_before, self.store.recent_results())
            peer.messages.clear()
            peer.writers[-1].close()
            await peer.wait_for(lambda: peer.connections == 2 and len(configs()) == 2)
            old_ids = set(configs())
            await peer.publish(
                "livolo/bridge/request/rename",
                {"device": "46715621", "name": "Kitchen"},
            )
            await peer.wait_for(
                lambda: all(
                    entity["device"]["name"] == "Kitchen"
                    for entity in configs().values()
                )
            )
            self.assertEqual(set(configs()), old_ids)
            peer.messages.clear()
            await peer.publish("livolo/bridge/request/remove", {"device": "46715621"})
            await peer.wait_for(
                lambda: all((topic, b"", 1) in peer.messages for topic in old_ids)
            )
        finally:
            service.stop.set()
            await asyncio.wait_for(task, 3)
            await broker.close()
            await peer.wait_for(lambda: not peer.tasks)
            server.close()
            await server.wait_closed()
