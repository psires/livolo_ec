"""Durable firmware jobs with an exclusive, deployment-independent radio handoff."""

import asyncio
import fcntl
import json
import os
from pathlib import Path
import re
import signal
import stat
import sys
import time

from . import __version__
from .version import parse_version
from .uart import LinkError
from .protocol import key_values

WORKER_TIMEOUT = 180
RESTORE_TIMEOUT = 45
CATALOG_REFRESH_SECONDS = 5


def scan_catalog(config, board, caps, cached, stamp):
    """Resolve bounded catalog changes on a worker thread, never on a radio.

    @param config Immutable configuration captured on the owner loop.
    @param board Observed board identity.
    @param caps Detached capabilities from that radio session.
    @param cached Previously validated immutable candidate, or None.
    @param stamp Previously validated filesystem revision, or None.
    @return Candidate, blocking reason and validated revision.
    """
    from .firmware_bundle import BundleError, select_candidate
    from .firmware_catalog import inventory
    from .firmware_backends import backend_status

    if not board:
        return None, "board_identity_unknown", None
    try:
        before = inventory(config.firmware_bundle_dir)[3]
        candidate = cached if before == stamp else None
        if candidate is None:
            candidate = select_candidate(
                config.firmware_bundle_dir,
                board=board,
                host_version=__version__,
                app_baudrate=config.serial_baudrate,
            )
        reason = backend_status(candidate, config.firmware_nordic)
        if (
            candidate["backend"] == "ti_uart_signed_v1"
            and caps.get("update") != "uart_signed_v1"
        ):
            reason = "bootloader_install_required"
        if inventory(config.firmware_bundle_dir)[3] != before:
            return None, "bundle_invalid", None
        return candidate, reason, before
    except (BundleError, OSError, ValueError) as error:
        return None, getattr(error, "code", "bundle_invalid"), None


def radio_guard(database):
    """Acquire the radio lifetime lock also inherited by update worker descendants.

    @param database Authoritative database path identifying this deployment.
    @return Locked file descriptor; close it without LOCK_UN to release ownership.
    """
    path = str(database) + ".firmware.lock"
    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise OSError("Firmware ownership guard must be a regular file")
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BaseException:
        os.close(fd)
        raise
    return fd


class FirmwareManager:
    def __init__(self, service):
        """Restore update intent without automatically replaying a transfer.

        @param service Owning Service; all journal operations run on its event loop.
        """
        self.service = service
        self.requested = asyncio.Event()
        self.candidate = None
        self._catalog_generation = 0
        self._catalog_stamp = None
        self._catalog_refresh_lock = asyncio.Lock()
        self.reason = "board_identity_unknown"
        self.board = None
        self.installed = None
        self.caps = {}
        self.version_info = {}
        self.update_info = {}
        self.worker_arguments = None
        self.manual_restore_serial = None
        self.process = None
        self.restore_deadline = None
        self.job = service.store.firmware_job()
        if self.job and self.job.get("status") == "pending":
            self.job.update(
                status="unknown",
                stage="interrupted",
                recovery="recovery_required",
                error="Daemon stopped before update completion",
            )
            self.persist()

    @property
    def busy(self):
        """Keep conflicting mutations blocked until hardware reconciliation finishes."""
        return bool(
            self.job
            and (self.job.get("status") == "pending" or self.job.get("recovery"))
        )

    def persist(self):
        """Commit the journal before notifying snapshot subscribers."""
        self.job["updated_at"] = time.time()
        self.service.store.save_firmware_job(self.job)
        self.service.notify_changed()

    def observe(self, caps):
        """Resolve a qualified candidate from the freshly identified radio.

        @param caps Current adapter capabilities, never a saved device guess.
        """
        from .firmware_bundle import BundleError, select_candidate
        from .firmware_backends import backend_status
        from .firmware_catalog import inventory

        self._catalog_generation += 1
        self._catalog_stamp = None
        self.caps = dict(caps)
        self.version_info = {}
        self.update_info = {}
        self.board = caps.get("board")
        self.installed = caps.get("fw_version")
        self.candidate = None
        self.reason = None
        if not self.board:
            self.reason = "board_identity_unknown"
            return
        try:
            inventory(self.service.config.firmware_bundle_dir)
            self.candidate = select_candidate(
                self.service.config.firmware_bundle_dir,
                board=self.board,
                host_version=__version__,
                app_baudrate=self.service.config.serial_baudrate,
            )
            self.reason = backend_status(
                self.candidate, self.service.config.firmware_nordic
            )
            if (
                self.candidate["backend"] == "ti_uart_signed_v1"
                and caps.get("update") != "uart_signed_v1"
            ):
                self.reason = "bootloader_install_required"
        except BundleError as error:
            self.reason = error.code

    def catalog_context(self):
        """Identify the live facts/configuration a background catalog result belongs to.

        @return Detached generation/session/configuration tuple, without credentials.
        """
        config = self.service.config
        return (
            self._catalog_generation,
            self.service.session,
            self.board,
            config.firmware_bundle_dir,
            config.serial_baudrate,
            json.dumps(config.firmware_nordic, sort_keys=True),
        )

    async def refresh_catalog(self):
        """Refresh candidate eligibility without resetting facts, jobs or the radio.

        @return Whether a result was applied to the same observation context.
        """
        async with self._catalog_refresh_lock:
            context = self.catalog_context()
            work = asyncio.create_task(
                asyncio.to_thread(
                    scan_catalog,
                    self.service.config,
                    self.board,
                    dict(self.caps),
                    self.candidate,
                    self._catalog_stamp,
                )
            )
            try:
                candidate, reason, stamp = await asyncio.shield(work)
            except asyncio.CancelledError:
                await work
                raise
            if context != self.catalog_context():
                return False
            previous = self.snapshot()
            self.candidate, self.reason, self._catalog_stamp = candidate, reason, stamp
            if self.snapshot() != previous:
                self.service.notify_changed()
            return True

    async def watch_catalog(self):
        """Poll catalog revisions with one bounded background validation at a time."""
        while not self.service.stop.is_set():
            await self.refresh_catalog()
            try:
                await asyncio.wait_for(
                    self.service.stop.wait(), CATALOG_REFRESH_SECONDS
                )
            except asyncio.TimeoutError:
                pass

    def preflight_report(self, candidate_id=None, *, candidate=None):
        """Check current catalog bytes and cached owned-session identity without UART I/O.

        @param candidate_id Optional opaque artifact selection.
        @param candidate Optional freshly validated private candidate for install admission.
        @return Public advisory report; install independently repeats the checks.
        """
        from .firmware_preflight import build_report

        service = self.service
        blocked = None
        if self.busy:
            blocked = (
                "recovery_required"
                if self.job.get("recovery")
                else "update_in_progress"
            )
        elif (
            service.command_inflight
            or not service.commands.empty()
            or service.pending
            or service.pairing
            or service.reconfigure.is_set()
            or service.radio_lock.locked()
            or service.mqtt_lock.locked()
        ):
            blocked = "radio_busy"
        return build_report(
            service.config,
            candidate_id,
            board=self.board,
            caps=self.caps,
            version_info=self.version_info,
            update_info=self.update_info,
            live=service.ready,
            simulated=service.simulated,
            blocked=blocked,
            identity_source="live_session" if service.ready else "unknown",
            candidate=candidate,
        )

    def snapshot(self):
        """Return only public status; package paths, bytes and keys remain internal."""
        job = self.job or {}
        candidate = self.candidate or {}
        if self.busy:
            candidate = dict(
                id=job.get("candidate_id"),
                version=job.get("version"),
                rollback_supported=job.get("rollback_supported", False),
                release_notes=job.get("release_notes"),
                release_url=job.get("release_url"),
            )
        try:
            available = bool(
                candidate
                and self.installed
                and parse_version(self.installed) < parse_version(candidate["version"])
            )
        except ValueError:
            available = False
        reason = self.reason
        if self.service.simulated:
            reason = "simulation"
        elif self.busy:
            reason = (
                "recovery_required" if job.get("recovery") else "update_in_progress"
            )
        elif not self.service.ready:
            reason = "radio_offline"
        return dict(
            preflight_supported=True,
            board=self.board,
            installed_version=self.installed,
            latest_version=candidate.get("version"),
            candidate_id=candidate.get("id"),
            update_available=available,
            install_supported=bool(candidate and not reason),
            blocked_reason=reason,
            in_progress=job.get("status") == "pending",
            stage=job.get("stage", "idle"),
            progress_percent=None,
            transfer_percent=job.get("transfer_percent"),
            request_id=job.get("request_id"),
            status=job.get("status"),
            error=job.get("error"),
            recovery=job.get("recovery"),
            rollback_supported=bool(
                candidate.get(
                    "rollback_supported", job.get("rollback_supported", False)
                )
            ),
            release_notes=candidate.get("release_notes"),
            release_url=candidate.get("release_url"),
        )

    def start_local(self, payload, candidate, worker_arguments):
        """Admit a locally inspected operator artifact through the normal durable job.

        @param payload Request ID and exact hash-bound local candidate ID.
        @param candidate Private validated operator artifact, never accepted over HTTP.
        @param worker_arguments Fixed worker arguments constructed by the offline CLI.
        @return Existing or newly accepted command result; incompatible inputs are rejected.
        """
        from . import __version__

        if (
            candidate["version"] != __version__
            or candidate.get("operation") != "update"
        ):
            raise ValueError("Local update must match the installed host release")
        previous = self.service.store.previous(
            payload["request_id"],
            dict(action="firmware_update", candidate_id=payload["candidate_id"]),
        )
        if previous:
            return previous
        report = self.preflight_report(candidate["id"], candidate=candidate)
        if not report["executable"]:
            return dict(
                request_id=payload["request_id"],
                status="rejected",
                error=report["blocked_reason"],
            )
        old_candidate, old_reason = self.candidate, self.reason
        self.candidate, self.reason = candidate, None
        result = self._admit(payload, local_candidate=candidate)
        if result["status"] == "pending":
            self.worker_arguments = list(worker_arguments)
            self.job["operation"] = "local_update"
            self.persist()
        else:
            self.candidate, self.reason = old_candidate, old_reason
        return result

    def check_manual_restore(self, caps=None, version_info=None):
        """Require explicit application USB identity before resuming manual recovery.

        @param caps Optional fresh application capabilities to verify before network writes.
        @param version_info VERSION response from the same owned application connection.
        """
        if not self.busy or self.job.get("operation") != "recovery":
            return
        from .firmware_manual import application_target_status

        if (
            self.job.get("transfer_completed") is not True
            or not self.manual_restore_serial
            or not application_target_status(
                self.service.config.serial_port,
                self.manual_restore_serial,
                usb_profile=self.service.config.firmware_nordic,
            )
        ):
            raise LinkError(
                "Explicit application identity verification is required after manual recovery"
            )
        if caps is not None and (
            caps.get("board") != self.job["board"]
            or caps.get("fw_version") != self.job["version"]
            or caps.get("baud") != str(self.job["baudrate"])
            or caps.get("proto") != "3"
            or (version_info or {}).get("board") != self.job["board"]
            or (version_info or {}).get("fw_version") != self.job["version"]
            or (version_info or {}).get("proto") != "3"
        ):
            raise LinkError(
                "Recovered application does not match the recorded manual transfer"
            )

    def check_nordic_restore(self, caps=None, version_info=None):
        """Verify the configured physical Nordic board before opening or restoring it.

        @param caps Optional fresh capabilities, checked before network writes.
        @param version_info Optional VERSION response from the same owned serial session.
        """
        settings = self.service.config.firmware_nordic
        automatic = bool(self.busy and self.job.get("nordic_profile"))
        if not settings and not automatic:
            return
        from .firmware_nordic import target
        from .firmware_bundle import BundleError

        try:
            if automatic and (
                settings != self.job["nordic_profile"]
                or self.job.get("transfer_completed") is not True
            ):
                raise LinkError(
                    "Nordic update requires explicit recovery before radio restoration"
                )
            if target(settings, "application") != self.service.config.serial_port:
                raise LinkError("Nordic application path changed")
        except (BundleError, ValueError) as error:
            raise LinkError("Configured Nordic USB identity is unavailable") from error
        if caps is not None:
            facts = (caps, version_info or {})
            if any(
                f.get("board") != "nordic-pca10059" or f.get("proto") != "3"
                for f in facts
            ):
                raise LinkError(
                    "Nordic application identity did not match its USB profile"
                )
            if automatic and any(
                f.get("fw_version") != self.job["version"] for f in facts
            ):
                raise LinkError(
                    "Nordic application does not match the completed update"
                )

    def start(self, payload):
        """Deduplicate an explicit install and request an exclusive radio handoff.

        @param payload Exactly request_id and a hash-bound bundled candidate_id.
        @return Existing command envelope, or durable pending/rejected outcome.
        """
        return self._admit(payload)

    def _admit(self, payload, local_candidate=None):
        """Admit freshly validated catalog bytes or a CLI-inspected local artifact.

        @param payload Exact request/candidate identity checked by normal admission.
        @param local_candidate Private artifact from the explicit local CLI only.
        @return Durable command result without automatically replaying requests.
        """
        ident = payload.get("request_id") if isinstance(payload, dict) else None
        try:
            if not isinstance(payload, dict) or set(payload) != {
                "request_id",
                "candidate_id",
            }:
                raise ValueError("Expected request_id and candidate_id")
            if not isinstance(ident, str) or not re.fullmatch(
                r"[A-Za-z0-9_.:-]{1,128}", ident
            ):
                ident = None
                raise ValueError("Invalid request_id")
            if not isinstance(payload["candidate_id"], str) or not re.fullmatch(
                r"[A-Za-z0-9_.:-]{1,256}", payload["candidate_id"]
            ):
                raise ValueError("Invalid candidate_id")
            recorded = dict(
                action="firmware_update", candidate_id=payload["candidate_id"]
            )
            previous = self.service.store.previous(ident, recorded)
            if previous:
                return previous
            self.service.store.begin(ident, recorded)
            state = self.snapshot()
            error = state["blocked_reason"]
            if not error and (
                not state["update_available"]
                or payload["candidate_id"] != state["candidate_id"]
            ):
                error = "candidate_unavailable"
            if not error and (
                self.service.command_inflight
                or not self.service.commands.empty()
                or self.service.pending
                or self.service.pairing
                or self.service.reconfigure.is_set()
                or self.service.radio_lock.locked()
                or self.service.mqtt_lock.locked()
            ):
                error = "radio_busy"
            candidate = None
            if not error:
                from .firmware_bundle import BundleError, load_candidate
                from .firmware_catalog import inventory
                from .firmware_backends import backend_status

                try:
                    if local_candidate is None:
                        inventory(self.service.config.firmware_bundle_dir)
                        candidate = load_candidate(
                            self.service.config.firmware_bundle_dir,
                            payload["candidate_id"],
                            host_version=__version__,
                            app_baudrate=self.service.config.serial_baudrate,
                        )
                    else:
                        candidate = local_candidate
                    if (
                        candidate["board"] != self.board
                        or candidate["version"] != state["latest_version"]
                    ):
                        error = "candidate_unavailable"
                    elif local_candidate is None:
                        error = backend_status(
                            candidate, self.service.config.firmware_nordic
                        )
                except (BundleError, OSError, ValueError) as failure:
                    error = getattr(failure, "code", "bundle_invalid")
            if error:
                result = dict(
                    request_id=ident,
                    candidate_id=payload["candidate_id"],
                    action="firmware_update",
                    status="rejected",
                    error=error,
                )
                self.service.store.finish(ident, result)
                self.service.notify_changed()
                return result
            self.worker_arguments = None
            self.job = dict(
                recorded,
                request_id=ident,
                status="pending",
                stage="preparing",
                board=candidate["board"],
                version=candidate["version"],
                installed_version=self.installed,
                baudrate=self.service.config.serial_baudrate,
                image_sha512=candidate.get("image_sha512"),
                public_key=candidate.get("public_key"),
                backend=candidate["backend"],
                rollback_supported=candidate["rollback_supported"],
                release_notes=candidate.get("release_notes"),
                release_url=candidate.get("release_url"),
                error=None,
                recovery=None,
                started_at=time.time(),
                last_critical=None,
            )
            if candidate["backend"] == "nrf52840_usb_dfu_v1":
                self.job["nordic_profile"] = dict(self.service.config.firmware_nordic)
                self.job["transfer_completed"] = False
            self.persist()
            self.service.ready = False
            self.requested.set()
            self.service.snapshot()
            return self.service.store.command_result(ident)
        except (ValueError, TypeError) as error:
            return dict(
                request_id=ident,
                action="firmware_update",
                status="rejected",
                error=str(error),
            )

    def failed(self, message, *, uncertain=True):
        """Record a terminal result while retaining recovery intent where needed.

        @param message Public failure description without raw worker output.
        @param uncertain Whether hardware state may have changed.
        """
        if not self.job:
            return
        self.job.update(
            status="unknown" if uncertain else "rejected",
            stage="failed",
            error=message,
            recovery="recovery_required" if uncertain else None,
        )
        self.restore_deadline = None
        self.persist()

    async def preflight(self, gateway):
        """Read current board identity and update ownership before radio disarm.

        @param gateway Still-owned normal transport after command tasks are drained.
        """
        live = key_values(await gateway.command("VERSION"))
        if (
            live.get("board") != self.job["board"]
            or live.get("fw_version") != self.job["installed_version"]
        ):
            raise LinkError("Radio identity changed before update")
        if self.job["backend"] == "ti_uart_signed_v1":
            update = key_values(await gateway.command("UPDATE_STATUS"))
            if (
                not self.job.get("public_key")
                or update.get("key") != self.job["public_key"]
                or update.get("trial") != "0"
                or update.get("state") not in ("ffffffff", "fffffff0", "ffffffc8")
            ):
                raise LinkError("Update key or trial state changed before handoff")
            digest = update.get("current_sha512", "")
            if not re.fullmatch(r"[0-9a-f]{128}", digest):
                raise LinkError("Original firmware digest unavailable")
            self.job["original_sha512"] = digest
            self.persist()

    async def reap(self, process):
        """Terminate the worker process group and reap it before any radio reopen.

        @param process Worker spawned in its own process group with inherited guard.
        """
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            await asyncio.wait_for(process.wait(), 3)
        except asyncio.TimeoutError:
            pass
        # Kill remaining descendants even when their group leader already exited.
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        await process.wait()

    async def events(self, process):
        """Validate worker IPC, committing every critical boundary before its ACK.

        @param process Child with bounded stdout lines and a writable stdin pipe.
        @return Whether a successful transfer event and exit code were both observed.
        """
        transferred = False
        sequence = 0
        allowed = {
            "disarm",
            "begin",
            "commit",
            "committed",
            "activate",
            "bootloader_entry",
            "flash",
            "recovery",
            "transfer",
            "transferred",
            "failed",
            "preflight",
        }
        while True:
            line = await process.stdout.readline()
            if not line:
                break
            event = json.loads(line)
            if not isinstance(event, dict) or event.get("stage") not in allowed:
                raise ValueError("Invalid firmware worker event")
            if event.get("job_id") != self.job["request_id"]:
                raise ValueError("Firmware worker job mismatch")
            stage = event["stage"]
            if (
                type(event.get("sequence")) is not int
                or event["sequence"] != sequence + 1
            ):
                raise ValueError("Invalid firmware worker sequence")
            sequence = event["sequence"]
            if type(event.get("critical")) is not bool:
                raise ValueError("Invalid firmware worker boundary")
            if event["critical"]:
                self.job["last_critical"] = stage
            percent = event.get("transfer_percent")
            if percent is not None:
                if type(percent) is not int or not 0 <= percent <= 100:
                    raise ValueError("Invalid transfer progress")
                self.job["transfer_percent"] = percent
            self.job["stage"] = stage
            self.persist()
            if event["critical"]:
                process.stdin.write(
                    (
                        json.dumps(dict(ack=sequence, job_id=self.job["request_id"]))
                        + "\n"
                    ).encode()
                )
                await process.stdin.drain()
            if stage == "failed":
                raise RuntimeError("Firmware worker failed; inspect recovery procedure")
            if stage == "transferred":
                transferred = True
        return transferred and await process.wait() == 0

    async def execute(self, guard, worker_arguments=None):
        """Run one bounded worker while retaining the cross-process radio guard.

        @param guard Locked lifetime descriptor; this method closes the parent's copy.
        @param worker_arguments Optional fixed offline CLI arguments; never accepted from HTTP.
        """
        process = None
        try:
            if self.service.stop.is_set():
                self.failed("Shutdown before update started", uncertain=False)
                return
            arguments = (
                worker_arguments
                or self.worker_arguments
                or [
                    "--bundle-dir",
                    self.service.config.firmware_bundle_dir,
                    "--candidate-id",
                    self.job["candidate_id"],
                    "--port",
                    self.service.config.serial_port,
                    "--baud",
                    str(self.job["baudrate"]),
                ]
            )
            if (
                self.job.get("nordic_profile")
                and self.job.get("operation") != "recovery"
            ):
                arguments = [
                    *arguments,
                    "--nordic-profile",
                    json.dumps(self.job["nordic_profile"], sort_keys=True),
                ]
            process = await asyncio.create_subprocess_exec(
                sys.executable,
                "-m",
                "livolod.firmware_worker",
                *arguments,
                "--job-id",
                self.job["request_id"],
                "--lock-fd",
                str(guard),
                pass_fds=(guard,),
                start_new_session=True,
                limit=8192,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            self.process = process
            os.close(guard)
            guard = None
            communication = asyncio.create_task(self.events(process))
            stopped = asyncio.create_task(self.service.stop.wait())
            try:
                done, _ = await asyncio.wait(
                    (communication, stopped),
                    timeout=WORKER_TIMEOUT,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if communication not in done or not communication.result():
                    raise RuntimeError("Firmware update interrupted or timed out")
                if self.job.get("operation") == "recovery":
                    self.job.update(
                        status="unknown",
                        stage="transferred",
                        transfer_completed=True,
                        error=None,
                        recovery="manual_verification_required",
                    )
                    self.restore_deadline = None
                else:
                    self.job.update(stage="restoring", error=None)
                    if self.job.get("nordic_profile"):
                        self.job["transfer_completed"] = True
                    self.restore_deadline = time.monotonic() + RESTORE_TIMEOUT
                self.persist()
            finally:
                communication.cancel()
                stopped.cancel()
                await asyncio.gather(communication, stopped, return_exceptions=True)
        except asyncio.CancelledError:
            self.failed("Daemon shutdown interrupted update")
            raise
        except Exception:
            self.failed(
                "Firmware update failed; inspect recovery procedure",
                uncertain=bool(self.job.get("last_critical")),
            )
        finally:
            if process:
                await self.reap(process)
            self.process = None
            self.worker_arguments = None
            if guard is not None:
                os.close(guard)
            self.requested.clear()

    def before_confirm(self, caps, update):
        """Bind trial confirmation to the selected image across process restarts.

        @param caps Fresh capabilities identifying the current board and release.
        @param update Read-only UPDATE_STATUS response before UPDATE_CONFIRM.
        """
        if (
            not self.job
            or not self.busy
            or self.job.get("backend") != "ti_uart_signed_v1"
        ):
            return
        if update.get("trial") == "1" and (
            caps.get("board") != self.job["board"]
            or caps.get("fw_version") != self.job["version"]
            or not self.job.get("image_sha512")
            or update.get("current_sha512") != self.job["image_sha512"]
        ):
            self.failed("Trial firmware does not match the selected image")
            raise LinkError("Selected firmware digest mismatch; confirmation refused")

    def restored(self, caps, update=None):
        """Finish only after exact identity and normal session readiness are verified.

        @param caps Fresh board/version/baud capabilities from the restored session.
        @param update Optional durable TI status including current image digest.
        """
        if not self.job or not self.busy or self.requested.is_set():
            return
        if self.job.get("operation") == "recovery":
            try:
                self.check_manual_restore(caps, self.version_info)
            except LinkError:
                return
        if self.job.get("nordic_profile"):
            try:
                self.check_nordic_restore(caps, self.version_info)
            except LinkError:
                return
        match = (
            caps.get("board") == self.job["board"]
            and caps.get("fw_version") == self.job["version"]
            and caps.get("baud") == str(self.job["baudrate"])
        )
        if self.job.get("backend") == "ti_uart_signed_v1":
            match = (
                match
                and bool(update)
                and update.get("trial") == "0"
                and update.get("current_sha512") == self.job.get("image_sha512")
            )
        if match:
            self.job.update(
                status="confirmed", stage="confirmed", error=None, recovery=None
            )
            self.restore_deadline = None
            self.persist()
        elif (
            caps.get("board") == self.job["board"]
            and caps.get("fw_version") == self.job["installed_version"]
            and caps.get("baud") == str(self.job["baudrate"])
        ):
            unchanged = self.job.get("last_critical") in (
                None,
                "disarm",
                "bootloader_entry",
            )
            if self.job.get("backend") == "ti_uart_signed_v1":
                unchanged = (
                    bool(update)
                    and update.get("trial") == "0"
                    and update.get("state") in ("ffffffff", "fffffff0", "ffffffc8")
                    and (
                        self.job.get("last_critical") is None
                        or (
                            bool(self.job.get("original_sha512"))
                            and update.get("current_sha512")
                            == self.job["original_sha512"]
                        )
                    )
                )
            if unchanged:
                self.job.update(
                    status="rejected",
                    stage="restored_previous",
                    recovery=None,
                    error="Update did not complete; previous firmware verified",
                )
                self.restore_deadline = None
                self.persist()
            elif self.job.get("status") == "pending":
                self.failed(
                    "Previous version returned but image recovery is unverified"
                )
        elif self.job.get("status") == "pending":
            self.failed("Restored firmware does not match the selected candidate")

    def check_deadline(self):
        """End an update whose normal radio session could not be restored in time."""
        if (
            self.restore_deadline is not None
            and time.monotonic() >= self.restore_deadline
        ):
            self.failed("Firmware restoration timed out; inspect recovery procedure")
