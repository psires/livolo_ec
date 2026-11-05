#!/usr/bin/env python3
"""Validate the generated integration and package a component-root HACS ZIP."""

import argparse
import hashlib
import json
from pathlib import Path
import re
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def build(root, tag=None):
    """Check pinned provenance and package only the fully assembled integration.

    @param root Generated repository root.
    @param tag Optional published tag; it must equal the component version exactly.
    @return Created ZIP path.
    """
    root = Path(root)
    provenance = json.loads((root / "source.json").read_text())
    component = root / "custom_components/livolo_ec"
    manifest = json.loads((component / "manifest.json").read_text())
    version = manifest["version"]
    if provenance.get("schema") != 1 or provenance.get("preview") is not False:
        raise ValueError("Only a pinned, non-preview source export may be released")
    if not re.fullmatch(r"[0-9a-f]{40}", provenance["source_commit"]):
        raise ValueError("Source commit must be pinned")
    if not re.fullmatch(r"[0-9a-f]{40}", provenance["packaging_commit"]):
        raise ValueError("Packaging commit must be pinned")
    for relative, digest in provenance["distribution_files"].items():
        path = Path(relative)
        if path.is_absolute() or ".." in path.parts:
            raise ValueError("Invalid distribution path")
        if hashlib.sha256((root / path).read_bytes()).hexdigest() != digest:
            raise ValueError("Distribution recipe differs from its reviewed export")
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise ValueError("Integration version must use ordinary SemVer")
    if provenance["integration_version"] != version or (
        tag is not None and tag != version
    ):
        raise ValueError("Release tag, provenance and manifest version must agree")
    files = {
        str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(component.rglob("*"))
        if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc"
    }
    if files != provenance["files"]:
        raise ValueError("Component differs from its reviewed export")
    for relative in ("__init__.py", "_client/__init__.py", "LICENSE", "brand/icon.png"):
        if not (component / relative).is_file():
            raise ValueError("Incomplete component: " + relative)
    if (component / "LICENSE").read_bytes() != (root / "LICENSE").read_bytes():
        raise ValueError("Bundled license mismatch")
    hacs = json.loads((root / "hacs.json").read_text())
    if hacs.get("filename") != "livolo_ec.zip" or hacs.get("zip_release") is not True:
        raise ValueError("Channel must install the assembled ZIP")
    output = root / "dist"
    output.mkdir(exist_ok=True)
    archive = output / "livolo_ec.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as bundle:
        for relative in sorted(files):
            path = root / relative
            info = zipfile.ZipInfo(
                str(path.relative_to(component)), (2026, 1, 1, 0, 0, 0)
            )
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            bundle.writestr(info, path.read_bytes())
    (output / "source.json").write_text(json.dumps(provenance, indent=2) + "\n")
    (output / "SHA256SUMS").write_text(
        "".join(
            f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}\n"
            for path in (archive, output / "source.json")
        )
    )
    return archive


def main():
    """Build the immutable release candidate, optionally checking a workflow tag."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tag")
    args = parser.parse_args()
    print(build(ROOT, args.tag))


if __name__ == "__main__":
    main()
