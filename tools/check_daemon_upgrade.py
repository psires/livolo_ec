#!/usr/bin/env python3
"""Install real daemon wheels and verify upgrade/explicit rollback with simulated state."""

import argparse
from email.parser import BytesParser
import json
import os
from pathlib import Path
import secrets
import shutil
import signal
import socket
import sqlite3
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
import venv
import zipfile

from packaging.version import Version


def wheel_version(path):
    """Read the actual daemon identity rather than trusting its wheel filename.

    @param path Wheel archive selected for the isolated installation.
    @return Python package version string; raises ValueError for another distribution.
    """
    with zipfile.ZipFile(path) as wheel:
        metadata = next(
            name for name in wheel.namelist() if name.endswith(".dist-info/METADATA")
        )
        fields = BytesParser().parsebytes(wheel.read(metadata))
        if fields["Name"] != "livolo-ec-daemon":
            raise ValueError("Expected a daemon wheel")
        return fields["Version"]


def api(base, token, path, body=None):
    """Call only the loopback test daemon using the disposable bearer token.

    @param base Loopback API URL allocated by the test.
    @param token Temporary test token, never printed.
    @param path Native API path relative to the base URL.
    @param body Optional JSON command body for the simulated registry seed.
    @return Decoded native API response; HTTP errors propagate.
    """
    request = urllib.request.Request(
        base + path,
        data=None if body is None else json.dumps(body).encode(),
        headers={
            "Authorization": "Bearer " + token,
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(request, timeout=2) as response:
        return json.load(response)


def state_check(environment, root, expected, compatibility, seed=False):
    """Start the installed simulator and check state across software replacements.

    @param environment Isolated venv containing the wheel currently under test.
    @param root Shared disposable configuration, database and catalog directory.
    @param expected Expected installed software version.
    @param compatibility Exact radio compatibility target for this installed phase.
    @param seed Whether this first phase must create the one simulated device.
    @return Exact stored network, registry and request rows for cross-phase comparison.
    """
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    token = secrets.token_urlsafe(32)
    config = {
        "network": {"gateway": "10203040", "tag": "42"},
        "devices": [],
        "serial_port": "/dev/livolo-version-test-no-radio",
        "serial_baudrate": 460800,
        "database": str(root / "state.sqlite3"),
        "output_mode": "disabled",
        "mqtt": {"enabled": False},
        "web": {"enabled": False},
        "firmware_bundle_dir": str(root / "catalog"),
        "api": {
            "enabled": True,
            "host": "127.0.0.1",
            "port": port,
            "token_env": "LIVOLO_TEST_TOKEN",
        },
    }
    path = root / "config.json"
    path.write_text(json.dumps(config))
    base = f"http://127.0.0.1:{port}/api/v1"
    command = [str(environment / "bin/livolod")]
    assert (
        subprocess.check_output(command + ["--version"], text=True).strip() == expected
    )
    env = dict(os.environ, LIVOLO_TEST_TOKEN=token)
    env.pop("PYTHONPATH", None)
    with (root / ("daemon-" + expected + ".log")).open("w+") as log:
        process = subprocess.Popen(
            command + ["--config", str(path), "--simulate"],
            cwd=root,
            env=env,
            stdout=log,
            stderr=log,
        )
        try:
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    raise RuntimeError("Installed daemon exited before readiness")
                try:
                    snapshot = api(base, token, "/snapshot")["data"]
                    if snapshot["ready"] and snapshot["simulated"]:
                        break
                except (OSError, urllib.error.URLError):
                    pass
                time.sleep(0.1)
            else:
                raise RuntimeError("Installed simulator readiness timeout")
            info = api(base, token, "/info")
            assert info["host_version"] == compatibility and info["api_version"] == 1
            if expected != compatibility:
                assert info["daemon_version"] == expected
                assert info["firmware_compat_version"] == compatibility
                assert info["uart_protocol"] == 3
            if seed:
                result = api(
                    base,
                    token,
                    "/commands",
                    {
                        "action": "add",
                        "payload": {
                            "request_id": "versioning-add",
                            "device": "10203041",
                            "name": "Preserved device",
                            "outputs": 1,
                            "suffix": "0001",
                        },
                    },
                )
                assert result["status"] == "confirmed"
            snapshot = api(base, token, "/snapshot")["data"]
            assert snapshot["gateway"] == "10203040" and snapshot["tag"] == "42"
            assert len(snapshot["devices"]) == 1
            assert snapshot["devices"][0]["id"] == "10203041"
            assert snapshot["devices"][0]["name"] == "Preserved device"
            assert api(base, token, "/commands/versioning-add")["status"] == "confirmed"
            assert not snapshot["firmware_update"].get("candidate_id")
            assert not snapshot["firmware_update"].get("in_progress")
            process.send_signal(signal.SIGTERM)
            assert process.wait(timeout=10) == 0
        except BaseException:
            log.seek(0)
            print(log.read())
            raise
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()
    with sqlite3.connect(root / "state.sqlite3.simulated") as database:
        assert database.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        return {
            "network": database.execute(
                "SELECT key, value FROM meta WHERE key='network'"
            ).fetchall(),
            "devices": database.execute(
                "SELECT id, value FROM devices ORDER BY id"
            ).fetchall(),
            "request": database.execute(
                "SELECT id, request, result, updated FROM commands WHERE id='versioning-add'"
            ).fetchall(),
        }


def catalog_check(environment, root, compatibility):
    """Check the unchanged catalog using the installed wheel's actual compatibility rules.

    @param environment Isolated environment holding the phase's installed wheel.
    @param root Shared persistent simulator state directory.
    @param compatibility Expected compatibility target for this installed phase.
    @return Actual rejection code for the empty old catalog; never a hardware qualification.
    """
    catalog_version = json.loads((root / "catalog/manifest.json").read_bytes())[
        "host_version"
    ]
    expected = (
        "artifact_missing" if catalog_version == compatibility else "incompatible_host"
    )
    script = """
import json, sys, livolod
from livolod.firmware_bundle import BundleError, select_candidate
target = getattr(livolod, 'FIRMWARE_COMPAT_VERSION', livolod.__version__)
assert target == sys.argv[2]
try:
    select_candidate(sys.argv[1], board='zb-gw04-1v2', host_version=target, app_baudrate=460800)
except BundleError as error:
    print(json.dumps({'reason': error.code}))
else:
    raise AssertionError('The persistent empty catalog unexpectedly offered an artifact')
"""
    result = json.loads(
        subprocess.check_output(
            [
                str(environment / "bin/python"),
                "-I",
                "-c",
                script,
                str(root / "catalog"),
                compatibility,
            ],
            cwd=root,
            text=True,
        )
    )
    assert result == {"reason": expected}, result
    return result["reason"]


def check(
    previous, candidate, compatibility, constraints=None, *, previous_compatibility=None
):
    """Verify real pip upgrade ordering and deliberate rollback without physical radios.

    @param previous Previously released daemon wheel, installed before the candidate.
    @param candidate New daemon wheel with strictly greater package version.
    @param compatibility Candidate's two-component firmware compatibility target.
    @param constraints Optional pinned dependency constraints for the initial install.
    @param previous_compatibility Previous and rollback target; defaults to candidate target for old callers.
    @return Evidence dictionary after all installation and persistence phases pass.
    """
    previous, candidate = Path(previous).resolve(), Path(candidate).resolve()
    old_version, new_version = wheel_version(previous), wheel_version(candidate)
    if not Version(new_version) > Version(old_version):
        raise ValueError("Candidate must sort above the installed daemon version")
    previous_compatibility = (
        compatibility if previous_compatibility is None else previous_compatibility
    )
    evidence = []
    with tempfile.TemporaryDirectory(prefix="livolo-version-upgrade-") as temporary:
        root = Path(temporary)
        environment = root / "venv"
        venv.EnvBuilder(with_pip=True).create(environment)
        python = str(environment / "bin/python")
        env = dict(os.environ)
        env.pop("PYTHONPATH", None)
        pip = [python, "-m", "pip"]
        arguments = (
            ["--constraint", str(Path(constraints).resolve())] if constraints else []
        )
        subprocess.run(
            pip
            + [
                "install",
                *arguments,
                str(previous) + "[mqtt,flash]",
                "paho-mqtt==2.1.0",
            ],
            cwd=root,
            env=env,
            check=True,
        )
        wheels = root / "wheels"
        wheels.mkdir()
        for path in (previous, candidate):
            shutil.copy2(path, wheels / path.name)
        state = root / "state"
        (state / "catalog").mkdir(parents=True)
        manifest = state / "catalog/manifest.json"
        manifest.write_text(
            json.dumps(
                {"schema": 1, "host_version": previous_compatibility, "artifacts": []}
            )
        )
        catalog_bytes = manifest.read_bytes()
        phases = [
            ("previous", old_version, previous_compatibility),
            ("upgrade", new_version, compatibility),
            ("rollback", old_version, previous_compatibility),
        ]
        preserved = None
        for phase, expected, phase_compatibility in phases:
            if phase == "upgrade":
                subprocess.run(
                    pip
                    + [
                        "install",
                        "--upgrade",
                        "--no-index",
                        "--find-links",
                        str(wheels),
                        "livolo-ec-daemon",
                    ],
                    cwd=root,
                    env=env,
                    check=True,
                )
            elif phase == "rollback":
                subprocess.run(
                    pip
                    + [
                        "install",
                        "--no-index",
                        "--no-deps",
                        "--force-reinstall",
                        str(previous),
                    ],
                    cwd=root,
                    env=env,
                    check=True,
                )
            subprocess.run(pip + ["check"], cwd=root, env=env, check=True)
            assert (
                subprocess.check_output(
                    [
                        python,
                        "-I",
                        "-c",
                        'from importlib.metadata import version; print(version("livolo-ec-daemon"))',
                    ],
                    cwd=root,
                    env=env,
                    text=True,
                ).strip()
                == expected
            )
            if phase == "upgrade":
                for name in (
                    "livolod",
                    "livolod-firmware",
                    "livolod-firmware-manage",
                    "livolod-commission",
                ):
                    report = json.loads(
                        subprocess.check_output(
                            [str(environment / "bin" / name), "--version-info"],
                            cwd=root,
                            env=env,
                            text=True,
                        )
                    )
                    assert report == dict(
                        daemon_version=expected,
                        firmware_compat_version=compatibility,
                        api_version=1,
                        uart_protocol=3,
                    )
            current = state_check(
                environment,
                state,
                expected,
                phase_compatibility,
                seed=phase == "previous",
            )
            if phase == "previous":
                preserved = current
            else:
                assert (
                    current == preserved
                ), "Persistent registry/network/request bytes changed"
            reason = catalog_check(environment, state, phase_compatibility)
            assert manifest.read_bytes() == catalog_bytes
            evidence.append(
                dict(
                    phase=phase,
                    daemon_version=expected,
                    firmware_compat_version=phase_compatibility,
                    catalog_reason=reason,
                )
            )
    return dict(
        previous_version=old_version,
        daemon_version=new_version,
        firmware_compat_version=compatibility,
        previous_firmware_compat_version=previous_compatibility,
        phases=evidence,
        upgrade=True,
        rollback=True,
        registry_and_request_history_preserved=True,
        catalog_unchanged=True,
        live_radio_access=False,
    )


def main():
    """Run explicit wheel checks; dependency download is the only external access."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--previous-wheel", required=True, type=Path)
    parser.add_argument("--candidate-wheel", required=True, type=Path)
    parser.add_argument("--firmware-compat-version", required=True)
    parser.add_argument(
        "--previous-firmware-compat-version",
        help="Previous/rollback target; defaults to candidate compatibility",
    )
    parser.add_argument("--constraints", type=Path)
    args = parser.parse_args()
    print(
        json.dumps(
            check(
                args.previous_wheel,
                args.candidate_wheel,
                args.firmware_compat_version,
                args.constraints,
                previous_compatibility=args.previous_firmware_compat_version,
            ),
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
