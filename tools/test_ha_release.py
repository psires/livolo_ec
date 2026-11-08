"""Check integration-only publication against real builders and historical assets."""

import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tarfile
import tempfile
import textwrap
import unittest
from unittest.mock import patch
import zipfile

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "ha_package_release", ROOT / "homeassistant/package_release.py"
)
RELEASE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(RELEASE)


def export_source(commit, destination):
    """Extract trusted committed regular files for isolated packaging checks.

    @param commit Existing local Git revision whose source must remain unchanged.
    @param destination Empty temporary source directory.
    """
    archive = subprocess.check_output(["git", "archive", commit], cwd=ROOT)
    destination.mkdir()
    with tarfile.open(fileobj=io.BytesIO(archive)) as bundle:
        for member in bundle.getmembers():
            if not member.isfile():
                continue
            path = Path(member.name)
            if path.is_absolute() or ".." in path.parts:
                raise ValueError("Unexpected source archive path")
            target = destination / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(bundle.extractfile(member).read())


class HAReleaseTests(unittest.TestCase):
    """Separate integration delivery from app qualification without weakening either."""

    def test_integration_only_has_exact_payloads_and_honest_metadata(self):
        """The real builder packages the client and license without touching app versions."""
        version = json.loads(
            (ROOT / "custom_components/livolo_ec/manifest.json").read_text()
        )["version"]
        commit = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
        ).strip()
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "assets"
            with (
                patch.object(
                    RELEASE,
                    "source_versions",
                    side_effect=AssertionError("App inspected"),
                ),
                patch.object(
                    RELEASE, "verify", side_effect=AssertionError("Firmware inspected")
                ),
            ):
                assets = RELEASE.package(
                    ROOT, version, commit, output, scope="integration-only"
                )
            self.assertEqual(
                {p.name for p in assets},
                {f"livolo-ec-ha-{version}.zip", "release.json", "SHA256SUMS"},
            )
            metadata = json.loads((output / "release.json").read_text())
            self.assertEqual(metadata["publication_scope"], "integration-only")
            self.assertEqual(metadata["source_commit"], commit)
            for key in ("app_version", "daemon_version", "firmware_compat_version"):
                self.assertIsNone(metadata[key])
            for key in ("app_payloads", "daemon_payloads", "radio_firmware_payloads"):
                self.assertIs(metadata[key], False)
            with zipfile.ZipFile(output / f"livolo-ec-ha-{version}.zip") as bundle:
                prefix = "custom_components/livolo_ec/"
                self.assertEqual(
                    bundle.read(prefix + "LICENSE"), (ROOT / "LICENSE").read_bytes()
                )
                for path in (ROOT / "custom_components/livolo_ec").rglob("*"):
                    if path.is_file() and "__pycache__" not in path.parts:
                        relative = path.relative_to(
                            ROOT / "custom_components/livolo_ec"
                        )
                        self.assertEqual(
                            bundle.read(prefix + str(relative)), path.read_bytes()
                        )
                self.assertEqual(
                    bundle.read(prefix + "_client/__init__.py"),
                    (ROOT / "client/livolo_ec_client/__init__.py").read_bytes(),
                )
                self.assertTrue(
                    all(name.startswith(prefix) for name in bundle.namelist())
                )
            for line in (output / "SHA256SUMS").read_text().splitlines():
                digest, name = line.split("  ", 1)
                self.assertEqual(
                    hashlib.sha256((output / name).read_bytes()).hexdigest(), digest
                )

    def test_invalid_scope_or_firmware_input_creates_no_assets(self):
        """A misspelled scope or firmware injection cannot become a partial publication."""
        for options in (
            {"scope": "integration"},
            {"scope": None},
            {"scope": "integration-only", "firmware_bundle": Path("unused")},
        ):
            with (
                self.subTest(options=options),
                tempfile.TemporaryDirectory() as temporary,
            ):
                output = Path(temporary) / "assets"
                with self.assertRaises(ValueError):
                    RELEASE.package(ROOT, "0.5.2", "a" * 40, output, **options)
                self.assertFalse(output.exists())

    def test_integration_version_mismatch_is_rejected(self):
        """Integration-only scope still refuses to mislabel source bytes."""
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "assets"
            with self.assertRaisesRegex(ValueError, "does not match"):
                RELEASE.package(
                    ROOT, "99.99.99", "a" * 40, output, scope="integration-only"
                )
            self.assertFalse(output.exists())

    def test_default_mode_preserves_app_qualification_gate(self):
        """An unqualified current app cannot be published through the historical default."""
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "source"
            export_source("HEAD", source)
            version = json.loads(
                (source / "custom_components/livolo_ec/manifest.json").read_text()
            )["version"]
            policy = source / "host/release/firmware-policy.json"
            policy.write_text(
                json.dumps({"schema": 1, "require_firmware_after": "0.0.0"})
            )
            (source / "host/release/firmware.json").unlink(missing_ok=True)
            catalog = source / "host/livolod/firmware/manifest.json"
            manifest = json.loads(catalog.read_text())
            manifest["artifacts"] = []
            catalog.write_text(json.dumps(manifest))
            output = Path(temporary) / "assets"
            result = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "homeassistant/package_release.py"),
                    "--source",
                    str(source),
                    "--version",
                    version,
                    "--commit",
                    "a" * 40,
                    "--output",
                    str(output),
                ],
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("requires all-board qualified firmware", result.stderr)
            self.assertFalse((output / "release.json").exists())
            self.assertFalse((output / "SHA256SUMS").exists())

    def test_historical_default_still_builds_app_and_integration(self):
        """An already qualified historical source keeps its combined delivery semantics."""
        commit = subprocess.check_output(
            ["git", "rev-parse", "ha-0.5.1^{commit}"], cwd=ROOT, text=True
        ).strip()
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "source"
            export_source(commit, source)
            output = Path(temporary) / "assets"
            assets = RELEASE.package(source, "0.5.1", commit, output)
            self.assertEqual(
                {p.name for p in assets},
                {
                    "livolo-ec-ha-0.5.1.zip",
                    "livolo-ec-ha-app-0.5.1-source.tar.gz",
                    "release.json",
                    "SHA256SUMS",
                },
            )
            metadata = json.loads((output / "release.json").read_text())
            self.assertEqual(metadata["app_version"], "0.5.1")
            self.assertEqual(metadata["daemon_version"], "0.14.1")
            self.assertEqual(metadata["firmware_compat_version"], "0.13")
            self.assertFalse(metadata["radio_firmware_payloads"])

    def test_actual_workflow_plan_defaults_history_and_rejects_bad_scopes(self):
        """The publication matrix preserves old manifests and fails closed on unknown scope."""
        workflow = (ROOT / ".github/workflows/ha-release.yml").read_text()
        script = textwrap.dedent(
            re.search(r"python - <<'PY'\n(.*?)          PY", workflow, re.S)[1]
        )
        baseline = {
            "version": "0.5.1",
            "tag": "ha-0.5.1",
            "commit": "a" * 40,
            "latest": True,
        }
        for extra, valid in (
            ({}, True),
            ({"scope": "integration-only"}, True),
            ({"scope": "integration-and-app"}, True),
            ({"scope": None}, False),
            ({"scope": "integration"}, False),
            ({"unexpected": True}, False),
        ):
            with self.subTest(extra=extra), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                (root / "homeassistant").mkdir()
                (root / "homeassistant/releases.json").write_text(
                    json.dumps([{**baseline, **extra}])
                )
                result = subprocess.run(
                    [sys.executable, "-c", script],
                    cwd=root,
                    env={**os.environ, "GITHUB_OUTPUT": str(root / "output")},
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(result.returncode == 0, valid, result.stderr)
                if valid:
                    plan = json.loads((root / "output").read_text().split("=", 1)[1])
                    self.assertEqual(
                        plan[0]["scope"], extra.get("scope", "integration-and-app")
                    )
                else:
                    self.assertFalse((root / "output").exists())


if __name__ == "__main__":
    unittest.main()
