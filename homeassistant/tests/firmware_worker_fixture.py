"""Run the real firmware worker with only its hardware installer replaced."""

import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import time

import serial

from livolod import firmware_worker


def forbidden(*args, **kwargs):
    """Fail closed if the fixture accidentally reaches real hardware or a helper.

    @param args Unexpected operation arguments.
    @param kwargs Unexpected operation keyword arguments.
    """
    raise AssertionError("Unexpected hardware/helper access in offline fixture")


def install(candidate, port, guard, before, progress):
    """Emulate transfer while checking durable ACKs across real worker IPC.

    @param candidate Artifact revalidated by the real worker before this boundary.
    @param port Unused synthetic serial path.
    @param guard Inherited descriptor already validated by the real worker.
    @param before Actual Channel critical-boundary callback.
    @param progress Actual Channel measured-progress callback.
    """
    root = Path(os.environ["LIVOLO_TEST_ROOT"])
    scenario = os.environ["LIVOLO_TEST_SCENARIO"]
    assert port == "offline-test-radio"
    assert candidate["board"] == "zb-gw04-1v2"
    assert candidate["_data"]
    os.fstat(guard)
    if scenario == "before":
        raise RuntimeError("Synthetic failure before mutation")
    for stage in ("bootloader_entry", "flash"):
        before(stage)
        with sqlite3.connect(root / "state.db") as db:
            job = json.loads(
                db.execute(
                    "SELECT value FROM meta WHERE key='firmware_job'"
                ).fetchone()[0]
            )
        assert job["last_critical"] == stage
        assert job["request_id"] == sys.argv[sys.argv.index("--job-id") + 1]
    if scenario == "after":
        raise RuntimeError("Synthetic failure after mutation")
    if scenario == "death":
        os._exit(9)
    progress({"stage": "transfer", "percent": 37})
    progress({"stage": "transfer", "percent": 100})
    deadline = time.monotonic() + 20
    while not (root / "release-worker").exists():
        if time.monotonic() > deadline:
            raise RuntimeError("Offline test did not release worker")
        time.sleep(0.01)
    (root / "transferred").touch()


if __name__ == "__main__":
    serial.Serial = forbidden
    subprocess.Popen = forbidden
    firmware_worker.install = install
    raise SystemExit(firmware_worker.main())
