"""Durable physical controller tests; all physical dependencies are non-I/O fixtures."""

import asyncio
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import AsyncMock, Mock

from livolod import FIRMWARE_COMPAT_VERSION
from livolod.bootstrap_controller import BootstrapController, BootstrapError
from livolod.bootstrap_store import BootstrapStore
from test_firmware_bundle import gbl, mg21_image


def catalog(root):
    """Create a hash-valid synthetic catalog that must never be sent to a radio.

    @param root Private fixture directory.
    """
    root.mkdir(exist_ok=True)
    data = gbl(mg21_image("zb-gw04-1v2", FIRMWARE_COMPAT_VERSION))
    entry = dict(
        id="fixture",
        board="zb-gw04-1v2",
        role="gateway",
        version=FIRMWARE_COMPAT_VERSION,
        protocol=3,
        backend="mg21_gecko_gbl_v1",
        path="image.gbl",
        app_baudrate=460800,
        layout="mg21-app-4000-30000",
        qualified=True,
        qualification="qualification.md",
        source_commit="a" * 40,
        notices=["NOTICE"],
        bootloader_baudrate=115200,
        size=len(data),
        sha256=hashlib.sha256(data).hexdigest(),
    )
    (root / "image.gbl").write_bytes(data)
    (root / "qualification.md").write_text("OFFLINE SYNTHETIC FIXTURE ONLY")
    (root / "NOTICE").write_text("Fixture only")
    (root / "manifest.json").write_text(
        json.dumps(
            dict(schema=1, host_version=FIRMWARE_COMPAT_VERSION, artifacts=[entry])
        )
    )


class ControllerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        """Create an independent physical journal with injected nonphysical work."""
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        catalog(self.root / "catalog")
        self.settings = dict(
            serial_port="/dev/test-only",
            catalog=str(self.root / "catalog"),
            mode="bootstrap",
            network=dict(gateway="10203040", tag="42"),
        )
        self.store = BootstrapStore(self.root / "bootstrap.db")
        self.worker = Mock()
        self.worker.blocked_reason = None
        self.worker.run = AsyncMock(side_effect=self.work)
        self.runtime = Mock()
        self.runtime.admission_reason.return_value = None
        self.runtime.has_gateway_state.return_value = False
        self.runtime.enter = AsyncMock(
            return_value=dict(target={"fixture": True}, version="0.13")
        )
        self.runtime.handoff = AsyncMock()
        self.runtime.start_gateway = AsyncMock()
        self.controller = BootstrapController(
            self.store, self.settings, self.worker, self.runtime
        )
        self.calls = []

    async def asyncTearDown(self):
        """Drain operations before releasing independent journal ownership."""
        await self.controller.close()
        self.store.close()
        self.temp.cleanup()

    async def work(self, kind, job, candidate, binding, checkpoint):
        """Emulate physical observations while asserting durable intent precedes work.

        @param kind Allowlisted operation.
        @param job Durable job.
        @param candidate Validated synthetic candidate.
        @param binding Private inspected fixture binding.
        @param checkpoint Durable parent phase callback.
        """
        self.assertEqual(self.store.lookup(job["request_id"])[1]["status"], "pending")
        self.calls.append(kind)
        if kind == "prepare":
            return dict(target={"fixture": True}, loader={"fixture": True})
        if kind == "cancel":
            await checkpoint("run_intact")
            facts = dict(board="zb-gw04-1v2", fw_version="0.13", proto="3")
            return dict(
                version=facts,
                caps=dict(facts, baud="460800"),
                status=dict(armed="0", ack="0"),
            )
        await checkpoint("flash")
        self.assertTrue(self.store.meta("installation_attempted"))
        return dict(
            application_verified=True,
            board="zb-gw04-1v2",
            version=candidate["version"],
            artifact_sha256=candidate["sha256"],
            network_ready=False,
        )

    async def submit(self, kind, request_id, **fields):
        """Admit a fixture intent and drain its scheduled operation.

        @param kind Fixed operation name.
        @param request_id Unique or intentionally repeated request identity.
        @param fields Operation-specific arguments.
        """
        payload = dict(
            request_id=request_id, instance_id=self.store.instance_id, **fields
        )
        result = await self.controller.submit(kind, payload)
        await asyncio.gather(*tuple(self.controller.tasks))
        return self.controller.result(request_id), payload, result

    async def prepare(self, request_id="prepare"):
        """Explicitly prepare the synthetic artifact through actual catalog validation.

        @param request_id Optional unique request identity.
        """
        value, _, _ = await self.submit(
            "prepare",
            request_id,
            target_id=self.controller.target_id,
            candidate_id=self.controller.candidate["id"],
            dedicated=True,
            board_confirmed=True,
        )
        return value

    async def test_idempotent_install_and_handoff_are_separate(self):
        """Lost replies reconcile without replay; firmware success is not network readiness."""
        prepared = await self.prepare()
        result, payload, initial = await self.submit(
            "install", "install", plan_id=prepared["plan_id"], confirmed=True
        )
        self.assertEqual(initial["status"], "pending")
        self.assertTrue(result["application_verified"])
        self.assertFalse(result["network_ready"])
        self.assertEqual(await self.controller.submit("install", payload), result)
        self.assertEqual(self.calls, ["prepare", "install"])
        result, _, _ = await self.submit(
            "handoff",
            "resume",
            install_request_id="install",
            network=None,
            confirmed=True,
        )
        self.assertTrue(result["network_ready"])
        self.assertIsNone(self.store.meta("verified_target"))
        self.runtime.normal_confirmed.assert_called_once()
        self.runtime.handoff.assert_awaited_once_with(self.settings["network"])
        await self.submit("enter", "next-cycle", confirmed=True)
        self.assertFalse(self.store.meta("installation_attempted"))
        reject, _, _ = await self.submit(
            "handoff",
            "stale-install",
            install_request_id="install",
            network=None,
            confirmed=True,
        )
        self.assertEqual(reject["error"], "application_not_verified")

    async def test_expired_plan_has_durable_bound_rejection(self):
        """A stale review never becomes a missing operation after the reply is lost."""
        prepared = await self.prepare()
        self.controller._deadlines.clear()
        result, payload, _ = await self.submit(
            "install", "expired", plan_id=prepared["plan_id"], confirmed=True
        )
        self.assertEqual(result["status"], "rejected")
        self.assertEqual(result["error"], "plan_expired")
        self.assertEqual(result["candidate_id"], prepared["candidate_id"])
        self.assertEqual(result["plan_id"], prepared["plan_id"])
        self.assertEqual(await self.controller.submit("install", payload), result)
        self.assertEqual(self.calls, ["prepare"])

    async def test_restart_fences_pending_and_never_replays_worker(self):
        """A restart after possible flash retains recovery independently of gateway state."""
        prepared = await self.prepare()
        result, _, _ = await self.submit(
            "install", "install", plan_id=prepared["plan_id"], confirmed=True
        )
        result.update(status="pending", stage="flash")
        self.store.save(result)
        await self.controller.close()
        self.store.close()
        self.store = BootstrapStore(self.root / "bootstrap.db")
        self.controller = BootstrapController(
            self.store, self.settings, self.worker, self.runtime
        )
        await self.controller.resume_startup()
        self.assertEqual(self.controller.result("install")["status"], "unknown")
        self.assertEqual(self.controller.info()["blocked_reason"], "recovery_required")
        self.runtime.start_gateway.assert_not_awaited()
        self.assertEqual(self.calls, ["prepare", "install"])

    async def test_handoff_failure_cannot_resume_from_gateway_mode(self):
        """Committed normal-start permission does not override failed readiness recovery."""
        prepared = await self.prepare()
        await self.submit(
            "install", "install", plan_id=prepared["plan_id"], confirmed=True
        )
        self.runtime.handoff.side_effect = BootstrapError("normal_service_not_ready")
        failed, _, _ = await self.submit(
            "handoff",
            "resume",
            install_request_id="install",
            network=None,
            confirmed=True,
        )
        self.assertEqual(failed["status"], "unknown")
        self.assertEqual(self.store.meta("mode"), "bootstrap")
        await self.controller.resume_startup()
        self.runtime.start_gateway.assert_not_awaited()

    async def test_cancel_inspects_intact_application_without_catalog_or_install(self):
        """Cancellation restores only the captured intact app through explicit inspection."""
        self.controller.mode = "gateway"
        await self.submit("enter", "enter", confirmed=True)
        self.controller.candidate = None
        self.controller.blocked_reason = "bundle_missing"
        result, _, _ = await self.submit("cancel", "cancel", confirmed=True)
        self.assertTrue(result["network_ready"])
        self.assertEqual(self.calls, ["cancel"])
        self.assertFalse(self.store.meta("installation_attempted"))

    async def test_old_baseline_uses_actual_readiness_not_version(self):
        """A failed normal restore advises matching software without claiming readiness."""
        self.controller.mode = "gateway"
        await self.submit("enter", "enter", confirmed=True)
        await self.prepare()
        self.runtime.handoff.side_effect = BootstrapError("normal_service_not_ready")
        result, _, _ = await self.submit("cancel", "cancel", confirmed=True)
        self.assertEqual(result["error"], "software_rollback_required")
        self.assertFalse(result["network_ready"])
        self.assertEqual(self.controller.mode, "bootstrap")

    async def test_new_network_initialization_is_explicit_and_stable(self):
        """No network is invented by GET; explicit retry returns the same persisted choice."""
        self.store.set_meta("network", None)
        self.assertIsNone(self.controller.info()["expected_network_id"])
        result, payload, _ = await self.submit(
            "initialize", "initialize", confirmed=True
        )
        self.assertFalse(result["network_ready"])
        self.assertNotIn(result["network"]["gateway"], ("00000000", "ffffffff"))
        self.assertEqual(await self.controller.submit("initialize", payload), result)
        self.assertEqual(self.calls, [])

    async def test_conflicting_ids_and_legacy_recovery_remain_blocked(self):
        """A second journal cannot erase unresolved legacy update state."""
        self.runtime.admission_reason.return_value = "existing_update_unresolved"
        result, payload, _ = await self.submit(
            "prepare",
            "blocked",
            target_id=self.controller.target_id,
            candidate_id=self.controller.candidate["id"],
            dedicated=True,
            board_confirmed=True,
        )
        self.assertEqual(result["error"], "existing_update_unresolved")
        payload["target_id"] = "other"
        with self.assertRaises(BootstrapError):
            await self.controller.submit("prepare", payload)
        self.assertEqual(self.calls, [])

    async def test_refuses_model_or_gateway_database(self):
        """A model journal or network database cannot authorize physical work."""
        for application in (0, 0x4C505631):
            path = self.root / f"foreign-{application}.db"
            with sqlite3.connect(path) as db:
                db.execute(f"PRAGMA application_id={application}")
                db.execute("CREATE TABLE model (id TEXT)")
            with self.assertRaises(ValueError):
                BootstrapStore(path)

    async def test_missing_prerequisites_reject_enter_before_fence(self):
        """A normal hub is not interrupted when setup prerequisites are unavailable."""
        self.controller.mode = "gateway"
        for number, reason in enumerate(("bundle_missing", "loader_policy_required")):
            self.controller.blocked_reason = reason
            self.assertFalse(self.controller.info()["enter_supported"])
            result, _, _ = await self.submit(
                "enter", f"missing-{number}", confirmed=True
            )
            self.assertEqual(result["error"], reason)
            self.runtime.fence.assert_not_called()
            self.runtime.enter.assert_not_awaited()
        self.assertEqual(self.controller.mode, "gateway")

    async def test_cancel_before_run_failure_is_retryable_after_physical_entry(self):
        """Missing loader evidence does not invent a possible flash or run boundary."""
        self.controller.mode = "gateway"
        await self.submit("enter", "enter", confirmed=True)
        self.worker.run.side_effect = BootstrapError("loader_unverified")
        result, _, _ = await self.submit("cancel", "cancel", confirmed=True)
        self.assertEqual(result["status"], "rejected")
        self.assertIsNone(result["recovery"])
        self.assertTrue(self.controller.info()["cancel_supported"])

    async def test_initialized_but_unconfigured_network_can_cancel_without_radio(self):
        """Accepted new-network proposal is not mistaken for a running existing hub."""
        self.store.set_meta("network", None)
        await self.submit("initialize", "init", confirmed=True)
        result, _, _ = await self.submit("cancel", "cancel-new", confirmed=True)
        self.assertEqual(result["status"], "confirmed")
        self.assertTrue(result["cancelled_unconfigured"])
        self.assertFalse(result["network_ready"])
        self.runtime.handoff.assert_not_awaited()
        self.assertEqual(self.calls, [])

    async def test_unexpected_failure_logs_location_without_private_error_contents(
        self,
    ):
        """Server diagnostics identify generic failures without disclosing exception secrets."""
        secret = "fixture-private-token /private/credential-file"
        self.worker.run.side_effect = OSError(secret)
        with self.assertLogs("livolod.bootstrap_controller", level="ERROR") as captured:
            result = await self.prepare()
        self.assertEqual(result["status"], "rejected")
        self.assertEqual(result["error"], "operation_failed")
        self.assertIsNone(result["plan_id"])
        logged = "\n".join(captured.output)
        self.assertIn("kind=prepare", logged)
        self.assertIn("stage=accepted", logged)
        self.assertIn("type=OSError", logged)
        self.assertIn("livolod.bootstrap_controller._execute:", logged)
        self.assertNotIn(secret, logged)
        self.assertNotIn("credential-file", logged)
        self.assertFalse(self.store.meta("installation_attempted", False))
