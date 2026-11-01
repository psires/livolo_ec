"""Host policy, commissioning, durable state and the MQTT application contract."""

import asyncio
from contextlib import asynccontextmanager
from dataclasses import asdict, replace
import heapq
import json
import logging
import os
import re
import secrets
import time
import uuid

from .config import Device, hex_value, identity, integer, mqtt_config
from .homeassistant import discovery_messages
from .protocol import decode, frame, heartbeat, key_values, rx_envelope
from .phase import PhaseClient
from .scheduler import SchedulerClient
from . import __version__
from .version import firmware_info
from .firmware_manager import FirmwareManager, radio_guard
from .uart import CommandError, Gateway, LinkError

LOG = logging.getLogger(__name__)
HEARTBEAT_WAIT_TIMEOUT = 12.0
SET_CONFIRM_TIMEOUT = 4.0
SET_ATTEMPTS = 3
PHASE_SET_ATTEMPTS = 6
SET_RETRY_INTERVAL = 0.2
REFRESH_ROUNDS = 3
REFRESH_QUERY_INTERVAL = 0.2
# At 460800, shorter UART serialization otherwise reduces the RF pair gap
# from about 237 ms to 213 ms. This measured correction is baud-specific.
REFRESH_QUERY_INTERVAL_460800 = 0.224
REFRESH_RESPONSE_WAIT = 4.0
REFRESH_TIMEOUT = 14.0


def refresh_query_interval(baudrate):
    """Select the measured host delay between read-only query transmissions.

    @param baudrate Configured application UART baud rate.
    @return Delay in seconds; unmeasured rates retain the existing policy.
    """
    return (
        REFRESH_QUERY_INTERVAL_460800 if baudrate == 460800 else REFRESH_QUERY_INTERVAL
    )


class Service:
    def __init__(
        self, config, store, broker=None, gateway_factory=None, *, simulated=False
    ):
        """Restore host policy state and prepare a reconciled gateway service.

        @param config Validated daemon configuration used to seed persistent settings.
        @param store Authoritative state and command ledger.
        @param broker Optional existing MQTT publisher.
        @param gateway_factory Optional no-argument factory for fresh adapter sessions.
        @param simulated Whether to isolate MQTT identity and disable live settings changes.
        """
        self.config, self.store, self.broker = config, store, broker
        mqtt = mqtt_config(config.mqtt, config.gateway, allow_password=True)
        self.mqtt_enabled = mqtt.get("enabled", True)
        self.config = replace(
            config,
            devices=store.devices(config.devices),
            mqtt=(
                mqtt_config(
                    store.mqtt_settings(config.mqtt),
                    config.gateway,
                    allow_password=True,
                )
                if self.mqtt_enabled
                else mqtt
            ),
        )
        if simulated and self.mqtt_enabled:
            mqtt = dict(self.config.mqtt)
            mqtt["base_topic"] += "/simulated"
            mqtt["client_id"] += "-simulated"
            self.config = replace(self.config, mqtt=mqtt)
        self.mqtt_factory = None
        self.mqtt_lock = asyncio.Lock()
        self.gateway_factory = gateway_factory or (
            lambda: Gateway.open(config.serial_port, config.serial_baudrate)
        )
        self.devices = {d.id: d for d in self.config.devices}
        self.states = {k: v for k, v in store.states().items() if k in self.devices}
        self.seen = {}
        self.state_received = {}
        self.state_sequences = {}
        self.availability = {}
        self.commands = asyncio.Queue(maxsize=64)
        self.stop = asyncio.Event()
        self.reconfigure = asyncio.Event()
        self.registry_revision = 0
        self.ready = False
        self.simulated = simulated
        self.gateway = None
        self.phase_client = None
        self.scheduler = None
        self.state_sequences.clear()
        self.phase_reference_missing = True
        self.session = None
        self.sequence = secrets.randbelow(256)
        self.pending = None
        self.radio_lock = asyncio.Lock()
        self.next_heartbeat = 0.0
        self.last_heartbeat = None
        self.heartbeat_reserved = False
        self.pairing = None
        self.schedule = []
        self.diagnostics = {"rx_gaps": 0, "invalid_rx": 0, "ack_failures": 0}
        self.diagnostics["output_mode"] = config.output_mode
        self.diagnostics["serial_baudrate"] = config.serial_baudrate
        self.diagnostics["query_timing"] = {
            "query_interval_seconds": refresh_query_interval(config.serial_baudrate),
            "response_wait_seconds": REFRESH_RESPONSE_WAIT,
            "rounds": REFRESH_ROUNDS,
            "deadline_seconds": REFRESH_TIMEOUT,
        }
        self.last_rx = None
        self.discovery_seen = {}
        self.discoveries = {}
        self.started = time.monotonic()
        self.change_listeners = set()
        self.command_inflight = None
        self.firmware_manager = FirmwareManager(self)

    def start_firmware_update(self, payload):
        """Start an explicitly selected update through the shared durable manager.

        @param payload Mapping containing only request_id and candidate_id.
        @return Durable command result; acceptance is not firmware confirmation.
        """
        return self.firmware_manager.start(payload)

    def firmware_update_snapshot(self):
        """Expose public update eligibility and the latest persisted job."""
        return self.firmware_manager.snapshot()

    def notify_changed(self):
        """Schedule API observations on the owner loop without waiting on clients."""
        for callback in tuple(self.change_listeners):
            try:
                callback()
            except Exception:
                LOG.exception("State observer failed")

    def publish(self, suffix, value, retain=False):
        """Publish service state when an MQTT broker is configured.

        @param suffix Topic path relative to the configured prefix.
        @param value String or JSON-serializable payload.
        @param retain Whether the broker should retain the payload.
        """
        self.notify_changed()
        if self.broker:
            self.broker.publish(suffix, value, retain)

    def snapshot(self):
        """Reconcile retained broker state and Home Assistant discovery with host authority."""
        if self.broker and not self.simulated:
            for topic, payload in discovery_messages(
                self.config, self.store.removed_devices()
            ):
                self.broker.publish_topic(topic, payload, retain=True)
        self.publish("bridge/state", "online" if self.ready else "offline", True)
        self.publish(
            "bridge/controls",
            "offline" if self.config.output_mode == "disabled" else "online",
            True,
        )
        self.publish(
            "bridge/info",
            {
                "version": 1,
                "uart_protocol": 3,
                "host_version": __version__,
                "firmware_version": self.diagnostics.get("firmware", {}).get(
                    "running_version"
                ),
                "gateway": self.config.gateway,
                "tag": self.config.tag,
                "ready": self.ready,
                "pairing": self.pairing_summary(),
            },
            True,
        )
        self.publish("bridge/devices", [asdict(d) for d in self.config.devices], True)
        for device in self.store.removed_devices():
            self.publish(device + "/state", "", True)
            self.publish(device + "/availability", "", True)
        for device in self.devices:
            if device in self.states:
                self.publish(device + "/state", self.states[device], True)
            else:
                # Clear a broker's old retained state when using a fresh host database.
                self.publish(device + "/state", "", True)
        self.publish_availability(force=True)
        self.publish("bridge/diagnostics", self.diagnostics, True)

    def pairing_summary(self):
        """Expose the active pairing target and remaining time.

        @return A pairing summary mapping, or None when no window is open.
        """
        if not self.pairing:
            return None
        return {
            "device": self.pairing["device"],
            "request_id": self.pairing["request_id"],
            "seconds_remaining": max(
                0, int(self.pairing["deadline"] - time.monotonic())
            ),
        }

    def publish_availability(self, force=False):
        """Publish device reachability derived from recent reports and radio readiness.

        @param force Whether to publish even when the availability value has not changed.
        """
        now = time.monotonic()
        for device in self.devices:
            online = (
                self.ready
                and device in self.seen
                and now - self.seen[device] < self.config.offline_after
            )
            value = "online" if online else "offline"
            if force or self.availability.get(device) != value:
                self.publish(device + "/availability", value, True)
                self.availability[device] = value

    def result(self, request_id, status, **values):
        """Persist and publish a command outcome with its stored request context.

        @param request_id Durable command identifier.
        @param status Command lifecycle outcome.
        @param values Additional result fields such as device, state, token, or error.
        @return The persisted result with available request metadata.
        """
        result = dict(request_id=request_id, status=status, **values)
        self.store.finish(request_id, result)
        result = self.store.command_result(request_id) or result
        self.publish("bridge/result", result)
        return result

    def receive(self, topic, payload, retained):
        """Accept MQTT commands while rejecting retained actuation and malformed requests.

        @param topic Complete MQTT command topic.
        @param payload JSON command payload bytes.
        @param retained Whether the broker delivered a retained message.
        """
        request_id = None
        try:
            # A retained actuation request must never execute after subscription/reconnect.
            if retained:
                raise ValueError("Retained commands are not accepted")
            request = json.loads(payload)
            if not isinstance(request, dict):
                raise ValueError("Command payload must be a JSON object")
            prefix = self.config.mqtt["base_topic"] + "/"
            if not topic.startswith(prefix):
                raise ValueError("Unexpected topic")
            suffix = topic[len(prefix) :]
            if suffix.endswith("/set") and suffix.count("/") == 1:
                self.submit("set", dict(request, device=suffix.split("/")[0]))
            elif suffix.startswith("bridge/request/"):
                self.submit(suffix[len("bridge/request/") :], request)
            else:
                raise ValueError("Unknown command topic")
        except (ValueError, TypeError, KeyError, UnicodeError) as error:
            self.publish(
                "bridge/result",
                {"request_id": request_id, "status": "rejected", "error": str(error)},
            )

    def submit(self, action, request):
        """Deduplicate and validate commands before applying registry changes or queuing radio
        work.

        @param action Requested daemon operation.
        @param request Command payload including an optional request_id.
        @return The previous, pending, confirmed, or rejected command result.
        """
        request_id = None
        try:
            if not isinstance(request, dict):
                raise ValueError("Command payload must be a JSON object")
            request_id = request.get("request_id", str(uuid.uuid4()))
            if not isinstance(request_id, str) or not re.fullmatch(
                r"[A-Za-z0-9_.:-]{1,128}", request_id
            ):
                request_id = None
                raise ValueError("Invalid request_id")
            recorded = dict(
                {k: v for k, v in request.items() if k != "request_id"}, action=action
            )
            previous = self.store.previous(request_id, recorded)
            if previous:
                self.publish("bridge/result", previous)
                return previous
            if self.firmware_manager.busy and action != "snapshot":
                raise ValueError("Firmware maintenance blocks conflicting commands")
            values = self.validate_action(action, request)
            self.store.begin(request_id, recorded)
            self.notify_changed()
            if action in ("add", "rename", "remove"):
                return self.manage_registry(request_id, action, values)
            if action == "snapshot":
                self.snapshot()
                return self.result(request_id, "confirmed")
            if not self.ready:
                return self.result(request_id, "rejected", error="radio is offline")
            try:
                self.commands.put_nowait(
                    (time.monotonic(), self.session, request_id, action, values)
                )
            except asyncio.QueueFull:
                return self.result(
                    request_id, "rejected", error="command queue is full"
                )
            return {"request_id": request_id, "status": "pending"}
        except (ValueError, TypeError, KeyError, UnicodeError) as error:
            result = {
                "request_id": request_id,
                "status": "rejected",
                "error": str(error),
            }
            self.publish("bridge/result", result)
            return result

    def command_result(self, request_id):
        """Look up the durable result of a submitted request.

        @param request_id Command deduplication identifier.
        @return The stored result, or None when unknown.
        """
        return self.store.command_result(request_id)

    def web_snapshot(self):
        """Expose gateway, device, and activity state without MQTT credentials.

        @return A JSON-compatible UI snapshot.
        """
        now = time.monotonic()
        bridge = {
            "ready": self.ready,
            "gateway": self.config.gateway,
            "tag": self.config.tag,
            "mqtt_connected": bool(
                self.broker and getattr(self.broker, "connected", False)
            ),
            "pairing": self.pairing_summary(),
            "diagnostics": dict(self.diagnostics),
        }
        return {
            "bridge": bridge,
            "simulated": self.simulated,
            "ready": self.ready,
            "gateway": self.config.gateway,
            "tag": self.config.tag,
            "pairing": self.pairing_summary(),
            "diagnostics": dict(self.diagnostics),
            "devices": [
                dict(
                    asdict(device),
                    state=self.states.get(device.id),
                    availability=(
                        "online"
                        if self.ready
                        and now - self.seen.get(device.id, -1e9)
                        < self.config.offline_after
                        else "offline"
                    ),
                )
                for device in self.devices.values()
            ],
            "discoveries": [
                dict(value, configured=device in self.devices)
                for device, value in self.discoveries.items()
            ],
            "results": self.store.recent_results(),
            "mqtt": self.mqtt_summary(),
            "firmware_update": self.firmware_update_snapshot(),
        }

    api_snapshot = web_snapshot

    def mqtt_summary(self):
        """Expose editable broker settings and connection health without credentials.

        @return Public MQTT settings and availability metadata.
        """
        settings = self.config.mqtt
        if not self.mqtt_enabled:
            return {
                "enabled": False,
                "host": "",
                "port": 1883,
                "base_topic": "",
                "username": "",
                "password_set": False,
                "tls": False,
                "editable": False,
                "connected": False,
                "error": None,
            }
        return {
            "enabled": True,
            "host": settings["host"],
            "port": settings["port"],
            "base_topic": settings["base_topic"],
            "username": settings.get("username", ""),
            "password_set": bool(
                settings.get("password") or settings.get("password_env")
            ),
            "tls": bool(settings.get("tls_ca")),
            "editable": self.mqtt_factory is not None and not self.simulated,
            "connected": bool(self.broker and getattr(self.broker, "connected", False)),
            "error": getattr(self.broker, "error", None),
        }

    async def configure_mqtt(self, request):
        """Apply validated broker settings and retain the previous configuration on failure.

        @param request Editable MQTT fields and explicit password action.
        @return The public summary of the effective broker configuration.
        """
        from .mqtt_settings import updated_settings

        async with self.mqtt_lock:
            if self.firmware_manager.busy:
                raise ValueError("Firmware maintenance blocks configuration changes")
            if not self.mqtt_enabled or self.mqtt_factory is None or self.simulated:
                raise ValueError("MQTT settings are unavailable in this demo")
            settings = updated_settings(self.config.mqtt, request, self.config.gateway)
            if settings == self.config.mqtt:
                return self.mqtt_summary()
            # Construct/validate before disturbing the live client. Neither the
            # command ledger nor MQTT publications ever receive these credentials.
            candidate = self.mqtt_factory(settings, self.receive, self.snapshot)
            old = self.broker
            if old:
                await old.close()
            try:
                candidate.start()
                self.store.save_mqtt_settings(settings)
            except Exception:
                await candidate.close()
                if old:
                    old.start()
                raise RuntimeError(
                    "Could not apply MQTT settings; previous settings retained"
                ) from None
            self.config = replace(self.config, mqtt=settings)
            self.broker = candidate
            return self.mqtt_summary()

    async def web_request(self, action, payload):
        """Submit a web command through the shared validation and deduplication contract.

        @param action Requested daemon operation.
        @param payload Command parameters including an optional request_id.
        @return The command submission result.
        """
        return self.submit(action, payload)

    def manage_registry(self, request_id, action, values):
        """Commit a device registry change and reconcile dependent radio and broker state.

        @param request_id Durable command identifier.
        @param action Validated add, rename, or remove operation.
        @param values Validated registry fields for the operation.
        @return The confirmed or rejected command result.
        """
        device_id = values["device"]
        devices = dict(self.devices)
        try:
            if action == "add":
                if device_id in devices:
                    raise ValueError("Device is already registered")
                used = {d.slot for d in devices.values()}
                slot = values.get(
                    "slot", next((n for n in range(256) if n not in used), -1)
                )
                if slot == -1 or slot in used:
                    raise ValueError("Peer slot is occupied or registry is full")
                devices[device_id] = Device(
                    device_id, values["name"], slot, values["outputs"], values["suffix"]
                )
            else:
                if device_id not in devices:
                    raise ValueError("Unknown configured device")
                if action == "rename":
                    devices[device_id] = replace(
                        devices[device_id], name=values["name"]
                    )
                else:
                    del devices[device_id]
            self.store.replace_devices(devices.values())
            self.devices = devices
            self.config = replace(self.config, devices=tuple(devices.values()))
            if action != "rename":
                self.registry_revision += 1
                self.ready = False
                self.reconfigure.set()
            if action == "remove":
                if self.pairing and self.pairing["device"] == device_id:
                    self.finish_pairing("cancelled", "device removed from registry")
                self.states.pop(device_id, None)
                self.seen.pop(device_id, None)
                self.state_received.pop(device_id, None)
                self.discoveries.pop(device_id, None)
                self.discovery_seen.pop(device_id, None)
                self.publish(device_id + "/state", "", True)
                self.publish(device_id + "/availability", "", True)
            self.snapshot()
            return self.result(request_id, "confirmed", device=device_id, action=action)
        except ValueError as error:
            return self.result(request_id, "rejected", error=str(error))

    def validate_action(self, action, request):
        """Normalize a requested operation and enforce registry and output policy.

        @param action Requested daemon operation.
        @param request Untrusted command mapping.
        @return Validated operation-specific fields.
        """
        request = dict(request)
        if action == "set":
            if self.config.output_mode == "disabled":
                raise ValueError(
                    "Output controls are disabled; select an explicit experimental output_mode"
                )
            device = request.pop("device", None)
            _, values = self.validate_request(str(device) + "/set", request)
            return values
        if action in ("pair", "snapshot", "refresh"):
            _, values = self.validate_request("bridge/request/" + action, request)
            return values
        if action not in ("add", "rename", "remove"):
            raise ValueError("Unknown command action")
        allowed = {"request_id", "device"}
        if action in ("add", "rename"):
            allowed.add("name")
        if action == "add":
            allowed.update(("outputs", "slot", "suffix"))
        if set(request) - allowed:
            raise ValueError("Unknown registry command fields")
        device_id = identity(request.get("device"))
        if device_id == self.config.gateway:
            raise ValueError("Device identity must differ from gateway")
        values = {"device": device_id}
        if action in ("add", "rename"):
            name = request.get("name")
            if not isinstance(name, str) or not name.strip() or len(name) > 100:
                raise ValueError("Name must contain 1..100 characters")
            values["name"] = name.strip()
        if action == "add":
            values["outputs"] = integer(request.get("outputs"), 1, 2, "outputs")
            values["suffix"] = hex_value(request.get("suffix", "0002"), 4, "suffix")
            if "slot" in request:
                values["slot"] = integer(request["slot"], 0, 255, "slot")
        return values

    def validate_request(self, suffix, request):
        """Validate the MQTT-compatible radio command contract.

        @param suffix Command topic relative to the configured MQTT prefix.
        @param request Command mapping whose device may be supplied by the topic.
        @return The normalized action and its validated fields.
        """
        if suffix.endswith("/set") and suffix.count("/") == 1:
            device = suffix.split("/")[0]
            if device not in self.devices:
                raise ValueError("Unknown configured device")
            if set(request) - {"request_id", "state", "mask", "value"}:
                raise ValueError("Unknown set fields")
            if "state" in request:
                if (
                    "mask" in request
                    or "value" in request
                    or request["state"] not in ("ON", "OFF")
                ):
                    raise ValueError("Use state ON/OFF, or mask and value")
                mask = self.devices[device].mask
                value = mask if request["state"] == "ON" else 0
            else:
                mask = integer(
                    request.get("mask"), 1, self.devices[device].mask, "mask"
                )
                value = integer(
                    request.get("value"), 0, self.devices[device].mask, "value"
                )
                if value & ~mask:
                    raise ValueError("value must fit mask")
            return "set", {"device": device, "mask": mask, "value": value}
        if suffix == "bridge/request/pair":
            if (
                set(request) - {"request_id", "device", "seconds"}
                or request.get("device") not in self.devices
            ):
                raise ValueError("Pairing requires a configured device ID")
            return "pair", {
                "device": request["device"],
                "seconds": integer(request.get("seconds", 120), 0, 300, "seconds"),
            }
        if suffix == "bridge/request/refresh":
            if (
                set(request) - {"request_id", "device"}
                or request.get("device") not in self.devices
            ):
                raise ValueError("Refresh requires a configured device ID")
            return "refresh", {"device": request["device"]}
        if suffix == "bridge/request/snapshot":
            if set(request) - {"request_id"}:
                raise ValueError("Unknown snapshot fields")
            return "snapshot", {}
        raise ValueError("Unknown command topic")

    def next_sequence(self):
        """Allocate the next wrapping radio frame sequence number.

        @return An eight-bit sequence number.
        """
        self.sequence = (self.sequence + 1) & 255
        return self.sequence

    async def restore(self):
        """Establish and verify a fresh adapter session before accepting radio work."""
        self.ready = False
        self.phase_client = None
        self.scheduler = None
        self.state_sequences.clear()
        self.phase_reference_missing = True
        revision = self.registry_revision
        devices = self.config.devices
        self.session = secrets.token_hex(8)
        while int(self.session, 16) == 0:
            self.session = secrets.token_hex(8)
        if await self.gateway.command("PING") != "OK EC-GATEWAY 3":
            raise LinkError("UART protocol v3 is required")
        caps = key_values(await self.gateway.command("CAPS"))
        try:
            self.diagnostics["firmware"] = firmware_info(caps)
        except ValueError as error:
            raise LinkError("Malformed firmware release version") from error
        self.diagnostics["host_version"] = __version__
        self.firmware_manager.observe(caps)
        if caps.get("fw_version"):
            reported = key_values(await self.gateway.command("VERSION"))
            if reported.get("fw_version") != caps["fw_version"]:
                raise LinkError("Firmware version changed during setup")
        try:
            capable = (
                caps["proto"] == "3"
                and int(caps["peers"])
                > max((d.slot for d in self.config.devices), default=-1)
                and int(caps["ack_mask"], 16) & 0x1F == 0x1F
                and int(caps["lease_max"]) >= 30
                and int(caps["session_bytes"]) == 8
                and int(caps["channel"]) == 11
                and int(caps["max_line"]) >= 319
                and int(caps["max_raw"]) >= 41
            )
        except (KeyError, ValueError):
            capable = False
        if "baud" in caps and caps["baud"] != str(self.config.serial_baudrate):
            raise LinkError("Adapter baud does not match serial_baudrate")
        if not capable:
            raise LinkError("Adapter capabilities do not meet daemon requirements")
        if self.config.output_mode == "experimental_phase":
            self.phase_client = PhaseClient(
                self.gateway,
                self.session,
                caps,
                gateway_id=self.config.gateway,
                allow_outputs=True,
            )
        if caps.get("scheduler") == "timed_v1" and self.phase_client:
            self.scheduler = SchedulerClient(self.gateway, self.session, caps)
        self.diagnostics["heartbeat_owner"] = "mcu" if self.scheduler else "host"
        self.diagnostics.pop("scheduler", None)
        self.diagnostics["query_mode"] = (
            "mcu"
            if self.scheduler and self.scheduler.query_supported
            else (
                "phase"
                if self.phase_client and self.phase_client.query_supported
                else "raw"
            )
        )
        await self.gateway.command(
            f"NETWORK {self.config.gateway} {self.config.tag} {self.session}"
        )
        for device in devices:
            await self.gateway.command(f"PEER SET {device.slot} {device.id} 1f")
        for device in devices:
            peer = key_values(await self.gateway.command(f"PEER GET {device.slot}"))
            if any(
                peer.get(k) != v
                for k, v in {
                    "slot": str(device.slot),
                    "used": "1",
                    "device": device.id,
                    "ack_mask": "1f",
                }.items()
            ):
                raise LinkError("Peer table verification failed")
        self.check_status(key_values(await self.gateway.command("STATUS")), armed=False)
        # Pre-installation RX/status records cannot confirm this new session's commands.
        while not self.gateway.events.empty():
            self.gateway.events.get_nowait()
        if revision != self.registry_revision:
            raise LinkError("Registry changed during radio setup")
        await self.gateway.command(f"ARM {self.session} 30")
        await self.gateway.command(f"ACK {self.session} 1")
        self.check_status(key_values(await self.gateway.command("STATUS")), armed=True)
        while not self.gateway.events.empty():
            self.gateway.events.get_nowait()
        if revision != self.registry_revision:
            raise LinkError("Registry changed during radio setup")
        self.next_heartbeat = 0.0
        if self.phase_client:
            self.last_heartbeat = None
            try:
                async with self.radio_lock:
                    if not await self._emit_heartbeat(restoring=True):
                        raise LinkError(
                            "Phase reference heartbeat was busy during restore"
                        )
            except BaseException:
                self.ready = False
                raise
        firmware = self.diagnostics["firmware"]
        update = None
        if firmware["update_supported"]:
            update = key_values(await self.gateway.command("UPDATE_STATUS"))
            self.firmware_manager.before_confirm(caps, update)
            if update.get("trial") not in ("0", "1"):
                raise LinkError("Invalid update trial status")
            if update["trial"] == "1":
                if firmware["running_version"] != __version__:
                    raise LinkError(
                        "Trial firmware does not match this host release; leaving rollback armed"
                    )
                await self.gateway.command("UPDATE_CONFIRM")
                update = key_values(await self.gateway.command("UPDATE_STATUS"))
                if update.get("trial") != "0" or update.get("state") != "fffffff0":
                    raise LinkError("Firmware trial confirmation was not durable")
            firmware["trial"] = False
        firmware["verified_at"] = time.time()
        firmware["session"] = self.session
        with self.store.db:
            self.store.db.execute(
                "INSERT OR REPLACE INTO meta VALUES ('firmware_status', ?)",
                (json.dumps(firmware),),
            )
        self.ready = True
        self.firmware_manager.restored(caps, update)
        self.last_rx = None
        self.snapshot()
        LOG.info(
            "Radio ready: gateway=%s peers=%d", self.config.gateway, len(self.devices)
        )

    def check_status(self, status, armed=True):
        """Detect adapter reset, expired lease, or divergence from host configuration.

        @param status Adapter STATUS key/value fields.
        @param armed Whether the verified session must have both arming and ACK enabled.
        """
        expected = {
            "configured": "1",
            "gateway": self.config.gateway,
            "tag": self.config.tag,
            "session": self.session,
            "peers": str(len(self.devices)),
            "armed": "1" if armed else "0",
            "ack": "1" if armed else "0",
        }
        if any(status.get(k) != v for k, v in expected.items()):
            raise LinkError(
                "Adapter reset, lease expiry, or configuration drift detected"
            )
        self.diagnostics["adapter"] = status

    async def transmit(self, raw):
        """Serialize a raw frame transmission against other gateway radio work.

        @param raw Complete EC command frame bytes.
        @return True after successful submission, or False if the radio remained busy.
        """
        async with self.radio_lock:
            return await self._transmit(raw)

    async def _transmit(
        self, raw, *, reference_setup=False, still_needed=None, phase_query=False
    ):
        """Submit a frame with bounded retries only for definite channel-busy non-sends.

        @param raw Complete EC command frame bytes; caller must hold radio_lock.
        @param reference_setup Whether a heartbeat may establish the reference during restore.
        @param still_needed Optional predicate checked before each radio attempt.
        @param phase_query Use guarded query phase stamping instead of raw SEND.
        @return True after submission, or False if busy or no longer needed.
        """
        # CCA busy is a definite non-send. Other TX failures have uncertain RF outcome.
        for attempt in range(4):
            if still_needed is not None and not still_needed():
                return False
            if not self.ready and not (reference_setup and raw[21:23] == b"\x8c\x08"):
                raise LinkError("Radio session is being reconciled")
            try:
                if phase_query:
                    if self.phase_reference_missing:
                        return False
                    metadata = await self.phase_client.query(raw)
                    self.diagnostics["query_phase_tx"] = dict(metadata)
                else:
                    await self.gateway.command(f"SEND {self.session} {raw.hex()}")
                return True
            except CommandError as error:
                if phase_query and str(error) == "ERR PHASE_REFERENCE":
                    self.phase_reference_missing = True
                    return False
                busy = str(error) == "ERR TX 2"
                if phase_query:
                    busy = str(error).startswith("ERR TX 2 ")
                    if busy:
                        self.diagnostics["query_phase_tx"] = dict(
                            self.phase_client.last_tx
                        )
                if not busy:
                    raise LinkError(str(error)) from error
                if attempt < 3:
                    await asyncio.sleep(0.025 * (attempt + 1))
        return False

    async def emit_heartbeat(self, only_if_due=False):
        """Honor the normal heartbeat cadence and any reserved output window.

        @param only_if_due Compatibility flag; all calls honor the heartbeat due time.
        @return True if no heartbeat is needed or it succeeds; False if transmission stays busy.
        """
        async with self.radio_lock:
            # Sets reserve normal boundaries. No caller may force an early beat.
            if self.heartbeat_reserved or time.monotonic() < self.heartbeat_due():
                return True
            return await self._emit_heartbeat()

    def heartbeat_due(self):
        """Determine the earliest permitted next heartbeat without shortening its cadence.

        @return The monotonic timestamp of the next permitted heartbeat.
        """
        if self.scheduler:
            return self.next_heartbeat
        return max(
            self.next_heartbeat,
            self.last_heartbeat + 10 if self.last_heartbeat is not None else 0,
        )

    @asynccontextmanager
    async def set_radio_window(self):
        """Reserve the next regular heartbeat boundary for a legacy output transaction.

        @return An asynchronous context holding radio_lock at the reserved boundary.
        """
        if self.heartbeat_reserved:
            raise ValueError("Another set transaction owns the heartbeat window")
        self.heartbeat_reserved = True
        session = self.session
        deadline = time.monotonic() + HEARTBEAT_WAIT_TIMEOUT
        try:
            while True:
                if not self.ready or self.session != session:
                    raise LinkError("Radio session changed while waiting for heartbeat")
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise ValueError(
                        "Heartbeat window wait expired; no output command sent"
                    )
                try:
                    await asyncio.wait_for(self.radio_lock.acquire(), remaining)
                except asyncio.TimeoutError:
                    raise ValueError(
                        "Heartbeat window wait expired; no output command sent"
                    ) from None
                delay = self.heartbeat_due() - time.monotonic()
                if delay <= 0:
                    try:
                        if not self.ready or self.session != session:
                            raise LinkError(
                                "Radio session changed while waiting for heartbeat"
                            )
                        yield
                    finally:
                        self.radio_lock.release()
                    return
                self.radio_lock.release()
                # Never hold the radio lock during the intentional phase wait.
                await asyncio.sleep(min(delay, max(0, deadline - time.monotonic())))
        finally:
            self.heartbeat_reserved = False

    async def _emit_heartbeat(self, *, restoring=False):
        # Keep the existing heartbeat epoch. Phase-capable firmware establishes
        # its own TX-entry reference; raw heartbeat bytes remain compatible.
        """Maintain the network heartbeat and validate any adapter phase reference.

        @param restoring Whether to allow the reference heartbeat before readiness; caller holds
            radio_lock.
        @return True if sent successfully, or False when the radio remains busy.
        """
        if self.scheduler and not restoring:
            status = await self.scheduler.status()
            self.diagnostics["scheduler"] = status
            if status["hb_active"] != "1":
                raise LinkError("MCU heartbeat schedule lost")
            self.phase_reference_missing = not bool(
                self.phase_client.reference_status(status)
            )
            self.next_heartbeat = time.monotonic() + 0.5
            return not self.phase_reference_missing
        raw = heartbeat(
            self.config.gateway,
            self.config.tag,
            self.next_sequence(),
            int(time.monotonic() - self.started),
            self.config.heartbeat_parameter,
        )
        if self.scheduler:
            status = await self.scheduler.start_heartbeat(raw)
            self.diagnostics["scheduler"] = status
            self.diagnostics["phase_reference"] = self.phase_client.reference_status(
                status
            )
            self.phase_reference_missing = False
            self.next_heartbeat = time.monotonic() + 0.5
            return True
        sent = (
            await self._transmit(raw, reference_setup=True)
            if restoring
            else await self._transmit(raw)
        )
        if sent:
            self.last_heartbeat = time.monotonic()
            if self.phase_client:
                status = key_values(await self.gateway.command("STATUS"))
                self.check_status(status)
                reference = self.phase_client.reference_status(status)
                if reference is None:
                    raise LinkError(
                        "Successful heartbeat did not establish a phase reference"
                    )
                self.phase_reference_missing = False
                self.diagnostics["phase_reference"] = reference
        self.next_heartbeat = time.monotonic() + (10 if sent else 1)
        return sent

    async def maintenance(self):
        """Maintain the adapter lease, heartbeat, pairing schedule, and device availability."""
        next_lease = time.monotonic() + 10
        while True:
            now = time.monotonic()
            if now >= next_lease:
                # Verify before renewal: an expired lease requires a full reconciliation.
                self.check_status(key_values(await self.gateway.command("STATUS")))
                await self.gateway.command(f"ARM {self.session} 30")
                next_lease = time.monotonic() + 10
                self.publish("bridge/diagnostics", self.diagnostics, True)
            if now >= self.heartbeat_due() and not self.heartbeat_reserved:
                await self.emit_heartbeat(only_if_due=True)
            if self.pairing and now >= self.pairing["deadline"]:
                self.finish_pairing(
                    "timeout", "No native report with the configured tag was observed"
                )
            if self.schedule and self.schedule[0][0] <= now:
                _, opcode, data = heapq.heappop(self.schedule)
                if self.pairing:
                    device = self.devices[self.pairing["device"]]
                    await self.transmit(
                        frame(
                            self.config.gateway,
                            self.config.tag,
                            device.id,
                            device.suffix,
                            self.next_sequence(),
                            opcode,
                            data,
                        )
                    )
            self.publish_availability()
            await asyncio.sleep(0.05)

    async def events(self):
        """Apply unsolicited adapter reports and health events to the active session."""
        while True:
            received, line = await self.gateway.events.get()
            if line.startswith("STATUS "):
                self.check_status(key_values(line))
                continue
            if line.startswith("ACKTX "):
                if key_values(line).get("result") != "0":
                    self.diagnostics["ack_failures"] += 1
                continue
            self.handle_rx(received, line)

    def handle_rx(self, received, line, *, retained=False):
        """Accept valid device reports for discovery, durable state, and exact command
        confirmation.

        @param received Monotonic host timestamp at which the UART record arrived.
        @param line Complete RX record from the active adapter.
        @param retained Recover a confirmed query report without altering UART RX ordering.
        """
        try:
            sequence, rssi, lqi, raw = rx_envelope(line)
        except ValueError:
            self.diagnostics["invalid_rx"] += 1
            return
        if not retained and self.last_rx is not None:
            gap = (sequence - self.last_rx - 1) & 0xFFFFFFFF
            if gap < 0x80000000:
                self.diagnostics["rx_gaps"] += gap
        if not retained:
            self.last_rx = sequence
        try:
            packet = decode(raw)
        except ValueError:
            self.diagnostics["invalid_rx"] += 1
            return
        if (
            packet.family == 0x9E
            and packet.opcode == 1
            and packet.tag == "ff"
            and packet.source == packet.subject
            and packet.source not in ("00000000", "ffffffff")
            and packet.destination in (self.config.gateway, "ffffffff")
            and received - self.discovery_seen.get(packet.source, -100) >= 5
        ):
            if len(self.discovery_seen) >= 256:
                self.discovery_seen.clear()
            self.discovery_seen[packet.source] = received
            candidate = {
                "device": packet.source,
                "suffix": packet.suffix,
                "configured": packet.source in self.devices,
                "rssi": rssi,
                "lqi": lqi,
                "last_seen": time.time(),
            }
            if len(self.discoveries) >= 256:
                self.discoveries.clear()
            self.discoveries[packet.source] = candidate
            self.publish("bridge/discovery", candidate)
        device = self.devices.get(packet.source)
        if (
            not device
            or packet.family != 0x9E
            or packet.subject != device.id
            or packet.suffix != device.suffix
            or packet.destination not in (self.config.gateway, "ffffffff")
        ):
            return
        self.handle_pairing(received, device, packet)
        if packet.tag != self.config.tag:
            return
        self.seen[device.id] = received
        if packet.opcode != 5 or packet.data[:2] != b"\x01\x00":
            return
        mask, bits, counter, token = (
            packet.data[2],
            packet.data[3],
            packet.data[6],
            packet.data[7],
        )
        # The v3 query capture includes mask=0 current-state refreshes. Accept
        # their full state, but exact-mask matching below cannot confirm a set.
        if mask & ~device.mask or bits & ~device.mask:
            return
        # Retained MCU reports can overtake their bulk RX log. Apply them once
        # and never let a delayed older log replace a newer accepted state.
        previous_sequence = self.state_sequences.get(device.id)
        if (
            self.scheduler
            and self.scheduler.query_supported
            and previous_sequence is not None
        ):
            step = (sequence - previous_sequence) & 0xFFFFFFFF
            if step == 0 or step >= 0x80000000:
                return
        self.state_sequences[device.id] = sequence
        previous = self.states.get(device.id)
        signature = (bits, counter, token)
        state = {
            "device": device.id,
            "state_bits": bits,
            "outputs": {
                str(n + 1): "ON" if bits & (1 << n) else "OFF"
                for n in range(device.outputs)
            },
            "mask": mask,
            "counter": counter,
            "token": token,
            "last_seen": time.time(),
            "rssi": rssi,
            "lqi": lqi,
        }
        self.states[device.id] = state
        self.state_received[device.id] = received
        changed = not previous or signature != (
            previous["state_bits"],
            previous["counter"],
            previous["token"],
        )
        # Persist changes immediately; limit repeated current-state report writes to once/minute.
        if (
            changed
            or state["last_seen"] - self.diagnostics.get("saved_" + device.id, 0) >= 60
        ):
            self.store.save_state(device.id, state)
            self.diagnostics["saved_" + device.id] = state["last_seen"]
        self.publish(device.id + "/state", state, True)
        pending = self.pending
        if (
            pending
            and pending["device"] == device.id
            and received >= pending["started"]
            and received < pending.get("deadline", float("inf"))
            and mask == pending["mask"]
            and bits & mask == pending["value"]
            and token == pending["token"]
            and signature != pending["baseline"]
            and not pending["future"].done()
        ):
            pending["future"].set_result(state)

    def handle_pairing(self, received, device, packet):
        """Advance only the active device pairing attempt using eligible native reports.

        @param received Monotonic timestamp of the report.
        @param device Configured Device corresponding to the report.
        @param packet Validated native packet from that device.
        """
        pair = self.pairing
        if (
            not pair
            or pair["device"] != device.id
            or not pair["started"] <= received < pair["deadline"]
        ):
            return
        if packet.tag == self.config.tag:
            self.finish_pairing("confirmed")
        elif (
            packet.tag == "ff"
            and packet.opcode == 1
            and not self.schedule
            and received >= pair["next_attempt"]
        ):
            # Bounded reproduction of the proven spare-switch admission trial.
            for delay, opcode, data in (
                (0.6, 0xFA, b"\x01" + packet.data[:7]),
                (0.8, 0xFA, b"\x01" + packet.data[:7]),
                (1.0, 0x10, bytes(7) + b"\x01"),
                (1.2, 0xAC, bytes(7) + b"\x01"),
                (1.4, 0xAC, bytes(7) + b"\x01"),
            ):
                heapq.heappush(self.schedule, (received + delay, opcode, data))
            pair["next_attempt"] = received + 5

    def finish_pairing(self, status, error=None):
        """Record the pairing outcome and close its scheduled admission window.

        @param status Final pairing command status.
        @param error Optional explanation of failure or cancellation.
        """
        if self.pairing:
            values = {"device": self.pairing["device"]}
            if error:
                values["error"] = error
            self.result(self.pairing["request_id"], status, **values)
        self.pairing = None
        self.schedule.clear()
        self.snapshot()

    async def command_worker(self):
        """Execute queued radio work only while its original session and deadline remain valid."""
        while True:
            queued, session, request_id, action, values = await self.commands.get()
            if (
                not self.ready
                or session != self.session
                or time.monotonic() - queued > 5
            ):
                self.result(
                    request_id,
                    "rejected",
                    error="command expired or radio session changed",
                )
                continue
            self.command_inflight = request_id
            try:
                if action == "set":
                    if self.pairing and self.pairing["device"] == values["device"]:
                        raise ValueError("Device is in a pairing window")
                    await self.set_outputs(request_id, **values)
                elif action == "refresh":
                    if self.pairing and self.pairing["device"] == values["device"]:
                        raise ValueError("Device is in a pairing window")
                    await self.refresh_state(request_id, values["device"])
                elif action == "pair":
                    if values["seconds"] == 0:
                        if self.pairing and self.pairing["device"] != values["device"]:
                            raise ValueError("Another device owns the pairing window")
                        self.finish_pairing("cancelled")
                        self.result(request_id, "confirmed", device=values["device"])
                    else:
                        if self.pairing:
                            raise ValueError("A pairing window is already open")
                        now = time.monotonic()
                        self.pairing = dict(
                            values,
                            request_id=request_id,
                            started=now,
                            deadline=now + values["seconds"],
                            next_attempt=now,
                        )
                        self.result(request_id, "pairing", **values)
                        self.snapshot()
                else:
                    self.snapshot()
                    self.result(request_id, "confirmed")
            except ValueError as error:
                self.result(request_id, "rejected", error=str(error))
            except (LinkError, CommandError, asyncio.CancelledError):
                self.result(
                    request_id, "unknown", error="radio session ended before completion"
                )
                raise
            finally:
                self.command_inflight = None

    async def refresh_state(self, request_id, device):
        """Retry read-only query pairs until fresh state or bounded exhaustion.

        @param request_id Durable refresh command identifier.
        @param device Configured device identity to query.
        """
        if self.scheduler and self.scheduler.query_supported:
            await self.refresh_state_mcu(request_id, device)
            return
        peer = self.devices[device]
        started = time.monotonic()
        deadline = started + REFRESH_TIMEOUT
        session = self.session
        rounds = sent = 0

        def fresh():
            """Return whether this device has an accepted report since refresh began."""
            return (
                device in self.states and self.state_received.get(device, 0) >= started
            )

        def needed():
            """Abort a lost session and suppress sends after fresh state or deadline."""
            if not self.ready or self.session != session:
                raise LinkError("Radio session changed during state refresh")
            return not fresh() and time.monotonic() < deadline

        async def wait_for_report(seconds):
            """Wait at most the given seconds, stopping promptly on fresh state.

            @param seconds Maximum response or query-spacing wait in seconds.
            """
            until = min(deadline, time.monotonic() + seconds)
            while needed() and time.monotonic() < until:
                await asyncio.sleep(min(0.05, until - time.monotonic()))

        for _ in range(REFRESH_ROUNDS):
            if not needed():
                break
            rounds += 1
            for opcode in (0x10, 0xAC):
                if not needed():
                    break
                acquired = False
                while needed() and not acquired:
                    try:
                        await asyncio.wait_for(
                            self.radio_lock.acquire(),
                            max(0, min(0.05, deadline - time.monotonic())),
                        )
                        acquired = True
                    except asyncio.TimeoutError:
                        pass  # Recheck fresh state while another radio user owns the lock.
                if not acquired:
                    break
                try:
                    # A report may have arrived while waiting for another radio user.
                    if needed():
                        sent += bool(
                            await self._transmit(
                                frame(
                                    self.config.gateway,
                                    self.config.tag,
                                    device,
                                    peer.suffix,
                                    self.next_sequence(),
                                    opcode,
                                    bytes(7) + b"\x01",
                                ),
                                still_needed=needed,
                                phase_query=bool(
                                    self.phase_client
                                    and self.phase_client.query_supported
                                ),
                            )
                        )
                finally:
                    self.radio_lock.release()
                if opcode == 0x10:
                    await wait_for_report(
                        refresh_query_interval(self.config.serial_baudrate)
                    )
            await wait_for_report(REFRESH_RESPONSE_WAIT)
        if fresh():
            self.result(
                request_id,
                "confirmed",
                device=device,
                state=self.states[device],
                query_rounds=rounds,
                queries_sent=sent,
            )
            return
        self.result(
            request_id,
            "unconfirmed",
            device=device,
            error="No fresh state report after read-only queries",
            query_rounds=rounds,
            queries_sent=sent,
        )

    async def refresh_state_mcu(self, request_id, device):
        """Drain a complete MCU query without using host timers to transmit retries.

        @param request_id Durable refresh command identity.
        @param device Registry-authorized peer identity.
        """
        peer = self.devices[device]
        scheduler, session = self.scheduler, self.session
        self.diagnostics.pop("query_phase_tx", None)
        self.diagnostics["query_execution"] = dict(
            owner="mcu",
            pair_entry_seconds=0.2355,
            response_entry_seconds=REFRESH_RESPONSE_WAIT + 0.012,
            rounds=3,
            deadline_seconds=14.0,
            cca_attempts=4,
        )
        raw = frame(
            self.config.gateway,
            self.config.tag,
            device,
            peer.suffix,
            self.next_sequence(),
            0x10,
            bytes(7) + b"\x01",
        )
        started = time.monotonic()
        async with self.radio_lock:
            job = await scheduler.start_query(raw, peer.mask, REFRESH_RESPONSE_WAIT)
        completed = False
        try:
            until = started + REFRESH_TIMEOUT + 2
            while True:
                if not self.ready or self.session != session:
                    raise LinkError("Radio session changed during MCU query")
                result = await scheduler.query_result(job, raw, peer.mask)
                self.diagnostics["query_transaction"] = dict(result)
                if result["state"] == "done":
                    completed = True
                    break
                if time.monotonic() >= until:
                    result = await scheduler.query_result(
                        job, raw, peer.mask, cancel=True
                    )
                    completed = result["state"] == "done"
                    raise LinkError("MCU query exceeded bounded completion time")
                await asyncio.sleep(0.05)
            if int(result["attempts"]) and int(result["tx_result"]) >= 0:
                wire = bytes.fromhex(result["raw"])
                expected = bytearray(raw)
                if len(wire) != 41 or wire[22] not in (0x10, 0xAC):
                    raise LinkError("Invalid MCU query opcode")
                expected[22] = wire[22]
                expected[37] = sum(expected[21:37]) & 255
                code = int(result["tx_result"])
                fields = " ".join(
                    f"{k}={result[k]}"
                    for k in (
                        "entry_ticks",
                        "return_ticks",
                        "anchor_ticks",
                        "anchor_gen",
                        "age_ticks",
                        "raw",
                    )
                )
                metadata = self.phase_client.validate_reply(
                    f"{'OK' if code == 0 else 'ERR'} TX {code} timestamp=tx_entry {fields}",
                    bytes(expected),
                )
                self.diagnostics["query_phase_tx"] = dict(metadata)
            values = dict(
                device=device,
                query_rounds=int(result["rounds"]),
                queries_sent=int(result["sent"]),
            )
            if result["outcome"] == "1":
                report = result["report"]
                self.handle_rx(
                    time.monotonic(),
                    f"RX {result['rx_sequence']} 0 {result['rssi']} {result['lqi']} 39 {report}",
                    retained=True,
                )
                if self.state_received.get(device, 0) < started:
                    raise LinkError(
                        "Retained query confirmation did not validate on host"
                    )
                self.result(
                    request_id, "confirmed", state=self.states[device], **values
                )
            elif result["outcome"] == "2":
                self.result(
                    request_id,
                    "unconfirmed",
                    error="No fresh state report after read-only queries",
                    **values,
                )
            else:
                raise LinkError("MCU query cancelled or radio outcome uncertain")
        finally:
            if completed and self.ready and self.session == session:
                await scheduler.drop_query(job)

    async def _phase_ready(self):
        """Require a usable phase reference without forcing an early heartbeat.

        @return Whether a phase send may proceed; caller must hold radio_lock.
        """
        if not self.ready:
            raise LinkError("Phase radio session is unavailable")
        if time.monotonic() >= self.heartbeat_due():
            return await self._emit_heartbeat()
        return not self.phase_reference_missing

    @asynccontextmanager
    async def output_window(self):
        """Provide the heartbeat or phase-reference precondition for an output transaction.

        @return An asynchronous context yielding whether the transmission window is ready.
        """
        if self.phase_client:
            async with self.radio_lock:
                ready = await self._phase_ready()
            # Periodic heartbeats may run between individual phase attempts.
            yield ready
        else:
            async with self.set_radio_window():
                yield await self._emit_heartbeat()

    async def _send_output(self, raw):
        """Submit an output attempt while honoring its confirmation deadline and phase policy.

        @param raw Complete direct output frame; legacy callers must hold radio_lock.
        @return True if sent; False for a definite non-send or completed transaction.
        """
        if not self.phase_client:
            return await self._transmit(raw)
        async with self.radio_lock:
            if self.pending and (
                self.pending["future"].done()
                or time.monotonic() >= self.pending["deadline"]
            ):
                return False
            if not await self._phase_ready():
                return False
            if self.pending and (
                self.pending["future"].done()
                or time.monotonic() >= self.pending["deadline"]
            ):
                return False
            try:
                metadata = await self.phase_client.send(raw)
                self.diagnostics["phase_tx"] = dict(metadata)
                return True
            except CommandError as error:
                if str(error) == "ERR PHASE_REFERENCE":
                    # A proven non-send. Recover at the next normal heartbeat;
                    # this transaction remains bounded rather than waiting 10s.
                    self.phase_reference_missing = True
                    return False
                if str(error).startswith("ERR TX 2 "):
                    self.diagnostics["phase_tx"] = dict(self.phase_client.last_tx)
                    return False
                raise LinkError(str(error)) from error

    async def set_outputs(self, request_id, device, mask, value):
        """Apply requested output bits and require a matching fresh device confirmation.

        @param request_id Durable output command identifier.
        @param device Configured target device identity.
        @param mask Nonzero mask selecting outputs to change.
        @param value Desired state bits restricted to the selected mask.
        """
        if self.phase_client and self.scheduler and self.scheduler.output_supported:
            return await self.set_outputs_mcu(request_id, device, mask, value)
        peer = self.devices[device]
        future = None
        sent = False
        try:
            # Legacy mode reserves the prelude/retry window. Phase mode holds
            # the RF lock only around each send, allowing normal heartbeats.
            async with self.output_window() as window_ready:
                if not window_ready:
                    self.result(
                        request_id,
                        "busy",
                        device=device,
                        error="Heartbeat/reference unavailable; no output command sent",
                    )
                    return
                previous = self.states.get(device, {})
                token = self.store.token(
                    device,
                    previous.get("token"),
                    mask=mask,
                    value=value,
                    full_mask=peer.mask,
                )
                future = asyncio.get_running_loop().create_future()
                started = time.monotonic()
                deadline = started + SET_CONFIRM_TIMEOUT
                self.pending = {
                    "device": device,
                    "mask": mask,
                    "value": value,
                    "token": token,
                    "started": started,
                    "deadline": deadline,
                    "future": future,
                    "baseline": tuple(
                        previous.get(k) for k in ("state_bits", "counter", "token")
                    ),
                }
                data = bytes((1, 0, mask, value, 0, 0, 0, token))
                for _ in range(
                    PHASE_SET_ATTEMPTS if self.phase_client else SET_ATTEMPTS
                ):
                    remaining = deadline - time.monotonic()
                    if future.done() or remaining <= 0:
                        break
                    try:
                        sent |= await asyncio.wait_for(
                            self._send_output(
                                frame(
                                    self.config.gateway,
                                    self.config.tag,
                                    device,
                                    peer.suffix,
                                    self.next_sequence(),
                                    2,
                                    data,
                                )
                            ),
                            remaining,
                        )
                    except asyncio.TimeoutError:
                        # The UART outcome is uncertain. The worker must tear down
                        # this session, never issue a fresh RF retry after the limit.
                        raise LinkError(
                            "Set deadline expired during transmission; RF outcome unknown"
                        ) from None
                    if future.done():
                        break
                    try:
                        await asyncio.wait_for(
                            asyncio.shield(future),
                            min(
                                SET_RETRY_INTERVAL, max(0, deadline - time.monotonic())
                            ),
                        )
                        break
                    except asyncio.TimeoutError:
                        pass
            if future and not future.done():
                try:
                    await asyncio.wait_for(
                        asyncio.shield(future), max(0, deadline - time.monotonic())
                    )
                except asyncio.TimeoutError:
                    self.result(
                        request_id,
                        "unconfirmed" if sent else "busy",
                        device=device,
                        token=token,
                        error=(
                            "No matching state report"
                            if sent
                            else "Radio busy or phase reference unavailable; no transmission"
                        ),
                    )
                    return
            self.result(
                request_id,
                "confirmed",
                device=device,
                token=token,
                state=future.result(),
            )
        finally:
            self.pending = None
            if future and not future.done():
                future.cancel()

    async def set_outputs_mcu(self, request_id, device, mask, value):
        """Keep ownership on the Pi; drain a bounded MCU-aligned output transaction.

        @param request_id Durable command identifier used to publish the result.
        @param device Wire-order identifier of the registered target peer.
        @param mask Output bits selected by the validated command.
        @param value Requested state bits within the selected mask.
        """
        peer, scheduler, session = self.devices[device], self.scheduler, self.session
        previous = self.states.get(device, {})
        baseline = (
            bytes(previous[k] for k in ("state_bits", "counter", "token"))
            if previous
            else b"\xff" * 3
        )
        token = self.store.token(
            device, previous.get("token"), mask=mask, value=value, full_mask=peer.mask
        )
        raw = frame(
            self.config.gateway,
            self.config.tag,
            device,
            peer.suffix,
            self.next_sequence(),
            2,
            bytes((1, 0, mask, value, 0, 0, 0, token)),
        )
        self.diagnostics["output_execution"] = dict(
            owner="mcu", grid_ms=200, attempts=6, deadline_seconds=4
        )
        async with self.radio_lock:
            job = await scheduler.start_output(raw, peer.mask, baseline)
        completed = False
        try:
            # Four seconds on MCU; the extra two seconds only drain its result.
            until = time.monotonic() + SET_CONFIRM_TIMEOUT + 2
            while True:
                if not self.ready or self.session != session:
                    raise LinkError("Radio session changed during MCU output")
                result = await scheduler.query_result(
                    job, raw, peer.mask, output=True, baseline=baseline
                )
                self.diagnostics["output_transaction"] = dict(result)
                if result["state"] == "done":
                    completed = True
                    break
                if time.monotonic() >= until:
                    await scheduler.query_result(
                        job, raw, peer.mask, cancel=True, output=True, baseline=baseline
                    )
                    raise LinkError("MCU output exceeded bounded completion time")
                await asyncio.sleep(0.02)
            if int(result["attempts"]) and int(result["tx_result"]) >= 0:
                code = int(result["tx_result"])
                fields = " ".join(
                    f"{k}={result[k]}"
                    for k in (
                        "entry_ticks",
                        "return_ticks",
                        "anchor_ticks",
                        "anchor_gen",
                        "age_ticks",
                        "raw",
                    )
                )
                self.diagnostics["phase_tx"] = self.phase_client.validate_reply(
                    f"{'OK' if code == 0 else 'ERR'} TX {code} timestamp=tx_entry {fields}",
                    raw,
                )
            values = dict(
                device=device,
                token=token,
                output_attempts=int(result["attempts"]),
                output_sent=int(result["sent"]),
                grid_skips=int(result["grid_skips"]),
            )
            if result["outcome"] == "1":
                packet = decode(bytes.fromhex(result["report"]))
                self.handle_rx(
                    time.monotonic(),
                    f"RX {result['rx_sequence']} 0 {result['rssi']} {result['lqi']} 39 {result['report']}",
                    retained=True,
                )
                # Report the confirmed transaction's state even if a newer
                # physical event has already updated the authoritative registry.
                bits = packet.data[3]
                state = dict(
                    device=device,
                    state_bits=bits,
                    outputs={
                        str(n + 1): "ON" if bits & (1 << n) else "OFF"
                        for n in range(peer.outputs)
                    },
                    mask=packet.data[2],
                    counter=packet.data[6],
                    token=packet.data[7],
                    last_seen=time.time(),
                    rssi=int(result["rssi"]),
                    lqi=int(result["lqi"]),
                )
                self.result(request_id, "confirmed", state=state, **values)
            elif result["outcome"] == "2":
                self.result(
                    request_id,
                    "unconfirmed" if int(result["sent"]) else "busy",
                    error="No matching state report",
                    **values,
                )
            else:
                raise LinkError("MCU output cancelled or radio outcome uncertain")
        finally:
            if completed and self.ready and self.session == session:
                await scheduler.drop_query(job)

    async def run(self):
        """Keep the gateway reconciled across failures until shutdown is requested."""
        delay = 1
        while not self.stop.is_set():
            self.firmware_manager.check_deadline()
            tasks = []
            stop_task = None
            reconfigure_task = None
            firmware_task = None
            guard = None
            handoff = False
            try:
                self.reconfigure.clear()
                guard = radio_guard(self.config.database)
                self.gateway = self.gateway_factory()
                await self.restore()
                delay = 1
                tasks = [
                    asyncio.create_task(job())
                    for job in (self.events, self.maintenance, self.command_worker)
                ]
                stop_task = asyncio.create_task(self.stop.wait())
                reconfigure_task = asyncio.create_task(self.reconfigure.wait())
                firmware_task = asyncio.create_task(
                    self.firmware_manager.requested.wait()
                )
                done, _ = await asyncio.wait(
                    tasks
                    + [self.gateway.fault, stop_task, reconfigure_task, firmware_task],
                    return_when=asyncio.FIRST_COMPLETED,
                )
                for task in done:
                    if task is self.gateway.fault:
                        raise task.result()
                    task.result()
            except (OSError, LinkError, CommandError, ValueError) as error:
                LOG.warning("Radio unavailable: %s; reconnecting", error)
            except asyncio.CancelledError:
                self.stop.set()
                raise
            finally:
                self.ready = False
                waiters = [t for t in (stop_task, reconfigure_task, firmware_task) if t]
                for task in tasks + waiters:
                    task.cancel()
                await asyncio.gather(*tasks, *waiters, return_exceptions=True)
                self.finish_pairing("unknown", "radio session ended")
                while not self.commands.empty():
                    _, _, request_id, _, _ = self.commands.get_nowait()
                    self.result(
                        request_id,
                        "rejected",
                        error="radio session ended before execution",
                    )
                self.seen.clear()
                self.state_received.clear()
                self.snapshot()
                try:
                    if self.gateway:
                        verified = False
                        try:
                            if self.firmware_manager.requested.is_set():
                                try:
                                    if self.stop.is_set():
                                        raise LinkError(
                                            "Shutdown before firmware handoff"
                                        )
                                    await self.firmware_manager.preflight(self.gateway)
                                    await self.gateway.command("DISARM")
                                    status = key_values(
                                        await self.gateway.command("STATUS")
                                    )
                                    if (
                                        status.get("armed") != "0"
                                        or status.get("ack") != "0"
                                    ):
                                        raise LinkError("Radio disarm was not verified")
                                    verified = True
                                except (OSError, LinkError, CommandError):
                                    self.firmware_manager.failed(
                                        "Radio handoff could not be verified",
                                        uncertain=False,
                                    )
                                    self.firmware_manager.requested.clear()
                        finally:
                            await self.gateway.close()
                            self.gateway = None
                        handoff = verified
                finally:
                    if not handoff and guard is not None:
                        os.close(guard)
                        guard = None
            if handoff:
                await self.firmware_manager.execute(guard)
                continue
            if not self.stop.is_set():
                try:
                    await asyncio.wait_for(self.stop.wait(), delay)
                except asyncio.TimeoutError:
                    pass
                delay = min(delay * 2, 30)
