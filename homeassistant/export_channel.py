#!/usr/bin/env python3
"""Export an installable integration repository from an exact source revision."""

import argparse
import hashlib
import io
import json
from pathlib import Path
import re
import shutil
import subprocess
import tarfile
import tempfile

ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = ROOT / "homeassistant/channel"


def export(commit, version, output, preview=False):
    """Assemble the client and integration without publishing or modifying Git.

    @param commit Full source commit identity, verified against the local repository.
    @param version Expected integration version, matching the selected source manifest.
    @param output New directory for the generated distribution repository.
    @param preview Copy working files for review only; release builder rejects these exports.
    @return Generated repository directory.
    """
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise ValueError("Use a full source commit ID")
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise ValueError("Use a three-component integration version")
    resolved = subprocess.check_output(
        ["git", "rev-parse", commit + "^{commit}"], cwd=ROOT, text=True
    ).strip()
    if resolved != commit:
        raise ValueError("Source commit mismatch")
    packaging_commit = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
    ).strip()
    packaging_dirty = bool(
        subprocess.check_output(
            [
                "git",
                "status",
                "--porcelain",
                "--",
                "homeassistant/channel",
                "homeassistant/export_channel.py",
                "custom_components/livolo_ec/brand",
            ],
            cwd=ROOT,
        )
    )
    output = Path(output).resolve()
    if output.exists():
        raise ValueError("Export directory already exists")
    with tempfile.TemporaryDirectory() as temporary:
        source = Path(temporary)
        paths = ["custom_components/livolo_ec", "client/livolo_ec_client", "LICENSE"]
        if preview:
            for relative in paths:
                origin = ROOT / relative
                target = source / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                if origin.is_dir():
                    shutil.copytree(
                        origin,
                        target,
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
                    )
                else:
                    shutil.copy2(origin, target)
        else:
            archive = subprocess.check_output(
                ["git", "archive", commit, *paths], cwd=ROOT
            )
            with tarfile.open(fileobj=io.BytesIO(archive)) as bundle:
                for member in bundle.getmembers():
                    if member.isdir():
                        continue
                    if (
                        not member.isfile()
                        or Path(member.name).is_absolute()
                        or ".." in Path(member.name).parts
                    ):
                        raise ValueError("Unexpected source archive entry")
                    target = source / member.name
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(bundle.extractfile(member).read())
        component = source / "custom_components/livolo_ec"
        manifest = json.loads((component / "manifest.json").read_text())
        if manifest["version"] != version:
            raise ValueError("Source manifest does not match requested version")
        shutil.copytree(source / "client/livolo_ec_client", component / "_client")
        shutil.copy2(source / "LICENSE", component / "LICENSE")
        # Branding is packaging metadata, also supplied to the historical baseline.
        shutil.copytree(
            ROOT / "custom_components/livolo_ec/brand",
            component / "brand",
            dirs_exist_ok=True,
        )
        output.mkdir(parents=True)
        shutil.copytree(component, output / "custom_components/livolo_ec")
        shutil.copy2(source / "LICENSE", output / "LICENSE")
    shutil.copy2(TEMPLATES / "hacs.json", output / "hacs.json")
    shutil.copy2(TEMPLATES / "README.md", output / "README.md")
    shutil.copytree(
        TEMPLATES / "scripts",
        output / "scripts",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    shutil.copytree(TEMPLATES / "workflows", output / ".github/workflows")
    (output / ".gitignore").write_text("/dist/\n__pycache__/\n*.pyc\n")
    files = {
        str(path.relative_to(output)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted((output / "custom_components").rglob("*"))
        if path.is_file()
    }
    packaging_paths = [ROOT / "homeassistant/export_channel.py"]
    packaging_paths.extend(TEMPLATES.rglob("*"))
    packaging_paths.extend((ROOT / "custom_components/livolo_ec/brand").rglob("*"))
    packaging_files = {
        str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(packaging_paths)
        if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc"
    }
    distribution_files = {
        str(path.relative_to(output)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(output.rglob("*"))
        if path.is_file() and "custom_components" not in path.relative_to(output).parts
    }
    provenance = {
        "schema": 1,
        "integration_version": version,
        "source_repository": "https://github.com/psires/livolo_ec",
        "source_commit": commit,
        "packaging_commit": packaging_commit,
        "packaging_files": packaging_files,
        "distribution_files": distribution_files,
        "preview": preview or packaging_dirty,
        "packaging_note": "Generated _client, BSD license and original project branding included.",
        "files": files,
    }
    (output / "source.json").write_text(json.dumps(provenance, indent=2) + "\n")
    return output


def main():
    """Create a reviewable export; preview exports cannot be released."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--preview", action="store_true")
    args = parser.parse_args()
    print(export(args.commit, args.version, args.output, args.preview))


if __name__ == "__main__":
    main()
