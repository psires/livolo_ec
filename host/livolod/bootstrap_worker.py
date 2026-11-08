"""Supervised physical setup child; all serial work follows explicit durable intent."""

import argparse
import asyncio
from dataclasses import asdict
import fcntl
import json
import os
from pathlib import Path
import signal
import sys
import tempfile

from .bootstrap_store import private_open
from .firmware_backends import _run_flasher, backend_status
from .firmware_bundle import BundleError, MAX_FILE
from .firmware_worker import Channel, validate_guard
from .preserved_gecko import GeckoTarget, install_preserved_gecko, check_loader
from .zbgw04_provider import (
    DedicatedAccess,
    LinuxUSB,
    LoaderPolicy,
    PhysicalLease,
    ZBGW04Provider,
    select_target,
)
from .gecko_profiles import gecko_profile


def target_from_json(value):
    """Restore a server-recorded target and validate its fixed physical profile.

    @param value Private journal or IPC target mapping, never HTTP input.
    """
    fields = dict(value)
    fields["device_number"] = tuple(fields["device_number"])
    target = GeckoTarget(**fields)
    target.validate()
    return target


def target_json(target):
    """Return a canonical JSON projection for exact cross-process comparisons.

    @param target Frozen physical target.
    """
    value = asdict(target)
    if value.get("serial") is None:
        value.pop("serial", None)
    return json.loads(json.dumps(value))


def physical_selector(value):
    """Keep stable topology/profile separate from a setup attachment generation.

    @param value Canonical target mapping from a trusted runtime or journal.
    """
    result = {
        key: value[key] for key in ("usb_path", "interface", "board", "vid", "pid")
    }
    result["serial"] = value.get("serial")
    return result


def reap_adopted_group(group):
    """Reap only adopted descendants after asyncio has reaped their direct worker.

    A PID 1 runtime adopts orphaned helper children. WNOHANG bounds each syscall;
    the iteration limit bounds a group containing unexpectedly many descendants.
    Neither ECHILD nor a successful reap proves that the whole group disappeared.
    @param group Positive process group ID of the already-reaped owned worker.
    """
    if type(group) is not int or group <= 1:
        raise ValueError("Invalid owned worker process group")
    for _ in range(64):
        try:
            pid, _ = os.waitpid(-group, os.WNOHANG)
        except ChildProcessError:
            return
        except InterruptedError:
            continue
        if pid == 0:
            return


class RadioOwnership:
    """Retain the actual shared device lease across normal and setup owners."""

    def __init__(self, settings, inventory=None):
        """Bind trusted deployment paths without scanning or opening serial.

        @param settings Server-owned serial_port and lease_directory.
        @param inventory Optional metadata provider for isolated tests.
        """
        self.settings = settings
        self.profile = gecko_profile(settings.get("profile", "zb-gw04-1v2"))
        self.inventory = inventory or LinuxUSB(profile=self.profile.board).inventory
        self.target = self.lease = None
        self.pinned = False

    def acquire(self, allow_reattach=False):
        """Retain exact setup attachment; normal closed-owner reconnect may renew it.

        @param allow_reattach True only when the old normal serial owner is already closed.
        """
        target = select_target(
            self.settings["serial_port"], self.profile.board_evidence, self.inventory
        )
        if self.target is not None and self.target != target:
            if (
                self.pinned
                or not allow_reattach
                or physical_selector(target_json(self.target))
                != physical_selector(target_json(target))
            ):
                raise BundleError(
                    "target_changed", "Radio attachment changed; restart setup"
                )
            self.close()
            self.target = None
        if self.lease is None:
            self.lease = PhysicalLease.acquire(target, self.settings["lease_directory"])
            self.target = target
        self.lease.check()
        return target, self.lease

    def close(self):
        """Close only this process reference; descendants retain inherited ownership."""
        if self.lease is not None:
            self.lease.close()
            self.lease = None


class BootstrapWorker:
    """Run one bounded child, validating every event before acknowledging a mutation."""

    def __init__(self, settings, ownership, runtime, timeout=150):
        """Bind the production process launcher and quiescence observer.

        @param settings Trusted paths and reviewed exact-unit loader policy.
        @param ownership Shared RadioOwnership retained through later normal handoff.
        @param runtime Owner exposing actual quiesced state, never client-supplied.
        @param timeout Maximum total child lifetime including bounded physical work.
        """
        self.settings, self.ownership, self.runtime = settings, ownership, runtime
        self.blocked_reason = (
            "loader_policy_required"
            if not settings.get("loader_policy")
            else backend_status({"backend": "mg21_gecko_gbl_v1"})
        )
        self.timeout = timeout
        self.child = None
        self.poisoned = False

    def precheck(self):
        """Validate exact-unit policy and metadata before interrupting a normal owner."""
        if self.blocked_reason:
            return self.blocked_reason
        try:
            target, _ = self.ownership.acquire()
            policy = self.settings.get("loader_policy")
            if not policy or policy.get("target") != target_json(target):
                return "loader_policy_required"
            LoaderPolicy(**policy["loader"]).validate(target)
        except (BundleError, ValueError, TypeError, KeyError) as error:
            return getattr(error, "code", "loader_policy_required")
        return None

    async def run(self, kind, job, candidate, binding, checkpoint):
        """Stage immutable bytes and await a reaped worker under all real guards.

        @param kind Prepare, install or cancel.
        @param job Durable operation containing immutable expected candidate bindings.
        @param candidate Validated catalog candidate with immutable bytes, or None.
        @param binding Prepared target/loader and optional saved baseline.
        @param checkpoint Async durable phase callback invoked before sending an ACK.
        """
        if self.child is not None or self.poisoned or not self.runtime.quiesced:
            raise BundleError("ownership_unresolved", "Own service is not quiesced")
        target, lease = self.ownership.acquire()
        policy = self.settings.get("loader_policy")
        if not policy or policy.get("target") != target_json(target):
            raise BundleError(
                "loader_policy_required",
                "Exact-unit reviewed loader policy is required",
            )
        expected = (
            (
                binding["baseline"]["target"]
                if kind == "cancel"
                else binding["inspection"]["target"]
            )
            if binding
            else None
        )
        if expected is not None and expected != target_json(target):
            raise BundleError("target_changed", "Inspected radio attachment changed")
        guards = []
        directory = tempfile.TemporaryDirectory(prefix="livolo-bootstrap-")
        try:
            # Existing Service and all its updater descendants must have released both.
            for suffix in (".lock", ".firmware.lock"):
                fd = private_open(self.settings["database"] + suffix)
                guards.append(fd)
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            data = dict(
                kind=kind,
                job_id=job["request_id"],
                target=target_json(target),
                policy=policy,
                lease_directory=self.settings["lease_directory"],
                lease_fd=lease.fileno(),
                guards=guards,
                binding=binding,
                dedicated=True,
                quiesced=self.runtime.quiesced,
            )
            if candidate:
                payload = Path(directory.name) / "application.gbl"
                payload.write_bytes(candidate["_data"])
                payload.chmod(0o600)
                data["candidate"] = dict(
                    path=str(payload),
                    sha256=candidate["sha256"],
                    version=candidate["version"],
                )
            path = Path(directory.name) / "job.json"
            path.write_text(json.dumps(data))
            path.chmod(0o600)
            self.child = await asyncio.create_subprocess_exec(
                sys.executable,
                "-m",
                "livolod.bootstrap_worker",
                "--job",
                str(path),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
                limit=4097,
                pass_fds=(lease.fileno(), *guards),
                start_new_session=True,
            )
            result = await asyncio.wait_for(
                self._observe(job, checkpoint), self.timeout
            )
            return result
        finally:
            try:
                if self.child is not None:
                    await self._reap()
            except BaseException:
                self.poisoned = True
                raise
            finally:
                for fd in guards:
                    os.close(fd)
                directory.cleanup()

    async def _observe(self, job, checkpoint):
        """Accept only ordered events for this operation; exit zero alone is insufficient.

        @param job Durable parent operation identity.
        @param checkpoint Durable async phase callback.
        """
        sequence, receipt = 0, None
        boundary_seen = False
        while True:
            line = await self.child.stdout.readline()
            if not line:
                break
            if len(line) > 4096 or receipt is not None:
                raise BundleError("invalid_worker_result", "Invalid worker stream")
            value = json.loads(line)
            sequence += 1
            if (
                not isinstance(value, dict)
                or value.get("job_id") != job["request_id"]
                or type(value.get("sequence")) is not int
                or value["sequence"] != sequence
                or type(value.get("critical")) is not bool
            ):
                raise BundleError("invalid_worker_result", "Invalid worker identity")
            stage = value.get("stage")
            if value["critical"]:
                expected = "flash" if job["kind"] == "install" else "run_intact"
                if (
                    boundary_seen
                    or job["kind"] not in ("install", "cancel")
                    or stage != expected
                ):
                    raise BundleError(
                        "invalid_worker_result", "Unexpected mutation boundary"
                    )
                boundary_seen = True
                await checkpoint(stage)
                self.child.stdin.write(
                    (
                        json.dumps({"ack": sequence, "job_id": job["request_id"]})
                        + "\n"
                    ).encode()
                )
                await self.child.stdin.drain()
            elif stage == "completed":
                if job["kind"] in ("install", "cancel") and not boundary_seen:
                    raise BundleError(
                        "invalid_worker_result", "Missing durable mutation permission"
                    )
                receipt = value.get("result")
            elif stage == "failed":
                # Child emits only stable codes, but never expose arbitrary pipe strings.
                code = value.get("code", "worker_failed")
                if (
                    not isinstance(code, str)
                    or len(code) > 80
                    or not all(
                        c in "abcdefghijklmnopqrstuvwxyz_0123456789" for c in code
                    )
                ):
                    code = "worker_failed"
                raise BundleError(code, "Physical setup worker did not complete")
            elif stage in ("verifying_target", "verifying_application"):
                await checkpoint(stage)
            else:
                raise BundleError("invalid_worker_result", "Unexpected worker phase")
        if await self.child.wait() != 0 or not isinstance(receipt, dict):
            raise BundleError(
                "worker_failed", "Worker exited without a verified receipt"
            )
        return receipt

    async def _reap(self):
        """Terminate the process group and confirm bounded child/group disappearance."""
        child = self.child
        try:
            os.killpg(child.pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            # A disappearing process can race signalling. This is NOT proof of
            # cleanup: wait/reap the child and require ESRCH from the group probe.
            pass
        try:
            await asyncio.wait_for(child.wait(), 3)
        except asyncio.TimeoutError:
            try:
                os.killpg(child.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            await asyncio.wait_for(child.wait(), 3)
        # The direct worker's asyncio watcher has completed. Only now may PID 1
        # reap adopted helpers in this owned group, without stealing other waits.
        # A helper surviving its worker must not allow a normal-service handoff.
        for _ in range(30):
            reap_adopted_group(child.pid)
            try:
                os.killpg(child.pid, 0)
            except ProcessLookupError:
                self.child = None
                return
            except PermissionError:
                # Transient orphan/zombie group transitions may deny probing.
                # Retain the fence and retry; only ESRCH proves disappearance.
                await asyncio.sleep(0.1)
                continue
            try:
                os.killpg(child.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
            await asyncio.sleep(0.1)
        raise BundleError(
            "worker_cleanup_unconfirmed", "Child group has not disappeared"
        )


def execute(spec, channel):
    """Execute fixed physical operations inside the guarded child.

    @param spec Private parent-produced job settings, not a network payload.
    @param channel Bounded durable-ack IPC channel.
    """
    if spec["kind"] not in ("prepare", "install", "cancel"):
        raise ValueError("Unknown worker kind")
    target = target_from_json(spec["target"])
    guards = tuple(validate_guard(fd) for fd in spec["guards"])
    lease = PhysicalLease.adopt(spec["lease_fd"], target, spec["lease_directory"])
    policy = spec["policy"]
    if policy.get("target") != target_json(target):
        lease.close()
        raise BundleError("loader_policy_required", "Policy target differs")

    def access(current):
        """Project parent consent plus actual deployment-guard quiescence.

        @param current Fresh exact provider target.
        """
        for fd in guards:
            validate_guard(fd)
        return DedicatedAccess(
            current.attachment, lease.name, spec["dedicated"], spec["quiesced"], ()
        )

    provider = ZBGW04Provider(
        target,
        lease,
        board_evidence=gecko_profile(target.board).board_evidence,
        access_check=access,
        loader_policy=LoaderPolicy(**policy["loader"]),
    )
    with provider:
        if spec["kind"] == "prepare":
            loader = provider.probe_loader(target.port, 115200, timeout=3)
            check_loader(target, loader)
            return dict(target=target_json(target), loader=loader)
        if spec["kind"] == "cancel":
            return provider.resume_intact_application(
                spec["binding"]["baseline"]["version"],
                mode=spec["binding"]["mode"],
                before_run=lambda: channel.before("run_intact"),
            )
        candidate = spec["candidate"]
        with open(candidate["path"], "rb") as stream:
            data = stream.read(MAX_FILE + 1)
        if len(data) > MAX_FILE:
            raise ValueError("Oversized artifact")

        def flash(path, port, guard):
            """Inherit physical and deployment guards through the pinned helper.

            @param path Private immutable application artifact.
            @param port Exact provider endpoint.
            @param guard Held shared physical lease descriptor.
            """
            _run_flasher(path, port, guard, guard_fds=guards)

        def progress(value):
            """Emit real phases only; Gecko has no measured transfer percentage.

            @param value Adapter stage projection.
            """
            channel.emit(value["stage"])

        return install_preserved_gecko(
            target=target,
            data=data,
            sha256=candidate["sha256"],
            version=candidate["version"],
            lease_fd=provider.lease_fd,
            before=channel.before,
            progress=progress,
            observe=provider.observe,
            probe_loader=provider.probe_loader,
            verify_application=provider.verify_application,
            flash=flash,
        )


def main(argv=None):
    """Read one private staged operation and emit a bounded verification receipt.

    @param argv Optional fixed local command arguments for isolated tests.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--job", required=True)
    args = parser.parse_args(argv)
    channel = None
    try:
        with open(args.job, "rb") as stream:
            data = stream.read(65537)
        if len(data) > 65536:
            raise ValueError("Oversized job")
        spec = json.loads(data)
        channel = Channel(spec["job_id"])
        result = execute(spec, channel)
        channel.emit("completed", result=result)
        return 0
    except Exception as error:
        if channel:
            channel.emit("failed", code=getattr(error, "code", "worker_failed"))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
