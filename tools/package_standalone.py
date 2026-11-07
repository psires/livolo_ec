#!/usr/bin/env python3
"""Build a standalone distribution from a committed source tree, never local data."""

import argparse
from email.parser import BytesParser
import hashlib
from importlib.metadata import version
import json
import os
from pathlib import Path
import posixpath
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import zipfile

from release_versions import source_versions
from firmware_release import stage_for_source

ROOT = Path(__file__).resolve().parents[1]


def checksums(directory):
    """Record the integrity of every supplied file except the checksum list itself.

    @param directory Asset directory whose relative file names are recorded.
    """
    paths = sorted(path for path in directory.rglob("*") if path.is_file())
    (directory / "SHA256SUMS").write_text(
        "".join(
            f"{hashlib.sha256(path.read_bytes()).hexdigest()}  "
            f"{path.relative_to(directory).as_posix()}\n"
            for path in paths
            if path.name != "SHA256SUMS"
        )
    )


def document_links(text, relative, tag):
    """Keep copied documentation navigable without shipping the complete source tree.

    @param text Markdown document from the release source.
    @param relative Original repository-relative document path.
    @param tag Immutable release tag used for omitted source references.
    @return Markdown with relative links pointing to the tagged repository.
    """

    def replace(match):
        """Resolve a documentation reference to the exact release source.

        @param match Markdown link containing its label and target.
        @return Original absolute link or a link into the tagged repository.
        """
        label, target = match.groups()
        if target.startswith(("#", "https://", "http://", "mailto:")):
            return match.group(0)
        path = posixpath.normpath(posixpath.join(posixpath.dirname(relative), target))
        return f"{label}(https://github.com/psires/livolo_ec/blob/{tag}/{path})"

    return re.sub(r"(\[[^\]]*\])\(([^)\s]+)\)", replace, text)


def inspect_wheel(
    path,
    name,
    expected,
    license_text,
    firmware_compat_version=None,
    firmware_files=None,
):
    """Reject incomplete or incorrectly labelled Python distributions before release.

    @param path Built wheel to inspect.
    @param name Expected Python distribution name.
    @param expected Expected component version, independent of bundle version.
    @param license_text Canonical project licence bytes.
    @param firmware_compat_version Required catalog target for daemon wheels.
    @param firmware_files Optional exact catalog files from the pinned public bundle.
    """
    with zipfile.ZipFile(path) as wheel:
        if wheel.testzip() is not None:
            raise ValueError("Corrupt wheel")
        names = wheel.namelist()
        metadata = next(item for item in names if item.endswith(".dist-info/METADATA"))
        fields = BytesParser().parsebytes(wheel.read(metadata))
        if fields["Name"] != name or fields["Version"] != expected:
            raise ValueError("Component metadata does not match the release")
        licence = next(item for item in names if item.endswith("/licenses/LICENSE"))
        if wheel.read(licence) != license_text:
            raise ValueError("Wheel does not contain the canonical BSD licence")
        if name == "livolo-ec-daemon":
            for item in ("index.html", "app.js", "style.css"):
                if not wheel.read("livolod/static/" + item):
                    raise ValueError("Missing web asset")
            catalog = json.loads(wheel.read("livolod/firmware/manifest.json"))
            if (
                catalog.get("host_version") != firmware_compat_version
                or catalog.get("schema") != 1
            ):
                raise ValueError("Wheel firmware catalog compatibility mismatch")
            expected_license = (
                "BSD-2-Clause AND LicenseRef-Livolo-Firmware-Mixed"
                if catalog["artifacts"]
                else "BSD-2-Clause"
            )
            if fields.get("License-Expression") != expected_license:
                raise ValueError(
                    "Daemon wheel licence expression does not match its firmware contents"
                )
            if catalog["artifacts"]:
                mapping = next(
                    (
                        item
                        for item in names
                        if item.endswith("/licenses/FIRMWARE-LICENSES.txt")
                    ),
                    None,
                )
                if mapping is None or wheel.read(mapping) != (firmware_files or {}).get(
                    "FIRMWARE-LICENSES.txt"
                ):
                    raise ValueError(
                        "Daemon wheel is missing its exact firmware licence mapping"
                    )
            if firmware_files is None:
                if catalog["artifacts"]:
                    raise ValueError("Firmware payloads require a reviewed source pin")
            else:
                prefix = "livolod/firmware/"
                actual = {
                    name[len(prefix) :]: wheel.read(name)
                    for name in names
                    if name.startswith(prefix) and not name.endswith("/")
                }
                if actual != firmware_files:
                    raise ValueError(
                        "Wheel firmware differs from the pinned public catalog"
                    )


def stage_pairing_guide(source, staged, tag):
    """Include the pairing guide when it exists in the selected source revision.

    @param source Exported release source directory, never the working checkout.
    @param staged Standalone bundle root receiving the docs directory.
    @param tag Immutable standalone release tag for linked profile documentation.
    """
    relative = "docs/PAIRING.md"
    origin = source / relative
    if origin.is_file():
        destination = staged / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(document_links(origin.read_text(), relative, tag))


def package(commit, output, firmware_bundle=None):
    """Assemble installable packages and service instructions from one exact commit.

    @param commit Full Git commit identifying the reviewed source and bundle version.
    @param output New output directory receiving release assets and checksums.
    @param firmware_bundle Optional local copy of the source-pinned public firmware bundle.
    @return Generated release metadata.
    """
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise ValueError("A full source commit is required")
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        source = root / "source"
        source.mkdir()
        archive = root / "source.tar"
        with archive.open("wb") as stream:
            subprocess.run(
                ["git", "archive", "--format=tar", commit],
                cwd=ROOT,
                stdout=stream,
                check=True,
            )
        with tarfile.open(archive) as bundle:
            for member in bundle.getmembers():
                if (
                    Path(member.name).is_absolute()
                    or ".." in Path(member.name).parts
                    or not (member.isfile() or member.isdir())
                ):
                    raise ValueError("Unexpected source archive member")
            bundle.extractall(source, filter="data")
        metadata = source_versions(source)
        firmware = stage_for_source(source, firmware_bundle)
        firmware_files = None
        if firmware:
            catalog_root = source / "host/livolod/firmware"
            firmware_files = {
                p.relative_to(catalog_root).as_posix(): p.read_bytes()
                for p in catalog_root.rglob("*")
                if p.is_file()
            }
        release = metadata["distribution_version"]
        if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", release):
            raise ValueError("Invalid standalone distribution version")
        epoch = subprocess.check_output(
            ["git", "show", "-s", "--format=%ct", commit], cwd=ROOT, text=True
        ).strip()
        for component, name, key in (
            ("host", "livolo-ec-daemon", "daemon_version"),
            ("client", "livolo-ec-client", "client_version"),
        ):
            subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "build",
                    "--no-isolation",
                    "--outdir",
                    str(output),
                    str(source / component),
                ],
                env=dict(os.environ, SOURCE_DATE_EPOCH=epoch),
                check=True,
            )
            wheel = next(output.glob(name.replace("-", "_") + "-*.whl"))
            inspect_wheel(
                wheel,
                name,
                metadata[key],
                (source / "LICENSE").read_bytes(),
                metadata["firmware_compat_version"],
                firmware_files,
            )
        tag = "standalone-" + release
        metadata.update(
            source_commit=commit,
            tag=tag,
            native_api_revision=1,
            uart_revision=3,
            validation_python="3.13",
            radio_firmware_payloads=firmware is not None,
            firmware_release=firmware,
            dependencies="Installed separately from PyPI; network access required",
            build_tools={
                name: version(name)
                for name in (
                    "build",
                    "setuptools",
                    "wheel",
                    "packaging",
                    "pyproject_hooks",
                )
            },
        )
        (output / "release.json").write_text(json.dumps(metadata, indent=2) + "\n")
        staged = root / f"livolo-ec-{release}-standalone"
        (staged / "packages").mkdir(parents=True)
        for path in output.iterdir():
            shutil.copy2(
                path,
                staged
                / (
                    "release.json"
                    if path.suffix == ".json"
                    else "packages/" + path.name
                ),
            )
        for relative, target in (
            ("LICENSE", "LICENSE"),
            ("THIRD_PARTY_NOTICES.md", "THIRD_PARTY_NOTICES.md"),
            ("host/release/README.md", "README.md"),
            ("host/release/config.example.json", "config.example.json"),
            ("host/systemd/livolod.service", "systemd/livolod.service"),
            ("host/docker/smoke.py", "checks/smoke.py"),
            ("docs/FIRMWARE_RECOVERY.md", "docs/FIRMWARE_RECOVERY.md"),
            ("docs/FIRMWARE_FLASHING.md", "docs/FIRMWARE_FLASHING.md"),
            ("docs/FIRMWARE_CATALOG.md", "docs/FIRMWARE_CATALOG.md"),
            ("host/docker/NORDIC.md", "docs/NORDIC.md"),
            ("host/NATIVE_API.md", "docs/NATIVE_API.md"),
        ):
            destination = staged / target
            destination.parent.mkdir(parents=True, exist_ok=True)
            if relative.endswith(".md"):
                destination.write_text(
                    document_links((source / relative).read_text(), relative, tag)
                )
            else:
                shutil.copy2(source / relative, destination)
        stage_pairing_guide(source, staged, tag)
        migration = source / "docs/VERSIONING.md"
        if migration.is_file():
            (staged / "docs/VERSIONING.md").write_text(
                document_links(migration.read_text(), "docs/VERSIONING.md", tag)
            )
        (staged / "constraints.txt").write_text(
            (source / "host/docker/requirements-firmware.txt").read_text()
            + "\npaho-mqtt==2.1.0\n"
        )
        if firmware:
            shutil.copytree(source / "firmware-release", staged / "firmware-release")
        checksums(staged)
        with tarfile.open(output / (staged.name + ".tar.gz"), "w:gz") as bundle:
            bundle.add(staged, arcname=staged.name)
    checksums(output)
    return metadata


def main():
    """Produce release assets without creating tags, publishing or operating a radio."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--firmware-bundle", type=Path)
    args = parser.parse_args()
    print(json.dumps(package(args.commit, args.output, args.firmware_bundle), indent=2))


if __name__ == "__main__":
    main()
