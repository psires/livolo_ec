#!/usr/bin/env python3
"""Assemble a self-contained custom integration without unpublished PyPI dependencies."""

import argparse
import json
import re
from pathlib import Path
import shutil
import sys
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from firmware_release import stage_for_source
from release_versions import app_config_version, source_versions


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
    parser.add_argument("--firmware-bundle", type=Path)
    parser.add_argument(
        "--candidate",
        action="store_true",
        help="Non-publishing app qualification build; may omit pending public firmware",
    )
    parser.add_argument(
        "--experimental-amd64",
        action="store_true",
        help="Stage an experimental local-build app for 64-bit Intel/AMD HA OS",
    )
    args = parser.parse_args()
    if args.experimental_amd64 and not (args.app and args.candidate):
        parser.error("--experimental-amd64 requires --app and --candidate")
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
        if args.candidate:
            config = app / "config.yaml"
            config.write_text(
                app_config_version(
                    config.read_text(), source_versions(ROOT)["distribution_version"]
                )
            )
            if args.experimental_amd64:
                text = config.read_text()
                image_line = "image: ghcr.io/psires/livolo-ec-ha-app\n"
                arch_line = "arch:\n  - aarch64\n"
                if text.count(image_line) != 1 or text.count(arch_line) != 1:
                    raise ValueError("Unexpected public app configuration")
                config.write_text(
                    text.replace(image_line, "").replace(
                        arch_line, arch_line + "  - amd64\n"
                    )
                )
        for relative in (
            "LICENSE",
            "VERSION",
            "host/pyproject.toml",
            "host/PACKAGE.md",
            "host/docker/smoke.py",
            "host/docker/requirements-firmware.txt",
            "host/docker/requirements-nordic.txt",
            "host/docker/requirements-nordic-build.txt",
            "host/docker/build_nordic.py",
            "tools/container_sources.py",
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
        if (ROOT / "host/release/version.json").exists():
            (app / "host/release").mkdir(parents=True, exist_ok=True)
            for name in ("version.json", "firmware.json", "firmware-policy.json"):
                origin = ROOT / "host/release" / name
                if origin.exists():
                    shutil.copy2(origin, app / "host/release" / name)
        firmware = stage_for_source(app, args.firmware_bundle, candidate=args.candidate)
        recipe = (ROOT / "host/docker/Dockerfile").read_text()
        versions = source_versions(ROOT)
        for name, value in (
            ("RELEASE_VERSION", versions["daemon_version"]),
            ("FIRMWARE_COMPAT_VERSION", versions["firmware_compat_version"]),
        ):
            recipe, count = re.subn(
                rf"^ARG {name}=[^\n]+$", f"ARG {name}={value}", recipe, flags=re.M
            )
            if count != 2:
                raise ValueError(f"Unexpected Dockerfile {name} declarations")
        (app / "Dockerfile").write_text(
            recipe + (app / "Dockerfile.wrapper").read_text()
        )
        if args.candidate:
            (app / "CANDIDATE-NOT-FOR-RELEASE.txt").write_text(
                "Qualification candidate only; not a complete public firmware release.\n"
            )
        if firmware:
            recipe = app / "Dockerfile"
            if firmware["updater_board_count"]:
                marker = "COPY host/LICENSE ./host/LICENSE\n"
                original = recipe.read_text()
                if original.count(marker) != 1:
                    raise ValueError(
                        "App build needs a reviewed firmware licence copy step"
                    )
                recipe.write_text(
                    original.replace(
                        marker,
                        marker
                        + "COPY host/FIRMWARE-LICENSES.txt ./host/FIRMWARE-LICENSES.txt\n",
                    )
                )
            recipe.write_text(
                recipe.read_text()
                + '\nCOPY firmware-release /usr/share/livolo-ec/firmware-release\nLABEL org.opencontainers.image.licenses="BSD-2-Clause AND LicenseRef-Livolo-Firmware-Mixed"\n'
            )
            (app / "firmware-release.json").write_text(
                json.dumps(firmware, indent=2) + "\n"
            )
        print(app)


if __name__ == "__main__":
    main()
