"""Explicit offline firmware maintenance; stop the deployment's daemon first.

This command owns the normal database/radio locks, reuses the update manager and
confirms via normal session restoration. It never manages systemd/Docker or GPIO.
Use the board recovery procedure when application firmware cannot answer UART.
"""

import argparse
import asyncio
from dataclasses import replace
import json
import os
import re
import sqlite3
import time
import uuid
from pathlib import Path

from .config import load
from .firmware_manager import radio_guard
from .firmware_update import UpdatePort
from .protocol import key_values
from .service import Service
from .storage import Store


def recover_ti(port, job):
    """Reconcile only the journaled TI job without erasing a committed candidate.

    @param port Exclusive update transport while the normal daemon is stopped.
    @param job Persisted update identity, selected digest and original image digest.
    @return Description of the recovery action; normal restore must still confirm.
    """
    if not job or job.get("backend") != "ti_uart_signed_v1":
        raise ValueError(
            "No journaled TI update; use the exact-board recovery procedure"
        )
    version = key_values(port.command("VERSION", timeout=3, attempts=1))
    status = key_values(port.command("UPDATE_STATUS", timeout=3, attempts=1))
    if (
        version.get("board") != job["board"]
        or status.get("key") != job.get("public_key")
        or not job.get("public_key")
    ):
        raise ValueError(
            "Recovery board or signing identity does not match the journal"
        )
    if status.get("state") == "fffffffe":
        if status.get("candidate_sha512") != job.get("image_sha512"):
            raise ValueError(
                "Different committed candidate; refusing recovery mutation"
            )
        try:
            port.command("UPDATE_ACTIVATE", timeout=3, attempts=1)
        except TimeoutError:
            pass
        return "activation_requested"
    if status.get("trial") == "1":
        if status.get("current_sha512") != job.get("image_sha512"):
            raise ValueError("Trial does not match the selected image")
        return "resume_trial_confirmation"
    if (
        version.get("fw_version") == job.get("version")
        and status.get("trial") == "0"
        and status.get("state") == "fffffff0"
        and status.get("current_sha512") == job.get("image_sha512")
    ):
        return "selected_image_ready"
    if (
        version.get("fw_version") != job.get("installed_version")
        or not job.get("original_sha512")
        or status.get("current_sha512") != job["original_sha512"]
        or status.get("state") not in ("ffffffff", "fffffff0", "ffffffc8")
        or status.get("trial") != "0"
    ):
        raise ValueError("Unrecognized update state; refusing abort or reset")
    if status.get("receiving") == "1":
        port.command("UPDATE_ABORT", timeout=3, attempts=1)
        try:
            port.command("UPDATE_REBOOT", timeout=3, attempts=1)
        except TimeoutError:
            pass
        return "incomplete_transfer_aborted"
    if status.get("receiving") != "0":
        raise ValueError("Unknown transfer state; refusing recovery mutation")
    return "previous_image_ready"


async def maintain(config, action, candidate_id=None):
    """Run the shared daemon lifecycle until explicit offline maintenance finishes.

    @param config Validated local config; only its radio and authoritative state are used.
    @param action Install a bundled candidate or recover the journaled TI operation.
    @param candidate_id Opaque qualified candidate identity, required for install.
    @return Final public result; unknown/rejected is never presented as success.
    """
    config = replace(
        config, mqtt={"enabled": False}, web={"enabled": False}, api={"enabled": False}
    )
    store = Store(config.database, config.gateway + ":" + config.tag)
    service = None
    task = None
    try:
        service = Service(config, store)
        if action == "recover":
            job = store.firmware_job()
            if not job or job.get("status") not in ("pending", "unknown"):
                raise ValueError("No interrupted firmware job to recover")
            guard = radio_guard(config.database)
            port = None
            try:
                port = UpdatePort(config.serial_port, config.serial_baudrate)
                print(
                    json.dumps({"recovery_action": recover_ti(port, job)}), flush=True
                )
            finally:
                if port:
                    port.close()
                os.close(guard)
        task = asyncio.create_task(service.run())
        deadline = time.monotonic() + (240 if action == "install" else 60)
        ident = None
        previous = None
        while time.monotonic() < deadline:
            if task.done():
                await task
                raise RuntimeError("Radio service ended before firmware verification")
            if action == "install" and service.ready and ident is None:
                ident = "offline-" + uuid.uuid4().hex
                result = service.start_firmware_update(
                    dict(request_id=ident, candidate_id=candidate_id)
                )
                if result["status"] != "pending":
                    return result
            state = service.firmware_update_snapshot()
            encoded = json.dumps(state, sort_keys=True)
            if encoded != previous:
                print(encoded, flush=True)
                previous = encoded
            if ident:
                result = store.command_result(ident)
                if result["status"] != "pending":
                    return result
            elif (
                action == "recover"
                and service.ready
                and not service.firmware_manager.busy
            ):
                return store.command_result(service.firmware_manager.job["request_id"])
            await asyncio.sleep(0.1)
        raise RuntimeError(
            "Maintenance verification timed out; inspect recovery status"
        )
    finally:
        if service:
            service.stop.set()
        if task:
            await task
        store.close()


def saved_job(database):
    """Read status while the daemon is running without claiming radio ownership.

    @param database Existing authoritative SQLite database path.
    @return Detached firmware job or None when no maintenance was recorded.
    """
    with sqlite3.connect(
        Path(database).resolve().as_uri() + "?mode=ro", uri=True
    ) as db:
        row = db.execute("SELECT value FROM meta WHERE key='firmware_job'").fetchone()
    job = json.loads(row[0]) if row else None
    if job:
        return {
            key: job.get(key)
            for key in (
                "request_id",
                "candidate_id",
                "status",
                "stage",
                "error",
                "recovery",
                "board",
                "version",
            )
        }
    return None


def main():
    """Inspect status or explicitly maintain a stopped deployment without service control."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True)
    commands = parser.add_subparsers(dest="action", required=True)
    install = commands.add_parser("install")
    install.add_argument("--candidate-id", required=True)
    commands.add_parser("recover")
    commands.add_parser("status")
    args = parser.parse_args()
    try:
        config = load(args.config)
        if args.action == "status":
            print(json.dumps(saved_job(config.database)), flush=True)
            return
        if args.action == "install" and not re.fullmatch(
            r"[A-Za-z0-9_.:-]{1,256}", args.candidate_id
        ):
            raise ValueError("Invalid candidate_id")
        result = asyncio.run(
            maintain(config, args.action, getattr(args, "candidate_id", None))
        )
        print(json.dumps(result), flush=True)
        if result["status"] != "confirmed":
            parser.exit(
                1,
                "Firmware update was not confirmed; inspect status and recovery instructions\n",
            )
    except (ValueError, RuntimeError, OSError) as error:
        parser.exit(1, f"livolod-firmware-manage: {error}\n")


if __name__ == "__main__":
    main()
