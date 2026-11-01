"""Exercise the installed image as its non-root user without a radio or broker."""

import json
from importlib.metadata import version
import os
from pathlib import Path
import signal
import secrets
import socket
import sqlite3
import subprocess
import tempfile
import time
import urllib.error
import urllib.request

from livolod import __version__


def main():
    """Check packaging, simulated startup, web assets, SQLite and clean SIGTERM."""
    assert os.geteuid() != 0, "Image smoke test must run as the daemon user"
    assert (
        subprocess.check_output(["livolod", "--version"], text=True).strip()
        == __version__
    )
    assert version("paho-mqtt") == "2.1.0"
    with tempfile.TemporaryDirectory(
        dir=os.environ.get("LIVOLO_SMOKE_ROOT")
    ) as directory:
        root = Path(directory)
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
            with socket.socket() as api_listener:
                api_listener.bind(("127.0.0.1", 0))
                api_port = api_listener.getsockname()[1]
        config = {
            "network": {"gateway": "10203040", "tag": "42"},
            "devices": [],
            "serial_port": "/dev/nonexistent",
            "serial_baudrate": 460800,
            "database": str(root / "state.sqlite3"),
            "output_mode": "experimental_phase",
            "mqtt": {"enabled": False},
            "web": {"enabled": True, "host": "127.0.0.1", "port": port},
            "api": {
                "enabled": True,
                "host": "127.0.0.1",
                "port": api_port,
                "token_env": "LIVOLO_SMOKE_API_TOKEN",
            },
        }
        path = root / "config.json"
        path.write_text(json.dumps(config))
        command = ["livolod", "--config", str(path)]
        subprocess.run(command + ["--check-config"], check=True)
        with (root / "daemon.log").open("w+") as log:
            token = secrets.token_urlsafe(32)
            process = subprocess.Popen(
                command + ["--simulate"],
                stdout=log,
                stderr=log,
                env=dict(os.environ, LIVOLO_SMOKE_API_TOKEN=token),
            )
            try:
                deadline = time.monotonic() + 15
                while time.monotonic() < deadline:
                    if process.poll() is not None:
                        raise RuntimeError("Daemon exited before readiness")
                    try:
                        with urllib.request.urlopen(
                            f"http://127.0.0.1:{port}/api/state", timeout=1
                        ) as response:
                            state = json.load(response)
                        if state["ready"] and state["simulated"]:
                            break
                    except (OSError, urllib.error.URLError):
                        pass
                    time.sleep(0.1)
                else:
                    raise RuntimeError("Simulated radio did not become ready")
                with urllib.request.urlopen(
                    f"http://127.0.0.1:{port}/", timeout=2
                ) as response:
                    assert (
                        response.status == 200 and b"<html" in response.read().lower()
                    )
                api_url = f"http://127.0.0.1:{api_port}/api/v1/snapshot"
                try:
                    urllib.request.urlopen(api_url, timeout=2)
                except urllib.error.HTTPError as error:
                    assert error.code == 401
                else:
                    raise AssertionError(
                        "Machine API accepted an unauthenticated request"
                    )
                request = urllib.request.Request(
                    api_url, headers={"Authorization": "Bearer " + token}
                )
                with urllib.request.urlopen(request, timeout=2) as response:
                    snapshot = json.load(response)
                assert snapshot["api_version"] == 1
                assert snapshot["data"]["ready"] and snapshot["data"]["simulated"]
                assert "mqtt" not in snapshot["data"]
                assert state["mqtt"]["enabled"] is False
                process.send_signal(signal.SIGTERM)
                assert process.wait(timeout=10) == 0
                with sqlite3.connect(str(root / "state.sqlite3.simulated")) as db:
                    assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
                    assert db.execute(
                        "SELECT value FROM meta WHERE key='network'"
                    ).fetchone()
            except BaseException:
                log.seek(0)
                print(log.read())
                raise
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait()
    print(f"livolod {__version__}: non-root daemon/web/native API smoke check passed")


if __name__ == "__main__":
    main()
