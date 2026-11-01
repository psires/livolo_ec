"""Signed UART updates. Optional systemd handoff; never manipulates GPIO.

Use with the daemon stopped, or --service livolod for a managed stop/start.
The matching host release must already be installed to confirm the trial boot.
"""

import argparse
import json
from pathlib import Path
import re
import secrets
import sqlite3
import subprocess
import time

from . import __version__
from .config import load, uart_baudrate
from .protocol import key_values
from .update_package import inspect_package, public_key
from .version import parse_version


class UpdatePort:
    def __init__(self, port, baudrate=115200):
        """Claim an exclusive serial connection for a firmware update session.

        @param port Serial device path.
        @param baudrate Application rate matching the installed firmware.
        """
        import serial

        self.serial = serial.Serial(
            port,
            uart_baudrate(baudrate),
            timeout=0.2,
            write_timeout=2,
            exclusive=True,
            rtscts=False,
            dsrdtr=False,
        )
        self.request_id = secrets.randbelow(0xFFFFFF00) + 1

    def close(self):
        """Release the updater serial connection."""
        self.serial.close()

    def command(self, text, timeout=15, attempts=2):
        """Request an updater operation with bounded retries under one request ID.

        @param text UART operation and arguments without transport framing.
        @param timeout Seconds allowed for each matching reply.
        @param attempts Maximum number of transmissions of the same request.
        @return The successful OK response; missing replies raise TimeoutError.
        """
        ident = self.request_id
        self.request_id = ident % 0xFFFFFFFF + 1
        raw = f"{ident} {text}\n".encode("ascii")
        if len(raw) > 320 or any(x < 32 or x > 126 for x in raw[:-1]):
            raise ValueError("Invalid UART update command")
        for _ in range(attempts):
            self.serial.write(raw)
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                line = self.serial.readline(1025)
                if len(line) > 1024:
                    raise RuntimeError("Oversized UART reply")
                prefix = f"RSP {ident} ".encode()
                if line.startswith(prefix):
                    value = line[len(prefix) :].decode("ascii").strip()
                    if not value.startswith("OK"):
                        raise RuntimeError(value)
                    return value
        raise TimeoutError(
            "UART update response missing; inspect status before retrying activation"
        )


def transfer(
    port, package, public, progress=lambda value: None, transaction=None, before=None
):
    """Verify, transfer, and activate a newer signed firmware candidate.

    @param port UpdatePort connected to the installed adapter.
    @param package Signed manifest and application image bytes.
    @param public Ed25519 public key used to verify the package.
    @param progress Callback receiving progress dictionaries.
    @param transaction Optional mutable ownership and commit-state record for recovery.
    @param before Optional blocking phase callback; must return before each destructive
        boundary. A daemon worker uses this to await durable parent acknowledgement.
    @return Verified candidate package metadata; trial confirmation remains the daemon's
        responsibility.
    """
    transaction = transaction if transaction is not None else {}
    manifest, image = package[:192], package[192:]
    info = inspect_package(package, public)
    running = key_values(port.command("VERSION"))
    if (
        running.get("update") != "uart_signed_v1"
        or running.get("board") != "codm-cc2652p"
    ):
        raise RuntimeError("This firmware needs the one-time bootloader installation")
    if parse_version(info["version"]) <= parse_version(running.get("fw_version")):
        raise ValueError("Candidate version must be newer than running firmware")
    status = key_values(port.command("UPDATE_STATUS"))
    from cryptography.hazmat.primitives import serialization

    if (
        status.get("key")
        != public.public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw
        ).hex()
    ):
        raise ValueError("Package verification key does not match installed firmware")
    if status.get("state") == "fffffffe":
        if status.get("candidate_sha512") != info["sha512"]:
            raise RuntimeError(
                "A different package is already committed; refusing activation"
            )
        transaction["started"] = True
        transaction["commit_possible"] = True
        if before:
            before("activate")
        try:
            port.command("UPDATE_ACTIVATE", timeout=3, attempts=1)
        except TimeoutError:
            pass  # Same uncertain-reset handling as a newly committed package.
        return info
    if status.get("trial") != "0" or status.get("state") in ("fffffffc", "ffffffe8"):
        raise RuntimeError("An earlier update must finish or recover first")
    # Preflight is read-only. Only this point transfers ownership to this update.
    if before:
        before("disarm")
    transaction["started"] = True
    port.command("DISARM")
    token = secrets.token_hex(8)
    if before:
        before("begin")
    port.command("UPDATE_ABORT")
    for offset in (0, 96):
        port.command(
            f"UPDATE_HEADER {token} {offset} {manifest[offset:offset+96].hex()}"
        )
    port.command(f"UPDATE_BEGIN {token}")
    last_percent = -1
    for offset in range(0, len(image), 96):
        data = image[offset : offset + 96]
        reply = key_values(port.command(f"UPDATE_DATA {token} {offset} {data.hex()}"))
        if reply.get("received") != str(offset + len(data)):
            raise RuntimeError("Firmware transfer offset mismatch")
        percent = (offset + len(data)) * 100 // len(image)
        if percent != last_percent:
            progress(dict(stage="transfer", percent=percent))
            last_percent = percent
    if before:
        before("commit")
    transaction["commit_possible"] = True
    try:
        port.command(f"UPDATE_COMMIT {token}", timeout=30)
    except TimeoutError:
        # The commit may be durable despite a lost response. Query, never erase.
        pass
    committed = key_values(port.command("UPDATE_STATUS"))
    if (
        committed.get("state") != "fffffffe"
        or committed.get("candidate_sha512") != info["sha512"]
    ):
        raise RuntimeError("Candidate was not durably committed")
    progress(dict(stage="committed", version=info["version"]))
    # Do not blindly retry reset under a new ID: it may already have happened.
    if before:
        before("activate")
    try:
        port.command("UPDATE_ACTIVATE", timeout=3, attempts=1)
    except TimeoutError:
        pass
    return info


def recover_failed_transfer(port, transaction, info, progress=lambda value: None):
    """Recover only an update owned by this attempt without erasing a committed candidate.

    @param port UpdatePort connected to the adapter.
    @param transaction Ownership and commit-state record populated by transfer.
    @param info Verified metadata for the intended package.
    @param progress Callback receiving recovery status dictionaries.
    """
    if not transaction.get("started"):
        return  # Wrong key/version/existing trial is not permission to reset.
    try:
        status = key_values(port.command("UPDATE_STATUS", timeout=2, attempts=1))
    except Exception:
        progress(
            dict(
                stage="recovery_unknown",
                action="Restart daemon; inspect firmware status",
            )
        )
        return
    if status.get("state") == "fffffffe":
        if status.get("candidate_sha512") == info["sha512"]:
            try:
                port.command("UPDATE_ACTIVATE", timeout=3, attempts=1)
            except TimeoutError:
                pass
            progress(dict(stage="activation_recovery", version=info["version"]))
        else:
            progress(
                dict(
                    stage="different_pending_package",
                    action="Inspect before activating",
                )
            )
        return
    if status.get("trial") == "1":
        return  # Let the matching daemon confirm; do not turn a lost reply into rollback.
    try:
        port.command("UPDATE_ABORT", timeout=2, attempts=1)
    finally:
        # A failed abort must not suppress restoring normal RX after a transfer.
        port.command("UPDATE_REBOOT", timeout=2, attempts=1)


def saved_status(database):
    """Read daemon-reported firmware status without modifying its database.

    @param database Path to the daemon state database.
    @return Saved firmware status, or an empty mapping when absent.
    """
    with sqlite3.connect(
        Path(database).resolve().as_uri() + "?mode=ro", uri=True, timeout=1
    ) as db:
        row = db.execute(
            "SELECT value FROM meta WHERE key='firmware_status'"
        ).fetchone()
    return json.loads(row[0]) if row else {}


def main():
    """Run a signed update with optional service handoff and trial verification."""
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--config", required=True)
    p.add_argument("--package", required=True)
    p.add_argument("--public-key", required=True)
    p.add_argument(
        "--service",
        help="Systemd service to stop and restart, e.g. livolod; requires appropriate permissions",
    )
    a = p.parse_args()
    config = load(a.config)
    data = Path(a.package).read_bytes()
    key = public_key(a.public_key)
    info = inspect_package(data, key)
    if info["version"] != __version__:
        p.error("Install the matching host release before updating firmware")
    if a.service and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.@-]*", a.service):
        p.error("Invalid service name")
    if (
        a.service
        and saved_status(config.database).get("target_version") != info["version"]
    ):
        p.error("Running service has not reported the matching target firmware version")
    port = None
    stopped = False
    completed = False
    started = time.time()
    transaction = {}
    # Emit machine-readable update progress for an operator or service wrapper.
    # @param value Progress or recovery status dictionary to write as one JSON line.
    progress = lambda value: print(json.dumps(value), flush=True)
    try:
        if a.service:
            subprocess.run(["systemctl", "stop", a.service], check=True)
            stopped = True
        port = UpdatePort(config.serial_port, config.serial_baudrate)
        transfer(port, data, key, progress, transaction)
        deadline = time.monotonic() + 35
        while time.monotonic() < deadline:
            try:
                version = key_values(port.command("VERSION", timeout=1, attempts=1))
                status = key_values(
                    port.command("UPDATE_STATUS", timeout=1, attempts=1)
                )
                if (
                    version.get("fw_version") == info["version"]
                    and status.get("trial") == "1"
                    and status.get("current_sha512") == info["sha512"]
                ):
                    completed = True
                    break
            except (TimeoutError, RuntimeError):
                pass
            time.sleep(0.2)
        if not completed:
            raise RuntimeError(
                "Candidate did not reach trial boot; inspect rollback status"
            )
    finally:
        if port:
            if not completed:
                try:
                    recover_failed_transfer(port, transaction, info, progress)
                except Exception:
                    pass  # ROM/GPIO recovery remains independent; never hide original error.
            port.close()
        if stopped:
            subprocess.run(["systemctl", "start", a.service], check=True)
    if a.service:
        deadline = time.monotonic() + 70
        while time.monotonic() < deadline:
            state = saved_status(config.database)
            if (
                state.get("verified_at", 0) > started
                and state.get("running_version") == info["version"]
                and state.get("trial") is False
            ):
                progress(
                    dict(
                        stage="confirmed",
                        version=info["version"],
                        session=state.get("session"),
                    )
                )
                return
            time.sleep(0.5)
        raise RuntimeError(
            "Host did not confirm trial; inspect service and rollback status"
        )
    progress(
        dict(
            stage="trial",
            version=info["version"],
            action="Start matching daemon within 60 seconds to confirm; otherwise rollback",
        )
    )


if __name__ == "__main__":
    main()
