"""Authoritative host runtime state and a durable command deduplication ledger."""

import fcntl
import json
import os
from pathlib import Path
import sqlite3
import time


class Store:
    def __init__(self, path, network):
        """Claim exclusive ownership of persistent state for one gateway network.

        @param path SQLite database path; its directory is created if needed.
        @param network Stable gateway-and-tag identifier bound to this database.
        """
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.lock = open(path + ".lock", "a")
        try:
            fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.lock.close()
            raise RuntimeError("Another daemon owns this state database") from None
        try:
            descriptor = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
            os.fchmod(descriptor, 0o600)
            os.close(descriptor)
            for suffix in ("-wal", "-shm"):
                if Path(path + suffix).exists():
                    os.chmod(path + suffix, 0o600)
            self.db = sqlite3.connect(path)
            self.db.execute("PRAGMA journal_mode=WAL")
            self.db.execute("PRAGMA synchronous=FULL")
            self.db.executescript(
                """
                CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS states (device TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS devices (id TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS removed_devices (id TEXT PRIMARY KEY);
                CREATE TABLE IF NOT EXISTS commands (
                    id TEXT PRIMARY KEY, request TEXT NOT NULL, result TEXT NOT NULL, updated REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS tokens (
                    device TEXT NOT NULL, token INTEGER NOT NULL, used REAL NOT NULL,
                    PRIMARY KEY(device, token));
            """
            )
            previous = self.db.execute(
                "SELECT value FROM meta WHERE key='network'"
            ).fetchone()
            if previous and previous[0] != network:
                raise ValueError(
                    "Database belongs to another network; use a new database when changing identity/tag"
                )
            with self.db:
                self.db.execute(
                    "INSERT OR IGNORE INTO meta VALUES ('network', ?)", (network,)
                )
                for request_id, encoded in self.db.execute(
                    "SELECT id, result FROM commands"
                ).fetchall():
                    result = json.loads(encoded)
                    if result["status"] in ("pending", "pairing"):
                        result.update(
                            status="unknown", error="daemon restarted before completion"
                        )
                        self.finish(request_id, result)
        except BaseException:
            if hasattr(self, "db"):
                self.db.close()
            self.lock.close()
            raise

    def mqtt_settings(self, seed):
        """Restore saved broker settings or use installation defaults.

        @param seed Default broker settings when no override is stored.
        @return The effective MQTT settings mapping.
        """
        row = self.db.execute(
            "SELECT value FROM meta WHERE key='mqtt_settings'"
        ).fetchone()
        return json.loads(row[0]) if row else dict(seed)

    def save_mqtt_settings(self, settings):
        """Persist broker settings for subsequent daemon starts.

        @param settings Validated settings, including any saved credentials.
        """
        with self.db:
            self.db.execute(
                "INSERT OR REPLACE INTO meta VALUES ('mqtt_settings', ?)",
                (json.dumps(settings),),
            )

    def states(self):
        """Restore the last persisted reports for all devices.

        @return A mapping from device IDs to state reports.
        """
        return {
            key: json.loads(value)
            for key, value in self.db.execute("SELECT device,value FROM states")
        }

    def devices(self, seed):
        """Restore the authoritative registry, seeding it only on first use.

        @param seed Configured Device objects used when the registry is uninitialized.
        @return The registered Device objects ordered by identity.
        """
        from dataclasses import asdict
        from .config import Device

        if not self.db.execute(
            "SELECT 1 FROM meta WHERE key='registry_initialized'"
        ).fetchone():
            with self.db:
                self.db.executemany(
                    "INSERT INTO devices VALUES (?,?)",
                    [(d.id, json.dumps(asdict(d))) for d in seed],
                )
                self.db.execute("INSERT INTO meta VALUES ('registry_initialized','1')")
        return tuple(
            Device(**json.loads(value))
            for (value,) in self.db.execute("SELECT value FROM devices ORDER BY id")
        )

    def replace_devices(self, devices):
        """Commit a replacement registry and retain removal tombstones for discovery.

        @param devices Complete iterable of Device objects that should remain registered.
        """
        from dataclasses import asdict

        devices = tuple(devices)
        with self.db:
            self.db.execute(
                "INSERT OR IGNORE INTO removed_devices SELECT id FROM devices"
            )
            self.db.execute("DELETE FROM devices")
            self.db.executemany(
                "INSERT INTO devices VALUES (?,?)",
                [(d.id, json.dumps(asdict(d))) for d in devices],
            )
            self.db.execute(
                "DELETE FROM states WHERE device NOT IN (SELECT id FROM devices)"
            )
            self.db.execute(
                "DELETE FROM removed_devices WHERE id IN (SELECT id FROM devices)"
            )

    def removed_devices(self):
        """Identify removed devices whose retained broker records need clearing.

        @return A list of removed device identities.
        """
        return [
            device for (device,) in self.db.execute("SELECT id FROM removed_devices")
        ]

    def command_result(self, request_id):
        """Find the durable result and context of a submitted command.

        @param request_id Command deduplication identifier.
        @return The enriched result mapping, or None for an unknown identifier.
        """
        row = self.db.execute(
            "SELECT result,request,updated FROM commands WHERE id=?", (request_id,)
        ).fetchone()
        return self._result(row) if row else None

    @staticmethod
    def _result(row):
        """Present a stored command result with its action and update time.

        @param row Database tuple containing encoded result, encoded request, and timestamp.
        @return The result mapping enriched with request context.
        """
        encoded, request, updated = row
        result, request = json.loads(encoded), json.loads(request)
        result.setdefault("action", request.get("action"))
        if "device" in request:
            result.setdefault("device", request["device"])
        result["updated"] = updated
        return result

    def recent_results(self):
        """Provide a bounded command history for the activity view.

        @return Up to 50 enriched results, newest first.
        """
        return [
            self._result(row)
            for row in self.db.execute(
                "SELECT result,request,updated FROM commands ORDER BY updated DESC LIMIT 50"
            )
        ]

    def save_state(self, device, value):
        """Persist the latest accepted report for a device.

        @param device Reporting device identity.
        @param value JSON-serializable state report.
        """
        with self.db:
            self.db.execute(
                "INSERT OR REPLACE INTO states VALUES (?,?)",
                (device, json.dumps(value)),
            )

    def previous(self, request_id, request):
        """Detect command retries without allowing an ID to change its meaning.

        @param request_id Command deduplication identifier.
        @param request Canonical command fields to compare with the original request.
        @return The previous result, or None when this ID has not been used.
        """
        encoded = json.dumps(request, sort_keys=True, separators=(",", ":"))
        previous = self.db.execute(
            "SELECT request,result FROM commands WHERE id=?", (request_id,)
        ).fetchone()
        if previous:
            if previous[0] != encoded:
                raise ValueError("request_id was already used for a different command")
            return self.command_result(request_id)
        return None

    def begin(self, request_id, request):
        """Record a new pending command before any side effects are attempted.

        @param request_id Command deduplication identifier.
        @param request Canonical JSON-serializable command fields.
        @return The existing result for a retry, or None for a newly recorded command.
        """
        previous = self.previous(request_id, request)
        if previous:
            return previous
        encoded = json.dumps(request, sort_keys=True, separators=(",", ":"))
        self.finish(
            request_id, {"request_id": request_id, "status": "pending"}, encoded
        )
        return None

    def finish(self, request_id, result, request=None):
        """Commit a command outcome to the durable ledger.

        @param request_id Command deduplication identifier.
        @param result JSON-serializable outcome mapping.
        @param request Encoded canonical request for a new entry, or None to update an existing
            one.
        """
        with self.db:
            if request is None:
                self.db.execute(
                    "UPDATE commands SET result=?,updated=? WHERE id=?",
                    (json.dumps(result), time.time(), request_id),
                )
            else:
                self.db.execute(
                    "INSERT INTO commands VALUES (?,?,?,?)",
                    (request_id, request, json.dumps(result), time.time()),
                )

    def token(self, device, avoid=None, *, mask=0, value=0, full_mask=3):
        # Experimental compatibility guard: captured ON/OFF failures shared a
        # checksum and tokens differing only in bit 0. Neither dedup key is proven.
        """Reserve an output transaction token with experimental replay-avoidance guards.

        @param device Target device identity.
        @param avoid Latest device-reported token to avoid, or None.
        @param mask Output bits targeted by the new command.
        @param value Desired state of the targeted outputs.
        @param full_mask Supported device outputs used to interpret older ledger entries.
        @return The durably reserved nonzero eight-bit token.
        """
        now = time.time()
        used = dict(
            self.db.execute("SELECT token,used FROM tokens WHERE device=?", (device,))
        )
        key = "last_output:" + device
        row = self.db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        previous = json.loads(row[0]) if row else None
        if previous is None:
            # Bootstrap pre-guard databases without changing their schema. A
            # failed/unconfirmed transmission is just as relevant as a success.
            for request, result in self.db.execute(
                "SELECT request,result FROM commands ORDER BY updated DESC"
            ):
                request, result = json.loads(request), json.loads(result)
                if (
                    request.get("action") != "set"
                    or request.get("device") != device
                    or "token" not in result
                ):
                    continue
                old_mask = request.get("mask", full_mask)
                old_value = request.get(
                    "value", full_mask if request.get("state") == "ON" else 0
                )
                previous = {
                    "token": result["token"],
                    "sum": (old_mask + old_value + result["token"]) & 255,
                }
                break
        recent = max(used, key=used.get) if used else None
        avoid_tokens = {
            n
            for n in (avoid, recent, previous["token"] if previous else None)
            if n is not None
        }
        candidates = [
            n
            for n in range(1, 256)
            if now - used.get(n, 0) >= 60
            and all(
                ((n - old) & 255) not in (0, 1, 255) and (n >> 1) != (old >> 1)
                for old in avoid_tokens
            )
            and (previous is None or ((mask + value + n) & 255) != previous["sum"])
        ]
        if not candidates:
            raise ValueError("Transaction tokens exhausted; wait before sending again")
        token = min(candidates, key=lambda n: used.get(n, 0))
        # Reserve both token and command signature atomically before any SEND.
        # A crash or uncertain transmission must not make them reusable on restart.
        with self.db:
            self.db.execute(
                "INSERT OR REPLACE INTO tokens VALUES (?,?,?)", (device, token, now)
            )
            self.db.execute(
                "INSERT OR REPLACE INTO meta VALUES (?,?)",
                (
                    key,
                    json.dumps({"token": token, "sum": (mask + value + token) & 255}),
                ),
            )
        return token

    def close(self):
        """Release the database and exclusive daemon ownership lock."""
        self.db.close()
        self.lock.close()
