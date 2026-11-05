#!/usr/bin/env python3
"""Test exact image digests with disposable state, network and no radio devices."""

import argparse
import json
from pathlib import Path
import subprocess
import tempfile
import uuid

ROOT = Path(__file__).resolve().parents[1]
TOKEN = "isolated-container-test-token-0000000000000000"


def docker(*args, **kwargs):
    """Run Docker without a shell and propagate failures.

    @param args Docker subcommand and arguments.
    @param kwargs Additional subprocess options.
    """
    return subprocess.check_output(
        ["docker", *map(str, args)], text=True, **kwargs
    ).strip()


def check(image, kind, broker_image, previous=None):
    """Exercise first installation and replacement with shared persistent state.

    @param image Candidate image digest under test.
    @param kind Standalone daemon or HA wrapper mode.
    @param broker_image Pinned broker image for isolated MQTT testing.
    @param previous Optional earlier image digest for a real version upgrade.
    """
    identity = "livolo-check-" + uuid.uuid4().hex[:10]
    containers = []
    with tempfile.TemporaryDirectory(prefix=identity) as temporary:
        root = Path(temporary)
        root.chmod(0o755)
        state = root / "data"
        state.mkdir(mode=0o755)
        checks = str(ROOT / "tools") + ":/checks:ro"
        network_created = False
        try:
            # Only disposable CI hosts: the fixed subnet models Supervisor's trusted peer.
            docker(
                "network",
                "create",
                "--internal",
                "--subnet",
                "172.30.32.0/24",
                identity,
            )
            network_created = True
            if kind == "standalone":
                (root / "mosquitto.conf").write_text(
                    "listener 1883\nallow_anonymous true\npersistence false\n"
                )
                broker = identity + "-broker"
                containers.append(broker)
                docker(
                    "run",
                    "-d",
                    "--name",
                    broker,
                    "--network",
                    identity,
                    "--ip",
                    "172.30.32.3",
                    "--network-alias",
                    "broker",
                    "-v",
                    str(root / "mosquitto.conf")
                    + ":/mosquitto/config/mosquitto.conf:ro",
                    broker_image,
                )
            if kind == "ha":
                options = {
                    "serial_port": "/dev/nonexistent",
                    "serial_baudrate": 460800,
                    "gateway": "10203040",
                    "tag": "42",
                    "api_token": TOKEN,
                    "ha_origin": "http://homeassistant.test:8123",
                    "output_mode": "disabled",
                }
                (state / "options.json").write_text(json.dumps(options))
            else:
                config = {
                    "network": {"gateway": "10203040", "tag": "42"},
                    "devices": [],
                    "serial_port": "/dev/nonexistent",
                    "serial_baudrate": 460800,
                    "database": "/data/state.sqlite3",
                    "output_mode": "disabled",
                    "mqtt": {
                        "host": "broker",
                        "port": 1883,
                        "base_topic": "container-test",
                        "client_id": identity,
                    },
                    "api": {
                        "enabled": True,
                        "host": "127.0.0.1",
                        "port": 8090,
                        "token_env": "LIVOLO_API_TOKEN",
                    },
                    "web": {"enabled": True, "host": "127.0.0.1", "port": 8080},
                }
                (root / "config.json").write_text(json.dumps(config))
            docker(
                "run",
                "--rm",
                "--network",
                "none",
                "--user",
                "0",
                "--entrypoint",
                "python",
                "-v",
                str(state) + ":/data",
                image,
                "-c",
                "import os; os.chown('/data',10001,10001); os.chmod('/data',0o700)",
            )
            for number, current in enumerate((previous or image, image)):
                docker("pull", current)
                name = identity + "-app-" + str(number)
                containers.append(name)
                args = [
                    "run",
                    "-d",
                    "--name",
                    name,
                    "--network",
                    identity,
                    "--ip",
                    "172.30.32.10",
                    "--read-only",
                    "--tmpfs",
                    "/tmp:rw,noexec,nosuid,size=32m",
                    "-v",
                    str(state) + ":/data",
                    "-v",
                    checks,
                    "--security-opt",
                    "no-new-privileges:true",
                    "--cap-drop",
                    "ALL",
                ]
                if kind == "ha":
                    args += [
                        "--cap-add",
                        "CHOWN",
                        "--cap-add",
                        "DAC_OVERRIDE",
                        "--cap-add",
                        "FOWNER",
                        "--cap-add",
                        "SETUID",
                        "--cap-add",
                        "SETGID",
                    ]
                else:
                    args += [
                        "--user",
                        "10001:10001",
                        "-e",
                        "LIVOLO_API_TOKEN=" + TOKEN,
                        "-v",
                        str(root / "config.json") + ":/etc/livolod/config.json:ro",
                    ]
                args += [current]
                if kind == "standalone":
                    args += ["--config", "/etc/livolod/config.json", "--simulate"]
                docker(*args)
                probe = [
                    "exec",
                    name,
                    "python",
                    "/checks/container_probe.py",
                    "--kind",
                    kind,
                ]
                if number == 0:
                    probe += ["--seed"]
                print(docker(*probe))
                if kind == "ha":
                    print(
                        docker(
                            "run",
                            "--rm",
                            "--network",
                            identity,
                            "--ip",
                            "172.30.32.2",
                            "--entrypoint",
                            "python",
                            "-v",
                            checks,
                            current,
                            "/checks/container_probe.py",
                            "--ingress",
                            name,
                        )
                    )
                docker("stop", "-t", "20", name)
                assert docker("inspect", "--format", "{{.State.ExitCode}}", name) == "0"
                docker("rm", name)
                containers.remove(name)
                if kind == "ha" and number == 0:
                    # Model root-owned files restored from a backup; wrapper must repair them.
                    docker(
                        "run",
                        "--rm",
                        "--network",
                        "none",
                        "--user",
                        "0",
                        "--entrypoint",
                        "python",
                        "-v",
                        str(state) + ":/data",
                        image,
                        "-c",
                        "from pathlib import Path; import os; [os.chown(p,0,0) for p in Path('/data').glob('state.sqlite3*')]",
                    )
            database = "/data/state.sqlite3" + (
                ".simulated" if kind == "standalone" else ""
            )
            print(
                docker(
                    "run",
                    "--rm",
                    "--network",
                    "none",
                    "--entrypoint",
                    "python",
                    "--user",
                    "10001:10001",
                    "-v",
                    str(state) + ":/data",
                    image,
                    "-c",
                    "import sqlite3; d=sqlite3.connect("
                    + repr(database)
                    + "); assert d.execute('pragma integrity_check').fetchone()[0]=='ok'; assert d.execute('select count(*) from devices').fetchone()[0]==1; print('SQLite persistence/integrity passed')",
                )
            )
            print(
                json.dumps(
                    {
                        "image": image,
                        "previous_image": previous,
                        "test": (
                            "version-upgrade"
                            if previous
                            else "first-install-and-recreation"
                        ),
                        "kind": kind,
                        "passed": True,
                        "radio_access": False,
                    }
                )
            )
        except BaseException:
            for name in containers:
                subprocess.run(["docker", "logs", "--tail", "60", name], check=False)
            raise
        finally:
            for name in containers:
                subprocess.run(
                    ["docker", "rm", "-f", name], stdout=subprocess.DEVNULL, check=False
                )
            if network_created:
                docker("network", "rm", identity)
            # Return ownership so TemporaryDirectory can remove protected daemon state.
            docker(
                "run",
                "--rm",
                "--network",
                "none",
                "--user",
                "0",
                "--entrypoint",
                "python",
                "-v",
                str(state) + ":/data",
                image,
                "-c",
                "import os; from pathlib import Path; "
                + f"[os.chown(p,{root.stat().st_uid},{root.stat().st_gid}) for p in [Path('/data'),*Path('/data').rglob('*')]]",
            )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", required=True)
    parser.add_argument("--kind", choices=["standalone", "ha"], required=True)
    parser.add_argument("--broker-image", required=True)
    parser.add_argument("--previous-image")
    args = parser.parse_args()
    check(args.image, args.kind, args.broker_image, args.previous_image)
