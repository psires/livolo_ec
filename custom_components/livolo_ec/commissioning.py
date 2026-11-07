"""One bounded daemon pairing session shared by HA buttons, options and notifications."""

import asyncio
import re
import uuid

from homeassistant.components import persistent_notification
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.storage import Store

from ._client import (
    COMMISSIONING_ACTIVE,
    CommandFailed,
    IncompatibleError,
    LivoloError,
    commissioning_data,
)
from .const import DOMAIN

PAIRING_STATES = {
    "unsupported": "Update the daemon to enable automatic pairing. Advanced manual setup remains available.",
    "idle": "Press Pair new device, then put only the intended new switch into pairing mode. The window lasts at most two minutes.",
    "listening": "Waiting for a fresh supported switch. Put only the intended switch into physical pairing mode.",
    "registering": "A switch was detected. Its profile is being saved and the radio briefly reconfigured. Pairing is not yet confirmed.",
    "pairing": "The switch is registered. Waiting for its fresh physical confirmation; registration alone is not pairing success.",
    "confirmed": "The daemon confirmed pairing from a fresh native device report.",
    "expired": "The pairing window expired without confirmation. Any registered device is retained; use advanced targeted pairing to retry it.",
    "cancelled": "The pairing window was closed. Messages already sent cannot be undone; any registered device is retained.",
    "conflict": "Conflicting devices or profiles were observed. Put only the intended supported switch into pairing mode and start a new session.",
    "failed": "The session failed without pairing confirmation. Check the reported reason; any registered device is retained.",
    "unknown": "The pairing outcome is unknown after an interruption. Inspect the retained device and use advanced targeted pairing if needed. No automatic retry occurs.",
    "invalid": "The daemon reported an invalid pairing status. No success can be inferred; check the daemon version and connection.",
    "uncertain": "A start request could not be resolved. Do not start another session. Reconnect to read its result or press Cancel pairing to fence that request, even if the daemon has not shown a session yet.",
}
PAIRING_REASONS = {
    "unknown_pairing_suffix": "Unsupported switch variant; use advanced manual setup with a verified profile.",
    "unknown_pairing_payload": "Unrecognized pairing signature; use advanced manual setup with a verified profile.",
    "unsupported_device_seen": "Only unsupported announcements were observed. Use advanced manual setup with a verified profile.",
    "discovery_timeout": "No supported new switch was detected before the window expired.",
    "multiple_devices": "Multiple eligible switches were detected. Put only one into pairing mode.",
    "too_many_devices": "Too many devices were detected. Put only one into pairing mode.",
    "conflicting_pairing_profiles": "The observed switch profile changed or conflicted. Do not assume its output count.",
    "daemon_restarted": "The daemon restarted and did not resume the session.",
    "radio_session_ended": "The radio disconnected unexpectedly; pairing was not confirmed.",
    "registry_full": "The device registry is full.",
    "registry_error": "The device profile could not be saved.",
    "session_not_reported": "The previously active session is no longer reported. Its outcome is unknown; it was not automatically restarted.",
    "session_replaced": "A different session replaced the previously active session. The earlier outcome is unknown.",
}


class LivoloCommissioning:
    """Observe daemon results and submit explicit actions once, without owning admission."""

    def __init__(self, coordinator):
        """Bind one observer and mutation guard to the existing logical gateway.

        @param coordinator Shared authenticated push coordinator.
        """
        self.coordinator = coordinator
        self._busy = False
        self._observed = None
        self._notification = None
        self._pending = None
        self._last_active = None
        self._interrupted = None
        self._store = Store(
            coordinator.hass, 1, f"{DOMAIN}.commissioning.{coordinator.entry.entry_id}"
        )

    async def restore(self):
        """Restore only unresolved local intent; never replay a stored command."""
        saved = await self._store.async_load()
        ident = saved.get("request_id") if isinstance(saved, dict) else None
        if isinstance(ident, str) and re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", ident):
            self._pending = ident
            self._observed = ident

    def _resolve_pending(self):
        """Resolve local uncertainty after authoritative observation, retaining no credentials."""
        self._pending = None
        self._store.async_delay_save(lambda: {"request_id": self._pending}, 1)

    @property
    def pending(self):
        """Expose whether an unresolved start must be observed or explicitly cancelled."""
        return self._pending is not None

    @property
    def supported(self):
        """Require an explicit capability from the daemon before offering mutations."""
        return self.coordinator.data.get("commissioning_supported") is True

    @property
    def summary(self):
        """Expose only validated session facts; malformed status cannot claim confirmation."""
        try:
            return (
                commissioning_data(self.coordinator.data.get("commissioning"))
                or self._interrupted
            )
        except IncompatibleError:
            return {"status": "invalid"}

    @property
    def active(self):
        """Recognize every daemon-owned active phase, including registry reconciliation."""
        return (self.summary or {}).get("status") in COMMISSIONING_ACTIVE

    @property
    def status(self):
        """Provide a finite UI state without interpreting command acceptance as pairing."""
        if not self.supported:
            return "unsupported"
        if self.pending:
            return "uncertain"
        return (self.summary or {}).get("status", "idle")

    @property
    def attributes(self):
        """Expose bounded public result details and actionable unsupported-device reasons."""
        summary = self.summary or {}
        attributes = dict(summary, explanation=PAIRING_STATES[self.status])
        if self._pending:
            attributes["unresolved_request_id"] = self._pending
        if self._interrupted:
            attributes["previous_session_id"] = self._interrupted["session_id"]
            attributes["previous_outcome"] = "unknown"
        error = summary.get("error")
        attributes["reason"] = PAIRING_REASONS.get(error, error) if error else None
        attributes["observations"] = [
            dict(
                item,
                explanation=PAIRING_REASONS.get(
                    item.get("reason"), "Observed pairing announcement"
                ),
            )
            for item in summary.get("observations", [])
        ]
        return attributes

    @property
    def can_start(self):
        """Disable starting while disconnected, busy or in known firmware maintenance."""
        firmware = self.coordinator.data.get("firmware_update") or {}
        return bool(
            self.coordinator.connected
            and self.supported
            and not self._busy
            and self.coordinator.data.get("ready")
            and not self.active
            and not self.pending
            and not self.coordinator.data.get("pairing")
            and (self.summary or {}).get("status") != "invalid"
            and not firmware.get("in_progress")
            and not firmware.get("recovery")
        )

    @property
    def can_cancel(self):
        """Keep explicit cancellation usable during radio downtime and registration."""
        return (
            self.coordinator.connected
            and self.supported
            and (self.active or self.pending)
            and not self._busy
        )

    async def refresh(self):
        """Read current snapshot and, if needed, one original command result without replay."""
        self.coordinator.accept(await self.coordinator.client.snapshot())
        if self._pending:
            ident = self._pending
            result = await self.coordinator.client.result(ident)
            if (
                result
                and result.get("request_id") == ident
                and result.get("status")
                in (
                    "rejected",
                    "confirmed",
                    "cancelled",
                    "expired",
                    "failed",
                    "conflict",
                    "unknown",
                )
            ):
                self._resolve_pending()
                self._observed = None
                self._interrupted = {
                    "session_id": ident,
                    "status": "unknown",
                    "error": "session_not_reported",
                }
                self.coordinator.async_update_listeners()

    async def act(self, cancel=False):
        """Start or cancel once, with durable result recovery and no mutation retry.

        @param cancel True closes the current session; False starts one 120-second session.
        """
        if self._busy:
            raise HomeAssistantError("A pairing action is already being submitted")
        try:
            await self.refresh()
        except LivoloError as error:
            raise HomeAssistantError(
                "Cannot check the current pairing session; no action was submitted"
            ) from error
        if not (self.can_cancel if cancel else self.can_start):
            raise HomeAssistantError(
                "Pairing is unavailable or another session is active. Check Pairing status."
            )
        # No await between this check and setting the shared guard.
        self._busy = True
        self.coordinator.async_update_listeners()
        request_id = str(uuid.uuid4())
        payload = {"request_id": request_id}
        if cancel:
            payload["session_id"] = self._pending or self.summary["session_id"]
        else:
            payload["seconds"] = 120
            self._observed = request_id
        try:
            if not cancel:
                await self._store.async_save({"request_id": request_id})
                self._pending = request_id
            await self.coordinator.client.command(
                "commission_cancel" if cancel else "commission", payload
            )
            if cancel and payload["session_id"] == self._pending:
                self._resolve_pending()
                self._observed = None
            # The cancellation command confirms closure only. The session decides the outcome.
            self.coordinator.accept(await self.coordinator.client.snapshot())
        except CommandFailed as error:
            if (
                not cancel
                and error.result.get("request_id") == request_id
                and error.result.get("status") == "rejected"
            ):
                self._resolve_pending()
                self._observed = None
            raise HomeAssistantError(
                f"Pairing action {request_id}: {error.result.get('status', 'unknown')}. Check Pairing status before retrying."
            ) from error
        except LivoloError as error:
            raise HomeAssistantError(
                f"Pairing action {request_id} could not be observed. It may have started; check Pairing status before retrying."
            ) from error
        finally:
            self._busy = False
            self.coordinator.async_update_listeners()

    def observe(self, data):
        """Notify only a confirmed result belonging to an observed or explicitly started session.

        @param data Validated logical-network snapshot accepted by the coordinator.
        """
        try:
            summary = commissioning_data(data.get("commissioning"))
        except IncompatibleError:
            return
        if not summary:
            if self._last_active:
                self._interrupted = dict(
                    self._last_active,
                    status="unknown",
                    error="session_not_reported",
                    seconds_remaining=0,
                )
                self._last_active = None
                self._observed = None
            return
        session_id = summary["session_id"]
        if self._pending == session_id:
            self._resolve_pending()
        if self._last_active and self._last_active["session_id"] != session_id:
            self._interrupted = dict(
                self._last_active,
                status="unknown",
                error="session_replaced",
                seconds_remaining=0,
            )
            self._observed = None
        elif self._interrupted and self._interrupted["session_id"] == session_id:
            self._interrupted = None
        if summary["status"] in COMMISSIONING_ACTIVE:
            self._last_active = summary
            self._observed = session_id
            return
        self._last_active = None
        if session_id != self._observed:
            return
        self._observed = None
        if summary["status"] == "confirmed":
            if self._notification and not self._notification.done():
                self._notification.cancel()
            self._notification = self.coordinator.entry.async_create_background_task(
                self.coordinator.hass,
                self._notify(summary),
                "Livolo paired device notification",
            )

    async def _notify(self, summary):
        """Wait briefly for normal entity discovery, then link the confirmed device.

        @param summary Validated terminal session with a confirmed native report.
        """
        hass = self.coordinator.hass
        identifier = (DOMAIN, f"{self.coordinator.entry.unique_id}:{summary['device']}")
        registry = dr.async_get(hass)
        device = None
        for _ in range(40):
            device = registry.async_get_device_by_identifier(
                identifier, self.coordinator.entry.entry_id
            )
            if device:
                break
            await asyncio.sleep(0.05)
        message = f"Pairing confirmed for Livolo {summary['device']} ({summary['outputs']} output(s))."
        if device:
            message += f" [Open device](/config/devices/device/{device.id}). You can rename it and assign an area."
        else:
            message += (
                " Open the Livolo EC integration to find the device and its outputs."
            )
        persistent_notification.async_create(
            hass,
            message,
            title="Livolo device paired",
            notification_id=f"livolo_ec_pairing_{self.coordinator.entry.entry_id}",
        )

    async def close(self):
        """Stop local notification work without cancelling or replaying a daemon session."""
        if self._notification:
            self._notification.cancel()
            await asyncio.gather(self._notification, return_exceptions=True)
            self._notification = None
        await self._store.async_save({"request_id": self._pending})
