"""Keep pairing instructions tied to the source selected for a native release."""

from pathlib import Path
import tempfile
import unittest

import package_standalone as package


class PairingGuideTests(unittest.TestCase):
    """Exercise guide delivery without building or publishing a release."""

    def test_guide_is_bundled_with_versioned_profile_links(self):
        """The native bundle preserves the guide and resolves links to its release."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, staged = root / "source", root / "bundle"
            (source / "docs").mkdir(parents=True)
            (source / "docs/PAIRING.md").write_text(
                "# Pair a switch\n[Profiles](DEVICE_PROFILES.md)\n"
                "[External](https://example.invalid/guide)\n"
            )
            package.stage_pairing_guide(source, staged, "standalone-0.5.0")
            text = (staged / "docs/PAIRING.md").read_text()
            self.assertIn("# Pair a switch", text)
            self.assertIn(
                "https://github.com/psires/livolo_ec/blob/standalone-0.5.0/"
                "docs/DEVICE_PROFILES.md",
                text,
            )
            self.assertIn("https://example.invalid/guide", text)
            package.checksums(staged)
            self.assertIn("  docs/PAIRING.md\n", (staged / "SHA256SUMS").read_text())

    def test_old_source_does_not_inherit_current_pairing_guide(self):
        """An older source tree lacking the guide must not acquire newer instructions."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, staged = root / "old-source", root / "bundle"
            source.mkdir()
            staged.mkdir()
            package.stage_pairing_guide(source, staged, "standalone-0.4.0")
            self.assertFalse((staged / "docs/PAIRING.md").exists())


if __name__ == "__main__":
    unittest.main()
