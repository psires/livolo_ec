#!/usr/bin/env python3
"""Assemble publicly downloadable support assets for the frozen AMD64 preview."""

import argparse
import gzip
import hashlib
import io
import json
from pathlib import Path
import subprocess
import tarfile
import tempfile
import urllib.request
import zipfile

SOURCE = "81cf6fb4628987cdd9ca2ec7637e1535d9feecbe"
FLASHER_SHA = "7f7a4a744273277430cbff19a1c7522372b434ab147adcbc821395566c3a6a14"


def sha256(data):
    """Identify exact asset bytes for publication and anonymous verification.

    @param data Complete asset bytes.
    @return Lowercase SHA-256 digest.
    """
    return hashlib.sha256(data).hexdigest()


def assemble(output):
    """Build only the reviewed source revision, independent of dirty workspace files.

    @param output New directory for immutable preview assets.
    @return Asset hashes, including integration member hashes for independent review.
    """
    output.mkdir(parents=True, exist_ok=False)
    archive = subprocess.check_output(["git", "archive", "--format=tar", SOURCE])
    (output / "livolo-ec-81cf6fb-source.tar.gz").write_bytes(
        gzip.compress(archive, mtime=0)
    )
    members = {}
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        with tarfile.open(fileobj=io.BytesIO(archive)) as source:
            source.extractall(root, filter="data")
        with zipfile.ZipFile(
            output / "livolo-ec-ha-0.6.0-experimental-amd64.zip", "w"
        ) as bundle:
            sources = [
                (root / "custom_components/livolo_ec", "custom_components/livolo_ec"),
                (
                    root / "client/livolo_ec_client",
                    "custom_components/livolo_ec/_client",
                ),
            ]
            entries = {"custom_components/livolo_ec/LICENSE": root / "LICENSE"}
            for directory, prefix in sources:
                for path in directory.rglob("*"):
                    if (
                        path.is_file()
                        and "__pycache__" not in path.parts
                        and path.suffix != ".pyc"
                    ):
                        entries[
                            prefix + "/" + path.relative_to(directory).as_posix()
                        ] = path
            for name, path in sorted(entries.items()):
                data = path.read_bytes()
                info = zipfile.ZipInfo(name, date_time=(2026, 11, 8, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = 0o100644 << 16
                bundle.writestr(info, data)
                members[name] = sha256(data)
    with urllib.request.urlopen(
        "https://pypi.org/pypi/universal-silabs-flasher/0.0.31/json", timeout=30
    ) as response:
        metadata = json.load(response)
    candidates = [
        item
        for item in metadata["urls"]
        if item["packagetype"] == "sdist" and item["digests"]["sha256"] == FLASHER_SHA
    ]
    if len(candidates) != 1 or not candidates[0]["url"].startswith(
        "https://files.pythonhosted.org/"
    ):
        raise ValueError("Pinned flasher source unavailable")
    with urllib.request.urlopen(candidates[0]["url"], timeout=30) as response:
        flasher = response.read()
    if sha256(flasher) != FLASHER_SHA:
        raise ValueError("Flasher source checksum mismatch")
    (output / "universal_silabs_flasher-0.0.31.tar.gz").write_bytes(flasher)
    hashes = {path.name: sha256(path.read_bytes()) for path in sorted(output.iterdir())}
    manifest = dict(source_commit=SOURCE, assets=hashes, integration_members=members)
    (output / "preview-assets.json").write_text(json.dumps(manifest, indent=2) + "\n")
    hashes["preview-assets.json"] = sha256(
        (output / "preview-assets.json").read_bytes()
    )
    (output / "SHA256SUMS").write_text(
        "".join(f"{digest}  {name}\n" for name, digest in sorted(hashes.items()))
    )
    return manifest


def main():
    """Create support assets without publishing or changing any runtime."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    assemble(args.output)


if __name__ == "__main__":
    main()
