"""Prove the isolated provisioning contract, crash fencing and zero hardware access."""

from copy import deepcopy
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from livolod import FIRMWARE_COMPAT_VERSION
from livolod.provisioning import PROFILE, ProvisioningError
from livolod.provisioning_store import ProvisioningStore
from provisioning_fixture import ProvisioningFixture, TOKEN
from test_firmware_bundle import gbl, mg21_image


class ProvisioningTests(unittest.TestCase):
    def setUp(self):
        """Create private model state and synthetic nondeployable firmware."""
        self.directory = tempfile.TemporaryDirectory()
        self.fixture = ProvisioningFixture(self.directory.name)
        self.addCleanup(self.directory.cleanup)
        self.addCleanup(self.fixture.close)

    def start(self):
        """Review then explicitly confirm one job; no worker executes on admission."""
        plan = self.fixture.plan()
        self.assertTrue(plan["ok"], plan)
        self.payload = self.fixture.submission(plan["data"])
        reply = self.fixture.call("start", self.payload)
        self.assertTrue(reply["ok"], reply)
        self.assertEqual(self.fixture.transfers, 0)
        return reply["data"]

    def job(self):
        """Read the existing job without reusing an install submission."""
        return self.fixture.call("job", {"request_id": "model-job-1"})["data"]

    def test_guided_lifecycle_never_claims_network_readiness(self):
        """Transfer 100% remains pending; verified app still requires normal setup."""
        info = self.fixture.call("info")
        self.assertTrue(info["simulated"])
        self.assertFalse(info["data"]["physical_install_supported"])
        identity = info["instance_id"]
        self.start()
        self.fixture.transfer()
        self.assertEqual(self.job()["transfer_percent"], 100)
        self.assertEqual(self.job()["status"], "pending")
        self.assertFalse(self.job()["application_verified"])
        self.fixture.finish()
        job = self.job()
        self.assertEqual(
            (job["status"], job["stage"]), ("confirmed", "application_verified")
        )
        self.assertFalse(job["network_ready"])
        self.assertTrue(job["setup_required"])
        self.fixture.restart()
        self.assertEqual(self.fixture.call("info")["instance_id"], identity)
        self.assertEqual(self.job(), job)
        self.assertEqual(self.fixture.call("start", self.payload)["data"], job)
        self.assertEqual(self.fixture.transfers, 1)

    def test_credentials_and_unsupported_actions_cannot_start_work(self):
        """Invalid credentials, arbitrary paths and worker actions never admit jobs."""
        for token in (None, "wrong", "秘密"):
            self.assertEqual(
                self.fixture.model.request(token, "info")["error"],
                "authentication_failed",
            )
        for action, payload in (
            ("before_transfer", {}),
            ("flash", {"port": "/dev/ttyUSB0"}),
            ("preflight", {"target_id": "/dev/ttyUSB0", "candidate_id": "fixture-gbl"}),
            (
                "preflight",
                {
                    "target_id": "fixture-radio",
                    "candidate_id": "fixture-gbl",
                    "url": "https://invalid",
                },
            ),
            ("start", []),
        ):
            self.assertEqual(
                self.fixture.call(action, payload)["error"], "invalid_request"
            )
        self.assertFalse(self.fixture.store.blocked())

    def test_confirmation_and_instance_are_mandatory(self):
        """A reviewed plan grants nothing without true confirmation and instance match."""
        payload = self.fixture.submission(self.fixture.plan()["data"])
        for value in (False, 1, "yes", None):
            self.assertEqual(
                self.fixture.call("start", dict(payload, confirmed=value))["error"],
                "confirmation_required",
            )
        self.assertEqual(
            self.fixture.call("start", dict(payload, instance_id="replacement"))[
                "error"
            ],
            "instance_changed",
        )
        self.assertFalse(self.fixture.store.blocked())

    def test_plan_expiry_uses_monotonic_time_and_restart_discards_plans(self):
        """Review expiry blocks new work while lost responses reconcile durable jobs."""
        payload = self.fixture.submission(self.fixture.plan()["data"])
        self.fixture.clock.now += 120
        self.assertEqual(self.fixture.call("start", payload)["error"], "plan_expired")
        payload = self.fixture.submission(self.fixture.plan()["data"])
        self.fixture.restart()
        self.assertEqual(self.fixture.call("start", payload)["error"], "plan_unknown")

    def test_replayed_request_and_consumed_plan_cannot_transfer_twice(self):
        """Lost admission replies keep one job; reusing a plan under a new ID fails."""
        first = self.start()
        self.fixture.clock.now += 500
        self.assertEqual(self.fixture.call("start", self.payload)["data"], first)
        self.assertEqual(
            self.fixture.call("start", dict(self.payload, plan_id="different"))[
                "error"
            ],
            "request_conflict",
        )
        self.fixture.transfer()
        self.fixture.finish()
        self.assertEqual(
            self.fixture.call("start", dict(self.payload, request_id="different"))[
                "error"
            ],
            "plan_used",
        )

    def test_target_and_artifact_changes_invalidate_review(self):
        """Same display handles cannot hide replaced evidence or candidate bytes."""
        for change in ("generation", "transport_identity", "artifact", "candidate_id"):
            with self.subTest(change=change):
                fixture = self.fixture
                original_target, original_artifact = deepcopy(fixture.target), dict(
                    fixture.artifact
                )
                payload = fixture.submission(fixture.plan()["data"])
                if change in ("generation", "transport_identity"):
                    fixture.replace_target(**{change: "replacement"})
                elif change == "candidate_id":
                    fixture.artifact["candidate_id"] = "replacement"
                else:
                    image = bytearray(mg21_image(PROFILE, FIRMWARE_COMPAT_VERSION))
                    image[-1] = 1
                    fixture.artifact["data"] = gbl(bytes(image))
                fixture.model.replace_fixture(fixture.target, fixture.artifact)
                self.assertEqual(
                    fixture.call("start", payload)["error"], "plan_changed"
                )
                fixture.target, fixture.artifact = original_target, original_artifact
                fixture.model.replace_fixture(fixture.target, fixture.artifact)

    def test_preflight_rejects_unproven_target_owner_and_loader(self):
        """An operator board claim, unique transport, modeled owner and loader all matter."""
        for fields, code in (
            ({"board": "sonoff-zbdongle-e"}, "unsupported_profile"),
            ({"board_evidence": "unknown"}, "board_evidence_required"),
            ({"unique": False}, "target_ambiguous"),
            ({"ownership": "conflict"}, "ownership_unresolved"),
            ({"ownership": "unknown"}, "ownership_unresolved"),
            ({"mode": "absent"}, "target_absent"),
            ({"loader_supported": False}, "loader_unverified"),
            ({"mode": "application"}, "use_managed_update"),
        ):
            with self.subTest(fields=fields):
                original = dict(self.fixture.target)
                self.fixture.replace_target(**fields)
                self.assertEqual(self.fixture.plan()["error"], code)
                self.fixture.target = original
                self.fixture.model.replace_fixture(original, self.fixture.artifact)
        self.assertEqual(self.fixture.transfers, 0)

    def test_protected_regions_and_wrong_board_fail_artifact_inspection(self):
        """GBL CRC does not authorize loader writes or another MG21 board image."""
        for data in (
            gbl(mg21_image(PROFILE, FIRMWARE_COMPAT_VERSION), address=0),
            gbl(mg21_image("sonoff-zbdongle-e", FIRMWARE_COMPAT_VERSION)),
            b"not-a-gbl",
        ):
            self.fixture.artifact["data"] = data
            self.fixture.model.replace_fixture(
                self.fixture.target, self.fixture.artifact
            )
            self.assertEqual(self.fixture.plan()["error"], "artifact_invalid")
        self.assertFalse(self.fixture.store.blocked())

    def test_durable_boundary_failure_prevents_even_fake_transfer(self):
        """A failed journal commit never grants the fake worker permission to transfer."""
        self.start()
        with patch.object(self.fixture.store, "save", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                self.fixture.transfer()
        self.assertEqual(self.fixture.transfers, 0)
        self.assertFalse(self.job()["mutation_possible"])

    def test_admission_failure_creates_no_job_or_transfer(self):
        """Failure to durably admit is not mistaken for an acknowledged install."""
        payload = self.fixture.submission(self.fixture.plan()["data"])
        with patch.object(
            self.fixture.store, "admit", side_effect=OSError("disk full")
        ):
            with self.assertRaises(OSError):
                self.fixture.call("start", payload)
        self.assertIsNone(self.fixture.store.lookup(payload["request_id"]))
        self.assertEqual(self.fixture.transfers, 0)

    def test_restart_fences_every_pending_phase_without_replay(self):
        """Restart preserves the admitted ID and blocks further work at all checkpoints."""
        for phase in ("awaiting_bootloader", "transferring", "verifying_application"):
            with self.subTest(phase=phase), tempfile.TemporaryDirectory() as root:
                fixture = ProvisioningFixture(root)
                try:
                    payload = fixture.submission(fixture.plan()["data"])
                    fixture.call("start", payload)
                    if phase != "awaiting_bootloader":
                        fixture.transfer()
                    if phase == "verifying_application":
                        fixture.model.transferred("model-job-1")
                    count = fixture.transfers
                    fixture.restart()
                    job = fixture.call("start", payload)["data"]
                    self.assertEqual(job["status"], "unknown")
                    self.assertEqual(job["recovery"], "recovery_required")
                    self.assertEqual(fixture.plan()["error"], "job_unresolved")
                    with self.assertRaises(ProvisioningError):
                        fixture.transfer()
                    self.assertEqual(fixture.transfers, count)
                finally:
                    fixture.close()

    def test_post_transfer_wrong_identity_or_armed_state_requires_recovery(self):
        """An upload cannot mask incorrect firmware, protocol, rate or armed state."""
        for key, value in (
            ("board", "sonoff-zbdongle-e"),
            ("fw_version", "0.1"),
            ("proto", "2"),
            ("baud", "115200"),
            ("armed", "1"),
            ("ack", "1"),
        ):
            with self.subTest(key=key), tempfile.TemporaryDirectory() as root:
                fixture = ProvisioningFixture(root)
                try:
                    fixture.call("start", fixture.submission(fixture.plan()["data"]))
                    fixture.transfer()
                    fixture.finish(**{key: value})
                    job = fixture.call("job", {"request_id": "model-job-1"})["data"]
                    self.assertEqual(job["status"], "unknown")
                    self.assertFalse(job["application_verified"])
                    self.assertEqual(job["recovery"], "recovery_required")
                finally:
                    fixture.close()

    def test_target_swap_after_admission_blocks_before_write(self):
        """A reused target handle cannot authorize a different attachment generation."""
        self.start()
        self.fixture.replace_target(generation="replacement")
        with self.assertRaisesRegex(ProvisioningError, "target_changed"):
            self.fixture.transfer()
        self.assertEqual(self.fixture.transfers, 0)
        self.fixture.model.fail("model-job-1", "target_changed")
        self.assertEqual(self.job()["status"], "rejected")

    def test_target_disappearance_after_transfer_never_confirms(self):
        """Lost physical continuity after upload stays unresolved instead of guessed."""
        self.start()
        self.fixture.transfer()
        self.fixture.model.transferred("model-job-1")
        self.fixture.replace_target(mode="absent")
        self.fixture.model.verify("model-job-1", {})
        self.assertEqual(self.job()["error"], "target_absent")
        self.assertEqual(self.job()["recovery"], "recovery_required")

    def test_invalid_progress_and_duplicate_worker_events_are_rejected(self):
        """Worker ordering and finite monotonic percentages cannot invent success."""
        self.start()
        self.fixture.transfer()
        for value in (float("nan"), float("inf"), -1, 101, True, 99):
            with self.assertRaises(ProvisioningError):
                self.fixture.model.progress("model-job-1", value)
        with self.assertRaises(ProvisioningError):
            self.fixture.model.before_transfer("model-job-1")
        self.fixture.finish()
        with self.assertRaises(ProvisioningError):
            self.fixture.model.transferred("model-job-1")

    def test_response_mutation_cannot_change_model_or_journal(self):
        """Client-owned response dictionaries cannot alter trusted evidence or plans."""
        self.fixture.call("targets")["data"]["targets"][0]["ownership"] = "conflict"
        plan = self.fixture.plan()["data"]
        payload = self.fixture.submission(plan)
        plan["artifact_sha256"] = "forged"
        job = self.fixture.call("start", payload)["data"]
        job["status"] = "confirmed"
        self.assertEqual(self.job()["status"], "pending")
        self.assertNotEqual(self.job()["artifact_sha256"], "forged")

    def test_isolation_guards_and_journal_never_contain_credentials(self):
        """The complete model lifecycle needs neither serial, discovery nor subprocesses."""
        with (
            patch(
                "serial.Serial", side_effect=AssertionError("physical serial forbidden")
            ),
            patch(
                "serial.tools.list_ports.comports",
                side_effect=AssertionError("scan forbidden"),
            ),
            patch("subprocess.Popen", side_effect=AssertionError("helper forbidden")),
        ):
            self.start()
            self.fixture.transfer()
            self.fixture.finish()
        encoded = json.dumps(self.fixture.store.lookup("model-job-1"))
        self.assertNotIn(TOKEN, encoded)
        self.assertNotIn("_data", encoded)
        self.assertNotIn("/dev/", encoded)

    def test_store_refuses_live_gateway_database_and_second_owner(self):
        """Dedicated journal ownership never migrates or writes gateway database tables."""
        with self.assertRaises(OSError):
            ProvisioningStore(self.fixture.path)
        path = Path(self.directory.name) / "gateway.sqlite3"
        with sqlite3.connect(path) as db:
            db.execute("CREATE TABLE devices (id TEXT PRIMARY KEY)")
            db.execute("INSERT INTO devices VALUES ('preserved')")
        before = path.read_bytes()
        with self.assertRaisesRegex(ValueError, "gateway state refused"):
            ProvisioningStore(path)
        self.assertEqual(path.read_bytes(), before)

    def test_journal_capacity_is_bounded_without_forgetting_request_ids(self):
        """A full ledger rejects admission instead of allowing replay through eviction."""
        payload = self.fixture.submission(self.fixture.plan()["data"])
        with patch("livolod.provisioning_store.MAX_JOBS", 0):
            self.assertEqual(
                self.fixture.call("start", payload)["error"], "journal_full"
            )
        self.assertFalse(self.fixture.store.blocked())

    def test_plans_are_bounded_and_unknown_job_has_stable_error(self):
        """Untrusted repeated review requests cannot grow unbounded model memory."""
        with patch("livolod.provisioning.MAX_PLANS", 1):
            self.assertTrue(self.fixture.plan()["ok"])
            self.assertEqual(self.fixture.plan()["error"], "too_many_plans")
        self.assertEqual(
            self.fixture.call("job", {"request_id": "not-admitted"})["error"],
            "job_unknown",
        )
