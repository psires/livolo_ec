"""Bounded installation child with durable parent/worker phase handshakes.

CLI arguments are supplied only by the daemon or local recovery wrapper. Each
stdout line is JSON {stage, critical, sequence, job_id, ...}. A critical event
blocks for stdin {ack: sequence, job_id: job_id}, at most ten seconds. EOF,
malformed or wrong acknowledgements stop before the next destructive operation.
Exit zero plus a final `transferred` event means the parent must still reconnect,
verify and restore; this child never confirms a TI trial or accesses SQLite.
"""

import argparse
import fcntl
import json
import os
import re
import select
import stat
import sys
import time

from . import FIRMWARE_COMPAT_VERSION
from .firmware_backends import install
from .firmware_bundle import BundleError, load_candidate


class Channel:
    """Synchronous bounded IPC; never buffer future acknowledgements."""

    def __init__(self, job_id, input_fd=0, output=None, timeout=10):
        """Bind this worker's job identity and private communication handles.

        @param job_id Parent's durable request identity, repeated in every event.
        @param input_fd File descriptor carrying parent acknowledgement lines.
        @param output Text stream for JSON events; defaults to stdout.
        @param timeout Maximum seconds allowed for one critical acknowledgement.
        """
        self.job_id, self.input_fd = job_id, input_fd
        self.output = sys.stdout if output is None else output
        self.timeout, self.sequence = timeout, 0
        self.mutation_possible = False

    def emit(self, stage, critical=False, **fields):
        """Send one bounded event and flush it before any dependent action.

        @param stage Stable maintenance stage identifier.
        @param critical Whether a durable acknowledgement is required.
        @param fields Additional public progress/error/result fields.
        @return Monotonically increasing event sequence.
        """
        self.sequence += 1
        value = dict(
            stage=stage,
            critical=critical,
            sequence=self.sequence,
            job_id=self.job_id,
            **fields,
        )
        line = json.dumps(value, separators=(",", ":"))
        if len(line) > 4096:
            raise ValueError("Worker event exceeds IPC limit")
        self.output.write(line + "\n")
        self.output.flush()
        return self.sequence

    def before(self, stage):
        """Wait for the parent to durably record a destructive boundary.

        @param stage Mutation about to occur; the callback returning permits it.
        """
        sequence = self.emit(stage, critical=True)
        data = bytearray()
        until = time.monotonic() + self.timeout
        while b"\n" not in data:
            remaining = until - time.monotonic()
            if (
                remaining <= 0
                or not select.select([self.input_fd], [], [], remaining)[0]
            ):
                raise BundleError(
                    "parent_unavailable",
                    "Firmware manager did not acknowledge the phase",
                )
            chunk = os.read(self.input_fd, 1025 - len(data))
            if not chunk:
                raise BundleError("parent_unavailable", "Firmware manager disconnected")
            data.extend(chunk)
            if len(data) > 1024:
                raise BundleError(
                    "parent_unavailable", "Invalid firmware phase acknowledgement"
                )
        try:
            ack = json.loads(data)
        except (ValueError, UnicodeError) as error:
            raise BundleError(
                "parent_unavailable", "Invalid firmware phase acknowledgement"
            ) from error
        if (
            not isinstance(ack, dict)
            or set(ack) != {"ack", "job_id"}
            or type(ack["ack"]) is not int
            or ack != {"ack": sequence, "job_id": self.job_id}
        ):
            raise BundleError(
                "parent_unavailable", "Firmware phase acknowledgement does not match"
            )
        self.mutation_possible = True

    def progress(self, value):
        """Forward only measured transfer progress; never fabricate overall progress.

        @param value Existing TI updater progress dictionary.
        """
        if value.get("stage") == "transfer" and type(value.get("percent")) is int:
            self.emit("transfer", transfer_percent=value["percent"])
        elif value.get("stage") == "committed":
            self.emit("committed")


def validate_guard(fd):
    """Keep an inherited regular-file radio lease alive through all child processes.

    @param fd Descriptor for the parent's exclusively flocked stable radio guard.
    @return Same descriptor; never unlock it explicitly, including on failure.
    """
    try:
        if fd < 3 or not stat.S_ISREG(os.fstat(fd).st_mode):
            raise ValueError("Invalid radio guard")
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (OSError, ValueError) as error:
        raise BundleError(
            "radio_busy", "Exclusive radio update guard is unavailable"
        ) from error
    return fd


def main(argv=None):
    """Run one fixed-board installation and report transfer outcome to its parent.

    @param argv Optional argument list for isolated CLI tests; None uses sys.argv.
    @return Zero after transfer, one on failure; never claims network readiness.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--bundle-dir")
    source.add_argument("--artifact")
    parser.add_argument("--operation", choices=("update", "recovery"), default="update")
    parser.add_argument("--board")
    parser.add_argument("--version")
    parser.add_argument("--public-key")
    parser.add_argument("--dfu-serial")
    parser.add_argument("--helper-python")
    parser.add_argument("--nordic-profile")
    parser.add_argument("--candidate-id", required=True)
    parser.add_argument("--port", required=True)
    parser.add_argument("--baud", type=int, required=True)
    parser.add_argument("--job-id", required=True)
    parser.add_argument("--lock-fd", type=int, required=True)
    args = parser.parse_args(argv)
    if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", args.job_id):
        parser.error("Invalid job identity")
    channel = Channel(args.job_id)
    try:
        guard = validate_guard(args.lock_fd)
        if args.artifact:
            from .firmware_artifacts import inspect_artifact

            candidate = inspect_artifact(
                args.artifact,
                board=args.board,
                version=args.version,
                public_key=args.public_key,
                operation=args.operation,
            )
            if (
                candidate["id"] != args.candidate_id
                or candidate["version"] != FIRMWARE_COMPAT_VERSION
                or candidate["app_baudrate"] != args.baud
            ):
                raise BundleError(
                    "artifact_invalid",
                    "Local artifact changed or does not match this host",
                )
        else:
            if args.operation != "update" or any(
                (
                    args.board,
                    args.version,
                    args.public_key,
                    args.dfu_serial,
                    args.helper_python,
                )
            ):
                raise BundleError(
                    "unsupported_operation",
                    "Catalog updates cannot override transport or board",
                )
            candidate = load_candidate(
                args.bundle_dir,
                args.candidate_id,
                host_version=FIRMWARE_COMPAT_VERSION,
                app_baudrate=args.baud,
            )
        if args.operation == "recovery":
            from .firmware_manual import install_manual
            from .nordic_usb import profile

            install_manual(
                candidate,
                port_name=args.port,
                helper_python=args.helper_python,
                serial_number=args.dfu_serial,
                lock_fd=guard,
                before=channel.before,
                progress=channel.progress,
                usb_profile=(
                    profile(json.loads(args.nordic_profile))
                    if args.nordic_profile
                    else None
                ),
            )
        else:
            if args.dfu_serial or args.helper_python:
                raise BundleError(
                    "unsupported_operation",
                    "Routine updates cannot override their helper",
                )
            if args.nordic_profile:
                from .firmware_nordic import profile

                if candidate["backend"] != "nrf52840_usb_dfu_v1":
                    raise BundleError(
                        "unsupported_operation",
                        "Nordic profile cannot override another backend",
                    )
                nordic = profile(json.loads(args.nordic_profile))
                install(
                    candidate,
                    args.port,
                    guard,
                    channel.before,
                    channel.progress,
                    nordic=nordic,
                )
            else:
                install(candidate, args.port, guard, channel.before, channel.progress)
        channel.emit("transferred")
        return 0
    except Exception as error:
        code = error.code if isinstance(error, BundleError) else "worker_failed"
        # Raw helper/UART/filesystem exceptions can contain paths or credentials;
        # only reviewed BundleError messages cross the public event boundary.
        message = (
            str(error)
            if isinstance(error, BundleError)
            else "Firmware installation did not complete"
        )
        try:
            channel.emit(
                "failed", code=code, error=message, recovery=channel.mutation_possible
            )
        except (OSError, ValueError):
            pass
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
