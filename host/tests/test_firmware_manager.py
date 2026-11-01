"""Exercise durable update ownership, IPC, interruption and safe reconciliation."""

import asyncio
from dataclasses import replace
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from livolod.config import load, parse
from livolod.firmware_manager import FirmwareManager, radio_guard
from livolod.service import Service
from livolod.storage import Store
from livolod.uart import LinkError


class FirmwareManagerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        """Prepare a ready service with a selected synthetic, never-flashed candidate."""
        self.temp = tempfile.TemporaryDirectory()
        self.config = replace(
            load(Path(__file__).parents[1] / "config.example.json"),
            database=self.temp.name + "/state.db",
            serial_baudrate=460800,
        )
        self.store = Store(self.config.database, "network")
        self.service = Service(self.config, self.store)
        self.manager = self.service.firmware_manager
        self.service.ready = True
        self.manager.board = "codm-cc2652p"
        self.manager.installed = "0.11"
        self.manager.reason = None
        self.manager.candidate = dict(
            id="a" * 96 + ":" + "b" * 64,
            board="codm-cc2652p",
            version="0.12",
            backend="ti_uart_signed_v1",
            image_sha512="c" * 128,
            rollback_supported=True,
            public_key="e" * 64,
        )
        self.payload = dict(
            request_id="update-1", candidate_id=self.manager.candidate["id"]
        )

    async def asyncTearDown(self):
        """Release the test database after all child processes have been reaped."""
        self.store.close()
        self.temp.cleanup()

    def start_job(self):
        """Accept one synthetic update and return its durable pending result."""
        result = self.manager.start(self.payload)
        self.assertEqual(result["status"], "pending")
        return result

    def test_durable_dedupe_and_161_character_candidate(self):
        """Long hash-bound candidates deduplicate without replay or identity changes."""
        first = self.start_job()
        self.assertEqual(len(self.payload["candidate_id"]), 161)
        self.assertEqual(first, self.manager.start(self.payload))
        self.assertFalse(self.service.ready)
        changed = self.manager.start(dict(self.payload, candidate_id="different"))
        self.assertEqual(changed["status"], "rejected")
        self.assertEqual(self.store.command_result("update-1"), first)
        self.assertEqual(self.store.firmware_job()["stage"], "preparing")

    def test_missing_bundle_and_invalid_identity_do_not_start(self):
        """Missing artifacts and malformed input never request radio ownership."""
        self.manager.candidate = None
        self.manager.reason = "bundle_missing"
        self.assertEqual(self.manager.start(self.payload)["error"], "bundle_missing")
        self.assertFalse(self.manager.requested.is_set())
        self.assertTrue(self.service.ready)
        for payload in (
            {},
            dict(self.payload, path="/tmp/image"),
            dict(self.payload, candidate_id="../image"),
            dict(self.payload, request_id="x" * 129),
        ):
            self.assertEqual(self.manager.start(payload)["status"], "rejected")

    def test_malformed_installed_version_keeps_snapshot_readable(self):
        """Untrusted version text cannot crash a status response."""
        self.manager.installed = "broken"
        self.assertFalse(self.manager.snapshot()["update_available"])

    def test_inflight_queue_pairing_and_lock_block_start(self):
        """Every active radio-work form blocks maintenance without cancelling work."""
        self.service.command_inflight = "set-1"
        self.assertEqual(self.manager.start(self.payload)["error"], "radio_busy")
        self.service.command_inflight = None
        self.service.pairing = {"device": "46715621"}
        self.assertEqual(
            self.manager.start(dict(self.payload, request_id="p"))["error"],
            "radio_busy",
        )
        self.service.pairing = None
        self.service.commands.put_nowait((0, None, "other", "refresh", {}))
        self.assertEqual(
            self.manager.start(dict(self.payload, request_id="q"))["error"],
            "radio_busy",
        )
        self.service.commands.get_nowait()

    async def test_maintenance_blocks_registry_and_mqtt_changes(self):
        """Pending and recovery states reject conflicting mutations on the server."""
        self.start_job()
        self.assertEqual(
            self.service.submit(
                "rename", dict(request_id="rename", device="46715621", name="new")
            )["status"],
            "rejected",
        )
        self.manager.failed("interrupted")
        self.assertTrue(self.manager.busy)
        self.assertFalse(self.manager.snapshot()["in_progress"])
        self.assertEqual(
            self.service.submit("refresh", dict(request_id="r", device="46715621"))[
                "status"
            ],
            "rejected",
        )
        with self.assertRaises(ValueError):
            await self.service.configure_mqtt({})

    def test_restart_retains_expected_digest_and_never_requests_replay(self):
        """A restarted job is unknown but preserves identity for safe reconciliation."""
        self.start_job()
        restored = FirmwareManager(self.service)
        self.assertFalse(restored.requested.is_set())
        self.assertTrue(restored.busy)
        self.assertEqual(restored.job["image_sha512"], "c" * 128)
        result = self.store.command_result("update-1")
        self.assertEqual(result["status"], "unknown")
        self.assertEqual(result["recovery"], "recovery_required")

    def test_trial_digest_mismatch_is_never_confirmed(self):
        """Same-version TI images still require the selected digest before confirmation."""
        self.start_job()
        with self.assertRaises(LinkError):
            self.manager.before_confirm(
                dict(board="codm-cc2652p", fw_version="0.12"),
                dict(trial="1", current_sha512="d" * 128),
            )
        self.assertEqual(self.manager.job["status"], "unknown")
        self.assertTrue(self.manager.busy)

    def test_exact_restored_candidate_confirms_and_clears_maintenance(self):
        """Success requires target board, version, baud and durable TI digest."""
        self.start_job()
        self.manager.requested.clear()
        self.manager.restored(
            dict(board="codm-cc2652p", fw_version="0.12", baud="460800"),
            dict(trial="0", current_sha512="c" * 128),
        )
        self.assertEqual(self.manager.job["status"], "confirmed")
        self.assertFalse(self.manager.busy)
        self.assertEqual(self.store.command_result("update-1")["stage"], "confirmed")

    def test_verified_ti_rollback_restores_controls(self):
        """A stable prior image digest clears recovery without claiming update success."""
        self.start_job()
        self.manager.job["original_sha512"] = "d" * 128
        self.manager.job["last_critical"] = "activate"
        self.manager.failed("interrupted")
        self.manager.requested.clear()
        self.manager.restored(
            dict(board="codm-cc2652p", fw_version="0.11", baud="460800"),
            dict(trial="0", state="ffffffc8", current_sha512="d" * 128),
        )
        self.assertFalse(self.manager.busy)
        self.assertEqual(
            self.store.command_result("update-1")["stage"], "restored_previous"
        )
        self.assertEqual(self.manager.job["status"], "rejected")

    def test_partial_mg21_flash_does_not_clear_recovery_on_previous_version(self):
        """An old version response after possible flash is insufficient to resume controls."""
        self.start_job()
        self.manager.job.update(
            backend="mg21_gecko_gbl_v1", board="zb-gw04-1v2", last_critical="flash"
        )
        self.manager.failed("interrupted")
        self.manager.requested.clear()
        self.manager.restored(
            dict(board="zb-gw04-1v2", fw_version="0.11", baud="460800")
        )
        self.assertTrue(self.manager.busy)

    async def test_real_channel_transcript_acknowledges_persisted_stages(self):
        """Actual worker IPC covers progress, commit, activation and final transfer."""
        self.start_job()
        code = """from livolod.firmware_worker import Channel
c=Channel('update-1')
c.before('disarm')
c.before('begin')
c.progress({'stage':'transfer','percent':50})
c.before('commit')
c.progress({'stage':'committed'})
c.before('activate')
c.emit('transferred')
"""
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-c",
            code,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
        )
        try:
            self.assertTrue(await asyncio.wait_for(self.manager.events(process), 10))
            self.assertEqual(self.store.firmware_job()["last_critical"], "activate")
            self.assertEqual(self.store.firmware_job()["transfer_percent"], 50)
            self.assertEqual(self.manager.job["status"], "pending")
        finally:
            await self.manager.reap(process)

    async def test_real_channel_failure_is_not_transfer_success(self):
        """A real failed worker event leaves a non-successful durable stage."""
        self.start_job()
        code = "from livolod.firmware_worker import Channel; Channel('update-1').emit('failed',code='flash_failed',error='failed',recovery=True)"
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-c",
            code,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            start_new_session=True,
        )
        try:
            with self.assertRaises(RuntimeError):
                await self.manager.events(process)
            self.assertEqual(self.store.firmware_job()["stage"], "failed")
        finally:
            await self.manager.reap(process)

    async def test_descendant_guard_prevents_reopen_after_parent_closes(self):
        """An inherited worker lease survives parent closure and blocks a fresh daemon."""
        guard = radio_guard(self.config.database)
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-c",
            "import sys; sys.stdin.buffer.read()",
            pass_fds=(guard,),
            stdin=asyncio.subprocess.PIPE,
            start_new_session=True,
        )
        os.close(guard)
        try:
            with self.assertRaises(BlockingIOError):
                radio_guard(self.config.database)
            factory = AsyncMock()
            self.service.gateway_factory = factory
            task = asyncio.create_task(self.service.run())
            await asyncio.sleep(0.03)
            self.service.stop.set()
            await asyncio.wait_for(task, 2)
            factory.assert_not_called()
        finally:
            await self.manager.reap(process)
        guard = radio_guard(self.config.database)
        os.close(guard)

    async def test_timeout_reaps_worker_and_leaves_recovery_intent(self):
        """A bounded worker timeout releases ownership only after process-group cleanup."""
        self.start_job()
        guard = radio_guard(self.config.database)
        original = asyncio.create_subprocess_exec
        spawned = []

        async def launch(*args, **kwargs):
            """Replace only the worker executable with an isolated hung child.

            @param args Manager's fixed worker argument vector.
            @param kwargs Real pipes, process group and inherited lifetime guard.
            """
            process = await original(
                sys.executable,
                "-c",
                "from livolod.firmware_worker import Channel; import time; Channel('update-1').before('flash'); time.sleep(60)",
                **kwargs,
            )
            spawned.append(process)
            return process

        with (
            patch("livolod.firmware_manager.asyncio.create_subprocess_exec", launch),
            patch("livolod.firmware_manager.WORKER_TIMEOUT", 0.2),
        ):
            await self.manager.execute(guard)
        self.assertIsNotNone(spawned[0].returncode)
        self.assertEqual(self.manager.job["status"], "unknown")
        self.assertFalse(self.manager.requested.is_set())
        guard = radio_guard(self.config.database)
        os.close(guard)

    async def test_shutdown_before_worker_spawn_is_non_mutating(self):
        """Stopping before execution releases the guard without starting a child."""
        self.start_job()
        self.service.stop.set()
        with patch(
            "livolod.firmware_manager.asyncio.create_subprocess_exec",
            new_callable=AsyncMock,
        ) as launch:
            await self.manager.execute(radio_guard(self.config.database))
            launch.assert_not_called()
        self.assertEqual(self.manager.job["status"], "rejected")
        self.assertFalse(self.manager.busy)

    async def test_full_service_handoff_keeps_snapshots_live_and_restores(self):
        """The normal run loop closes its old radio before a worker and restores after it."""
        from livolod.simulator import Simulator

        trace = []
        radios = []
        entered = asyncio.Event()
        release = asyncio.Event()
        current = {"version": "0.11", "digest": "d" * 128}

        class Radio(Simulator):
            async def command(self, text, retries=2):
                """Record normal service commands and emulate TI update identity.

                @param text UART operation issued by the real service lifecycle.
                @param retries Transport-compatible retry argument.
                """
                trace.append(text)
                if text == "VERSION":
                    return f"OK board=codm-cc2652p fw_version={current['version']} proto=3 update=uart_signed_v1"
                if text == "UPDATE_STATUS":
                    return f"OK trial=0 state=fffffff0 key={'e'*64} current_sha512={current['digest']}"
                value = await super().command(text, retries)
                if text == "CAPS":
                    value += f" board=codm-cc2652p fw_version={current['version']} baud=460800 update=uart_signed_v1"
                return value

            async def close(self):
                """Record serial release before allowing the synthetic worker to start."""
                await super().close()
                trace.append("close")

        def factory():
            """Create a new radio only after the lifetime guard can be reacquired."""
            trace.append("open")
            radio = Radio()
            radios.append(radio)
            return radio

        async def execute(guard):
            """Stand in for a bounded flash process while exercising real handoff wiring.

            @param guard Held radio lease passed by the service after serial close.
            """
            self.assertEqual(trace[-1], "close")
            self.assertFalse(self.service.command_inflight)
            with self.assertRaises(BlockingIOError):
                radio_guard(self.config.database)
            trace.append("worker")
            entered.set()
            try:
                await release.wait()
                current.update(version="0.12", digest="c" * 128)
                self.manager.job["stage"] = "restoring"
                self.manager.persist()
            finally:
                os.close(guard)
                self.manager.requested.clear()

        self.service.gateway_factory = factory
        candidate = dict(self.manager.candidate)
        with (
            patch("livolod.firmware_bundle.select_candidate", return_value=candidate),
            patch("livolod.firmware_backends.backend_status", return_value=None),
            patch.object(self.manager, "execute", execute),
        ):
            self.service.ready = False
            task = asyncio.create_task(self.service.run())
            try:
                for _ in range(100):
                    if self.service.ready:
                        break
                    await asyncio.sleep(0.01)
                self.start_job()
                await asyncio.wait_for(entered.wait(), 2)
                snapshot = self.service.api_snapshot()
                self.assertFalse(snapshot["ready"])
                self.assertTrue(snapshot["firmware_update"]["in_progress"])
                self.assertEqual(len(radios), 1)
                self.assertEqual(
                    trace[trace.index("worker") - 4 : trace.index("worker")],
                    ["DISARM", "STATUS", "DISARM", "close"],
                )
                release.set()
                for _ in range(100):
                    if self.store.command_result("update-1")["status"] != "pending":
                        break
                    await asyncio.sleep(0.01)
                self.assertEqual(
                    self.store.command_result("update-1")["status"], "confirmed"
                )
                self.assertTrue(self.service.ready)
                self.assertEqual(len(radios), 2)
            finally:
                release.set()
                self.service.stop.set()
                await asyncio.wait_for(task, 2)

    def test_restart_before_first_mutation_restores_previous_without_digest(self):
        """A never-acknowledged job cannot strand a verified unchanged radio in maintenance."""
        self.start_job()
        restored = FirmwareManager(self.service)
        restored.restored(
            dict(board="codm-cc2652p", fw_version="0.11", baud="460800"),
            dict(trial="0", state="fffffff0"),
        )
        self.assertFalse(restored.busy)
        self.assertEqual(restored.job["stage"], "restored_previous")

    async def test_cancellation_reaps_worker_before_returning(self):
        """Cancellation cannot leave a worker owning UART after the manager returns."""
        self.start_job()
        original = asyncio.create_subprocess_exec
        spawned = []

        async def launch(*args, **kwargs):
            """Start a blocked IPC worker with the real inherited lease.

            @param args Manager worker executable arguments replaced for this test.
            @param kwargs Real subprocess pipes, group and inherited descriptor options.
            """
            process = await original(
                sys.executable,
                "-c",
                "from livolod.firmware_worker import Channel; import time; Channel('update-1').before('flash'); time.sleep(60)",
                **kwargs,
            )
            spawned.append(process)
            return process

        with patch("livolod.firmware_manager.asyncio.create_subprocess_exec", launch):
            task = asyncio.create_task(
                self.manager.execute(radio_guard(self.config.database))
            )
            for _ in range(100):
                if self.manager.job.get("last_critical") == "flash":
                    break
                await asyncio.sleep(0.01)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertIsNotNone(spawned[0].returncode)
        self.assertTrue(self.manager.busy)
        guard = radio_guard(self.config.database)
        os.close(guard)
