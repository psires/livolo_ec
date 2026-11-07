#!/usr/bin/env python3
"""Check a standalone release by installing its actual distributions in isolation."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tarfile
import tempfile
import venv

from release_versions import normalize_metadata


def verify(directory):
    """Reject changed release files before installation or archive extraction.

    @param directory Release directory containing a SHA256SUMS file.
    """
    for line in (directory / "SHA256SUMS").read_text().splitlines():
        digest, name = line.split("  ", 1)
        if Path(name).is_absolute() or ".." in Path(name).parts:
            raise ValueError("Unexpected checksum path")
        if hashlib.sha256((directory / name).read_bytes()).hexdigest() != digest:
            raise ValueError("Checksum mismatch: " + name)


def check(assets, distribution):
    """Verify real CLI entry points and simulated service behavior after installation.

    @param assets Directory containing the published packages and standalone archive.
    @param distribution Whether to install wheels or build from source distributions.
    """
    assets = Path(assets).resolve()
    verify(assets)
    metadata = normalize_metadata(json.loads((assets / "release.json").read_text()))
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        with tarfile.open(
            next(assets.glob("livolo-ec-*-standalone.tar.gz"))
        ) as archive:
            for member in archive.getmembers():
                if (
                    Path(member.name).is_absolute()
                    or ".." in Path(member.name).parts
                    or not (member.isfile() or member.isdir())
                ):
                    raise ValueError("Unexpected bundle member")
            archive.extractall(root, filter="data")
        bundle = next(root.glob("livolo-ec-*-standalone"))
        verify(bundle)
        environment = root / "venv"
        venv.EnvBuilder(with_pip=True).create(environment)
        python = environment / "bin/python"
        extension = "*.whl" if distribution == "wheel" else "*.tar.gz"
        daemon = next((bundle / "packages").glob("livolo_ec_daemon-" + extension))
        client = next((bundle / "packages").glob("livolo_ec_client-" + extension))
        env = dict(
            os.environ, PATH=str(environment / "bin") + os.pathsep + os.environ["PATH"]
        )
        env.pop("PYTHONPATH", None)
        subprocess.run(
            [
                str(python),
                "-m",
                "pip",
                "install",
                "--constraint",
                str(bundle / "constraints.txt"),
                "livolo-ec-daemon[mqtt,flash] @ " + daemon.as_uri(),
                str(client),
            ],
            cwd=root,
            env=env,
            check=True,
        )
        subprocess.run(
            [str(python), "-m", "pip", "check"], cwd=root, env=env, check=True
        )
        subprocess.run(
            [
                str(python),
                "-c",
                "import livolo_ec_client; from importlib.metadata import version; "
                f"assert version('livolo-ec-client') == {metadata['client_version']!r}; "
                f"assert version('livolo-ec-daemon') == {metadata['daemon_version']!r}",
            ],
            cwd=root,
            env=env,
            check=True,
        )
        commands = ["livolod", "livolod-firmware", "livolod-firmware-manage"]
        if metadata["schema"] == 2:
            commands.append("livolod-commission")
        for command in commands:
            subprocess.run(
                [command, "--help"],
                cwd=root,
                env=env,
                check=True,
                stdout=subprocess.DEVNULL,
            )
            if metadata["schema"] == 2:
                report = json.loads(
                    subprocess.check_output(
                        [command, "--version-info"], cwd=root, env=env, text=True
                    )
                )
                assert report == {
                    "daemon_version": metadata["daemon_version"],
                    "firmware_compat_version": metadata["firmware_compat_version"],
                    "api_version": 1,
                    "uart_protocol": 3,
                }
        subprocess.run(
            [
                "livolod",
                "--config",
                str(bundle / "config.example.json"),
                "--check-config",
            ],
            cwd=root,
            env=env,
            check=True,
        )
        subprocess.run(
            [str(python), "-m", "universal_silabs_flasher", "--help"],
            cwd=root,
            env=env,
            check=True,
            stdout=subprocess.DEVNULL,
        )
        subprocess.run(
            [str(python), str(bundle / "checks/smoke.py")],
            cwd=root,
            env=env,
            check=True,
        )
    print(
        f"Standalone {metadata['distribution_version']}: {distribution} installation passed"
    )


def main():
    """Check distributable artifacts without installing into the user's environment."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--assets", type=Path, required=True)
    parser.add_argument("--format", choices=("wheel", "sdist"), default="wheel")
    args = parser.parse_args()
    check(args.assets, args.format)


if __name__ == "__main__":
    main()
