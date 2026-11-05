"""Offline catalog admission and immutable publication tests."""

import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

from firmware_catalog import assemble

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "host/tests"))
from test_firmware_bundle import gbl, mg21_image


class CatalogTests(unittest.TestCase):
    """Keep local qualification approval bound to exact application bytes."""

    def setUp(self):
        """Create a synthetic, nonexecutable image and explicit trial review."""
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.image = self.root / "application.gbl"
        self.image.write_bytes(gbl(mg21_image()))
        self.notice = self.root / "notice.txt"
        self.notice.write_text("Test fixture only; not firmware.\n")
        self.record_path = self.root / "qualification.json"
        self.record = dict(
            board="sonoff-zbdongle-e",
            version="0.12",
            artifact_sha256=hashlib.sha256(self.image.read_bytes()).hexdigest(),
            scope="standalone_local_update_test",
            approved_for_installation_trial=True,
            source_commit="a" * 40,
            source_snapshot_sha256="b" * 64,
            limitations=["Synthetic fixture, never deploy"],
            checks={
                name: "passed"
                for name in (
                    "artifact_inspection",
                    "repeat_build",
                    "baseline_comparison",
                    "firmware_tests",
                    "notice_collection",
                )
            },
        )
        self.spec = dict(
            version="0.12",
            notices=[str(self.notice)],
            artifacts=[
                dict(
                    board="sonoff-zbdongle-e",
                    path=str(self.image),
                    qualification=str(self.record_path),
                )
            ],
        )

    def package(self):
        """Publish this test's current review record and attempt catalog assembly."""
        self.record_path.write_text(json.dumps(self.record))
        return assemble(self.spec, self.root / "catalog")

    def test_exact_trial_record_and_notices_travel_with_bytes(self):
        """Preserve the reviewed payload, scope, limitations and full notice."""
        result = self.package()
        artifact = result["artifacts"][0]
        root = self.root / "catalog"
        self.assertEqual(
            (root / artifact["path"]).read_bytes(), self.image.read_bytes()
        )
        self.assertEqual(
            json.loads((root / artifact["qualification"]).read_text()), self.record
        )
        self.assertEqual(
            (root / artifact["notices"][0]).read_bytes(), self.notice.read_bytes()
        )

    def test_changed_payload_cannot_reuse_approval(self):
        """Reject approval for another digest before publishing a directory."""
        self.record["artifact_sha256"] = "0" * 64
        with self.assertRaises(ValueError):
            self.package()
        self.assertFalse((self.root / "catalog").exists())

    def test_unreviewed_or_public_scope_is_rejected(self):
        """Never reinterpret absent or public qualification as a local trial review."""
        for scope in (None, "public_release"):
            with self.subTest(scope=scope):
                self.record["scope"] = scope
                with self.assertRaises(ValueError):
                    self.package()

    def test_failed_build_check_is_rejected(self):
        """A failed repeat build cannot be hidden by an approval boolean."""
        self.record["checks"]["repeat_build"] = "failed"
        with self.assertRaises(ValueError):
            self.package()

    def test_existing_catalog_is_never_replaced(self):
        """Preserve already published artifacts even when reusing an output path."""
        self.package()
        before = (self.root / "catalog/manifest.json").read_bytes()
        with self.assertRaises(ValueError):
            self.package()
        self.assertEqual((self.root / "catalog/manifest.json").read_bytes(), before)

    def test_missing_notice_does_not_publish_partial_catalog(self):
        """Reject missing attribution before creating visible release contents."""
        self.notice.unlink()
        with self.assertRaises(ValueError):
            self.package()
        self.assertFalse((self.root / "catalog").exists())


if __name__ == "__main__":
    unittest.main()
