"""Independent journal for the isolated provisioning model, never gateway state."""

import fcntl
import json
import os
from pathlib import Path
import sqlite3
import stat
import uuid

APPLICATION_ID = 0x4C505631
MAX_JOBS = 1024


class JournalFull(ValueError):
    """Refuse new jobs instead of evicting durable idempotency records."""


def private_file(path):
    """Open a private regular file without following a final symlink.

    @param path Operator-owned journal or journal-lock path, never HTTP input.
    @return Owned descriptor; the caller closes it.
    """
    fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise ValueError("Provisioning state must be a private regular file")
        os.fchmod(fd, 0o600)
        return fd
    except BaseException:
        os.close(fd)
        raise


class ProvisioningStore:
    """Single-owner model journal; this lock is NOT a physical-device lease."""

    def __init__(self, path):
        """Open dedicated state and conservatively fence interrupted model jobs.

        @param path Local private SQLite path, separate from the gateway database.
        """
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = private_file(str(path) + ".lock")
        self.db = None
        try:
            fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            fd = private_file(path)
            os.close(fd)
            self.db = sqlite3.connect(path)
            application = self.db.execute("PRAGMA application_id").fetchone()[0]
            tables = self.db.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
            if application != APPLICATION_ID and (application or tables):
                raise ValueError("Not a provisioning journal; gateway state refused")
            self.db.execute("PRAGMA synchronous=FULL")
            self.db.execute(f"PRAGMA application_id={APPLICATION_ID}")
            self.db.executescript(
                """
                CREATE TABLE IF NOT EXISTS identity (id TEXT PRIMARY KEY);
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY, plan TEXT UNIQUE NOT NULL,
                    request TEXT NOT NULL, value TEXT NOT NULL);
                """
            )
            with self.db:
                if not self.db.execute("SELECT id FROM identity").fetchone():
                    self.db.execute(
                        "INSERT INTO identity VALUES (?)", (uuid.uuid4().hex,)
                    )
                for ident, encoded in self.db.execute("SELECT id, value FROM jobs"):
                    job = json.loads(encoded)
                    if job["status"] == "pending":
                        job.update(
                            status="unknown",
                            stage="interrupted",
                            error="service_restarted",
                            recovery="recovery_required",
                        )
                        self.db.execute(
                            "UPDATE jobs SET value=? WHERE id=?",
                            (json.dumps(job), ident),
                        )
            identities = self.db.execute("SELECT id FROM identity").fetchall()
            if len(identities) != 1:
                raise ValueError("Ambiguous provisioning service identity")
            self.instance_id = identities[0][0]
        except BaseException:
            self.close()
            raise

    def lookup(self, request_id):
        """Read a detached request/job pair for idempotent reconciliation.

        @param request_id Opaque previously admitted request identity.
        @return Tuple of request and job, or None when not admitted.
        """
        row = self.db.execute(
            "SELECT request, value FROM jobs WHERE id=?", (request_id,)
        ).fetchone()
        return tuple(json.loads(value) for value in row) if row else None

    def blocked(self):
        """Report unresolved work without treating this as hardware ownership."""
        return any(
            job["status"] == "pending" or job.get("recovery")
            for (value,) in self.db.execute("SELECT value FROM jobs")
            for job in (json.loads(value),)
        )

    def plan_used(self, plan_id):
        """Keep one reviewed plan from authorizing multiple distinct jobs.

        @param plan_id Opaque plan identifier from a proposed new submission.
        """
        return (
            self.db.execute("SELECT 1 FROM jobs WHERE plan=?", (plan_id,)).fetchone()
            is not None
        )

    def admit(self, request, job):
        """Commit one immutable request and its initial job before any model work.

        @param request Validated exact submission, including its request identity.
        @param job Initial pending job, with no secrets or artifact bytes.
        """
        if self.db.execute("SELECT count(*) FROM jobs").fetchone()[0] >= MAX_JOBS:
            raise JournalFull("Provisioning identities are not evicted")
        with self.db:
            self.db.execute(
                "INSERT INTO jobs VALUES (?, ?, ?, ?)",
                (
                    request["request_id"],
                    request["plan_id"],
                    json.dumps(request),
                    json.dumps(job),
                ),
            )

    def save(self, job):
        """Durably save a model checkpoint before the test worker proceeds.

        @param job Detached update for an already admitted request identity.
        """
        with self.db:
            result = self.db.execute(
                "UPDATE jobs SET value=? WHERE id=?",
                (json.dumps(job), job["request_id"]),
            )
            if result.rowcount != 1:
                raise ValueError("Unknown provisioning request")

    def close(self):
        """Release local journal ownership; never operate on a radio."""
        if self.db is not None:
            self.db.close()
            self.db = None
        if self.lock is not None:
            os.close(self.lock)
            self.lock = None
