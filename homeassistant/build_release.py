#!/usr/bin/env python3
"""Assemble a self-contained custom integration without unpublished PyPI dependencies."""

import argparse
import json
from pathlib import Path
import shutil
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def stage_integration(destination):
    """Copy integration and independent client into an installable directory.

    @param destination Empty directory receiving custom_components/livolo_ec.
    @return Path of the assembled component.
    """
    target = Path(destination) / "custom_components" / "livolo_ec"
    shutil.copytree(
        ROOT / "custom_components/livolo_ec",
        target,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    shutil.copytree(
        ROOT / "client/livolo_ec_client",
        target / "_client",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    shutil.copy2(ROOT / "LICENSE", target / "LICENSE")
    return target


def main():
    """Create a local installation archive with one canonical client source."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "dist")
    parser.add_argument(
        "--app", action="store_true", help="Also stage a local HA app build context"
    )
    args = parser.parse_args()
    version = json.loads(
        (ROOT / "custom_components/livolo_ec/manifest.json").read_text()
    )["version"]
    args.output.mkdir(parents=True, exist_ok=True)
    archive = args.output / f"livolo-ec-ha-{version}.zip"
    with tempfile.TemporaryDirectory() as temporary:
        stage_integration(temporary)
        with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as bundle:
            for path in sorted(Path(temporary).rglob("*")):
                if path.is_file():
                    bundle.write(path, path.relative_to(temporary))
    print(archive)
    if args.app:
        app = args.output / "ha-addons" / "livolo_ec"
        if app.exists():
            raise SystemExit(
                f"App context already exists; choose a new --output: {app}"
            )
        shutil.copytree(
            ROOT / "homeassistant/app",
            app,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
        )
        for relative in (
            "LICENSE",
            "VERSION",
            "host/pyproject.toml",
            "host/docker/smoke.py",
        ):
            destination = app / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / relative, destination)
        shutil.copy2(ROOT / "LICENSE", app / "host/LICENSE")
        shutil.copytree(
            ROOT / "host/livolod",
            app / "host/livolod",
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
        )
        (app / "Dockerfile").write_text(
            (ROOT / "host/docker/Dockerfile").read_text()
            + (app / "Dockerfile.wrapper").read_text()
        )
        print(app)


if __name__ == "__main__":
    main()
