#!/usr/bin/env python3
"""Regression checks for immutable publication and historical source assembly."""

import copy
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

import container_prepare as prepare
import container_registry as registry
import container_sources as sources


class ReleaseChecks(unittest.TestCase):
    """Exercise release identity and fail-closed promotion without a registry."""

    def test_historical_contexts_preserve_code_and_record_overlay(self):
        """Both frozen sources must assemble without copying current application code."""
        data = prepare.manifest(prepare.ROOT / "host/docker/releases.json")
        with tempfile.TemporaryDirectory() as directory:
            for entry in data["releases"]:
                output = Path(directory) / (entry["kind"] + entry["version"])
                metadata = prepare.prepare(data, entry, output)
                for file in ("host/livolod/service.py", "host/pyproject.toml"):
                    expected = prepare.command(
                        "git", "show", entry["source_commit"] + ":" + file
                    )
                    self.assertEqual((output / file).read_text().strip(), expected)
                recipe = (output / "Dockerfile").read_text()
                self.assertIn(data["frontend_image"], recipe)
                self.assertIn("RUN python /src/container_sources.py", recipe)
                self.assertEqual(metadata["source_commit"], entry["source_commit"])
                self.assertEqual(
                    metadata["generated_dockerfile_sha256"],
                    hashlib.sha256(recipe.encode()).hexdigest(),
                )
                self.assertFalse(any(output.rglob("*.sqlite3")))
                if entry["kind"] == "ha":
                    self.assertIn(
                        'ENTRYPOINT ["python", "/usr/local/lib/livolo-app.py"]', recipe
                    )
                    self.assertEqual(
                        recipe.splitlines()[-1],
                        "RUN dpkg-query -W > /usr/share/livolo-ec/debian-packages.txt",
                    )
                else:
                    self.assertEqual(recipe.splitlines()[-1], "USER 10001:10001")

    def test_manifest_rejects_unpinned_or_foreign_inputs(self):
        """Malformed requests must fail before becoming workflow arguments."""
        original = prepare.manifest(prepare.ROOT / "host/docker/releases.json")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "request.json"
            for field, value in [
                ("image", "ghcr.io/other/project"),
                ("source_commit", "main"),
                ("previous_image", "ghcr.io/psires/livolod:latest"),
            ]:
                data = copy.deepcopy(original)
                data["releases"][0][field] = value
                path.write_text(json.dumps(data))
                with self.assertRaises(AssertionError):
                    prepare.manifest(path)

    def test_existing_version_cannot_be_replaced(self):
        """A different candidate never overwrites an existing immutable version."""
        one, two = "sha256:" + "1" * 64, "sha256:" + "2" * 64
        with (
            patch.object(registry, "digest", side_effect=[one, two]),
            patch.object(registry, "anonymous"),
            patch.object(registry.subprocess, "run") as run,
        ):
            with self.assertRaises(RuntimeError):
                registry.promote("ghcr.io/psires/livolod", "0.2.0", one, "/unused")
            run.assert_not_called()

    def test_visibility_failure_cannot_create_release_tag(self):
        """A private candidate must remain unpublished as a full version."""
        one = "sha256:" + "1" * 64
        with (
            patch.object(registry, "digest", return_value=one),
            patch.object(registry, "anonymous", side_effect=RuntimeError("private")),
            patch.object(registry.subprocess, "run") as run,
        ):
            with self.assertRaises(RuntimeError):
                registry.promote("ghcr.io/psires/livolod", "0.2.0", one, "/unused")
            run.assert_not_called()

    def test_promote_preserves_tested_digest(self):
        """Promotion copies the tested index and verifies both public references."""
        one = "sha256:" + "1" * 64
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.object(registry, "digest", side_effect=[one, None, one]),
            patch.object(registry, "anonymous") as anonymous,
            patch.object(registry.subprocess, "run") as run,
        ):
            path = Path(directory) / "published.json"
            registry.promote("ghcr.io/psires/livolod", "0.2.0", one, path)
            self.assertEqual(run.call_args.args[0][-1], "ghcr.io/psires/livolod@" + one)
            self.assertEqual(anonymous.call_count, 2)
            self.assertEqual(json.loads(path.read_text())["digest"], one)

    def test_source_inventory_rejects_corrupt_download(self):
        """A source hash mismatch must prevent the image build from continuing."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with zipfile.ZipFile(root / "example-1.0-py3-none-any.whl", "w") as wheel:
                wheel.writestr(
                    "example-1.0.dist-info/METADATA",
                    "Name: example\nVersion: 1.0\nLicense: MIT\n",
                )
            metadata = {
                "urls": [
                    {
                        "packagetype": "sdist",
                        "filename": "example-1.0.tar.gz",
                        "url": "https://files.pythonhosted.org/example-1.0.tar.gz",
                        "digests": {"sha256": "0" * 64},
                    }
                ]
            }
            with patch.object(
                sources.urllib.request,
                "urlopen",
                side_effect=[
                    io.BytesIO(json.dumps(metadata).encode()),
                    io.BytesIO(b"corrupt"),
                ],
            ):
                with self.assertRaisesRegex(ValueError, "Source digest mismatch"):
                    sources.collect(root, root / "sources")


if __name__ == "__main__":
    unittest.main()
