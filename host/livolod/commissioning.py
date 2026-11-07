"""Bounded, single-device commissioning using fresh native pairing evidence."""

import asyncio
from dataclasses import replace
import json
import sqlite3
import time

from .config import Device
from .device_profile import pairing_profile

ACTIVE = {"listening", "registering", "pairing"}
COLLECTION_SECONDS = 1.0


class Commissioning:
    def __init__(self, service):
        """Restore public outcome without resuming radio work after process restart.

        @param service Owner-loop Service and its durable registry/command ledger.
        """
        self.service = service
        self.state = None
        self.deadline = 0.0
        self.started = 0.0
        self.session = None
        self.candidate = None
        self.signatures = {}
        self.conflicts = set()
        self.expected_reconfigure = False
        self.awaiting_restore = False
        self.tx_started = None
        self.tx_confirmed = False
        self.buffered_report = False
        row = service.store.db.execute(
            "SELECT value FROM meta WHERE key='commissioning_session'"
        ).fetchone()
        if row:
            self.state = json.loads(row[0])
            if self.active:
                self.state["registered"] = self.state["device"] in service.devices
                self.finish("unknown", "daemon_restarted")

    @property
    def active(self):
        """Return whether an accepted session still owns commissioning policy."""
        return self.state is not None and self.state["status"] in ACTIVE

    def snapshot(self):
        """Return detached public session data with a monotonic bounded countdown."""
        if self.state is None:
            return None
        return dict(
            self.state,
            observations=[dict(item) for item in self.state.get("observations", [])],
            seconds_remaining=(
                max(0, int(self.deadline - time.monotonic())) if self.active else 0
            ),
        )

    def save(self):
        """Persist the public session transition and correlated command outcome."""
        with self.service.store.db:
            self.service.store.db.execute(
                "INSERT OR REPLACE INTO meta VALUES ('commissioning_session', ?)",
                (json.dumps(self.state),),
            )
            # Store.finish commits this same connection's transaction, including
            # the summary above, before Service publishes the result.
            return self.service.result(
                self.state["session_id"],
                self.state["status"],
                session_id=self.state["session_id"],
                device=self.state["device"],
                registered=self.state["registered"],
                error=self.state["error"],
            )

    def start(self, request_id, seconds):
        """Open a fresh evidence window; callers have already journaled the request.

        @param request_id Stable command and session identifier.
        @param seconds Validated total duration, including registry reconciliation.
        @return Initial durable listening result or rejected busy/offline result.
        """
        service = self.service
        if not service.ready:
            return service.result(request_id, "rejected", error="radio is offline")
        if (
            self.active
            or service.pairing
            or service.command_inflight
            or not service.commands.empty()
            or service.pending
            or service.reconfigure.is_set()
            or service.radio_lock.locked()
        ):
            return service.result(request_id, "rejected", error="radio_busy")
        self.started = time.monotonic()
        self.deadline = self.started + seconds
        self.session = service.session
        self.candidate = None
        self.signatures = {}
        self.conflicts = set()
        self.tx_started = None
        self.tx_confirmed = self.buffered_report = False
        self.expected_reconfigure = self.awaiting_restore = False
        self.state = dict(
            session_id=request_id,
            status="listening",
            device=None,
            name=None,
            outputs=None,
            suffix=None,
            profile=None,
            registered=False,
            error=None,
            observations=[],
        )
        return self.save()

    def cancel(self, request_id, session_id):
        """Close exactly one session, fencing a possibly delayed unaccepted start.

        @param request_id Identifier of the cancellation command.
        @param session_id Start command identifier, never a wildcard.
        @return Durable cancellation-command result, not a pairing success claim.
        """
        service = self.service
        if self.state and self.state["session_id"] == session_id:
            if self.active:
                self.finish("cancelled")
        else:
            previous = service.command_result(session_id)
            if previous is None:
                # A delayed start with this ID must conflict rather than reopen.
                service.store.begin(
                    session_id, {"action": "commission", "cancelled_before_start": True}
                )
                service.result(
                    session_id, "cancelled", session_id=session_id, registered=False
                )
            elif previous.get("action") != "commission":
                return service.result(request_id, "rejected", error="not_a_session")
        return service.result(request_id, "confirmed", session_id=session_id)

    def finish(self, status, error=None):
        """End the window without deleting an already registered device.

        @param status Terminal session outcome; only native confirmation means success.
        @param error Optional stable reason code for the terminal outcome.
        """
        if not self.active:
            return
        self.state.update(status=status, error=error)
        self.expected_reconfigure = self.awaiting_restore = False
        pair = self.service.pairing
        if pair and pair.get("commissioning"):
            self.service.pairing = None
            self.service.schedule.clear()
        self.save()

    def observe(self, packet, received):
        """Collect only post-start pairing evidence in the owned ready session.

        @param packet Checksum-validated, non-retained native packet.
        @param received Monotonic UART arrival timestamp.
        """
        service = self.service
        now = time.monotonic()
        if (
            not self.active
            or not service.ready
            or self.session != service.session
            or not self.started <= received <= now < self.deadline
            or self.state["status"] == "registering"
        ):
            return
        profile = pairing_profile(packet, gateway=service.config.gateway)
        if profile is None or packet.source == service.config.gateway:
            return
        if packet.source in service.devices and packet.source != self.state["device"]:
            return
        if packet.source not in self.signatures and len(self.signatures) >= 256:
            self.finish("conflict", "too_many_devices")
            return
        previous = self.signatures.setdefault(packet.source, profile["signature"])
        if previous != profile["signature"]:
            self.conflicts.add(packet.source)
        observation = {
            key: profile[key] for key in ("outputs", "suffix", "profile", "reason")
        }
        observation["device"] = packet.source
        observations = self.state["observations"]
        old = next(
            (item for item in observations if item["device"] == packet.source), None
        )
        if old != observation:
            if old is not None:
                observations.remove(old)
            observations.append(observation)
            del observations[:-8]
            self.save()
        if packet.source in self.conflicts and (
            packet.source == self.state["device"] or profile["status"] == "detected"
        ):
            self.finish("conflict", "conflicting_pairing_profiles")
            return
        if self.candidate and packet.source == self.state["device"]:
            if profile["signature"] != self.candidate["profile"]["signature"]:
                self.finish("conflict", "conflicting_pairing_profiles")
            return
        if packet.source in service.devices or profile["status"] != "detected":
            return
        if self.state["status"] != "listening":
            return
        if self.candidate:
            self.finish("conflict", "multiple_devices")
            return
        self.candidate = dict(profile=profile, collected=now)
        self.state.update(
            device=packet.source,
            name="Livolo " + packet.source,
            outputs=profile["outputs"],
            suffix=profile["suffix"],
            profile=profile["profile"],
        )
        self.save()

    def tick(self):
        """Expire the fixed window or atomically select a collected candidate."""
        if not self.active:
            return
        now = time.monotonic()
        if now >= self.deadline:
            self.finish(
                "expired",
                (
                    "confirmation_timeout"
                    if self.state["registered"]
                    else (
                        "unsupported_device_seen"
                        if self.candidate is None and self.state["observations"]
                        else "discovery_timeout"
                    )
                ),
            )
            return
        if (
            self.state["status"] != "listening"
            or self.candidate is None
            or now - self.candidate["collected"] < COLLECTION_SECONDS
        ):
            return
        service = self.service
        if not service.ready or service.session != self.session:
            self.finish("unknown", "radio_session_ended")
            return
        used = {device.slot for device in service.devices.values()}
        slot = next((n for n in range(service.peer_capacity) if n not in used), None)
        if slot is None:
            self.finish("failed", "registry_full")
            return
        device = Device(
            self.state["device"],
            self.state["name"],
            slot,
            self.state["outputs"],
            self.state["suffix"],
        )
        self.state["status"] = "registering"
        self.save()
        devices = dict(service.devices)
        devices[device.id] = device
        service.store.replace_devices(devices.values())
        service.devices = devices
        service.config = replace(service.config, devices=tuple(devices.values()))
        self.state["registered"] = True
        self.expected_reconfigure = True
        self.save()
        service.registry_revision += 1
        service.ready = False
        service.reconfigure.set()
        service.snapshot()

    def radio_ended(self, expected=False):
        """Permit only the one intentional registry reconciliation to continue.

        @param expected Whether the radio owner ended solely for our reconfigure event.
        """
        if not self.active:
            return
        if expected and self.expected_reconfigure:
            self.expected_reconfigure = False
            self.awaiting_restore = True
        else:
            self.finish("unknown", "radio_session_ended")

    def restored(self):
        """Resume admission after verified peer reconciliation, without extending time."""
        if not self.active or not self.awaiting_restore:
            return
        self.tick()
        if not self.active:
            return
        self.awaiting_restore = False
        self.session = self.service.session
        now = time.monotonic()
        self.service.pairing = dict(
            device=self.state["device"],
            request_id=self.state["session_id"],
            started=now,
            deadline=self.deadline,
            next_attempt=now,
            commissioning=True,
        )
        self.state["status"] = "pairing"
        self.save()

    def begin_transmit(self):
        """Mark the first potentially successful admission send before awaiting UART."""
        if not self.tx_confirmed:
            self.tx_started = time.monotonic()
            self.buffered_report = False

    def end_transmit(self, sent):
        """Promote buffered evidence only after positive radio transmission completion.

        @param sent True only for successful raw SEND, never queue admission or CCA busy.
        """
        if not self.active or time.monotonic() >= self.deadline:
            self.tick()
            return
        if sent:
            self.tx_confirmed = True
            if self.buffered_report:
                self.finish("confirmed")
        elif not self.tx_confirmed:
            self.tx_started = None
            self.buffered_report = False

    def report(self, packet, received):
        """Confirm only a fresh addressed state report following our admission TX.

        @param packet Checksum-validated native packet, not retained query data.
        @param received Monotonic UART arrival timestamp.
        """
        if (
            not self.active
            or self.state["status"] != "pairing"
            or self.tx_started is None
            or self.service.session != self.session
            or not self.tx_started <= received <= time.monotonic() < self.deadline
            or packet.family != 0x9E
            or packet.opcode != 5
            or packet.source != self.state["device"]
            or packet.subject != packet.source
            or packet.suffix != self.state["suffix"]
            or packet.tag != self.service.config.tag
            or packet.destination != self.service.config.gateway
            or len(packet.data) != 8
            or packet.data[:2] != b"\x01\x00"
        ):
            return
        mask = (1 << self.state["outputs"]) - 1
        if packet.data[2] & ~mask or packet.data[3] & ~mask:
            return
        if self.tx_confirmed:
            self.finish("confirmed")
        else:
            self.buffered_report = True

    async def watch(self):
        """Keep expiry active even while the radio is offline or reconciling."""
        while not self.service.stop.is_set():
            try:
                self.tick()
            except (OSError, sqlite3.Error, ValueError):
                self.finish("failed", "registry_error")
            await asyncio.sleep(0.05)
