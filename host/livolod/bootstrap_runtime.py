"""Opt-in HA app lifecycle with a persistent setup API and one physical radio owner."""

import argparse
import asyncio
from copy import deepcopy
from contextlib import closing
import json
import logging
import os
from pathlib import Path
import signal
import sqlite3

from .bootstrap_controller import BootstrapController, BootstrapError
from .bootstrap_store import BootstrapStore
from .bootstrap_worker import (
    BootstrapWorker,
    RadioOwnership,
    target_json,
    physical_selector,
)
from .config import parse
from .native_api import NativeAPI
from .service import Service
from .storage import Store
from .uart import Gateway
from .gecko_profiles import gecko_profile


class SetupService(Service):
    """Normal gateway behavior plus an immediate explicit-maintenance admission fence."""

    setup_active = False

    def submit(self, action, request):
        """Reject new registry/radio work as soon as setup has been durably accepted.

        @param action Normal service command.
        @param request Normal command mapping.
        """
        if self.setup_active:
            return dict(
                request_id=request.get("request_id"),
                status="rejected",
                error="radio_setup_active",
            )
        return super().submit(action, request)

    def start_firmware_update(self, payload):
        """Route opted-in preserved-loader installation through explicit guided setup.

        @param payload Legacy update command envelope.
        """
        return dict(
            request_id=payload.get("request_id"),
            status="rejected",
            error="guided_setup_required",
        )

    def firmware_update_snapshot(self):
        """Advertise guided-only installation before clients offer the legacy action."""
        value = super().firmware_update_snapshot()
        value.update(install_supported=False, blocked_reason="guided_setup_required")
        return value

    def firmware_preflight(self, candidate_id=None):
        """Keep catalog facts visible while directing installation to native setup.

        @param candidate_id Optional hash-bound legacy catalog selection.
        """
        value = super().firmware_preflight(candidate_id)
        value.update(
            executable=False,
            blocked_reason="guided_setup_required",
            requires_manual_action=True,
        )
        return value

    async def configure_mqtt(self, request):
        """Keep configuration immutable during the setup handoff.

        @param request Normal MQTT settings command.
        """
        if self.setup_active:
            raise ValueError("Radio setup active")
        return await super().configure_mqtt(request)


class BootstrapRuntime:
    """Retain API/journal while stopping and rebuilding the ordinary network Service."""

    def __init__(
        self,
        raw,
        settings,
        *,
        service_factory=SetupService,
        gateway_factory=None,
        ready_timeout=30,
    ):
        """Prepare lifecycle dependencies without opening UART or gateway state.

        @param raw Original non-secret daemon configuration, including optional network.
        @param settings Server-owned bootstrap paths/mode/policy.
        @param service_factory Service constructor; injectable for offline lifecycle tests.
        @param gateway_factory Optional fake no-argument gateway constructor for tests.
        @param ready_timeout Bounded normal readiness deadline after explicit handoff.
        """
        self.raw, self.settings = deepcopy(raw), deepcopy(settings)
        self.profile = gecko_profile(settings.get("profile", "zb-gw04-1v2"))
        self.service_factory, self.gateway_factory = service_factory, gateway_factory
        self.ready_timeout = ready_timeout
        self.service = self.task = self.store = self.web = self.api = None
        self.ownership = RadioOwnership(self.settings)
        self.quiesced = True
        self.baseline = None
        self.stop = asyncio.Event()
        self.journal = self.controller = None

    def has_gateway_state(self):
        """Report existing gateway state without opening, seeding or migrating its DB."""
        return Path(self.raw["database"]).exists()

    def admission_reason(self):
        """Keep unresolved legacy updates fenced across the separate setup journal."""
        if self.service is not None:
            job = self.service.firmware_manager.job
        elif self.has_gateway_state():
            # Read only; never seed a gateway network or clear its recovery record.
            try:
                with closing(
                    sqlite3.connect(
                        Path(self.raw["database"]).resolve().as_uri() + "?mode=ro",
                        uri=True,
                    )
                ) as db:
                    row = db.execute(
                        "SELECT value FROM meta WHERE key='firmware_job'"
                    ).fetchone()
                    job = json.loads(row[0]) if row else None
            except (sqlite3.Error, ValueError, OSError):
                return "gateway_state_unavailable"
        else:
            job = None
        if job and (job.get("status") in ("pending", "unknown") or job.get("recovery")):
            return "existing_update_unresolved"
        return None

    def _state_changed(self):
        """Refresh cached recovery eligibility from normal in-memory observations."""
        if self.controller is not None and self.service is not None:
            self.controller.legacy_reason = self.admission_reason()

    def fence(self):
        """Synchronously close all own admission before any maintenance await."""
        if self.service:
            self.service.setup_active = True
            self.service.ready = False
            self.service.stop.set()
        if self.api and self.api.service is not None:
            self.api.service.change_listeners.discard(self.api.changed)
            self.api.service = None

    def _gateway(self):
        """Open only after retaining the shared physical lease; no inferred board proof."""
        expected = self.journal.meta("verified_target") if self.journal else None
        target, _ = self.ownership.acquire(allow_reattach=expected is None)
        stable = self.journal.meta("normal_target") if self.journal else None
        stable = stable or (self.settings.get("loader_policy") or {}).get("target")
        if stable is not None and physical_selector(stable) != physical_selector(
            target_json(target)
        ):
            raise ValueError("Configured physical radio topology changed")
        if expected is not None and target_json(target) != expected:
            raise ValueError(
                "Verified radio attachment changed; inspect setup before resuming"
            )
        gateway = Gateway.open(target.port, self.raw["serial_baudrate"])
        try:
            metadata = os.fstat(gateway.serial.fileno())
            if (
                os.major(metadata.st_rdev),
                os.minor(metadata.st_rdev),
            ) != target.device_number:
                raise ValueError("Opened radio differs from the held physical lease")
            self.ownership.acquire()
            return gateway
        except BaseException:
            # Never send DISARM or any application command to a mismatched endpoint.
            gateway.loop.remove_reader(gateway.serial.fileno())
            gateway.serial.close()
            gateway.closed = True
            raise

    def _baseline(self):
        """Capture separately correlated runtime identity, independent of compatibility readiness."""
        if not self.service or self.ownership.target is None:
            return None
        manager = self.service.firmware_manager
        caps, reported = manager.caps, manager.version_info
        if (
            not caps
            or not reported
            or caps.get("board") != self.profile.board
            or reported.get("board") != caps["board"]
            or reported.get("fw_version") != caps.get("fw_version")
            or caps.get("proto") != "3"
            or reported.get("proto") != "3"
        ):
            return None
        return dict(
            target=target_json(self.ownership.target), version=caps["fw_version"]
        )

    async def enter(self):
        """Quiesce all own tasks and close actual DB/serial resources; preserve config."""
        baseline = self._baseline()
        self.ownership.pinned = True
        self.fence()
        if self.api:
            await self.api.attach(None)
        if self.web:
            await self.web.close()
            self.web = None
        if self.task:
            self.task.cancel()
            try:
                await asyncio.wait_for(asyncio.shield(self.task), 10)
            except asyncio.CancelledError:
                if not self.task.done():
                    raise
            if not self.task.done():
                raise BootstrapError("quiescence_unconfirmed")
            self.task = None
        if self.service and self.service.broker:
            await self.service.broker.close()
        if self.store:
            self.store.close()
            self.store = None
        self.service = None
        self.quiesced = True
        return baseline

    async def start_gateway(self, network):
        """Start ordinary Service against preserved state without requiring it to be ready.

        @param network Previously configured or explicitly initialized identity.
        """
        if not self.quiesced or self.task is not None:
            raise BootstrapError("ownership_unresolved")
        raw = deepcopy(self.raw)
        raw["network"] = dict(raw.get("network") or {}, **network)
        config = parse(raw)
        if not self.has_gateway_state() and config.output_mode != "disabled":
            raise BootstrapError("initial_outputs_must_be_disabled")
        store = Store(config.database, config.gateway + ":" + config.tag)
        try:
            service = self.service_factory(
                config, store, gateway_factory=self.gateway_factory or self._gateway
            )
            self.service, self.store, self.quiesced = service, store, False
            service.change_listeners.add(self._state_changed)
            if config.web["enabled"]:
                from .web import start_web

                self.web = await start_web(service, config)
            self.task = asyncio.create_task(service.run())
            if self.api:
                await self.api.attach(service)
        except BaseException:
            await self.enter()

            raise

    async def handoff(self, network):
        """Confirm actual normal readiness and identity, never bypass compatibility checks.

        @param network Explicitly accepted immutable network identity.
        """
        await self.start_gateway(network)
        try:
            deadline = asyncio.get_running_loop().time() + self.ready_timeout
            while not self.service.ready:
                if self.task.done():
                    self.task.result()
                    raise BootstrapError("normal_service_failed")
                if asyncio.get_running_loop().time() >= deadline:
                    raise BootstrapError("normal_service_not_ready")
                await asyncio.sleep(0.05)
            if (
                self.service.config.gateway != network["gateway"]
                or self.service.config.tag != network["tag"]
                or self.service.simulated
            ):
                raise BootstrapError("network_changed")
        except BaseException:
            await self.enter()
            raise

    def normal_confirmed(self):
        """Allow ordinary reconnect only after durable successful network handoff."""
        self.ownership.pinned = False

    async def run(self, token):
        """Serve authenticated setup status before starting any ordinary radio owner.

        @param token Runtime bearer token provided separately from persistent settings.
        """
        self.journal = BootstrapStore(self.settings["journal"])
        worker = BootstrapWorker(self.settings, self.ownership, self)
        self.controller = BootstrapController(self.journal, self.settings, worker, self)
        self.api = NativeAPI(
            None,
            token=token,
            host=self.raw["api"]["host"],
            port=self.raw["api"]["port"],
            provisioning=self.controller,
        )
        try:
            await self.api.start()
            await self.controller.resume_startup()
            await self.stop.wait()
        finally:
            await self.controller.close()
            await self.enter()
            await self.api.close()
            self.ownership.close()
            self.journal.close()


def main(argv=None):
    """Launch the explicitly selected app mode without changing standalone CLI behavior.

    @param argv Optional arguments for local packaging checks.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO)
    value = json.loads(Path(args.config).read_text())
    runtime = BootstrapRuntime(value["daemon"], value["bootstrap"])

    async def serve():
        """Register orderly shutdown and retain the API through setup transitions."""
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, runtime.stop.set)
        await runtime.run(os.environ["LIVOLO_API_TOKEN"])

    asyncio.run(serve())


if __name__ == "__main__":
    main()
