"""Physical provisioning journal, deliberately incompatible with model and gateway state."""

import fcntl
import hashlib
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

    def __init__(self, path, *, recover_pending=True):
        """Fence unfinished work before accepting another operation.

        @param path Independent app-owned journal path, not its gateway database.
        @param recover_pending False for stopped-owner maintenance, which must reject pending work unchanged.
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
                        if not recover_pending:
                            raise ValueError("Pending operation prevents maintenance")
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
                            elif result["kind"] == "prepare" and result.get(
                                "entry_attempted"
                            ):
                                result["recovery"] = (
                                    "manual_bootloader_inspection_required"
                                )
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
        reconciled = self.meta("entry_reconciled", [])
        maintenance = self.reconciliation()
        return [
            value
            for (encoded,) in self.db.execute("SELECT result FROM operations")
            for value in (json.loads(encoded),)
            if value["status"] == "pending" or value.get("recovery")
            if not maintenance or value["request_id"] != maintenance["request_id"]
            if not (
                value["request_id"] in reconciled
                and value.get("recovery") == "manual_bootloader_inspection_required"
                and value["kind"] == "prepare"
                and value["status"] != "pending"
            )
        ]

    def reconciliation(self):
        """Validate an immutable maintenance binding before excluding its old receipt."""
        record = self.meta("preflash_reconciliation")
        if not isinstance(record, dict):
            return None
        try:
            row = self.lookup(record["request_id"])
            plan = self.plan(record["plan_id"])
            if (
                row is None
                or plan is None
                or plan[1] != record["request_id"]
                or row[1].get("status") != "unknown"
                or row[1].get("kind") != "install"
                or row[1].get("stage") != "verifying_target"
                or row[1].get("error") != "inspection_timeout"
                or row[1].get("recovery") != "recovery_required"
                or row[1].get("profile") != "sonoff-zbdongle-e"
                or row[1].get("application_verified") is not False
                or row[1].get("network_ready") is not False
                or row[1].get("transfer_percent") is not None
                or row[1].get("expected_network_id") is not None
                or row[1].get("install_request_id") is not None
                or record["resolution"] != "verified_no_flash"
                or record["permits"] != "prepare_only"
                or record["kind"] != "install"
                or record["target_id"] != row[1]["target_id"]
                or record["operation_digest"] != _digest(row)
                or record["plan_digest"] != _digest(plan)
            ):
                return None
            return record
        except (KeyError, TypeError):
            return None

    def reconcile_preflash(self, request_id, target, evidence):
        """Record reviewed stopped-owner no-flash recovery without editing any receipt/plan.

        This is an administrative method, deliberately absent from the HTTP API.
        Its caller must hold the physical and service guards, verify exact target
        metadata and review the separate intact-sniffer evidence. It grants only
        a fresh explicit Prepare; no stored operation is replayed.
        @param request_id Exact original unknown install operation.
        @param target Current exact physical target matching the consumed plan.
        @param evidence Reviewed private evidence SHA-256 bindings, not live-mode proof.
        """
        if self.meta("preflash_reconciliation") is not None:
            raise ValueError("A maintenance reconciliation already exists")
        pending = self.unresolved()
        row = self.lookup(request_id)
        if (
            row is None
            or len(pending) != 1
            or pending[0]["request_id"] != request_id
            or self.meta("installation_attempted", False) is not False
            or self.meta("active_request_id")
            or self.meta("worker_cleanup_unconfirmed")
            or self.meta("baseline")
            or self.meta("verified_target")
            or self.meta("normal_target")
            or self.meta("handoff_request_id")
            or self.meta("network")
            or self.meta("mode") != "bootstrap"
            or self.meta("profile") != "sonoff-zbdongle-e"
            or not isinstance(evidence, dict)
            or set(evidence) != {"result_sha256", "events_sha256"}
            or any(
                not isinstance(value, str)
                or len(value) != 64
                or any(c not in "0123456789abcdef" for c in value)
                for value in evidence.values()
            )
        ):
            raise ValueError("No-flash maintenance boundary is not established")
        job = row[1]
        plan = self.plan(job.get("plan_id"))
        if (
            plan is None
            or plan[1] != request_id
            or plan[0]["inspection"]["target"] != target
        ):
            raise ValueError("Consumed plan or exact target changed")
        record = dict(
            reconciliation_id=uuid.uuid4().hex,
            request_id=request_id,
            kind="install",
            plan_id=job["plan_id"],
            target_id=job["target_id"],
            resolution="verified_no_flash",
            permits="prepare_only",
            setup_session=uuid.uuid4().hex,
            operation_digest=_digest(row),
            plan_digest=_digest(plan),
            evidence=evidence,
        )
        with self.db:
            self._meta("preflash_reconciliation", record)
            if self.reconciliation() is None:
                raise ValueError("Failed operation does not qualify for maintenance")
            self._meta("setup_session", record["setup_session"])
            self._meta("reconciliation_requires_prepare", True)
        return record

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

    def prepared(self, result, plan, reconciled=()):
        """Commit inspected plan and successful prepare result atomically.

        @param result Confirmed prepare result containing the public plan.
        @param plan Private immutable plan binding, including controller epoch.
        @param reconciled Prior uncertain entry requests superseded by explicit loader inspection.
        """
        with self.db:
            self.db.execute(
                "INSERT INTO plans VALUES (?,?,NULL)",
                (plan["plan_id"], json.dumps(plan)),
            )
            self._save(result)
            if self.reconciliation() is not None:
                self._meta("reconciliation_requires_prepare", False)
            if reconciled:
                self._meta(
                    "entry_reconciled",
                    sorted(set(self.meta("entry_reconciled", [])) | set(reconciled)),
                )

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


def _digest(value):
    """Bind immutable private journal values to their maintenance record.

    @param value JSON-compatible recorded request/result or consumed plan pair.
    """
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
