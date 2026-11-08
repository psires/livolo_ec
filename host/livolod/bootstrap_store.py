"""Physical provisioning journal, deliberately incompatible with model and gateway state."""

import fcntl
import json
import os
from pathlib import Path
import sqlite3
import stat
import uuid

APPLICATION_ID = 0x4C425031
LIMIT = 2048


def private_open(path):
    """Open one regular private state file without following a final symlink.

    @param path Trusted application-owned state or lock pathname.
    @return Owned file descriptor, closed by the caller.
    """
    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise ValueError("Invalid physical provisioning state file")
        os.fchmod(fd, 0o600)
        return fd
    except BaseException:
        os.close(fd)
        raise


class BootstrapStore:
    """Single-owner durable intents/results; never an assertion of physical exclusivity."""

    def __init__(self, path):
        """Fence unfinished work before accepting another operation.

        @param path Independent app-owned journal path, not its gateway database.
        """
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = None
        self.lock = private_open(str(path) + ".lock")
        try:
            fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            descriptor = private_open(path)
            os.close(descriptor)
            self.db = sqlite3.connect(path)
            application = self.db.execute("PRAGMA application_id").fetchone()[0]
            tables = self.db.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
            if application != APPLICATION_ID and (application or tables):
                raise ValueError("Not a physical bootstrap journal")
            self.db.execute("PRAGMA synchronous=FULL")
            self.db.execute(f"PRAGMA application_id={APPLICATION_ID}")
            self.db.executescript(
                """
                CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS operations (
                    id TEXT PRIMARY KEY, request TEXT NOT NULL, result TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS plans (
                    id TEXT PRIMARY KEY, value TEXT NOT NULL, consumed_by TEXT UNIQUE);
                """
            )
            with self.db:
                if self.meta("instance_id") is None:
                    self._meta("instance_id", uuid.uuid4().hex)
                    self._meta("physical", True)
                if self.meta("physical") is not True:
                    raise ValueError(
                        "Simulated journals cannot authorize physical provisioning"
                    )
                for ident, encoded in self.db.execute(
                    "SELECT id,result FROM operations"
                ).fetchall():
                    result = json.loads(encoded)
                    if result["status"] == "pending":
                        # Only separately committed handoff permission may resume startup.
                        resumable = (
                            result["kind"] == "handoff"
                            and result["stage"] == "handoff_authorized"
                        )
                        if not resumable:
                            result.update(
                                status="unknown",
                                stage="interrupted",
                                error="service_restarted",
                            )
                            if result["kind"] in (
                                "install",
                                "handoff",
                                "enter",
                                "cancel",
                            ):
                                result["recovery"] = "recovery_required"
                            self._save(result)
            self.instance_id = self.meta("instance_id")
            self.epoch = uuid.uuid4().hex
        except BaseException:
            self.close()
            raise

    def meta(self, key, default=None):
        """Read detached persistent service metadata.

        @param key Internal metadata name.
        @param default Value returned when the key has not been written.
        """
        row = self.db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def bind_profile(self, profile, legacy):
        """Prevent a configured board from reinterpreting another board's journal.

        @param profile Selected immutable board profile identifier.
        @param legacy Only profile supported by journals predating explicit tagging.
        """
        with self.db:
            saved = self.meta("profile")
            populated = any(
                self.db.execute(query).fetchone()[0]
                for query in (
                    "SELECT count(*) FROM meta WHERE key NOT IN ('instance_id','physical')",
                    "SELECT count(*) FROM operations",
                    "SELECT count(*) FROM plans",
                )
            )
            if (saved is not None and saved != profile) or (
                saved is None and populated and profile != legacy
            ):
                raise ValueError(
                    "Configured profile differs from physical setup journal"
                )
            self._meta("profile", profile)

    def _meta(self, key, value):
        """Write metadata within the caller's transaction.

        @param key Internal metadata name.
        @param value JSON-compatible non-secret value.
        """
        self.db.execute(
            "INSERT OR REPLACE INTO meta VALUES (?,?)", (key, json.dumps(value))
        )

    def set_meta(self, key, value):
        """Commit independent service metadata before an external effect.

        @param key Internal metadata name.
        @param value JSON-compatible non-secret value.
        """
        with self.db:
            self._meta(key, value)

    def lookup(self, request_id):
        """Read a request/result without replaying it.

        @param request_id Client-generated operation identity.
        @return Request/result pair or None.
        """
        row = self.db.execute(
            "SELECT request,result FROM operations WHERE id=?", (request_id,)
        ).fetchone()
        return tuple(json.loads(value) for value in row) if row else None

    def unresolved(self):
        """Return pending or recovery-fenced operations, including orphan worker intent."""
        return [
            value
            for (encoded,) in self.db.execute("SELECT result FROM operations")
            for value in (json.loads(encoded),)
            if value["status"] == "pending" or value.get("recovery")
        ]

    def admit(self, request, result, plan_id=None):
        """Atomically consume a plan and record an operation before scheduling work.

        @param request Exact validated kind/payload idempotency binding.
        @param result Initial pending public result.
        @param plan_id Optional reviewed plan that must be unused.
        """
        with self.db:
            if (
                self.db.execute("SELECT count(*) FROM operations").fetchone()[0]
                >= LIMIT
            ):
                raise ValueError("journal_full")
            if plan_id:
                count = self.db.execute(
                    "UPDATE plans SET consumed_by=? WHERE id=? AND consumed_by IS NULL",
                    (result["request_id"], plan_id),
                ).rowcount
                if count != 1:
                    raise ValueError("plan_used")
            self.db.execute(
                "INSERT INTO operations VALUES (?,?,?)",
                (result["request_id"], json.dumps(request), json.dumps(result)),
            )
            self._meta("latest_request_id", result["request_id"])

    def _save(self, result):
        """Update a result inside an existing transaction.

        @param result Detached result containing its admitted request ID.
        """
        if (
            self.db.execute(
                "UPDATE operations SET result=? WHERE id=?",
                (json.dumps(result), result["request_id"]),
            ).rowcount
            != 1
        ):
            raise ValueError("unknown_operation")

    def save(self, result, metadata=None):
        """Commit checkpoint and optional mode/config together before acknowledgement.

        @param result New durable result for an admitted operation.
        @param metadata Optional internal metadata to commit atomically with the checkpoint.
        """
        with self.db:
            self._save(result)
            for key, value in (metadata or {}).items():
                self._meta(key, value)

    def prepared(self, result, plan):
        """Commit inspected plan and successful prepare result atomically.

        @param result Confirmed prepare result containing the public plan.
        @param plan Private immutable plan binding, including controller epoch.
        """
        with self.db:
            self.db.execute(
                "INSERT INTO plans VALUES (?,?,NULL)",
                (plan["plan_id"], json.dumps(plan)),
            )
            self._save(result)

    def plan(self, plan_id):
        """Read a prepared binding without changing its consumption state.

        @param plan_id Opaque prepared plan identifier.
        """
        row = self.db.execute(
            "SELECT value,consumed_by FROM plans WHERE id=?", (plan_id,)
        ).fetchone()
        return (json.loads(row[0]), row[1]) if row else None

    def close(self):
        """Close only journal ownership; physical workers have separate inherited leases."""
        if self.db is not None:
            self.db.close()
            self.db = None
        if self.lock is not None:
            os.close(self.lock)
            self.lock = None
