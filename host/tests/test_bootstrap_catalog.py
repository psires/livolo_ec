"""Engineering artifacts never inherit public firmware qualification or HTTP overrides."""

import asyncio
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, Mock, patch

from livolod import FIRMWARE_COMPAT_VERSION
from livolod.bootstrap_catalog import APPROVED_TRIAL, bootstrap_candidate
from livolod.bootstrap_controller import BootstrapController
from livolod.bootstrap_store import BootstrapStore
from livolod.bootstrap_worker import target_json
from livolod.firmware_bundle import BundleError, load_candidate, select_candidate
from livolod.preserved_gecko import GeckoTarget
from test_bootstrap_controller import catalog


class EngineeringCatalogTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        """Use synthetic GBL bytes; substitute only the reviewed digest in test memory."""
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        catalog(self.root)
        self.manifest = json.loads((self.root / "manifest.json").read_text())
        self.entry = self.manifest["artifacts"][0]
        self.entry.update(
            qualified=False, source_commit=APPROVED_TRIAL["source_commit"]
        )
        self.write()
        self.approved = deepcopy(APPROVED_TRIAL)
        self.approved["sha256"] = self.entry["sha256"]
        self.patch = patch.dict(APPROVED_TRIAL, self.approved, clear=True)
        self.patch.start()
        self.target = GeckoTarget(
            "/dev/test-only",
            "devices/fixture/usb1/1-2",
            "generation",
            (188, 0),
            "00",
            "1.9.2",
        )
        self.policy = dict(
            schema=1,
            target=target_json(self.target),
            loader=dict(
                attachment="generation",
                evidence_id="b437e13",
                version="1.9.2",
                requires_signed=False,
                requires_encrypted=False,
            ),
            engineering=deepcopy(self.approved),
        )
        self.settings = dict(
            serial_port=self.target.port,
            catalog=str(self.root),
            loader_policy=self.policy,
            mode="bootstrap",
        )

    async def asyncTearDown(self):
        """Remove the fixture approval and all private artifact files."""
        self.patch.stop()
        self.temp.cleanup()

    def write(self):
        """Persist only the synthetic fixture manifest."""
        (self.root / "manifest.json").write_text(json.dumps(self.manifest))

    def test_ordinary_loaders_reject_unqualified_engineering_artifact(self):
        """The private exception is unavailable to normal update and release loaders."""
        with self.assertRaises(BundleError) as error:
            select_candidate(
                self.root,
                board="zb-gw04-1v2",
                host_version=FIRMWARE_COMPAT_VERSION,
                app_baudrate=460800,
            )
        self.assertEqual(error.exception.code, "artifact_unqualified")
        with self.assertRaises(BundleError):
            load_candidate(
                self.root,
                self.entry["id"] + ":" + self.entry["sha256"],
                host_version=FIRMWARE_COMPAT_VERSION,
                app_baudrate=460800,
            )
        result = bootstrap_candidate(self.settings)
        self.assertTrue(result["engineering_only"])
        self.assertIs(self.entry["qualified"], False)

    def test_missing_wrong_or_unbound_policy_rejects(self):
        """No missing evidence or mismatched target/source/hash can widen engineering scope."""
        for field, value in (
            ("board", "sonoff-zbdongle-e"),
            ("sha256", "0" * 64),
            ("source_commit", "0" * 40),
            ("evidence_id", "other"),
            ("version", "0.13"),
        ):
            altered = deepcopy(self.settings)
            altered["loader_policy"]["engineering"][field] = value
            with self.subTest(field=field), self.assertRaises(BundleError):
                bootstrap_candidate(altered)
        for mutate in ("missing", "target", "attachment", "evidence"):
            altered = deepcopy(self.settings)
            if mutate == "missing":
                altered.pop("loader_policy")
            elif mutate == "target":
                altered["loader_policy"]["target"]["board"] = "sonoff-zbdongle-e"
            elif mutate == "attachment":
                altered["loader_policy"]["loader"]["attachment"] = "other"
            else:
                altered["loader_policy"]["loader"]["evidence_id"] = "other"
            with self.subTest(mutate=mutate), self.assertRaises(BundleError):
                bootstrap_candidate(altered)

    def test_manifest_cannot_change_provenance_or_claim_public_qualification(self):
        """The private record cannot be reused to admit a different manifest assertion."""
        for field, value in (
            ("source_commit", "0" * 40),
            ("sha256", "0" * 64),
            ("board", "sonoff-zbdongle-e"),
            ("qualified", True),
        ):
            original = self.entry[field]
            self.entry[field] = value
            self.write()
            with self.subTest(field=field), self.assertRaises(BundleError):
                bootstrap_candidate(self.settings)
            self.entry[field] = original
        self.write()

    async def test_artifact_change_after_prepare_rejects_install_without_worker(self):
        """Every admission reruns byte/hash/GBL validation against the reviewed binding."""
        store = BootstrapStore(self.root / "physical.db")
        runtime = Mock()
        runtime.admission_reason.return_value = None
        runtime.has_gateway_state.return_value = False
        worker = Mock(blocked_reason=None)
        worker.run = AsyncMock(
            return_value=dict(target=target_json(self.target), loader={"fixture": True})
        )
        controller = BootstrapController(store, self.settings, worker, runtime)
        try:
            body = dict(
                request_id="prepare",
                instance_id=store.instance_id,
                target_id=controller.target_id,
                candidate_id=controller.candidate["id"],
                dedicated=True,
                board_confirmed=True,
            )
            await controller.submit("prepare", body)
            await asyncio.gather(*tuple(controller.tasks))
            prepared = controller.result("prepare")
            self.assertTrue(prepared["plan"]["engineering_only"])
            data = bytearray((self.root / "image.gbl").read_bytes())
            data[-1] ^= 1
            (self.root / "image.gbl").write_bytes(data)
            result = await controller.submit(
                "install",
                dict(
                    request_id="install",
                    instance_id=store.instance_id,
                    plan_id=prepared["plan_id"],
                    confirmed=True,
                ),
            )
            self.assertEqual(result["status"], "rejected")
            self.assertTrue(result["engineering_only"])
            self.assertEqual(worker.run.await_count, 1)
        finally:
            await controller.close()
            store.close()
