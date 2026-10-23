"""Paho's network thread hands all service work to the asyncio event loop."""

import asyncio
import json
import logging
import os

from .homeassistant import STATUS_TOPIC

LOG = logging.getLogger(__name__)


class Broker:
    def __init__(self, config, on_message, on_connect):
        """Prepare a broker connection whose service callbacks run on the owner loop.

        @param config Validated MQTT settings, including authentication and TLS options.
        @param on_message Callback receiving topic, payload bytes, and retained flag.
        @param on_connect Callback publishing authoritative state after connection or HA birth.
        """
        import paho.mqtt.client as mqtt

        self.loop = asyncio.get_running_loop()
        self.config = config
        self.base = config["base_topic"]
        self.on_message = on_message
        self.on_connect = on_connect
        self.connected = False
        self.closed = False
        self.error = None
        self.client = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2,
            client_id=config["client_id"],
            clean_session=True,
        )
        self.client.max_queued_messages_set(4096)
        self.client.reconnect_delay_set(1, 30)
        self.client.will_set(self.base + "/bridge/state", "offline", qos=1, retain=True)
        if config.get("password_env") and config["password_env"] not in os.environ:
            raise ValueError("MQTT password environment variable is not set")
        if config.get("username"):
            self.client.username_pw_set(
                config["username"],
                config.get("password", os.environ.get(config.get("password_env", ""))),
            )
        if config.get("tls_ca"):
            self.client.tls_set(
                ca_certs=config["tls_ca"],
                certfile=config.get("tls_cert"),
                keyfile=config.get("tls_key"),
            )
        self.client.on_connect_fail = self._connect_failed
        self.client.on_connect = self._connected
        self.client.on_disconnect = self._disconnected
        self.client.on_message = self._message

    def start(self):
        """Start background connection attempts and clear a previous connection error."""
        self.closed = False
        self.error = None
        self.client.connect_async(
            self.config["host"], self.config["port"], keepalive=30
        )
        self.client.loop_start()

    def _connected(self, client, userdata, flags, reason_code, properties):
        """Subscribe to commands after broker acceptance and announce connectivity.

        @param client Paho client delivering the callback.
        @param userdata Unused Paho application context.
        @param flags Unused broker connection flags.
        @param reason_code Paho connection outcome.
        @param properties Unused MQTT connection properties.
        """
        if reason_code.is_failure:
            self.loop.call_soon_threadsafe(
                self._failed,
                "Broker rejected the connection. Check credentials and broker permissions.",
            )
            return
        client.subscribe(
            [
                (self.base + "/+/set", 1),
                (self.base + "/bridge/request/+", 1),
                (STATUS_TOPIC, 1),
            ]
        )
        self.loop.call_soon_threadsafe(self._online)

    def _connect_failed(self, client, userdata):
        """Report a failed broker connection attempt on the owner loop.

        @param client Unused Paho client delivering the callback.
        @param userdata Unused Paho application context.
        """
        self.loop.call_soon_threadsafe(
            self._failed,
            "Cannot connect to broker. Check server, port and network access.",
        )

    def _failed(self, message):
        """Expose a connection failure unless this broker has already been closed.

        @param message User-facing failure description.
        """
        if not self.closed:
            self.connected = False
            self.error = message

    def _online(self):
        """Mark an active broker ready and request authoritative publications."""
        if self.closed:
            return
        self.error = None
        self.connected = True
        self.on_connect()

    def _disconnected(self, client, userdata, flags, reason_code, properties):
        """Hand a broker disconnection notification to the owner loop.

        @param client Unused Paho client delivering the callback.
        @param userdata Unused Paho application context.
        @param flags Unused disconnect flags.
        @param reason_code Unused Paho disconnect reason.
        @param properties Unused MQTT disconnect properties.
        """
        self.loop.call_soon_threadsafe(self._offline)

    def _offline(self):
        """Mark the broker unavailable for outgoing publications."""
        self.connected = False

    def _message(self, client, userdata, message):
        """Forward bounded incoming MQTT messages to the service event loop.

        @param client Unused Paho client delivering the callback.
        @param userdata Unused Paho application context.
        @param message Paho message carrying topic, payload, and retained flag.
        """
        if len(message.payload) > 4096:
            LOG.warning("Ignoring oversized MQTT command")
            return
        self.loop.call_soon_threadsafe(
            self._deliver, message.topic, bytes(message.payload), message.retain
        )

    def _deliver(self, topic, payload, retained):
        """Handle Home Assistant birth messages or deliver daemon commands.

        @param topic Complete MQTT topic.
        @param payload Message payload bytes.
        @param retained Whether the broker delivered a retained message.
        """
        if not self.closed:
            if topic == STATUS_TOPIC:
                # HA birth is not an actuation request. Retained birth is safe.
                if payload == b"online" and self.connected:
                    self.on_connect()
            else:
                self.on_message(topic, payload, retained)

    def publish(self, suffix, value, retain=False):
        """Publish a daemon topic beneath the configured gateway prefix.

        @param suffix Topic path relative to the configured prefix.
        @param value String or JSON-serializable payload.
        @param retain Whether the broker should retain the payload.
        """
        self.publish_topic(self.base + "/" + suffix, value, retain)

    def publish_topic(self, topic, value, retain=False):
        # No unbounded offline outbox. Authoritative snapshots are republished on connect.
        """Publish state while connected without building an offline backlog.

        @param topic Complete destination topic.
        @param value String or JSON-serializable payload.
        @param retain Whether the broker should retain the payload.
        """
        if not self.connected:
            return
        payload = (
            value
            if isinstance(value, str)
            else json.dumps(value, separators=(",", ":"))
        )
        info = self.client.publish(topic, payload, qos=1, retain=retain)
        if info.rc:
            LOG.warning("MQTT publish not accepted: %s", info.rc)

    async def close(self):
        """Announce offline status when possible and stop the broker connection."""
        self.closed = True
        was_connected = self.connected
        self.connected = False
        if was_connected:
            info = self.client.publish(
                self.base + "/bridge/state", "offline", qos=1, retain=True
            )
            try:
                await asyncio.to_thread(info.wait_for_publish, 2)
            except (RuntimeError, ValueError):
                pass
        self.client.disconnect()
        await asyncio.to_thread(self.client.loop_stop)
