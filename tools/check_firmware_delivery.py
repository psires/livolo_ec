#!/usr/bin/env python3
"""Exercise real package builders with synthetic, non-flashable firmware only."""

import argparse
from email.parser import BytesParser
import io
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import zipfile

from firmware_release import UPDATE_CHECKS, archive, verify_installed
from project_version import synchronize
from release_versions import source_versions
from test_firmware_release import PublicReleaseTests
import container_prepare
import package_standalone

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "homeassistant"))
import package_release


def exercise(output):
    """Build exact inventories and container contexts without radio or publication access.

    @param output New test directory; all generated payloads are non-flashable fixtures.
    @return Build parameters for disposable local-only image validation.
    """
    output = Path(output).absolute()
    output.mkdir(parents=True, exist_ok=False)
    fixture = PublicReleaseTests()
    fixture.setUp()
    try:
        fixture.records["sonoff-zbdongle-e"]["automatic_update"] = dict(
            eligible=True,
            reviewer="NONFLASHABLE TEST FIXTURE",
            tested_runtime_image="ghcr.io/example/never-deploy@sha256:" + "b" * 64,
            checks=fixture.checks(UPDATE_CHECKS),
        )
        digest = fixture.package()
        bundle = fixture.root / "output"
        source = output / "source"
        source.mkdir()
        raw = subprocess.check_output(["git", "archive", "HEAD"], cwd=ROOT)
        with tarfile.open(fileobj=io.BytesIO(raw)) as snapshot:
            snapshot.extractall(source, filter="data")
        (source / "VERSION").write_text("0.12\n")
        synchronize(source, "radio")
        archive_sha = archive(
            bundle,
            output / "NEVER-DEPLOY-fixture.tar.gz",
            inventory_sha256=digest,
            source_commit="a" * 40,
            version="0.12",
        )
        pin = dict(
            schema=1,
            inventory_sha256=digest,
            source_commit="a" * 40,
            version="0.12",
            archive_url="https://example.invalid/NEVER-DEPLOY-fixture.tar.gz",
            archive_sha256=archive_sha,
        )
        (source / "host/release/firmware.json").write_text(json.dumps(pin))
        subprocess.run(["git", "init", "-q"], cwd=source, check=True)
        subprocess.run(["git", "add", "."], cwd=source, check=True)
        subprocess.run(
            [
                "git",
                "-c",
                "user.name=Fixture test",
                "-c",
                "user.email=fixture@example.invalid",
                "commit",
                "-qm",
                "Non-flashable packaging fixture; never publish",
            ],
            cwd=source,
            check=True,
        )
        commit = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=source, text=True
        ).strip()
        versions = source_versions(source)
        release = versions["distribution_version"]
        package_standalone.ROOT = source
        metadata = package_standalone.package(commit, output / "standalone", bundle)
        assert metadata["firmware_release"]["board_count"] == 4
        assert metadata["firmware_release"]["updater_board_count"] == 1
        wheel = next((output / "standalone").glob("livolo_ec_daemon*.whl"))
        mixed = "BSD-2-Clause AND LicenseRef-Livolo-Firmware-Mixed"
        with zipfile.ZipFile(wheel) as package:
            info = next(
                name
                for name in package.namelist()
                if name.endswith(".dist-info/METADATA")
            )
            fields = BytesParser().parsebytes(package.read(info))
            assert fields["License-Expression"] == mixed
            assert "FIRMWARE-LICENSES.txt" in fields.get_all("License-File")
            assert any(
                name.endswith("/licenses/FIRMWARE-LICENSES.txt")
                for name in package.namelist()
            )
            assert (
                len(
                    json.loads(package.read("livolod/firmware/manifest.json"))[
                        "artifacts"
                    ]
                )
                == 1
            )
        sdist = next((output / "standalone").glob("livolo_ec_daemon-*.tar.gz"))
        with tarfile.open(sdist) as package:
            info = next(
                member
                for member in package.getmembers()
                if member.name.endswith("/PKG-INFO")
            )
            fields = BytesParser().parsebytes(package.extractfile(info).read())
            assert fields["License-Expression"] == mixed
            assert "FIRMWARE-LICENSES.txt" in fields.get_all("License-File")
            assert any(
                member.name.endswith("/FIRMWARE-LICENSES.txt")
                for member in package.getmembers()
            )
        package_release.package(source, release, commit, output / "ha", bundle)
        for path in (
            output / "standalone" / f"livolo-ec-{release}-standalone.tar.gz",
            output / "ha" / f"livolo-ec-ha-app-{release}-source.tar.gz",
        ):
            with tarfile.open(path) as package:
                assert not any(
                    member.name.endswith("CANDIDATE-NOT-FOR-RELEASE.txt")
                    for member in package.getmembers()
                )
                assert (
                    len(
                        [
                            member
                            for member in package.getmembers()
                            if member.isfile()
                            and "/firmware-release/artifacts/" in member.name
                        ]
                    )
                    == 5
                )
        container_prepare.ROOT = source
        request = container_prepare.manifest(ROOT / "host/docker/releases.json")
        for kind in ("standalone", "ha"):
            entry = dict(
                schema=2,
                kind=kind,
                version=release,
                daemon_version=versions["daemon_version"],
                firmware_compat_version="0.12",
                source_commit=commit,
                source_tag=None,
            )
            context = output / (kind + "-context")
            result = container_prepare.prepare(
                request, entry, context, candidate=True, firmware_bundle=bundle
            )
            assert result["firmware_release"]["board_count"] == 4
            assert (
                "LicenseRef-Livolo-Firmware-Mixed"
                in (context / "Dockerfile").read_text()
            )
            verify_installed(
                context / "firmware-release",
                context / "host/livolod/firmware",
                inventory_sha256=digest,
                source_commit="a" * 40,
                version="0.12",
            )
        result = dict(
            fixture_only=True,
            physical_qualification=False,
            firmware_pin=pin,
            daemon_version=versions["daemon_version"],
            firmware_compat_version="0.12",
            buildx_version=request["buildx_version"],
            buildkit_image=request["buildkit_image"],
            python_image=request["python_image"],
        )
        (output / "fixture.json").write_text(json.dumps(result, indent=2) + "\n")
        print(
            "PASS: actual wheels/sdists, standalone and HA archives, and both image contexts preserve four applications plus TI bootstrap; one updater entry only. Non-flashable fixtures."
        )
        return result
    finally:
        fixture.doCleanups()


def main():
    """Run isolated package-delivery regression checks, never a release operation."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    exercise(args.output)


if __name__ == "__main__":
    main()
