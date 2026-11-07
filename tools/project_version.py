#!/usr/bin/env python3
"""Validate software and firmware versions; propagate only an explicitly selected domain."""

import argparse
import json
from pathlib import Path
import re

from release_versions import normalize_metadata, numeric_version

ROOT = Path(__file__).resolve().parents[1]


def synchronize(root, write=None):
    """Validate all version rules before optionally writing one version domain.

    @param root Project source root with canonical radio and software metadata.
    @param write None for validation, or software/radio for explicit propagation.
    @return Explicit version identities; raises ValueError before any writes on invalid inputs.
    """
    root = Path(root)
    if write not in (None, "software", "radio"):
        raise ValueError("Select software or radio propagation")
    compatibility = (root / "VERSION").read_text().strip()
    numeric_version(compatibility, 2)
    metadata_path = root / "host/release/version.json"
    metadata = normalize_metadata(json.loads(metadata_path.read_text()))
    if metadata["schema"] != 2:
        raise ValueError("Independent propagation requires schema 2 release metadata")
    for key in ("distribution_version", "client_version"):
        numeric_version(metadata[key], 3)
    daemon = metadata["daemon_version"]
    major, minor = compatibility.split(".")
    rules = [
        (
            "software",
            "host/pyproject.toml",
            r'(?m)^version = "[^"]+"$',
            f'version = "{daemon}"',
        ),
        (
            "software",
            "host/livolod/__init__.py",
            r'(?m)^__version__ = "[^"]+"$',
            f'__version__ = "{daemon}"',
        ),
        (
            "radio",
            "host/livolod/__init__.py",
            r'(?m)^FIRMWARE_COMPAT_VERSION = "[^"]+"$',
            f'FIRMWARE_COMPAT_VERSION = "{compatibility}"',
        ),
        (
            "radio",
            "firmware/version.h",
            r"(?m)^#define EC_VERSION_MAJOR [0-9]+$",
            f"#define EC_VERSION_MAJOR {major}",
        ),
        (
            "radio",
            "firmware/version.h",
            r"(?m)^#define EC_VERSION_MINOR [0-9]+$",
            f"#define EC_VERSION_MINOR {minor}",
        ),
    ]
    pending = {}
    for domain, name, pattern, wanted in rules:
        path = root / name
        source = pending.get(path, path.read_text())
        if len(re.findall(pattern, source)) != 1:
            raise ValueError(f"{name}: missing/duplicate version declaration")
        updated = re.sub(pattern, lambda _: wanted, source)
        if updated != source:
            if write != domain:
                raise ValueError(
                    f"{name}: {domain} version drift; select --write {domain}"
                )
            pending[path] = updated
    if metadata["firmware_compat_version"] != compatibility:
        if write != "radio":
            raise ValueError("Release metadata firmware compatibility drift")
        metadata["firmware_compat_version"] = compatibility
        pending[metadata_path] = json.dumps(metadata, indent=2) + "\n"
    bundle_path = root / "host/livolod/firmware/manifest.json"
    bundle = json.loads(bundle_path.read_text())
    if bundle.get("host_version") != compatibility:
        if write != "radio":
            raise ValueError("Firmware bundle version drift")
        if bundle.get("artifacts") != []:
            raise ValueError(
                "Refusing to relabel firmware artifacts; rebuild and qualify a new bundle"
            )
        bundle["host_version"] = compatibility
        pending[bundle_path] = json.dumps(bundle, indent=2) + "\n"
    # All structural and cross-domain checks complete before the first mutation.
    for path, content in pending.items():
        path.write_text(content)
    return {
        "daemon_version": daemon,
        "firmware_compat_version": compatibility,
        "distribution_version": metadata["distribution_version"],
    }


def main():
    """Check both version domains or explicitly propagate one canonical identity."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", choices=("software", "radio"))
    args = parser.parse_args()
    try:
        print(json.dumps(synchronize(ROOT, args.write), sort_keys=True))
    except (ValueError, KeyError) as error:
        raise SystemExit(str(error)) from error


if __name__ == "__main__":
    main()
