"""Public release gates use synthetic containers; no fixture is flashable firmware."""

import copy
import hashlib
import io
import tarfile
import json
import shutil
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from firmware_release import (
    PUBLIC_CHECKS,
    UPDATE_CHECKS,
    assemble,
    verify,
    verify_installed,
    stage_for_source,
    archive,
    fetched_files,
)

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "host/tests"))
from test_release_versions import source_fixture
from test_firmware_artifacts import nrf_zip
from test_firmware_bundle import gbl, mg21_image, ti_package
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization
from livolod.update_package import HEADER, FORMAT, BOARD
from test_firmware_bootstrap import bootloader, KEY


class PublicReleaseTests(unittest.TestCase):
    """Require independent download and updater evidence for every exact board."""

    def setUp(self):
        """Create four nonexecutable payloads and explicit synthetic review records."""
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        ti, _ = ti_package()
        signing = Ed25519PrivateKey.generate()
        key = signing.public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw
        )
        image = bytearray(ti[192:])
        image[300:332] = key
        header = HEADER.pack(
            b"ECUPD01\0",
            FORMAT,
            BOARD,
            0,
            12,
            3,
            0x8000,
            len(image),
            0,
            hashlib.sha512(image).digest(),
            bytes(24),
        )
        ti = header + signing.sign(header) + image
        self.signing = signing
        public_key = self.file("keys/public.key", key)
        notice = self.file("notices/fixture.txt", b"TEST ONLY, NEVER DISTRIBUTE\n")
        evidence = self.file("evidence/fixture.log", b"Synthetic test evidence only\n")
        self.evidence = evidence
        self.spec = dict(schema=1, version="0.12", source_commit="a" * 40, artifacts=[])
        self.records = {}
        payloads = {
            "codm-cc2652p": ti,
            "sonoff-zbdongle-e": gbl(mg21_image()),
            "zb-gw04-1v2": gbl(mg21_image("zb-gw04-1v2")),
            "nordic-pca10059": nrf_zip(),
        }
        for board, payload in payloads.items():
            artifact = self.file("artifacts/" + board + ".bin", payload)
            record = dict(
                schema=1,
                scope="public_firmware_download",
                board=board,
                version="0.12",
                source_commit="a" * 40,
                artifact_sha256=artifact["sha256"],
                technical_reviewer="Synthetic test reviewer",
                redistribution_reviewer="Synthetic test reviewer",
                limitations=["NOT REAL QUALIFICATION; never deploy these fixtures"],
                checks=self.checks(PUBLIC_CHECKS),
                automatic_update=dict(eligible=False, reason="Not updater qualified"),
            )
            self.records[board] = record
            item = dict(
                board=board, artifact=artifact, notices=[notice], qualification={}
            )
            if board == "codm-cc2652p":
                item["public_key"] = public_key
                boot = bootloader().replace(KEY, key)
                self.boot_record = copy.deepcopy(record)
                self.boot_record.update(
                    scope="public_firmware_bootstrap",
                    version="0.2",
                    artifact_sha256=hashlib.sha256(boot).hexdigest(),
                )
                item["bootstrap"] = dict(
                    version="0.2",
                    artifact=self.file("artifacts/ti-boot.bin", boot),
                    notices=[notice],
                    qualification={},
                )
            self.spec["artifacts"].append(item)
        self.file("keys/private-secret.pem", b"Must not be copied")

    def file(self, name, data):
        """Create a bounded fixture and return its exact reviewed hash reference.

        @param name Relative fixture path under the input directory.
        @param data Immutable fixture bytes.
        @return Path and SHA-256 mapping.
        """
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return dict(path=name, sha256=hashlib.sha256(data).hexdigest())

    def checks(self, required):
        """Create clearly synthetic acceptance fields for validator regression tests.

        @param required Check names and required evidence kinds.
        @return Complete fake evidence mappings; never an operator review.
        """
        return {
            name: dict(
                result="passed",
                kind=kind,
                summary="Synthetic test only",
                evidence=[self.evidence],
            )
            for name, kind in required.items()
        }

    def specification(self):
        """Write current review records with fresh hashes for this test's attempt."""
        for item in self.spec["artifacts"]:
            board = item["board"]
            if board == "codm-cc2652p":
                item["bootstrap"]["qualification"] = self.file(
                    "qualification/ti-boot.json", json.dumps(self.boot_record).encode()
                )
            item["qualification"] = self.file(
                "qualification/" + board + ".json",
                json.dumps(self.records[board]).encode(),
            )
        self.file("spec.json", json.dumps(self.spec).encode())
        return self.root / "spec.json"

    def package(self, output="output"):
        """Attempt an immutable export of this test's current input records.

        @param output New test-relative export directory.
        @return Release inventory digest.
        """
        return assemble(self.specification(), self.root / output)

    def test_manual_downloads_do_not_enable_updates(self):
        """Four inspected downloads may ship while the runtime catalog stays empty."""
        self.package()
        root = self.root / "output"
        self.assertEqual(
            len(json.loads((root / "release.json").read_text())["artifacts"]), 4
        )
        self.assertEqual(
            json.loads((root / "catalog/manifest.json").read_text())["artifacts"], []
        )
        self.assertFalse((root / "keys/private-secret.pem").exists())
        for line in (root / "SHA256SUMS").read_text().splitlines():
            digest, name = line.split("  ", 1)
            self.assertEqual(
                hashlib.sha256((root / name).read_bytes()).hexdigest(), digest
            )

    def test_only_separately_qualified_board_enters_runtime_catalog(self):
        """Updater eligibility never propagates from one board to another."""
        self.records["sonoff-zbdongle-e"]["automatic_update"] = dict(
            eligible=True,
            reviewer="Synthetic updater reviewer",
            tested_runtime_image="ghcr.io/example/test@sha256:" + "b" * 64,
            checks=self.checks(UPDATE_CHECKS),
        )
        self.package()
        catalog = json.loads((self.root / "output/catalog/manifest.json").read_text())
        self.assertEqual(
            [entry["board"] for entry in catalog["artifacts"]], ["sonoff-zbdongle-e"]
        )
        for entry in catalog["artifacts"]:
            self.assertEqual(
                (self.root / "output/catalog" / entry["path"]).read_bytes(),
                (self.root / entry["path"]).read_bytes(),
            )

    def test_incomplete_or_duplicated_board_set_fails(self):
        """An omitted board or a repeated supported board blocks the whole release."""
        original = copy.deepcopy(self.spec)
        for items in (original["artifacts"][:-1], [original["artifacts"][0]] * 4):
            self.spec["artifacts"] = items
            with self.assertRaises(ValueError):
                self.package()
            self.assertFalse((self.root / "output").exists())

    def test_local_trial_cannot_be_promoted_by_relabeling_catalog(self):
        """A local installation-trial approval cannot authorize public distribution."""
        self.records["sonoff-zbdongle-e"]["scope"] = "standalone_local_update_test"
        with self.assertRaises(ValueError):
            self.package()

    def test_simulated_or_failed_physical_checks_block_release(self):
        """Physical boot/control/recovery claims require passed physical evidence."""
        record = self.records["sonoff-zbdongle-e"]
        for field, value in (("kind", "test"), ("result", "pending")):
            original = copy.deepcopy(record["checks"])
            record["checks"]["manual_recovery"][field] = value
            with self.assertRaises(ValueError):
                self.package()
            record["checks"] = original

    def test_approval_is_bound_to_source_and_payload(self):
        """A different source or artifact cannot reuse a passing qualification."""
        record = self.records["sonoff-zbdongle-e"]
        for field in ("source_commit", "artifact_sha256"):
            old = record[field]
            record[field] = "0" * len(old)
            with self.assertRaises(ValueError):
                self.package()
            record[field] = old

    def test_changed_evidence_or_notices_fail_before_output(self):
        """Changed supporting material invalidates review even if firmware is unchanged."""
        for name in ("evidence/fixture.log", "notices/fixture.txt"):
            path = self.root / name
            original = path.read_bytes()
            path.write_bytes(b"Changed after review")
            with self.assertRaises(ValueError):
                self.package()
            self.assertFalse((self.root / "output").exists())
            path.write_bytes(original)

    def test_symlink_and_traversal_references_are_rejected(self):
        """Evidence allowlists cannot escape their root or follow linked inputs."""
        evidence = self.root / "evidence/fixture.log"
        original = evidence.read_bytes()
        evidence.unlink()
        target = self.root / "outside.log"
        target.write_bytes(original)
        evidence.symlink_to(target)
        with self.assertRaises(ValueError):
            self.package()
        evidence.unlink()
        evidence.write_bytes(original)
        self.evidence["path"] = "evidence/../outside.log"
        with self.assertRaises(ValueError):
            self.package()

    def test_update_requires_complete_checks_and_immutable_runtime(self):
        """A public payload gains no updater eligibility from an approval flag alone."""
        record = self.records["nordic-pca10059"]
        record["automatic_update"] = dict(eligible=True)
        with self.assertRaises(ValueError):
            self.package()
        record["automatic_update"] = dict(
            eligible=True,
            reviewer="Test",
            tested_runtime_image="image:latest",
            checks=self.checks(UPDATE_CHECKS),
        )
        with self.assertRaises(ValueError):
            self.package()

    def test_repeated_output_is_immutable_and_new_exports_are_identical(self):
        """Reexport is deterministic but cannot overwrite an existing release directory."""
        digest = self.package()
        with self.assertRaises(ValueError):
            self.package()
        self.assertEqual(self.package("second"), digest)
        self.assertEqual(
            (self.root / "output/SHA256SUMS").read_bytes(),
            (self.root / "second/SHA256SUMS").read_bytes(),
        )

    def test_export_verification_rejects_extra_files_and_catalog_changes(self):
        """Release staging rejects unsigned extras and forged updater eligibility."""
        digest = self.package()
        root = self.root / "output"
        pins = dict(inventory_sha256=digest, source_commit="a" * 40, version="0.12")
        files = verify(root, **pins)
        self.assertIn("catalog/manifest.json", files)
        (root / "private.key").write_bytes(b"Not reviewed")
        with self.assertRaises(ValueError):
            verify(root, **pins)
        (root / "private.key").unlink()
        (root / "catalog/manifest.json").write_text("{}")
        with self.assertRaises(ValueError):
            verify(root, **pins)

    def test_software_pin_cannot_select_another_inventory_or_compatibility(self):
        """Matching filenames cannot substitute another reviewed firmware release."""
        digest = self.package()
        pins = dict(inventory_sha256=digest, source_commit="a" * 40, version="0.12")
        for field, wrong in (
            ("inventory_sha256", "0" * 64),
            ("source_commit", "b" * 40),
            ("version", "0.13"),
        ):
            with self.subTest(field=field), self.assertRaises(ValueError):
                verify(self.root / "output", **dict(pins, **{field: wrong}))

    def test_package_injection_requires_source_pin_and_preserves_all_downloads(self):
        """Approved manual payloads are delivered without creating updater offers."""
        digest = self.package()
        source = source_fixture(self.root / "source")
        for path in source.rglob("*"):
            if path.is_file():
                path.write_text(
                    path.read_text()
                    .replace("0.13", "0.12")
                    .replace("MINOR 13", "MINOR 12")
                )
        self.assertIsNone(stage_for_source(source))
        with self.assertRaises(ValueError):
            stage_for_source(source, self.root / "output")
        pin = dict(
            archive_url="https://example.invalid/firmware.tar.gz",
            archive_sha256="c" * 64,
            schema=1,
            inventory_sha256=digest,
            source_commit="a" * 40,
            version="0.12",
        )
        (source / "host/release/firmware.json").write_text(json.dumps(pin))
        with (
            patch(
                "firmware_release.fetched_files",
                side_effect=ValueError("Unavailable pinned transport"),
            ),
            self.assertRaises(ValueError),
        ):
            stage_for_source(source)
        result = stage_for_source(source, self.root / "output")
        self.assertEqual((result["board_count"], result["updater_board_count"]), (4, 0))
        self.assertEqual(
            len(list((source / "firmware-release/artifacts").iterdir())), 5
        )
        self.assertEqual(
            json.loads((source / "host/livolod/firmware/manifest.json").read_text())[
                "artifacts"
            ],
            [],
        )
        with self.assertRaises(ValueError):
            stage_for_source(source, self.root / "output")

    def test_ti_bootstrap_is_mandatory_separately_reviewed_and_manual_only(self):
        """Application approval cannot authorize a missing or automatic TI bootstrap."""
        self.boot_record["scope"] = "public_firmware_download"
        with self.assertRaises(ValueError):
            self.package()
        self.boot_record["scope"] = "public_firmware_bootstrap"
        self.boot_record["automatic_update"] = dict(
            eligible=True,
            reviewer="Fixture",
            tested_runtime_image="ghcr.io/example/test@sha256:" + "b" * 64,
            checks=self.checks(UPDATE_CHECKS),
        )
        with self.assertRaises(ValueError):
            self.package()

    def test_pinned_archive_is_deterministic_and_verified_after_download(self):
        """Transport hashes and full inventory verification survive a staged download."""
        digest = self.package()
        pins = dict(inventory_sha256=digest, source_commit="a" * 40, version="0.12")
        first, second = self.root / "one.tar.gz", self.root / "two.tar.gz"
        sha = archive(self.root / "output", first, **pins)
        self.assertEqual(archive(self.root / "output", second, **pins), sha)
        pin = dict(
            pins,
            archive_url="https://example.invalid/firmware.tar.gz",
            archive_sha256=sha,
        )
        with patch("firmware_release.urllib.request.urlopen") as fetch:
            response = fetch.return_value.__enter__.return_value
            response.read.return_value = first.read_bytes()
            response.geturl.return_value = pin["archive_url"]
            self.assertIn("artifacts/ti-boot.bin", fetched_files(pin))
            with self.assertRaises(ValueError):
                fetched_files(dict(pin, archive_sha256="0" * 64))
            response.geturl.return_value = "http://example.invalid/firmware.tar.gz"
            with self.assertRaises(ValueError):
                fetched_files(pin)

    def test_even_hash_pinned_archives_cannot_escape_or_link(self):
        """A compromised reviewed transport cannot write outside its temporary root."""
        for name, member_type in (
            ("../escape", tarfile.REGTYPE),
            ("evidence/link", tarfile.SYMTYPE),
        ):
            raw = io.BytesIO()
            with tarfile.open(fileobj=raw, mode="w:gz") as bundle:
                info = tarfile.TarInfo(name)
                info.type, info.size = member_type, 0
                info.linkname = "../../escape" if member_type == tarfile.SYMTYPE else ""
                bundle.addfile(info)
            pin = dict(
                archive_url="https://example.invalid/firmware.tar.gz",
                archive_sha256=hashlib.sha256(raw.getvalue()).hexdigest(),
            )
            with patch("firmware_release.urllib.request.urlopen") as fetch:
                response = fetch.return_value.__enter__.return_value
                response.read.return_value = raw.getvalue()
                response.geturl.return_value = pin["archive_url"]
                with self.assertRaises(ValueError):
                    fetched_files(pin)
            self.assertFalse((self.root / "escape").exists())

    def test_next_distribution_cannot_silently_omit_firmware(self):
        """The first version after the software-only baseline needs an all-board pin."""
        source = source_fixture(self.root / "policy-source")
        metadata = source / "host/release/version.json"
        value = json.loads(metadata.read_text())
        value["distribution_version"] = "0.5.0"
        metadata.write_text(json.dumps(value))
        (source / "host/release/firmware-policy.json").write_text(
            json.dumps(dict(schema=1, require_firmware_after="0.5.0"))
        )
        self.assertIsNone(stage_for_source(source))
        for version in ("0.5.1", "0.6.0", "1.0.0"):
            value["distribution_version"] = version
            metadata.write_text(json.dumps(value))
            with (
                self.subTest(version=version),
                self.assertRaisesRegex(ValueError, "requires all-board"),
            ):
                stage_for_source(source)
            self.assertIsNone(stage_for_source(source, candidate=True))
        (source / "host/release/firmware.json").write_text("{}")
        with self.assertRaisesRegex(ValueError, "Unsupported firmware release pin"):
            stage_for_source(source, candidate=True)
        digest = self.package()
        pin = dict(
            schema=1,
            inventory_sha256="0" * 64,
            source_commit="a" * 40,
            version="0.13",
            archive_url="https://example.invalid/firmware.tar.gz",
            archive_sha256="c" * 64,
        )
        (source / "host/release/firmware.json").write_text(json.dumps(pin))
        with self.assertRaisesRegex(ValueError, "inventory differs"):
            stage_for_source(source, self.root / "output", candidate=True)

    def test_signing_a_mismatched_application_key_does_not_qualify_it(self):
        """A valid package signature cannot hide a different embedded runtime key."""
        item = self.spec["artifacts"][0]
        path = self.root / item["artifact"]["path"]
        original = path.read_bytes()[192:]
        for change in ("wrong", "missing", "duplicate"):
            image = bytearray(original)
            if change == "duplicate":
                image[340:372] = image[300:332]
            else:
                image[300:332] = (b"x" if change == "wrong" else b"\0") * 32
            header = HEADER.pack(
                b"ECUPD01\0",
                FORMAT,
                BOARD,
                0,
                12,
                3,
                0x8000,
                len(image),
                0,
                hashlib.sha512(image).digest(),
                bytes(24),
            )
            payload = header + self.signing.sign(header) + image
            item["artifact"] = self.file(item["artifact"]["path"], payload)
            self.records["codm-cc2652p"]["artifact_sha256"] = item["artifact"]["sha256"]
            with (
                self.subTest(change=change),
                self.assertRaisesRegex(ValueError, "embed the selected release key"),
            ):
                self.package()

    def test_installed_catalog_cannot_add_unqualified_entries(self):
        """A correct public bundle cannot conceal a different installed wheel catalog."""
        digest = self.package()
        root = self.root / "output"
        installed = self.root / "installed"
        shutil.copytree(root / "catalog", installed)
        pins = dict(inventory_sha256=digest, source_commit="a" * 40, version="0.12")
        verify_installed(root, installed, **pins)
        (installed / "manifest.json").write_text("{}")
        with self.assertRaises(ValueError):
            verify_installed(root, installed, **pins)


if __name__ == "__main__":
    unittest.main()
