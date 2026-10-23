"""Real Paho TCP and pySerial PTY tests; run after installing host dependencies."""

import asyncio
from dataclasses import replace
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest

from livolod.config import load
from livolod.mqtt import Broker
from livolod.service import Service
from livolod.simulator import Simulator
from livolod.storage import Store
from livolod.uart import Gateway


def mqtt_packet(header, body):
    """Build an MQTT packet for the minimal local broker fixture.

    @param header MQTT fixed-header byte.
    @param body Encoded request or packet payload.
    """
    length = len(body)
    encoded = bytearray([header])
    while True:
        part = length % 128
        length //= 128
        encoded.append(part | (128 if length else 0))
        if not length:
            return bytes(encoded) + body


def mqtt_string(text):
    """Encode a UTF-8 MQTT string for the local broker fixture.

    @param text Command or encoded protocol text for this exchange.
    """
    value = text.encode()
    return len(value).to_bytes(2, "big") + value


class TestBroker:
    """Minimal MQTT 3.1.1 test peer, not a deployable broker."""

    def __init__(self):
        """Prepare the simulated collaborator with the requested test behavior."""
        self.writers = []
        self.connections = 0
        self.connect_packets = []
        self.messages = []
        self.tasks = set()

    async def connection(self, reader, writer):
        """Serve one local MQTT connection for client integration tests.

        @param reader Async input stream of a local test connection.
        @param writer Async output stream of a local test connection.
        """
        task = asyncio.current_task()
        self.tasks.add(task)
        self.writers.append(writer)
        self.connections += 1
        try:
            while True:
                header = (await reader.readexactly(1))[0]
                length, multiplier = 0, 1
                while True:
                    part = (await reader.readexactly(1))[0]
                    length += (part & 127) * multiplier
                    multiplier *= 128
                    if not part & 128:
                        break
                body = await reader.readexactly(length)
                kind = header >> 4
                if kind == 1:  # CONNECT
                    self.connect_packets.append(body)
                    writer.write(b"\x20\x02\x00\x00")
                elif kind == 8:  # SUBSCRIBE: count requested subscriptions
                    index, count = 2, 0
                    while index < len(body):
                        size = int.from_bytes(body[index : index + 2], "big")
                        index += 2 + size + 1
                        count += 1
                    writer.write(mqtt_packet(0x90, body[:2] + bytes([1] * count)))
                elif kind == 3:  # PUBLISH
                    size = int.from_bytes(body[:2], "big")
                    topic = body[2 : 2 + size].decode()
                    index = 2 + size
                    if (header >> 1) & 3:
                        writer.write(mqtt_packet(0x40, body[index : index + 2]))
                        index += 2
                    self.messages.append((topic, body[index:], header & 1))
                elif kind == 12:
                    writer.write(b"\xd0\x00")
                elif kind == 14:
                    break
                await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionError):
            pass
        finally:
            writer.close()
            await writer.wait_closed()
            self.writers.remove(writer)
            self.tasks.remove(task)

    async def publish(self, topic, value, retained=False):
        """Record or deliver a publication for assertions without a production broker.

        @param topic MQTT topic for the test publication.
        @param value Payload or value supplied to the simulated collaborator.
        @param retained Whether the MQTT publication carries its retained flag.
        """
        self.writers[-1].write(
            mqtt_packet(
                0x31 if retained else 0x30,
                mqtt_string(topic) + json.dumps(value).encode(),
            )
        )
        await self.writers[-1].drain()

    async def wait_for(self, predicate, timeout=4):
        """Wait for a test observation, failing if its deadline expires.

        @param predicate Callable that becomes true when the expected observation is available.
        @param timeout Maximum wait in seconds.
        """

        async def wait():
            """Wait until the enclosing test's readiness or result condition is satisfied."""
            while not predicate():
                await asyncio.sleep(0.01)

        await asyncio.wait_for(wait(), timeout)


@unittest.skipUnless(
    importlib.util.find_spec("paho"), "Install host dependencies to test real MQTT"
)
class MqttIntegration(unittest.IsolatedAsyncioTestCase):
    async def test_real_client_command_retained_rejection_reconnect_and_shutdown(self):
        """Verify real client command retained rejection reconnect and shutdown."""
        peer = TestBroker()
        server = await asyncio.start_server(peer.connection, "127.0.0.1", 0)
        with tempfile.TemporaryDirectory() as directory:
            config = load(Path(__file__).resolve().parents[1] / "config.example.json")
            config = replace(
                config,
                database=directory + "/state.db",
                mqtt=dict(config.mqtt, port=server.sockets[0].getsockname()[1]),
            )
            store = Store(config.database, "network")
            service = Service(config, store, gateway_factory=Simulator)
            broker = Broker(config.mqtt, service.receive, service.snapshot)
            service.broker = broker
            task = asyncio.create_task(service.run())
            broker.start()
            try:
                await peer.wait_for(
                    lambda: any(
                        t == "livolo/bridge/state" and p == b"online"
                        for t, p, _ in peer.messages
                    )
                )
                await peer.publish(
                    "livolo/46715621/set", {"request_id": "wire1", "state": "ON"}
                )
                await peer.wait_for(
                    lambda: any(
                        t == "livolo/bridge/result"
                        and json.loads(p).get("status") == "confirmed"
                        for t, p, _ in peer.messages
                    ),
                    timeout=16,
                )
                self.assertEqual(service.states["46715621"]["state_bits"], 3)
                await peer.publish(
                    "livolo/46715621/set",
                    {"request_id": "bad", "state": "OFF"},
                    retained=True,
                )
                await peer.wait_for(
                    lambda: any(
                        t == "livolo/bridge/result"
                        and json.loads(p).get("status") == "rejected"
                        for t, p, _ in peer.messages
                    )
                )
                self.assertEqual(service.states["46715621"]["state_bits"], 3)
                # Real network reconnect must resubscribe and replay authoritative snapshots.
                peer.messages.clear()
                peer.writers[-1].close()
                await peer.wait_for(
                    lambda: peer.connections >= 2
                    and any(t == "livolo/46715621/state" for t, _, _ in peer.messages)
                )
                await peer.publish(
                    "livolo/46715621/set", {"request_id": "wire1", "state": "ON"}
                )
                await peer.wait_for(
                    lambda: any(
                        t == "livolo/bridge/result" for t, _, _ in peer.messages
                    )
                )
                from livolod.protocol import decode

                self.assertEqual(
                    sum(
                        decode(bytes.fromhex(c.split()[-1])).opcode == 2
                        for c in service.gateway.commands
                        if c.startswith("SEND ")
                    ),
                    1,
                )
            finally:
                service.stop.set()
                await asyncio.wait_for(task, 3)
                await broker.close()
                store.close()
                await peer.wait_for(lambda: not peer.tasks)
                server.close()
                await server.wait_closed()
            self.assertTrue(
                any(
                    t == "livolo/bridge/state" and p == b"offline"
                    for t, p, _ in peer.messages
                )
            )


@unittest.skipUnless(
    importlib.util.find_spec("serial"), "Install host dependencies to test real serial"
)
class SerialIntegration(unittest.IsolatedAsyncioTestCase):
    async def test_pyserial_posix_readiness_and_exclusive_owner(self):
        """Verify pyserial posix readiness and exclusive owner."""
        import serial

        master, slave = os.openpty()
        gateway = Gateway.open(os.ttyname(slave))
        loop = asyncio.get_running_loop()
        commands = []

        def respond():
            """Answer the test pseudo-terminal exchange from its peer endpoint."""
            data = os.read(master, 1024)
            commands.append(data)
            request_id = data.split()[0]
            os.write(master, b"RSP " + request_id + b" OK test\n")

        loop.add_reader(master, respond)
        try:
            with self.assertRaises(serial.SerialException):
                other = serial.Serial(os.ttyname(slave), exclusive=True)
                other.close()
            self.assertEqual(await gateway.command("PING"), "OK test")
        finally:
            await gateway.close()
            loop.remove_reader(master)
            os.close(master)
            os.close(slave)
        self.assertTrue(commands[-1].endswith(b" DISARM\n"))
