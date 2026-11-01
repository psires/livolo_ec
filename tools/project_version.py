#!/usr/bin/env python3
"""Check or propagate root VERSION (major.minor) into release build metadata."""
import argparse
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]


def main():
    """Check release-version consistency or propagate the root VERSION on request."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    release = (ROOT / "VERSION").read_text().strip()
    if (
        not re.fullmatch(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)", release)
        or max(map(int, release.split("."))) > 65535
    ):
        raise SystemExit("VERSION must be major.minor, each component <=65535")
    major, minor = release.split(".")
    bundle_path = ROOT / "host/livolod/firmware/manifest.json"
    bundle = json.loads(bundle_path.read_text())
    if bundle.get("host_version") != release:
        if not args.write:
            raise SystemExit("Firmware bundle version drift; rebuild for this release")
        if bundle.get("artifacts") != []:
            raise SystemExit(
                "Refusing to relabel firmware artifacts; build and qualify a new bundle"
            )
        bundle["host_version"] = release
        bundle_path.write_text(json.dumps(bundle, indent=2) + "\n")
    rules = [
        ("host/pyproject.toml", r'(?m)^version = "[^"]+"', f'version = "{release}"'),
        (
            "host/livolod/__init__.py",
            r'__version__ = "[^"]+"',
            f'__version__ = "{release}"',
        ),
        (
            "firmware/version.h",
            r"#define EC_VERSION_MAJOR [0-9]+",
            f"#define EC_VERSION_MAJOR {major}",
        ),
        (
            "firmware/version.h",
            r"#define EC_VERSION_MINOR [0-9]+",
            f"#define EC_VERSION_MINOR {minor}",
        ),
    ]
    for name, pattern, wanted in rules:
        path = ROOT / name
        source = path.read_text()
        if len(re.findall(pattern, source)) != 1:
            raise SystemExit(f"{name}: missing/duplicate version")
        updated = re.sub(pattern, lambda _: wanted, source)
        if updated != source:
            if not args.write:
                raise SystemExit(f"{name}: version drift; run --write")
            path.write_text(updated)
    print(release)


if __name__ == "__main__":
    main()
