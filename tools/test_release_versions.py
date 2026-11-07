"""Reject mixed version domains before packaging or partial propagation can occur."""

import json
from pathlib import Path
import tempfile
import unittest

from project_version import synchronize
from release_versions import normalize_metadata, source_versions


def source_fixture(root):
    """Create minimal divergent-version source with every propagation target.

    @param root Temporary directory receiving only synthetic version metadata.
    @return Fixture root path.
    """
    files = {
        "VERSION": "0.13\n",
        "host/pyproject.toml": '[project]\nversion = "0.14.0"\n',
        "host/livolod/__init__.py": '__version__ = "0.14.0"\nFIRMWARE_COMPAT_VERSION = "0.13"\n',
        "firmware/version.h": "#define EC_VERSION_MAJOR 0\n#define EC_VERSION_MINOR 13\n",
        "host/livolod/firmware/manifest.json": json.dumps(
            {"schema": 1, "host_version": "0.13", "artifacts": []}
        ),
        "host/release/version.json": json.dumps(
            {
                "schema": 2,
                "distribution_version": "0.4.0",
                "daemon_version": "0.14.0",
                "firmware_compat_version": "0.13",
                "client_version": "0.4.0",
            }
        ),
    }
    for name, text in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    return root


class VersionDomainsTests(unittest.TestCase):
    """Exercise failure boundaries using unequal software and radio identities."""

    def test_legacy_requires_two_components_and_matching_compatibility(self):
        """Only the old unambiguous equal-version contract may omit compatibility."""
        self.assertEqual(
            normalize_metadata({"daemon_version": "0.13"})["firmware_compat_version"],
            "0.13",
        )
        for data in (
            {"daemon_version": "0.14.0"},
            {"schema": 2, "daemon_version": "0.14.0"},
            {"schema": 1, "daemon_version": "0.13", "firmware_compat_version": "0.12"},
            {"schema": True, "daemon_version": "0.13"},
        ):
            with self.subTest(data=data), self.assertRaises(ValueError):
                normalize_metadata(data)

    def test_divergent_source_is_valid(self):
        """Package 0.14.0 admits source/catalog compatibility 0.13 without conflation."""
        with tempfile.TemporaryDirectory() as directory:
            root = source_fixture(Path(directory))
            values = source_versions(root)
            self.assertEqual(values["daemon_version"], "0.14.0")
            self.assertEqual(values["firmware_compat_version"], "0.13")
            self.assertEqual(synchronize(root)["daemon_version"], "0.14.0")

    def test_catalog_and_header_drift_fail_source_preparation(self):
        """A valid package identity cannot hide an incompatible bundled catalog/header."""
        changes = {
            "host/livolod/firmware/manifest.json": json.dumps(
                {"schema": 1, "host_version": "0.14", "artifacts": []}
            ),
            "firmware/version.h": "#define EC_VERSION_MAJOR 0\n#define EC_VERSION_MINOR 14\n",
            "host/livolod/__init__.py": '__version__ = "0.14.0"\n',
        }
        for name, text in changes.items():
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                root = source_fixture(Path(directory))
                (root / name).write_text(text)
                with self.assertRaises(ValueError):
                    source_versions(root)

    def test_software_propagation_preserves_radio_bytes(self):
        """A software bump cannot change firmware headers, root version or catalog."""
        with tempfile.TemporaryDirectory() as directory:
            root = source_fixture(Path(directory))
            path = root / "host/release/version.json"
            metadata = json.loads(path.read_text())
            metadata["daemon_version"] = "0.15.0"
            path.write_text(json.dumps(metadata))
            names = (
                "VERSION",
                "firmware/version.h",
                "host/livolod/firmware/manifest.json",
            )
            before = {name: (root / name).read_bytes() for name in names}
            synchronize(root, "software")
            self.assertEqual(source_versions(root)["daemon_version"], "0.15.0")
            self.assertEqual(
                before, {name: (root / name).read_bytes() for name in names}
            )

    def test_invalid_later_rule_prevents_all_writes(self):
        """An invalid firmware declaration must not leave a partially bumped package."""
        with tempfile.TemporaryDirectory() as directory:
            root = source_fixture(Path(directory))
            path = root / "host/pyproject.toml"
            path.write_text('[project]\nversion = "0.13"\n')
            before = path.read_bytes()
            (root / "firmware/version.h").write_text("invalid header\n")
            with self.assertRaises(ValueError):
                synchronize(root, "software")
            self.assertEqual(path.read_bytes(), before)

    def test_radio_relabel_of_nonempty_catalog_is_rejected_before_writes(self):
        """A radio bump cannot relabel payloads or partially change runtime/header state."""
        with tempfile.TemporaryDirectory() as directory:
            root = source_fixture(Path(directory))
            (root / "VERSION").write_text("0.14\n")
            (root / "host/livolod/firmware/manifest.json").write_text(
                json.dumps(
                    {
                        "schema": 1,
                        "host_version": "0.13",
                        "artifacts": [{"id": "qualified-old"}],
                    }
                )
            )
            paths = [path for path in root.rglob("*") if path.is_file()]
            before = {path: path.read_bytes() for path in paths}
            with self.assertRaisesRegex(ValueError, "Refusing to relabel"):
                synchronize(root, "radio")
            self.assertEqual(before, {path: path.read_bytes() for path in paths})


if __name__ == "__main__":
    unittest.main()
