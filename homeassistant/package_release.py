#!/usr/bin/env python3
"""Build and inspect HA release assets from an exact, clean source checkout."""

import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import tarfile
import tempfile
import zipfile


def package(source, version, commit, output):
    """Assemble installable integration and app sources with provenance and hashes.

    @param source Clean checkout or archive of the reviewed release commit.
    @param version Exact HA integration version, independent of daemon/app versions.
    @param commit Full source commit identity recorded with the release assets.
    @param output New directory receiving only inspected release assets.
    @return Paths of the generated assets, including SHA256SUMS.
    """
    if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", version):
        raise ValueError("Expected a three-component HA version")
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise ValueError("Expected a full source commit")
    source = Path(source).resolve()
    manifest = json.loads(
        (source / "custom_components/livolo_ec/manifest.json").read_text()
    )
    if manifest["version"] != version:
        raise ValueError("Release version does not match the integration")
    catalog = source / "host/livolod/firmware/manifest.json"
    if catalog.exists() and json.loads(catalog.read_text())["artifacts"]:
        raise ValueError("This HA-only publication does not distribute radio payloads")
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    with tempfile.TemporaryDirectory() as temporary:
        staged = Path(temporary) / "staged"
        subprocess.run(
            [
                sys.executable,
                str(source / "homeassistant/build_release.py"),
                "--app",
                "--output",
                str(staged),
            ],
            check=True,
            cwd=source,
        )
        archive = staged / f"livolo-ec-ha-{version}.zip"
        prefix = "custom_components/livolo_ec/"
        with zipfile.ZipFile(archive) as bundle:
            if bundle.testzip() is not None:
                raise ValueError("Integration ZIP is corrupt")
            if any(
                not name.startswith(prefix) or ".." in Path(name).parts
                for name in bundle.namelist()
            ):
                raise ValueError("Unexpected integration archive layout")
            if json.loads(bundle.read(prefix + "manifest.json"))["version"] != version:
                raise ValueError("Packaged integration version mismatch")
            if bundle.read(prefix + "LICENSE") != (source / "LICENSE").read_bytes():
                raise ValueError("Packaged BSD licence mismatch")
            if not bundle.read(prefix + "_client/__init__.py"):
                raise ValueError("Bundled client is missing")
        target = output / archive.name
        target.write_bytes(archive.read_bytes())
        app = staged / "ha-addons/livolo_ec"
        app_version = re.search(
            r'^version:\s*["\']?([^"\'\s]+)',
            (app / "config.yaml").read_text(),
            re.MULTILINE,
        ).group(1)
        app_archive = output / f"livolo-ec-ha-app-{app_version}-source.tar.gz"
        with tarfile.open(app_archive, "w:gz") as bundle:
            bundle.add(app, arcname="livolo_ec")
        metadata = {
            "integration_version": version,
            "app_version": app_version,
            "daemon_version": (source / "VERSION").read_text().strip(),
            "source_commit": commit,
            "tag": "ha-" + version,
            "integration_install": "Manual ZIP; extract custom_components into HA config",
            "app_delivery": "Source build context for aarch64; not a prebuilt image",
            "radio_firmware_payloads": False,
            "qualified_ha_core": "2026.10.4",
        }
        metadata_path = output / "release.json"
        metadata_path.write_text(json.dumps(metadata, indent=2) + "\n")
    assets = sorted(output.iterdir())
    checksums = output / "SHA256SUMS"
    checksums.write_text(
        "".join(
            f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}\n"
            for path in assets
        )
    )
    return assets + [checksums]


def main():
    """Build inspected release archives without creating tags or publishing anything."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    for path in package(args.source, args.version, args.commit, args.output):
        print(path)


if __name__ == "__main__":
    main()
