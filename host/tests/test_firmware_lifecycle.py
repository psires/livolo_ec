"""Exercise local-artifact admission and durable manual recovery without hardware."""

import asyncio
from contextlib import redirect_stdout
from dataclasses import replace
import fcntl
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, Mock, patch

from livolod import __version__
from livolod.config import load
from livolod.firmware_artifacts import inspect_artifact
from livolod.firmware_cli import manual_flash
from livolod.firmware_manager import radio_guard
from livolod.firmware_worker import main as worker_main
from livolod.service import Service
from livolod.storage import Store
from livolod.uart import LinkError
from test_firmware_artifacts import nrf_zip
from test_firmware_bundle import gbl, mg21_image


class LocalLifecycleTests(unittest.IsolatedAsyncioTestCase):
    """Real IPC must journal mutation before acknowledging a fake local worker."""

    async def asyncSetUp(self):
        """Prepare isolated state and a nonexecutable Nordic fixture."""
        self.temp = tempfile.TemporaryDirectory()
        self.config = replace(
            load(Path(__file__).parents[1] / "config.example.json"),
            database=self.temp.name + "/state.db",
            serial_baudrate=460800,
        )
        path = Path(self.temp.name) / "application.zip"
        path.write_bytes(nrf_zip())
        self.candidate = inspect_artifact(
            str(path), board="nordic-pca10059", version="0.12", operation="recovery"
        )
        self.real_spawn = asyncio.create_subprocess_exec

    async def asyncTearDown(self):
        """Remove synthetic state after child processes and locks are released."""
        self.temp.cleanup()

    def worker(self, fail=False):
        """Return a spawning callback that executes real IPC without any radio module.

        @param fail Emit a failed outcome after the critical flash boundary.
        @return Async subprocess factory accepting the manager's real pipe/guard options.
        """

        async def spawn(*args, **kwargs):
            """Substitute a synthetic worker but preserve job identity and process ownership.

            @param args Original fixed worker command, including the current job ID.
            @param kwargs Original subprocess pipe, process group and inherited guard settings.
            @return Real child process running only the Channel protocol.
            """
            ident = args[args.index("--job-id") + 1]
            code = (
                "from livolod.firmware_worker import Channel\nc=Channel("
                + repr(ident)
                + ')\nc.before("flash")\n'
            )
            code += (
                'c.emit("failed",code="flash_failed",error="Synthetic transfer failure",recovery=True)\nraise SystemExit(1)\n'
                if fail
                else 'c.emit("transferred")\n'
            )
            return await self.real_spawn(sys.executable, "-c", code, **kwargs)

        return spawn

    async def test_manual_transfer_requires_explicit_identity_and_normal_restoration(
        self,
    ):
        """A completed upload remains blocked across restart until explicitly verified."""
        with (
            patch("asyncio.create_subprocess_exec", side_effect=self.worker()),
            patch.object(Service, "run", new_callable=AsyncMock) as run,
        ):
            result = await manual_flash(
                self.config, self.candidate, ["--synthetic-worker"]
            )
        run.assert_not_called()
        self.assertEqual(result["status"], "unknown")
        self.assertEqual(result["recovery"], "manual_verification_required")
        self.assertTrue(result["transfer_completed"])
        store = Store(self.config.database, self.config.gateway + ":" + self.config.tag)
        try:
            factory = Mock(
                side_effect=AssertionError(
                    "No radio opening before explicit verification"
                )
            )
            service = Service(self.config, store, gateway_factory=factory)
            manager = service.firmware_manager
            self.assertEqual(manager.job["last_critical"], "flash")
            task = asyncio.create_task(service.run())
            await asyncio.sleep(0.02)
            service.stop.set()
            await asyncio.wait_for(task, 2)
            factory.assert_not_called()
            caps = dict(
                board="nordic-pca10059", fw_version="0.12", baud="460800", proto="3"
            )
            manager.version_info = dict(caps)
            with patch(
                "livolod.firmware_manual.application_target_status", return_value=True
            ):
                manager.restored(caps)
                self.assertEqual(manager.job["status"], "unknown")
                manager.manual_restore_serial = "operator-selected-app-serial"
                with self.assertRaises(LinkError):
                    manager.check_manual_restore(dict(caps, fw_version="0.11"), caps)
                manager.check_manual_restore(caps, caps)
                manager.restored(caps)
            self.assertEqual(manager.job["status"], "confirmed")
            self.assertFalse(manager.busy)
        finally:
            store.close()

    async def test_failed_manual_transfer_cannot_clear_on_same_version_or_usb_identity(
        self,
    ):
        """Partial transfers never gain successful-transfer evidence from firmware banners."""
        with patch(
            "asyncio.create_subprocess_exec", side_effect=self.worker(fail=True)
        ):
            result = await manual_flash(
                self.config, self.candidate, ["--synthetic-worker"]
            )
        self.assertFalse(result["transfer_completed"])
        store = Store(self.config.database, self.config.gateway + ":" + self.config.tag)
        try:
            manager = Service(self.config, store).firmware_manager
            manager.manual_restore_serial = "operator-selected-app-serial"
            with patch(
                "livolod.firmware_manual.application_target_status", return_value=True
            ):
                with self.assertRaises(LinkError):
                    manager.check_manual_restore()
            self.assertTrue(manager.busy)
        finally:
            store.close()

    async def test_local_update_uses_durable_worker_then_normal_confirmation(self):
        """A local MG21 update reuses job IPC and cannot leak arguments into later jobs."""
        path = Path(self.temp.name) / "application.gbl"
        path.write_bytes(gbl(mg21_image(version=__version__)))
        candidate = inspect_artifact(
            str(path), board="sonoff-zbdongle-e", version=__version__
        )
        store = Store(self.config.database, self.config.gateway + ":" + self.config.tag)
        try:
            service = Service(self.config, store)
            manager = service.firmware_manager
            service.ready = True
            manager.board = candidate["board"]
            manager.installed = "0.11"
            manager.caps = dict(
                board=candidate["board"], fw_version="0.11", proto="3", baud="460800"
            )
            manager.version_info = dict(manager.caps)
            payload = dict(request_id="local-test", candidate_id=candidate["id"])
            with patch("livolod.firmware_preflight.backend_status", return_value=None):
                result = manager.start_local(payload, candidate, ["--local-fixture"])
            self.assertEqual(result["status"], "pending")
            self.assertEqual(manager.job["operation"], "local_update")
            with patch("asyncio.create_subprocess_exec", side_effect=self.worker()):
                await manager.execute(radio_guard(self.config.database))
            self.assertEqual(manager.job["stage"], "restoring")
            self.assertEqual(manager.job["status"], "pending")
            self.assertIsNone(manager.worker_arguments)
            manager.restored(
                dict(board=candidate["board"], fw_version=__version__, baud="460800")
            )
            self.assertEqual(manager.job["status"], "confirmed")
        finally:
            store.close()

    async def test_manual_retry_preserves_interrupted_automatic_nordic_ledger(self):
        """Only explicit retry can supersede an uncertain same-board Nordic job."""
        store = Store(self.config.database, self.config.gateway + ":" + self.config.tag)
        store.begin(
            "previous-auto",
            dict(action="firmware_update", candidate_id="previous:hash"),
        )
        store.save_firmware_job(
            dict(
                request_id="previous-auto",
                candidate_id="previous:hash",
                backend="nrf52840_usb_dfu_v1",
                board="nordic-pca10059",
                status="unknown",
                recovery="recovery_required",
                stage="flash",
            )
        )
        store.close()
        with self.assertRaises(ValueError):
            await manual_flash(self.config, self.candidate, ["--synthetic-worker"])
        with patch("asyncio.create_subprocess_exec", side_effect=self.worker()):
            result = await manual_flash(
                self.config, self.candidate, ["--synthetic-worker"], retry=True
            )
        self.assertEqual(result["recovery"], "manual_verification_required")
        store = Store(self.config.database, self.config.gateway + ":" + self.config.tag)
        try:
            self.assertEqual(store.command_result("previous-auto")["status"], "unknown")
            self.assertEqual(
                store.firmware_job()["previous_request_id"], "previous-auto"
            )
        finally:
            store.close()


class LocalWorkerTests(unittest.TestCase):
    """Worker entry must revalidate a local path against the parent's selected digest."""

    def test_changed_local_artifact_is_rejected_before_backend_execution(self):
        """Replacing an inspected file cannot transfer newly selected bytes implicitly."""
        with (
            tempfile.TemporaryDirectory() as directory,
            tempfile.TemporaryFile() as guard,
        ):
            fcntl.flock(guard, fcntl.LOCK_EX | fcntl.LOCK_NB)
            path = Path(directory) / "app.gbl"
            path.write_bytes(gbl(mg21_image()))
            candidate = inspect_artifact(
                str(path), board="sonoff-zbdongle-e", version="0.12"
            )
            image = bytearray(mg21_image())
            image[-1] = 1
            path.write_bytes(gbl(bytes(image)))
            output = io.StringIO()
            with (
                redirect_stdout(output),
                patch("livolod.firmware_worker.install") as install,
            ):
                code = worker_main(
                    [
                        "--artifact",
                        str(path),
                        "--board",
                        "sonoff-zbdongle-e",
                        "--version",
                        "0.12",
                        "--candidate-id",
                        candidate["id"],
                        "--port",
                        "/dev/nonexistent",
                        "--baud",
                        "460800",
                        "--job-id",
                        "hash-test",
                        "--lock-fd",
                        str(guard.fileno()),
                    ]
                )
            self.assertEqual(code, 1)
            install.assert_not_called()
            self.assertEqual(json.loads(output.getvalue())["code"], "artifact_invalid")


if __name__ == "__main__":
    unittest.main()
