"""Explicit no-flash reconciliation preserves history and cannot authorize a replay."""

from copy import deepcopy
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from livolod.bootstrap_controller import BootstrapController
from livolod.bootstrap_store import BootstrapStore
from livolod.bootstrap_worker import target_json
from livolod.bootstrap_maintenance import reviewed_evidence
from livolod.firmware_bundle import BundleError
from livolod.sniffer_identity import ENTRY_METHOD
import test_sonoff_bootstrap as fixture

EVIDENCE = dict(result_sha256="a" * 64, events_sha256="b" * 64)


class MaintenanceTests(unittest.IsolatedAsyncioTestCase):
    submit = fixture.SonoffControllerTests.submit
    prepare = fixture.SonoffControllerTests.prepare
    asyncTearDown = fixture.SonoffControllerTests.asyncTearDown

    async def asyncSetUp(self):
        """Produce an actual consumed-plan timeout through the real controller."""
        await fixture.SonoffControllerTests.asyncSetUp(self)
        self.target = target_json(fixture.sonoff())
        self.prepared = await self.prepare()
        self.failed, self.payload, _ = await self.submit(
            "install",
            "failed-install",
            plan_id=self.prepared["plan_id"],
            confirmed=True,
        )

    async def work(self, kind, job, candidate, binding, checkpoint):
        """Fail before the durable flash boundary, with optional fresh remote entry.

        @param kind Admitted worker operation.
        @param job Durable request binding.
        @param candidate Fixed synthetic artifact.
        @param binding Previously prepared target.
        @param checkpoint Actual journal checkpoint callback.
        """
        if kind == "install":
            await checkpoint("verifying_target")
            raise BundleError("inspection_timeout", "fixture")
        if kind == "prepare" and job.get("entry_method") == ENTRY_METHOD:
            await checkpoint("bootloader_entry")
        return await fixture.SonoffControllerTests.work(
            self, kind, job, candidate, binding, checkpoint
        )

    async def reconcile(self):
        """Simulate stopped-owner administrative action followed by service restart."""
        await self.controller.close()
        record = self.store.reconcile_preflash("failed-install", self.target, EVIDENCE)
        self.controller = BootstrapController(
            self.store, self.settings, self.worker, self.runtime
        )
        return record

    async def test_immutable_history_and_only_new_prepare_before_install(self):
        """A maintenance record changes eligibility, never the old receipt or consumed plan."""
        old_row = deepcopy(self.store.lookup("failed-install"))
        old_plan = deepcopy(self.store.plan(self.prepared["plan_id"]))
        record = await self.reconcile()
        info = self.controller.info()
        self.assertTrue(info["prepare_supported"])
        self.assertFalse(info["physical_install_supported"])
        self.assertFalse(info["initialize_supported"])
        self.assertFalse(info["cancel_supported"])
        self.assertEqual(info["reconciliation"]["setup_session"], info["setup_session"])
        self.assertNotIn("evidence", info["reconciliation"])
        self.assertIn(
            ENTRY_METHOD, self.controller.targets()["targets"][0]["entry_methods"]
        )
        self.assertEqual(
            await self.controller.submit("install", self.payload), old_row[1]
        )
        for kind, fields in (
            ("install", dict(plan_id=self.prepared["plan_id"], confirmed=True)),
            ("initialize", dict(confirmed=True)),
            ("cancel", dict(confirmed=True)),
            (
                "handoff",
                dict(install_request_id="failed-install", confirmed=True, network=None),
            ),
        ):
            outcome, _, _ = await self.submit(kind, "blocked-" + kind, **fields)
            self.assertEqual(outcome["error"], "fresh_preparation_required")
        fresh, _, _ = await self.submit(
            "prepare",
            "fresh-remote",
            target_id=self.controller.target_id,
            candidate_id=self.controller.candidate["id"],
            dedicated=True,
            board_confirmed=True,
            entry_method=ENTRY_METHOD,
            entry_confirmed=True,
        )
        self.assertEqual(fresh["status"], "confirmed")
        self.assertNotEqual(fresh["plan_id"], self.prepared["plan_id"])
        self.assertNotEqual(self.controller.session, record["setup_session"])
        self.assertIsNone(self.controller.info()["reconciliation"])
        self.assertFalse(self.store.meta("reconciliation_requires_prepare"))
        outcome, _, _ = await self.submit(
            "install", "used-plan", plan_id=self.prepared["plan_id"], confirmed=True
        )
        self.assertEqual(outcome["error"], "plan_used")
        self.assertEqual(self.store.lookup("failed-install"), old_row)
        self.assertEqual(self.store.plan(self.prepared["plan_id"]), old_plan)

    async def test_flash_or_unclean_worker_or_target_change_cannot_reconcile(self):
        """Positive mutation/cleanup uncertainty or different attachment keeps recovery fenced."""
        for key in (
            "installation_attempted",
            "worker_cleanup_unconfirmed",
            "active_request_id",
        ):
            self.store.set_meta(key, True)
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.store.reconcile_preflash("failed-install", self.target, EVIDENCE)
            self.store.set_meta(key, False)
        with self.assertRaises(ValueError):
            self.store.reconcile_preflash(
                "failed-install", dict(self.target, attachment="new"), EVIDENCE
            )
        self.assertIsNone(self.store.meta("preflash_reconciliation"))
        self.assertEqual(self.store.unresolved(), [self.failed])

    async def test_changed_receipt_or_plan_invalidates_maintenance_binding(self):
        """A stale binding cannot hide a different unknown operation."""
        await self.reconcile()
        changed = dict(self.failed, stage="flash")
        self.store.save(changed)
        self.assertIsNone(self.store.reconciliation())
        self.assertEqual(self.store.unresolved(), [changed])
        self.assertFalse(self.controller.info()["prepare_supported"])

    async def test_contradictory_success_or_duplicate_maintenance_rejected_unchanged(
        self,
    ):
        """Contradictory completion facts cannot be concealed by a preflash stage label."""
        for changes in (
            dict(application_verified=True),
            dict(network_ready=True),
            dict(transfer_percent=0),
            dict(stage="flash"),
        ):
            changed = dict(self.failed, **changes)
            self.store.save(changed)
            before = deepcopy(self.store.lookup("failed-install"))
            plan = deepcopy(self.store.plan(self.prepared["plan_id"]))
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.store.reconcile_preflash("failed-install", self.target, EVIDENCE)
            self.assertEqual(self.store.lookup("failed-install"), before)
            self.assertEqual(self.store.plan(self.prepared["plan_id"]), plan)
            self.assertIsNone(self.store.meta("preflash_reconciliation"))
        self.store.save(self.failed)
        await self.reconcile()
        with self.assertRaises(ValueError):
            self.store.reconcile_preflash("failed-install", self.target, EVIDENCE)

    async def test_new_uncertain_entry_remains_fenced(self):
        """Old administrative recovery cannot hide uncertainty from a new physical action."""
        await self.reconcile()

        async def fail_entry(kind, job, candidate, binding, checkpoint):
            """Record possible entry then fail the fresh loader inspection.

            @param kind New prepare kind.
            @param job Durable new request.
            @param candidate Synthetic candidate.
            @param binding Prior binding, absent here.
            @param checkpoint Actual durable controller callback.
            """
            await checkpoint("bootloader_entry")
            raise BundleError("inspection_timeout", "fixture")

        self.worker.run.side_effect = fail_entry
        result, _, _ = await self.submit(
            "prepare",
            "new-uncertain",
            target_id=self.controller.target_id,
            candidate_id=self.controller.candidate["id"],
            dedicated=True,
            board_confirmed=True,
            entry_method=ENTRY_METHOD,
            entry_confirmed=True,
        )
        self.assertEqual(result["status"], "unknown")
        self.assertEqual(self.store.unresolved(), [result])
        self.assertTrue(self.store.meta("reconciliation_requires_prepare"))
        self.assertFalse(self.controller.info()["initialize_supported"])
        self.assertIsNone(self.controller.info()["reconciliation"])

    async def test_reconciliation_survives_restart_without_radio_replay(self):
        """Recovery acknowledgment is durable while initialization and startup remain idle."""
        record = await self.reconcile()
        await self.controller.close()
        self.store.close()
        self.store = BootstrapStore(self.root / "bootstrap.db")
        self.controller = BootstrapController(
            self.store, self.settings, self.worker, self.runtime
        )
        count = self.worker.run.call_count
        await self.controller.resume_startup()
        self.assertEqual(self.worker.run.call_count, count)
        self.runtime.start_gateway.assert_not_called()
        self.assertEqual(
            self.controller.info()["reconciliation"]["reconciliation_id"],
            record["reconciliation_id"],
        )
        self.assertFalse(self.controller.info()["initialize_supported"])

    async def test_maintenance_open_preserves_pending_receipt(self):
        """Stopped-owner maintenance rejects orphan intent without rewriting its result."""
        pending = dict(self.failed, status="pending", stage="accepted")
        self.store.save(pending)
        self.store.close()
        with self.assertRaises(ValueError):
            BootstrapStore(self.root / "bootstrap.db", recover_pending=False)
        with closing(sqlite3.connect(self.root / "bootstrap.db")) as db:
            encoded = db.execute(
                "SELECT result FROM operations WHERE id=?", ("failed-install",)
            ).fetchone()[0]
        self.assertEqual(json.loads(encoded), pending)


class EvidenceTests(unittest.TestCase):
    def setUp(self):
        """Create a synthetic private transcript with no radio or remote file access."""
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.result_path, self.events_path = root / "result.json", root / "events.jsonl"
        self.target = fixture.sonoff()
        self.result = dict(
            entry_attempted=True,
            reopen_verified=True,
            run_attempted=True,
            sniffer_verified=True,
            firmware_upload=False,
            original_journal_modified=False,
            cleanup_errors=[],
        )
        self.rows = [
            dict(event="failed_install_read_only_basis", failed_request_id="failed"),
            dict(event="opened", modem_bits=6),
            dict(
                event="durable_bootloader_entry_intent",
                target_sha256=hashlib.sha256(
                    json.dumps(target_json(self.target), sort_keys=True).encode()
                ).hexdigest(),
            ),
        ]
        self.rows += [
            dict(event="write_intent", baud=921600, hex=b"\nBOOTLOADER\n".hex()),
            dict(event="write_completed", count=12),
            dict(event="write_intent", baud=115200, hex="0a"),
            dict(event="write_completed", count=1),
            dict(event="closed"),
            dict(event="entry_loader_verified"),
            dict(event="closed_review_interval", seconds=60),
            dict(event="opened", modem_bits=6),
            dict(event="write_intent", baud=115200, hex="0a"),
            dict(event="write_completed", count=1),
            dict(event="reopen_loader_verified"),
            dict(event="intact_sniffer_run_basis", failed_request_id="failed"),
            dict(event="durable_intact_sniffer_run_intent"),
            dict(event="write_intent", baud=115200, hex="32"),
            dict(event="write_completed", count=1),
            dict(event="closed"),
            dict(event="intact_sniffer_verified"),
            dict(event="finished"),
        ]

    def validate(self):
        """Validate current synthetic evidence and return only its private hash bindings."""
        self.result_path.write_text(json.dumps(self.result))
        self.events_path.write_text("\n".join(json.dumps(row) for row in self.rows))
        return reviewed_evidence(
            self.result_path, self.events_path, "failed", self.target
        )

    def test_complete_reviewed_sequence_binds_hashes(self):
        """The completed sequence creates detached evidence bindings without touching journal state."""
        self.assertEqual(set(self.validate()), {"result_sha256", "events_sha256"})

    def test_incomplete_or_mutating_evidence_rejected(self):
        """An upload indication, missing proof or wrong request cannot authorize recovery."""
        self.result["sniffer_verified"] = False
        with self.assertRaises(ValueError):
            self.validate()
        self.result["sniffer_verified"] = True
        self.rows[0]["failed_request_id"] = "other"
        with self.assertRaises(ValueError):
            self.validate()
        self.rows[0]["failed_request_id"] = "failed"
        self.rows[3]["hex"] = "31"
        with self.assertRaises(ValueError):
            self.validate()


if __name__ == "__main__":
    unittest.main()
