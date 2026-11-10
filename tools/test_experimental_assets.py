"""Offline checks for immutable experimental supporting-asset assembly."""

import io
import json
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest.mock import patch
import zipfile

import experimental_assets as assets


class AssetTests(unittest.TestCase):
    """Protect source binding, archive contents and source-download integrity."""

    def fixture(self):
        """Create a minimal git-archive substitute containing both client components."""
        stream = io.BytesIO()
        with tarfile.open(fileobj=stream, mode="w") as archive:
            for name in (
                "LICENSE",
                "custom_components/livolo_ec/a.py",
                "client/livolo_ec_client/b.py",
            ):
                data = name.encode()
                entry = tarfile.TarInfo(name)
                entry.size = len(data)
                archive.addfile(entry, io.BytesIO(data))
        return stream.getvalue()

    def build(self, path, *, downloaded=b"source"):
        """Assemble fixture assets with isolated, checksum-bound network responses.

        @param path New output directory.
        @param downloaded Bytes returned by the mocked source download.
        @return Assembler manifest.
        """
        digest = assets.sha256(b"source")
        metadata = dict(
            urls=[
                dict(
                    packagetype="sdist",
                    digests=dict(sha256=digest),
                    url="https://files.pythonhosted.org/source.tar.gz",
                )
            ]
        )
        with (
            patch.object(
                assets.subprocess, "check_output", return_value=self.fixture()
            ) as git,
            patch.object(assets, "FLASHER_SHA", digest),
            patch.object(
                assets.urllib.request,
                "urlopen",
                side_effect=[
                    io.BytesIO(json.dumps(metadata).encode()),
                    io.BytesIO(downloaded),
                ],
            ),
        ):
            result = assets.assemble(path)
        git.assert_called_once_with(["git", "archive", "--format=tar", assets.SOURCE])
        return result

    def test_members_and_repeatable_assets(self):
        """Keep both installable components and repeatable checksums in complete bundles."""
        with tempfile.TemporaryDirectory() as temporary:
            first, second = (Path(temporary) / name for name in ("a", "b"))
            result = self.build(first)
            self.build(second)
            self.assertEqual(result["source_commit"], assets.SOURCE)
            self.assertEqual(len(result["integration_members"]), 3)
            for path in first.iterdir():
                self.assertEqual(path.read_bytes(), (second / path.name).read_bytes())
            with zipfile.ZipFile(
                first / "livolo-ec-ha-0.6.0-experimental-amd64.zip"
            ) as bundle:
                self.assertEqual(
                    bundle.read("custom_components/livolo_ec/_client/b.py"),
                    b"client/livolo_ec_client/b.py",
                )

    def test_bad_download_cannot_complete(self):
        """Reject mismatched source bytes before generating a publishable manifest."""
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "assets"
            with self.assertRaisesRegex(ValueError, "checksum mismatch"):
                self.build(output, downloaded=b"wrong")
            self.assertFalse((output / "preview-assets.json").exists())

    def test_existing_directory_untouched(self):
        """Refuse to overwrite previously prepared assets."""
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "kept").write_text("original")
            with self.assertRaises(FileExistsError):
                assets.assemble(root)
            self.assertEqual((root / "kept").read_text(), "original")


if __name__ == "__main__":
    unittest.main()
