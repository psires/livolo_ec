#!/usr/bin/env python3
"""Validate explicit release requests and assemble exact-source container contexts."""

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

from firmware_release import stage_for_source
from release_versions import app_config_version, normalize_metadata, source_versions

ROOT = Path(__file__).resolve().parents[1]


def command(*args):
    """Run a checked command in the recipe checkout and return its output.

    @param args Executable and arguments, passed without a shell.
    """
    return subprocess.check_output(args, cwd=ROOT, text=True).strip()


def manifest(path):
    """Validate the tracked publication request before exposing a job matrix.

    @param path Tracked JSON release request file.
    """
    data = json.loads(Path(path).read_text())
    assert type(data["schema"]) is int and data["schema"] in (1, 2)
    for key in ("python_image", "buildkit_image", "frontend_image", "broker_image"):
        assert re.fullmatch(r"[a-z0-9./:_-]+@sha256:[0-9a-f]{64}", data[key]), key
    assert re.fullmatch(r"v\d+\.\d+\.\d+", data["buildx_version"])
    seen = set()
    for entry in data["releases"]:
        fields = {
            "enabled",
            "kind",
            "image",
            "version",
            "daemon_version",
            "source_tag",
            "source_commit",
            "previous_image",
        }
        if data["schema"] == 2:
            fields.add("firmware_compat_version")
            if "version_schema" in entry:
                fields.add("version_schema")
        assert set(entry) == fields
        entry.update(
            normalize_metadata(
                dict(entry, schema=entry.get("version_schema", data["schema"]))
            )
        )
        assert type(entry["enabled"]) is bool
        assert entry["kind"] in ("standalone", "ha")
        assert (
            entry["image"]
            == {
                "standalone": "ghcr.io/psires/livolod",
                "ha": "ghcr.io/psires/livolo-ec-ha-app",
            }[entry["kind"]]
        )
        assert re.fullmatch(r"\d+\.\d+\.\d+", entry["version"])
        assert re.fullmatch(r"\d+\.\d+(?:\.\d+)?", entry["daemon_version"])
        assert re.fullmatch(r"(?:standalone|ha)-\d+\.\d+\.\d+", entry["source_tag"])
        assert re.fullmatch(r"[0-9a-f]{40}", entry["source_commit"])
        assert entry["previous_image"] is None or re.fullmatch(
            re.escape(entry["image"]) + r"@sha256:[0-9a-f]{64}", entry["previous_image"]
        )
        identity = (entry["image"], entry["version"])
        assert identity not in seen
        seen.add(identity)
    return data


def prepare(
    data, entry, output, candidate=False, firmware_bundle=None, platform="linux/arm64"
):
    """Assemble a minimal historical source context plus a recorded external recipe.

    @param data Validated manifest containing pinned build tools and images.
    @param entry Validated release identity and historical source reference.
    @param output New directory receiving the assembled build context.
    @param candidate Build non-publishing source with its own app version instead of the public listing.
    @param firmware_bundle Optional local copy of the source-pinned firmware inventory.
    @param platform Target Linux platform; AMD64 is restricted to candidates.
    """
    if platform not in ("linux/arm64", "linux/amd64") or (
        platform != "linux/arm64" and not candidate
    ):
        raise ValueError("Unsupported platform for this publication")
    if not candidate:
        assert (
            command(
                "git", "rev-parse", "refs/tags/" + entry["source_tag"] + "^{commit}"
            )
            == entry["source_commit"]
        )
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    paths = [
        "VERSION",
        "LICENSE",
        "THIRD_PARTY_NOTICES.md",
        "host/pyproject.toml",
        "host/PACKAGE.md",
        "host/LICENSE",
        "host/release/version.json",
        "host/release/firmware.json",
        "host/release/firmware-policy.json",
        "host/livolod",
        "host/docker/Dockerfile",
        "host/docker/smoke.py",
        "host/docker/requirements-firmware.txt",
        "host/docker/requirements-nordic.txt",
        "host/docker/requirements-nordic-build.txt",
        "host/docker/build_nordic.py",
        "tools/container_sources.py",
    ]
    if entry["kind"] == "ha":
        paths.append("homeassistant/app")
    # PACKAGE.md was introduced after HA 0.2.0; only copy historical files that exist.
    paths = [
        p
        for p in paths
        if subprocess.run(
            ["git", "cat-file", "-e", entry["source_commit"] + ":" + p],
            cwd=ROOT,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        ).returncode
        == 0
    ]
    archive = subprocess.check_output(
        ["git", "archive", entry["source_commit"], *paths], cwd=ROOT
    )
    with tempfile.TemporaryDirectory() as temporary:
        source = Path(temporary)
        with tarfile.open(fileobj=io.BytesIO(archive)) as bundle:
            for member in bundle.getmembers():
                if (
                    not (member.isfile() or member.isdir())
                    or Path(member.name).is_absolute()
                    or ".." in Path(member.name).parts
                ):
                    raise ValueError("Unexpected source member")
            bundle.extractall(source, filter="data")
        versions = source_versions(source)
        firmware = stage_for_source(source, firmware_bundle, candidate=candidate)
        assert versions["schema"] == entry["schema"]
        assert versions["daemon_version"] == entry["daemon_version"]
        assert versions["firmware_compat_version"] == entry["firmware_compat_version"]
        if entry["kind"] == "standalone":
            assert (
                json.loads((source / "host/release/version.json").read_text())[
                    "distribution_version"
                ]
                == entry["version"]
            )
        else:
            # The exact release config uses a quoted scalar version, not arbitrary YAML execution.
            config_path = source / "homeassistant/app/config.yaml"
            config = config_path.read_text()
            if candidate:
                assert versions["distribution_version"] == entry["version"]
                config = app_config_version(config, versions["distribution_version"])
                config_path.write_text(config)
            assert re.search(
                r'^version:\s*["\']?' + re.escape(entry["version"]) + r'["\']?\s*$',
                config,
                re.M,
            )
        for relative in (
            "VERSION",
            "LICENSE",
            "THIRD_PARTY_NOTICES.md",
            "firmware-release",
            "host",
            "tools",
        ):
            origin = source / relative
            if not origin.exists():
                continue
            if origin.is_dir():
                shutil.copytree(origin, output / relative)
            else:
                shutil.copy2(origin, output / relative)
        original = (source / "host/docker/Dockerfile").read_text()
        recipe = original
        if firmware and firmware["updater_board_count"]:
            marker = "COPY host/LICENSE ./host/LICENSE\n"
            if recipe.count(marker) != 1:
                raise ValueError(
                    "Docker build needs a reviewed firmware licence copy step"
                )
            recipe = recipe.replace(
                marker,
                marker
                + "COPY host/FIRMWARE-LICENSES.txt ./host/FIRMWARE-LICENSES.txt\n",
            )
        # Reproducibility/inventory overlay; application sources remain byte-identical.
        recipe = re.sub(
            r"^# syntax=.*$",
            "# syntax=" + data["frontend_image"],
            recipe,
            count=1,
            flags=re.M,
        )
        recipe = recipe.replace(
            "WORKDIR /src\n",
            "WORKDIR /src\nCOPY container/build-requirements.txt /src/container/build-requirements.txt\nENV PIP_CONSTRAINT=/src/container/build-requirements.txt\nRUN python -m pip install --no-cache-dir -r /src/container/build-requirements.txt\n",
            1,
        )
        recipe = recipe.replace(
            "FROM ${PYTHON_IMAGE} AS runtime",
            "COPY container/container_sources.py /src/container_sources.py\nRUN python /src/container_sources.py\n\nFROM ${PYTHON_IMAGE} AS runtime",
            1,
        )
        if entry["kind"] == "ha":
            for origin in (source / "homeassistant/app").iterdir():
                if origin.is_file():
                    shutil.copy2(origin, output / origin.name)
            recipe += (source / "homeassistant/app/Dockerfile.wrapper").read_text()
        recipe += "\nUSER root\nCOPY container/release.json /usr/share/livolo-ec/release.json\nCOPY LICENSE THIRD_PARTY_NOTICES.md /usr/share/licenses/livolo-ec/\nRUN dpkg-query -W > /usr/share/livolo-ec/debian-packages.txt\n"
        if firmware:
            recipe += 'COPY firmware-release /usr/share/livolo-ec/firmware-release\nLABEL org.opencontainers.image.licenses="BSD-2-Clause AND LicenseRef-Livolo-Firmware-Mixed"\n'
        if entry["kind"] == "standalone":
            recipe += "USER 10001:10001\n"
        (output / "Dockerfile").write_text(recipe)
        container = output / "container"
        container.mkdir()
        shutil.copy2(
            ROOT / "tools/container_sources.py", container / "container_sources.py"
        )
        shutil.copy2(
            ROOT / "host/release/requirements-build.txt",
            container / "build-requirements.txt",
        )
        metadata = dict(
            entry,
            candidate=candidate,
            firmware_release=firmware,
            license_label=(
                "BSD-2-Clause AND LicenseRef-Livolo-Firmware-Mixed"
                if firmware
                else "BSD-2-Clause"
            ),
            platform=platform,
            recipe_commit=command("git", "rev-parse", "HEAD"),
            python_image=data["python_image"],
            buildkit_image=data["buildkit_image"],
            frontend_image=data["frontend_image"],
            buildx_version=data["buildx_version"],
            source_dockerfile_sha256=hashlib.sha256(original.encode()).hexdigest(),
            generated_dockerfile_sha256=hashlib.sha256(recipe.encode()).hexdigest(),
        )
        metadata["context_sha256"] = {
            str(p.relative_to(output)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(output.rglob("*"))
            if p.is_file()
        }
        (container / "release.json").write_text(json.dumps(metadata, indent=2) + "\n")
    return metadata


def main():
    """Print the enabled matrix or prepare one named immutable release context."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", default="host/docker/releases.json")
    parser.add_argument("--kind", choices=["standalone", "ha"])
    parser.add_argument("--version")
    parser.add_argument("--output")
    parser.add_argument("--firmware-bundle", type=Path)
    parser.add_argument(
        "--candidate",
        action="store_true",
        help="Stage HEAD for non-publishing local CI validation",
    )
    parser.add_argument(
        "--platform", choices=("linux/arm64", "linux/amd64"), default="linux/arm64"
    )
    args = parser.parse_args()
    if args.platform != "linux/arm64" and not args.candidate:
        parser.error("AMD64 is available only for non-publishing candidates")
    data = manifest(ROOT / args.manifest)
    if args.candidate:
        if not args.output or not args.kind or args.version:
            parser.error("--candidate requires --kind and --output, without --version")
        versions = source_versions(ROOT)
        entry = dict(
            schema=versions["schema"],
            kind=args.kind,
            version=versions["distribution_version"],
            daemon_version=versions["daemon_version"],
            firmware_compat_version=versions["firmware_compat_version"],
            source_commit=command("git", "rev-parse", "HEAD"),
            source_tag=None,
        )
        prepare(
            data,
            entry,
            args.output,
            candidate=True,
            firmware_bundle=args.firmware_bundle,
            platform=args.platform,
        )
        return
    if args.output:
        entry = next(
            e
            for e in data["releases"]
            if e["kind"] == args.kind and e["version"] == args.version
        )
        prepare(data, entry, args.output, firmware_bundle=args.firmware_bundle)
    else:
        print(
            json.dumps(
                {
                    "include": [
                        dict(
                            e,
                            **{
                                k: v
                                for k, v in data.items()
                                if k not in ("schema", "releases")
                            },
                        )
                        for e in data["releases"]
                        if e["enabled"]
                    ]
                }
            )
        )


if __name__ == "__main__":
    main()
