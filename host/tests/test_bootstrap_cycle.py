"""Full second-cycle preparation with real lifecycle/worker supervision and fake radio I/O."""

import asyncio
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from livolod.bootstrap_controller import BootstrapController
from livolod.bootstrap_runtime import BootstrapRuntime, SetupService
from livolod.bootstrap_store import BootstrapStore
from livolod.bootstrap_worker import BootstrapWorker, RadioOwnership, target_json
from livolod.preserved_gecko import GeckoTarget
from livolod.firmware_manager import radio_guard
from livolod.simulator import Simulator
from livolod.uart import LinkError

from test_bootstrap_controller import catalog


# Only the physical boundary is substituted. The child still executes production
# guard adoption, receipt generation and IPC; this fixture cannot upload firmware.
CHILD = """
import sys
from livolod import bootstrap_worker as worker
class Boundary:
    def __init__(self,target,lease,**kwargs): self.target,self.lease=target,lease
    def __enter__(self): self.lease.check(); return self
    def __exit__(self,*args): self.lease.close()
    def probe_loader(self,port,baud,timeout=3):
        assert port=="/dev/offline-only" and baud==115200
        return dict(kind="gecko", version="1.9.2", baud=115200, upload_supported=True,requires_signed=False,requires_encrypted=False)
    def resume_intact_application(self,version,mode,before_run):
        before_run()
        identity=dict(board="zb-gw04-1v2",fw_version=version,proto="3")
        return dict(version=identity,caps=dict(identity,baud="460800"),status=dict(armed="0",ack="0"))
worker.ZBGW04Provider=Boundary
raise SystemExit(worker.main(["--job",sys.argv[1]]))
"""


class Radio(Simulator):
    """Serial-free baseline 0.13 radio used by the actual normal service lifecycle."""

    async def command(self, text, retries=1):
        """Supply baseline identity while retaining simulator network behavior.

        @param text Command issued by the actual normal service.
        @param retries Simulator-compatible retry parameter.
        """
        identity = " board=zb-gw04-1v2 fw_version=0.13 proto=3 baud=460800"
        if text == "VERSION":
            self.commands.append(text)
            return "OK" + identity
        result = await super().command(text, retries)
        return result + identity if text == "CAPS" else result


class CycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_failed_uart_close_retains_guard_and_does_not_claim_quiescence(self):
        """An unverified close propagates through cancelled Service.run and fences setup."""
        with tempfile.TemporaryDirectory() as temporary:
            raw = json.loads(
                (Path(__file__).parents[1] / "config.example.json").read_text()
            )
            raw.update(
                database=temporary + "/state.db",
                serial_baudrate=460800,
                mqtt={"enabled": False},
                web={"enabled": False},
                output_mode="disabled",
            )

            class FailedClose(Radio):
                """Represent a UART whose descriptor closure cannot be verified."""

                async def close(self):
                    """Fail before physical closure, as a serial backend could do."""
                    raise OSError("fixture serial closure failed")

            runtime = BootstrapRuntime(
                raw, {}, gateway_factory=FailedClose, ready_timeout=3
            )
            network = {key: raw["network"][key] for key in ("gateway", "tag")}
            service = None
            try:
                await runtime.handoff(network)
                service = runtime.service
                with self.assertRaisesRegex(LinkError, "cleanup could not be verified"):
                    await runtime.enter()
                self.assertFalse(runtime.quiesced)
                self.assertIsNotNone(service.gateway)
                self.assertIsNotNone(service._unclosed_radio_guard)
                with self.assertRaises(BlockingIOError):
                    radio_guard(raw["database"])
            finally:
                # Explicit fixture-only recovery: no production path releases a
                # guard whose physical close failed, and no real UART exists here.
                if service and service.gateway:
                    await Radio.close(service.gateway)
                    service.gateway = None
                if service and service._unclosed_radio_guard is not None:
                    os.close(service._unclosed_radio_guard)
                    service._unclosed_radio_guard = None
                runtime.task = None
                await runtime.enter()

    async def test_real_worker_stages_artifact_after_cancel_and_second_entry(self):
        """Candidate staging, process IPC and plan persistence survive a real cancel roundtrip."""
        await self.run_cycle(False)

    async def test_second_entry_cancellation_drains_tasks_and_uart_before_releasing_guard(
        self,
    ):
        """The HA detach cancellation window cannot leak or prematurely release ownership."""
        await self.run_cycle(True)

    async def run_cycle(self, delayed):
        """Exercise real controller/runtime/worker cycles with optional deterministic teardown barriers.

        @param delayed Whether to interrupt shutdown while session tasks are still draining.
        """
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "leases").mkdir(mode=0o700)
            catalog(root / "catalog")
            raw = json.loads(
                (Path(__file__).parents[1] / "config.example.json").read_text()
            )
            raw.update(
                database=str(root / "state.db"),
                serial_port="/dev/offline-only",
                serial_baudrate=460800,
                mqtt={"enabled": False},
                web={"enabled": False},
                output_mode="disabled",
            )
            target = GeckoTarget(
                "/dev/offline-only",
                "devices/offline/usb1/1-2",
                "offline",
                (188, 0),
                "00",
                "1.9.2",
            )
            policy = dict(
                target=target_json(target),
                loader=dict(
                    attachment="offline",
                    evidence_id="fixture",
                    version="1.9.2",
                    requires_signed=False,
                    requires_encrypted=False,
                ),
            )
            settings = dict(
                serial_port=target.port,
                catalog=str(root / "catalog"),
                network={key: raw["network"][key] for key in ("gateway", "tag")},
                mode="gateway",
                database=raw["database"],
                lease_directory=str(root / "leases"),
                loader_policy=policy,
            )
            blocked = False
            draining = asyncio.Event()
            release_tasks = asyncio.Event()
            closing = asyncio.Event()
            release_uart = asyncio.Event()

            class DelayedService(SetupService):
                """Keep a cancelled session task alive until the test grants cleanup."""

                async def events(self):
                    """Model bounded asynchronous teardown of an active radio consumer."""
                    try:
                        await super().events()
                    finally:
                        if blocked:
                            draining.set()
                            await release_tasks.wait()

            class DelayedRadio(Radio):
                """Keep serial teardown distinct from session task drainage."""

                async def close(self):
                    """Hold the fake UART open until explicitly permitted to close."""
                    if blocked:
                        closing.set()
                        await release_uart.wait()
                    await super().close()

            async def detach(service):
                """Let shutdown reach its drainage barrier before HA detach completes.

                @param service Unused detached service, as in the actual API await boundary.
                """
                await draining.wait()

            runtime = BootstrapRuntime(
                raw,
                settings,
                gateway_factory=DelayedRadio,
                service_factory=DelayedService,
                ready_timeout=3,
            )
            runtime.ownership = RadioOwnership(settings, inventory=lambda: (target,))
            runtime.ownership.acquire()
            journal = BootstrapStore(root / "bootstrap.db")
            runtime.journal = journal
            with patch("livolod.bootstrap_worker.backend_status", return_value=None):
                worker = BootstrapWorker(
                    settings, runtime.ownership, runtime, timeout=5
                )
            controller = BootstrapController(journal, settings, worker, runtime)
            runtime.controller = controller
            original_spawn = asyncio.create_subprocess_exec
            children = []
            staged = []

            async def spawn(*args, **kwargs):
                """Keep real production spawning options while replacing only physical I/O.

                @param args Fixed production worker command including private job path.
                @param kwargs Production pipes, process group and inherited descriptors.
                """
                self.assertEqual(args[1:3], ("-m", "livolod.bootstrap_worker"))
                spec = json.loads(Path(args[-1]).read_text())
                self.assertIn(spec["kind"], ("prepare", "cancel"))
                if spec["kind"] == "prepare":
                    payload = Path(spec["candidate"]["path"]).read_bytes()
                    self.assertEqual(
                        hashlib.sha256(payload).hexdigest(), spec["candidate"]["sha256"]
                    )
                    self.assertEqual(payload, controller.candidate["_data"])
                    staged.append(Path(spec["candidate"]["path"]))
                child = await original_spawn(
                    sys.executable, "-c", CHILD, args[-1], **kwargs
                )
                children.append(child)
                return child

            async def submit(kind, ident, **fields):
                """Drain one explicit operation without retries or bypassing admission.

                @param kind Explicit setup action.
                @param ident Unique request identifier.
                @param fields Required operation-specific consent and selection fields.
                """
                await controller.submit(
                    kind,
                    dict(request_id=ident, instance_id=journal.instance_id, **fields),
                )
                await asyncio.gather(*tuple(controller.tasks))
                return controller.result(ident)

            try:
                await runtime.handoff(settings["network"])
                before = runtime.store.db.execute(
                    "SELECT id,value FROM devices ORDER BY id"
                ).fetchall()
                with patch(
                    "livolod.bootstrap_worker.asyncio.create_subprocess_exec",
                    side_effect=spawn,
                ):
                    self.assertEqual(
                        (await submit("enter", "enter-one", confirmed=True))["status"],
                        "confirmed",
                    )
                    self.assertEqual(
                        (await submit("cancel", "cancel-one", confirmed=True))["stage"],
                        "network_ready",
                    )
                    self.assertEqual(
                        runtime.store.db.execute(
                            "SELECT id,value FROM devices ORDER BY id"
                        ).fetchall(),
                        before,
                    )
                    blocked = delayed
                    if delayed:
                        runtime.api = SimpleNamespace(service=None, attach=detach)
                        entering = asyncio.create_task(
                            submit("enter", "enter-two", confirmed=True)
                        )
                        await asyncio.wait_for(draining.wait(), 1)
                        # Repeated parent cancellation must not kill the separate
                        # cleanup owner or make setup appear quiesced early.
                        await asyncio.sleep(0)
                        runtime.task.cancel()
                        await asyncio.sleep(0)
                        self.assertFalse(entering.done())
                        with self.assertRaises(BlockingIOError):
                            radio_guard(settings["database"])
                        release_tasks.set()
                        await asyncio.wait_for(closing.wait(), 1)
                        self.assertFalse(entering.done())
                        with self.assertRaises(BlockingIOError):
                            radio_guard(settings["database"])
                        release_uart.set()
                        entered = await asyncio.wait_for(entering, 2)
                    else:
                        entered = await submit("enter", "enter-two", confirmed=True)
                    self.assertEqual(entered["status"], "confirmed")
                    guard = radio_guard(settings["database"])
                    os.close(guard)
                    prepared = await submit(
                        "prepare",
                        "prepare-two",
                        target_id=controller.target_id,
                        candidate_id=controller.candidate["id"],
                        dedicated=True,
                        board_confirmed=True,
                    )
                self.assertEqual(prepared["stage"], "prepared", prepared)
                self.assertEqual(prepared["status"], "confirmed")
                self.assertIsNotNone(journal.plan(prepared["plan_id"]))
                self.assertEqual(len(staged), 1)
                self.assertFalse(staged[0].exists())
                self.assertTrue(all(child.returncode == 0 for child in children))
                self.assertFalse(journal.meta("installation_attempted", False))
                self.assertIsNone(worker.child)
            finally:
                release_tasks.set()
                release_uart.set()
                runtime.api = None
                await controller.close()
                await runtime.enter()
                runtime.ownership.close()
                journal.close()
