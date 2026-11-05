"""Explicit offline firmware maintenance; stop the deployment's daemon first.

This command owns the normal database/radio locks, reuses the update manager and
confirms via normal session restoration. It never manages systemd/Docker or GPIO.
Use the board recovery procedure when application firmware cannot answer UART.
"""

import argparse
import asyncio
from contextlib import contextmanager
from dataclasses import replace
import fcntl
import json
import os
import re
import sqlite3
import stat
import time
import uuid
from pathlib import Path

from .config import load
from .firmware_manager import radio_guard
from .firmware_update import UpdatePort
from .protocol import key_values
from .service import Service
from .storage import Store


@contextmanager
def probe_ownership(config):
    """Claim deployment and radio guards without initializing or modifying SQLite.

    @param config Validated deployment configuration whose owner must be stopped.
    @yield Held radio guard descriptor; lock files may be created, never unlinked.
    """
    database_lock = None
    guard = None
    try:
        database_lock = os.open(
            config.database + ".lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600
        )
        if not stat.S_ISREG(os.fstat(database_lock).st_mode):
            raise OSError("Deployment lock must be a regular file")
        fcntl.flock(database_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        guard = radio_guard(config.database)
        yield guard
    finally:
        if guard is not None:
            os.close(guard)
        if database_lock is not None:
            os.close(database_lock)


def probe_identity(config):
    """Read current firmware identity under both guards without restore or disarm.

    @param config Stopped deployment configuration and exact application serial path.
    @return Detached CAPS, VERSION and optional TI UPDATE_STATUS dictionaries.
    """
    from .firmware_bundle import BundleError

    with probe_ownership(config):
        passive = True
        if config.firmware_nordic:
            from .firmware_nordic import target

            if target(config.firmware_nordic, "application") != config.serial_port:
                raise BundleError(
                    "usb_identity_unavailable", "Nordic application node changed"
                )
            # Native USB CDC requires DTR to transmit; this board does not use it for reset.
            passive = False
        port = UpdatePort(config.serial_port, config.serial_baudrate, passive=passive)
        try:
            if port.command("PING", timeout=3, attempts=1) != "OK EC-GATEWAY 3":
                raise BundleError(
                    "incompatible_protocol", "Application protocol is unsupported"
                )
            version = key_values(port.command("VERSION", timeout=3, attempts=1))
            caps = key_values(port.command("CAPS", timeout=3, attempts=1))
            port.command("STATUS", timeout=3, attempts=1)
            if caps.get("board") != version.get("board") or caps.get(
                "fw_version"
            ) != version.get("fw_version"):
                raise BundleError(
                    "board_identity_changed",
                    "Application identity changed during probe",
                )
            update = {}
            if (
                caps.get("board") == "codm-cc2652p"
                and version.get("update") == "uart_signed_v1"
            ):
                update = key_values(
                    port.command("UPDATE_STATUS", timeout=3, attempts=1)
                )
            return caps, version, update
        finally:
            port.close()


def preflight(config, candidate_id=None, board=None, probe=False):
    """Return the common report, probing only when the operator explicitly asks.

    @param config Validated local deployment configuration.
    @param candidate_id Optional qualified catalog selection.
    @param board Optional operator-specified expected board.
    @param probe Whether to acquire ownership and send read-only application queries.
    @return Public schema-1 report without raw transport output or private paths.
    """
    from .firmware_bundle import BundleError
    from .firmware_preflight import build_report

    if not probe:
        return build_report(
            config,
            candidate_id,
            board=board,
            identity_source="explicit_operator" if board else "unknown",
        )
    try:
        caps, version, update = probe_identity(config)
        return build_report(
            config,
            candidate_id,
            board=board or caps.get("board"),
            caps=caps,
            version_info=version,
            update_info=update,
            live=True,
            identity_source="explicit_probe",
        )
    except (OSError, ValueError, RuntimeError, TimeoutError) as error:
        report = build_report(
            config,
            candidate_id,
            board=board,
            identity_source="explicit_operator" if board else "unknown",
        )
        code = (
            error.code
            if isinstance(error, BundleError)
            else (
                "radio_busy"
                if isinstance(error, BlockingIOError)
                else "device_access_unavailable"
            )
        )
        report["checks"].append(dict(id="probe", status="fail", code=code))
        report.update(executable=False, blocked_reason=code)
        return report


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


async def maintain(
    config,
    action,
    candidate_id=None,
    *,
    local_candidate=None,
    worker_arguments=None,
    application_serial=None,
):
    """Run the shared daemon lifecycle until explicit offline maintenance finishes.

    @param config Validated local config; only its radio and authoritative state are used.
    @param action Install a bundled candidate or recover the journaled TI operation.
    @param candidate_id Opaque qualified candidate identity, required for install.
    @param local_candidate Optional inspected operator artifact for CLI-only routine updates.
    @param worker_arguments Fixed local-artifact worker arguments constructed by this CLI.
    @param application_serial Explicit post-DFU application USB identity for manual recovery.
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
            if job.get("operation") == "recovery":
                service.firmware_manager.manual_restore_serial = application_serial
                service.firmware_manager.check_manual_restore()
            else:
                if application_serial:
                    raise ValueError(
                        "Application serial confirmation applies only to manual Nordic recovery"
                    )
                guard = radio_guard(config.database)
                port = None
                try:
                    port = UpdatePort(config.serial_port, config.serial_baudrate)
                    print(
                        json.dumps({"recovery_action": recover_ti(port, job)}),
                        flush=True,
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
                payload = dict(request_id=ident, candidate_id=candidate_id)
                result = (
                    service.firmware_manager.start_local(
                        payload, local_candidate, worker_arguments
                    )
                    if local_candidate
                    else service.start_firmware_update(payload)
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
                "operation",
                "transfer_completed",
            )
        }
    return None


def local_arguments(config, args, candidate):
    """Build fixed private worker arguments from validated local CLI inputs.

    @param config Deployment settings containing the normal application port/rate.
    @param args Parsed operator command line; no HTTP caller can supply these options.
    @param candidate Validated immutable operator artifact.
    @return Argument list without a shell, service command or arbitrary helper flags.
    """
    values = [
        "--artifact",
        str(candidate["path"]),
        "--candidate-id",
        candidate["id"],
        "--board",
        candidate["board"],
        "--version",
        candidate["version"],
        "--operation",
        args.operation,
        "--baud",
        str(config.serial_baudrate),
        "--port",
        args.dfu_port if args.operation == "recovery" else config.serial_port,
    ]
    if args.public_key:
        values += ["--public-key", args.public_key]
    if args.operation == "recovery":
        values += [
            "--dfu-serial",
            args.dfu_serial,
            "--helper-python",
            args.helper_python,
        ]
        if config.firmware_nordic:
            values += [
                "--nordic-profile",
                json.dumps(config.firmware_nordic, sort_keys=True),
            ]
    return values


async def manual_flash(config, candidate, worker_arguments, *, retry=False):
    """Journal an explicit DFU transfer without entering or restoring application mode.

    @param config Stopped deployment configuration and authoritative database.
    @param candidate Private validated Nordic recovery artifact.
    @param worker_arguments Fixed worker command arguments with exact DFU target identity.
    @param retry Explicitly supersede an interrupted Nordic job, preserving its command result.
    @return Durable result; transfer completion still requires manual application verification.
    """
    config = replace(
        config, mqtt={"enabled": False}, web={"enabled": False}, api={"enabled": False}
    )
    store = Store(config.database, config.gateway + ":" + config.tag)
    guard = None
    try:
        service = Service(config, store)
        manager = service.firmware_manager
        if manager.busy and not (
            retry
            and manager.job.get("backend") == "nrf52840_usb_dfu_v1"
            and manager.job.get("board") == candidate["board"]
            and manager.job["status"] == "unknown"
        ):
            raise ValueError(
                "An interrupted firmware job requires recovery or explicit --retry-interrupted"
            )
        guard = radio_guard(config.database)
        ident = "manual-" + uuid.uuid4().hex
        store.begin(ident, dict(action="firmware_update", candidate_id=candidate["id"]))
        previous = manager.job.get("request_id") if manager.job else None
        manager.job = dict(
            request_id=ident,
            action="firmware_update",
            operation="recovery",
            candidate_id=candidate["id"],
            board=candidate["board"],
            version=candidate["version"],
            installed_version=None,
            baudrate=config.serial_baudrate,
            backend=candidate["backend"],
            rollback_supported=False,
            status="pending",
            stage="preparing",
            error=None,
            recovery=None,
            last_critical=None,
            transfer_completed=False,
            previous_request_id=previous,
            started_at=time.time(),
        )
        if config.firmware_nordic:
            manager.job["nordic_profile"] = dict(config.firmware_nordic)
        manager.persist()
        owned, guard = guard, None
        await manager.execute(owned, worker_arguments)
        result = store.command_result(ident)
        result["transfer_completed"] = manager.job.get("transfer_completed", False)
        return result
    finally:
        if guard is not None:
            os.close(guard)
        store.close()


def main(argv=None):
    """Inspect artifacts, preflight or maintain a stopped deployment without service control.

    @param argv Optional arguments for isolated tests; None uses the process command line.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config")
    commands = parser.add_subparsers(dest="action", required=True)
    install = commands.add_parser("install")
    install.add_argument("--candidate-id", required=True)
    recover = commands.add_parser("recover")
    recover.add_argument("--application-serial")
    commands.add_parser("status")
    inspect = commands.add_parser("inspect")
    inspect.add_argument("--artifact", required=True)
    inspect.add_argument("--board", required=True)
    inspect.add_argument("--version", required=True)
    inspect.add_argument("--public-key")
    plan = commands.add_parser("preflight")
    plan.add_argument("--candidate-id")
    plan.add_argument("--board")
    plan.add_argument("--probe", action="store_true")
    flash = commands.add_parser("flash")
    flash.add_argument("--operation", choices=("update", "recovery"), required=True)
    flash.add_argument("--artifact", required=True)
    flash.add_argument("--board", required=True)
    flash.add_argument("--version", required=True)
    flash.add_argument("--sha256", required=True)
    flash.add_argument("--public-key")
    flash.add_argument("--dfu-port")
    flash.add_argument("--dfu-serial")
    flash.add_argument("--helper-python")
    flash.add_argument("--check-only", action="store_true")
    flash.add_argument("--probe", action="store_true")
    flash.add_argument("--retry-interrupted", action="store_true")
    args = parser.parse_args(argv)
    if args.action != "inspect" and not args.config:
        parser.error("--config is required for this operation")
    try:
        if args.action == "inspect":
            from .firmware_artifacts import inspect_artifact

            candidate = inspect_artifact(
                args.artifact,
                board=args.board,
                version=args.version,
                public_key=args.public_key,
            )
            print(
                json.dumps(
                    dict(
                        schema=1,
                        operation="inspect",
                        candidate_id=candidate["id"],
                        inspection=candidate["inspection"],
                    )
                ),
                flush=True,
            )
            return
        config = load(args.config)
        if args.action == "flash":
            from . import __version__
            from .firmware_artifacts import inspect_artifact
            from .firmware_bundle import BundleError
            from .firmware_preflight import build_report

            candidate = inspect_artifact(
                args.artifact,
                board=args.board,
                version=args.version,
                public_key=args.public_key,
                operation=args.operation,
            )
            if (
                not re.fullmatch(r"[0-9a-f]{64}", args.sha256)
                or args.sha256 != candidate["sha256"]
            ):
                raise BundleError(
                    "artifact_invalid", "Artifact does not match the reviewed SHA-256"
                )
            if (
                candidate["version"] != __version__
                or config.serial_baudrate != candidate["app_baudrate"]
            ):
                raise BundleError(
                    "incompatible_host",
                    "Artifact version and rate must match this deployment",
                )
            if args.operation == "recovery":
                if (
                    not all((args.dfu_port, args.dfu_serial, args.helper_python))
                    or args.probe
                ):
                    raise ValueError(
                        "Recovery requires exact DFU port, serial and helper; no application --probe"
                    )
                from .firmware_manual import manual_backend_status

                report = manual_backend_status(
                    candidate,
                    helper_python=args.helper_python,
                    dfu_port=args.dfu_port,
                    serial_number=args.dfu_serial,
                    usb_profile=config.firmware_nordic,
                )
                report.update(
                    schema=1,
                    operation="recovery",
                    candidate_id=candidate["id"],
                    board=candidate["board"],
                    target_version=candidate["version"],
                    rollback_supported=False,
                )
            else:
                if any(
                    (
                        args.dfu_port,
                        args.dfu_serial,
                        args.helper_python,
                        args.retry_interrupted,
                    )
                ):
                    raise ValueError(
                        "Routine updates cannot override their helper or DFU transport"
                    )
                if args.probe or not args.check_only:
                    caps, version, update = probe_identity(config)
                    report = build_report(
                        config,
                        candidate["id"],
                        board=args.board,
                        candidate=candidate,
                        caps=caps,
                        version_info=version,
                        update_info=update,
                        live=True,
                        identity_source="explicit_probe",
                    )
                else:
                    report = build_report(
                        config,
                        candidate["id"],
                        board=args.board,
                        candidate=candidate,
                        identity_source="explicit_operator",
                    )
            print(json.dumps(report), flush=True)
            if not report["executable"]:
                parser.exit(2)
            if args.check_only:
                return
            arguments = local_arguments(config, args, candidate)
            result = asyncio.run(
                manual_flash(config, candidate, arguments, retry=args.retry_interrupted)
                if args.operation == "recovery"
                else maintain(
                    config,
                    "install",
                    candidate["id"],
                    local_candidate=candidate,
                    worker_arguments=arguments,
                )
            )
            print(json.dumps(result), flush=True)
            if result["status"] != "confirmed":
                parser.exit(2)
            return
        if args.action == "preflight":
            report = preflight(config, args.candidate_id, args.board, args.probe)
            print(json.dumps(report), flush=True)
            if not report["executable"]:
                parser.exit(2)
            return
        if args.action == "status":
            print(json.dumps(saved_job(config.database)), flush=True)
            return
        if args.action == "install" and not re.fullmatch(
            r"[A-Za-z0-9_.:-]{1,256}", args.candidate_id
        ):
            raise ValueError("Invalid candidate_id")
        result = asyncio.run(
            maintain(
                config,
                args.action,
                getattr(args, "candidate_id", None),
                application_serial=getattr(args, "application_serial", None),
            )
        )
        print(json.dumps(result), flush=True)
        if result["status"] != "confirmed":
            parser.exit(
                1,
                "Firmware update was not confirmed; inspect status and recovery instructions\n",
            )
    except (ValueError, RuntimeError, OSError) as error:
        from .firmware_bundle import BundleError

        if args.action in ("inspect", "preflight", "flash"):
            print(
                json.dumps(
                    dict(
                        schema=1,
                        status="rejected",
                        error=(
                            error.code
                            if isinstance(error, BundleError)
                            else "preflight_failed"
                        ),
                    )
                ),
                flush=True,
            )
            parser.exit(1)
        parser.exit(1, f"livolod-firmware-manage: {error}\n")


if __name__ == "__main__":
    main()
