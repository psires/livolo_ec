#!/usr/bin/env python3
"""Regression checks for immutable publication and historical source assembly."""

import copy
import hashlib
import io
import json
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import patch
import zipfile

import container_prepare as prepare
import container_registry as registry
import container_sources as sources
import container_probe as probe


class ReleaseChecks(unittest.TestCase):
    """Exercise release identity and fail-closed promotion without a registry."""

    def test_offline_baseline_without_radio_diagnostics(self):
        """An older offline HA image can seed an upgrade without radio-derived metadata."""
        snapshot = {"firmware_update": {}, "diagnostics": {}}
        with (
            patch.object(probe, "request") as request,
            patch.object(probe, "version", return_value="0.12"),
        ):
            self.assertFalse(probe.firmware_preflight("unused", {}, snapshot))
            request.assert_not_called()
        with patch.object(probe, "version", return_value="0.13"):
            with self.assertRaises(AssertionError):
                probe.firmware_preflight("unused", {}, snapshot)

    def test_mqtt_transient_offline_then_retained_recovery(self):
        """A registry-reconcile transition must recover and prove retained online."""
        client = unittest.mock.Mock()
        messages = [
            types.SimpleNamespace(payload=b"offline", retain=True),
            types.SimpleNamespace(payload=b"online", retain=False),
            types.SimpleNamespace(payload=b"online", retain=True),
        ]

        def deliver():
            """Model initial retained offline, live recovery, then subscription replay."""
            for message in messages:
                client.on_message(client, None, message)

        client.loop_start.side_effect = deliver
        paho = types.ModuleType("paho")
        paho.mqtt = types.ModuleType("paho.mqtt")
        paho.mqtt.client = types.ModuleType("paho.mqtt.client")
        paho.mqtt.client.Client = unittest.mock.Mock(return_value=client)
        paho.mqtt.client.CallbackAPIVersion = types.SimpleNamespace(VERSION2=2)
        with patch.dict(
            "sys.modules",
            {
                "paho": paho,
                "paho.mqtt": paho.mqtt,
                "paho.mqtt.client": paho.mqtt.client,
            },
        ):
            probe.mqtt_online()
        client.subscribe.assert_called_once_with(
            "container-test/simulated/bridge/state", 1
        )
        client.disconnect.assert_called_once()

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
        original = json.loads((prepare.ROOT / "host/docker/releases.json").read_text())
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "request.json"
            for field, value in [
                ("image", "ghcr.io/other/project"),
                ("source_commit", "main"),
                ("previous_image", "ghcr.io/psires/livolod:latest"),
            ]:
                path.write_text(json.dumps(original))
                prepare.manifest(path)
                data = copy.deepcopy(original)
                data["releases"][0][field] = value
                path.write_text(json.dumps(data))
                with self.assertRaises(AssertionError):
                    prepare.manifest(path)

    def test_candidate_app_uses_source_version_but_release_rejects_listing_mismatch(
        self,
    ):
        """An independent stable listing cannot relabel a candidate or a historical release."""
        data = prepare.manifest(prepare.ROOT / "host/docker/releases.json")
        entry = next(e for e in reversed(data["releases"]) if e["kind"] == "ha")
        original_versions = prepare.source_versions

        def stable_listing(source):
            """Model an exported checkout whose public app listing trails its source.

            @param source Isolated export used by the real container assembler.
            @return Actual source package and compatibility identities.
            """
            path = source / "homeassistant/app/config.yaml"
            path.write_text(prepare.app_config_version(path.read_text(), "0.0.1"))
            return original_versions(source)

        with (
            tempfile.TemporaryDirectory() as directory,
            patch.object(prepare, "source_versions", side_effect=stable_listing),
        ):
            output = Path(directory) / "candidate"
            metadata = prepare.prepare(data, entry, output, candidate=True)
            self.assertTrue(metadata["candidate"])
            self.assertIn(
                f'version: "{entry["version"]}"', (output / "config.yaml").read_text()
            )
            with self.assertRaises(AssertionError):
                prepare.prepare(data, entry, Path(directory) / "release")

    def test_schema_two_manifest_preserves_explicit_legacy_and_split_entries(self):
        """A new request can retain old identities while requiring new compatibility metadata."""
        original = json.loads((prepare.ROOT / "host/docker/releases.json").read_text())
        original["schema"] = 2
        original["releases"] = [original["releases"][0]]
        legacy = original["releases"][0]
        legacy.update(
            version_schema=1, firmware_compat_version=legacy["daemon_version"]
        )
        current = dict(
            legacy,
            version="0.5.0",
            source_tag="standalone-0.5.0",
            daemon_version="0.14.0",
            firmware_compat_version="0.13",
            version_schema=2,
        )
        original["releases"].append(current)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "request.json"
            path.write_text(json.dumps(original))
            parsed = prepare.manifest(path)
            self.assertEqual(
                parsed["releases"][0]["firmware_compat_version"],
                legacy["daemon_version"],
            )
            self.assertEqual(parsed["releases"][1]["firmware_compat_version"], "0.13")
            del current["firmware_compat_version"]
            path.write_text(json.dumps(original))
            with self.assertRaises(AssertionError):
                prepare.manifest(path)

    def test_probe_rejects_semver_without_explicit_runtime_compatibility(self):
        """New package identities cannot use the legacy package-equals-firmware fallback."""
        runtime = types.ModuleType("livolod")
        runtime.__version__ = "0.14.0"
        with (
            patch.dict("sys.modules", {"livolod": runtime}),
            patch.object(probe, "version", return_value="0.14.0"),
        ):
            with self.assertRaises(AssertionError):
                probe.installed_versions()
            runtime.FIRMWARE_COMPAT_VERSION = "0.13"
            self.assertEqual(
                probe.installed_versions(),
                {"daemon_version": "0.14.0", "firmware_compat_version": "0.13"},
            )

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
