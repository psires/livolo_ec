"""Isolated, direct-call provisioning model with no executable hardware backend.

Nothing registers this model in Service, NativeAPI, the app, or a CLI. Worker
events are supplied by tests only. Authentication here protects the direct-call
contract; it does not implement a network listener or managed-service discovery.
"""

from copy import deepcopy
import hashlib
import hmac
import json
import math
import re
import time
import uuid

from . import FIRMWARE_COMPAT_VERSION
from .firmware_artifacts import inspect_artifact_bytes
from .firmware_bundle import BundleError
from .provisioning_store import JournalFull

PROFILE = "zb-gw04-1v2"
PLAN_SECONDS = 120
MAX_PLANS = 32
TARGET_FIELDS = {
    "target_id",
    "generation",
    "board",
    "transport_identity",
    "board_evidence",
    "unique",
    "ownership",
    "mode",
    "loader_supported",
}
IDENTITY_FIELDS = ("target_id", "generation", "board", "transport_identity")


class ProvisioningError(ValueError):
    """A stable contract failure, without exception text or device paths."""


def identifier(value):
    """Validate an opaque handle without accepting paths or URLs.

    @param value Client or fixture handle, limited to 128 ASCII characters.
    @return True only for a bounded opaque identifier.
    """
    return isinstance(value, str) and bool(
        re.fullmatch(r"[A-Za-z0-9_:-]{1,128}", value)
    )


def fingerprint(value):
    """Bind reviewed facts using deterministic JSON, without interpreting them.

    @param value Public JSON-compatible modeled facts.
    @return SHA-256 hex digest of the canonical serialization.
    """
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


class ProvisioningModel:
    """Model exactly one target; physically installing firmware is impossible here."""

    def __init__(
        self, store, token, target, artifact, clock=time.time, ticks=time.monotonic
    ):
        """Bind server-owned fixture facts and a stable independent journal.

        @param store Dedicated ProvisioningStore, owned and closed by the caller.
        @param token Private non-whitespace ASCII credential of at least 32 characters.
        @param target Modeled target evidence; never inferred from a USB bridge.
        @param artifact Mapping with candidate_id and immutable synthetic GBL data bytes.
        @param clock UTC seconds provider for display timestamps only.
        @param ticks Monotonic seconds provider for plan expiry.
        """
        if (
            not isinstance(token, str)
            or len(token) < 32
            or not token.isascii()
            or any(c.isspace() for c in token)
        ):
            raise ValueError("A private prototype credential is required")
        self.store, self._token = store, token
        self.clock, self.ticks = clock, ticks
        self._plans = {}
        self.replace_fixture(target, artifact)

    def replace_fixture(self, target, artifact):
        """Replace trusted test evidence, never scan or operate on a device.

        @param target Exact modeled fields, including explicit board and owner evidence.
        @param artifact Exact candidate_id/data mapping; data must be immutable bytes.
        """
        if (
            not isinstance(target, dict)
            or set(target) != TARGET_FIELDS
            or any(not identifier(target[key]) for key in IDENTITY_FIELDS)
            or type(target["unique"]) is not bool
            or type(target["loader_supported"]) is not bool
            or target["board_evidence"] not in ("operator_confirmed", "unknown")
            or target["ownership"] not in ("exclusive", "conflict", "unknown")
            or target["mode"] not in ("stock", "bootloader", "application", "absent")
            or not isinstance(artifact, dict)
            or set(artifact) != {"candidate_id", "data"}
            or not identifier(artifact["candidate_id"])
            or not isinstance(artifact["data"], bytes)
            or len(artifact["data"]) > 1024 * 1024
        ):
            raise ValueError("Invalid isolated provisioning fixture")
        self.target, self.artifact = deepcopy(target), dict(artifact)

    def _envelope(self, **fields):
        """Label every model response as simulated and bind service identity.

        @param fields Detached public response fields, never credentials or payload bytes.
        """
        return dict(
            schema=1, instance_id=self.store.instance_id, simulated=True, **fields
        )

    def request(self, token, action, payload=None):
        """Serve the small authenticated direct-call contract, without HTTP or I/O.

        @param token Credential presented by the test client, never persisted or echoed.
        @param action One of info, targets, preflight, start or job.
        @param payload Exact action-specific JSON object; omitted means an empty object.
        @return Simulated envelope with ok/data or ok/error stable code.
        """
        if (
            not isinstance(token, str)
            or not token.isascii()
            or not hmac.compare_digest(token, self._token)
        ):
            return self._envelope(ok=False, error="authentication_failed")
        payload = {} if payload is None else payload
        try:
            if not isinstance(payload, dict):
                raise ProvisioningError("invalid_request")
            if action == "info" and not payload:
                data = dict(
                    operation="provision",
                    profile=PROFILE,
                    physical_install_supported=False,
                    model_install_supported=True,
                    network_ready=False,
                )
            elif action == "targets" and not payload:
                data = dict(
                    targets=[deepcopy(self.target)],
                    candidate_id=self.artifact["candidate_id"],
                )
            elif action == "preflight":
                data = self._preflight(payload)
            elif action == "start":
                data = self._start(payload)
            elif action == "job" and set(payload) == {"request_id"}:
                data = self._job(payload["request_id"])
            else:
                raise ProvisioningError("invalid_request")
            return self._envelope(ok=True, data=deepcopy(data))
        except ProvisioningError as error:
            return self._envelope(ok=False, error=str(error))

    def _identity(self):
        """Return only modeled physical identity, separate from mutable mode evidence."""
        return {key: self.target[key] for key in IDENTITY_FIELDS}

    def _target_check(self):
        """Fail closed on unverified or ambiguous fixture evidence, without probing."""
        if self.target["board"] != PROFILE:
            raise ProvisioningError("unsupported_profile")
        if self.target["board_evidence"] != "operator_confirmed":
            raise ProvisioningError("board_evidence_required")
        if not self.target["unique"]:
            raise ProvisioningError("target_ambiguous")
        if self.target["mode"] == "absent":
            raise ProvisioningError("target_absent")
        if self.target["ownership"] != "exclusive":
            raise ProvisioningError("ownership_unresolved")
        if not self.target["loader_supported"]:
            raise ProvisioningError("loader_unverified")

    def _facts(self):
        """Inspect immutable in-memory bytes and bind exact current model evidence."""
        self._target_check()
        try:
            metadata = inspect_artifact_bytes(
                self.artifact["data"], board=PROFILE, version=FIRMWARE_COMPAT_VERSION
            )
        except BundleError:
            raise ProvisioningError("artifact_invalid") from None
        return dict(
            target=deepcopy(self.target),
            candidate_id=self.artifact["candidate_id"],
            artifact_sha256=metadata["sha256"],
            version=FIRMWARE_COMPAT_VERSION,
            profile=PROFILE,
        )

    def _preflight(self, payload):
        """Create a short-lived review plan without admitting a job or device action.

        @param payload Exact target_id and candidate_id selected by the client.
        """
        if set(payload) != {"target_id", "candidate_id"} or any(
            not identifier(value) for value in payload.values()
        ):
            raise ProvisioningError("invalid_request")
        if payload != dict(
            target_id=self.target["target_id"],
            candidate_id=self.artifact["candidate_id"],
        ):
            raise ProvisioningError("selection_changed")
        if self.store.blocked():
            raise ProvisioningError("job_unresolved")
        facts = self._facts()
        if self.target["mode"] == "application":
            raise ProvisioningError("use_managed_update")
        now = self.ticks()
        self._plans = {k: v for k, v in self._plans.items() if v["deadline"] > now}
        if len(self._plans) >= MAX_PLANS:
            raise ProvisioningError("too_many_plans")
        plan = dict(
            plan_id=uuid.uuid4().hex,
            instance_id=self.store.instance_id,
            target_id=payload["target_id"],
            candidate_id=payload["candidate_id"],
            profile=PROFILE,
            version=facts["version"],
            artifact_sha256=facts["artifact_sha256"],
            expires_at=self.clock() + PLAN_SECONDS,
            requires_confirmation=True,
            physical_install_supported=False,
        )
        self._plans[plan["plan_id"]] = dict(
            public=plan, facts=fingerprint(facts), deadline=now + PLAN_SECONDS
        )
        return plan

    def _start(self, payload):
        """Admit an explicitly confirmed plan once, before any fake worker executes.

        @param payload Exact request_id, plan_id, instance_id and confirmed=true.
        """
        if set(payload) != {"request_id", "plan_id", "instance_id", "confirmed"} or any(
            not identifier(payload[key])
            for key in ("request_id", "plan_id", "instance_id")
        ):
            raise ProvisioningError("invalid_request")
        if payload["confirmed"] is not True:
            raise ProvisioningError("confirmation_required")
        if payload["instance_id"] != self.store.instance_id:
            raise ProvisioningError("instance_changed")
        existing = self.store.lookup(payload["request_id"])
        if existing:
            if existing[0] != payload:
                raise ProvisioningError("request_conflict")
            return existing[1]
        if self.store.plan_used(payload["plan_id"]):
            raise ProvisioningError("plan_used")
        plan = self._plans.get(payload["plan_id"])
        if not plan:
            raise ProvisioningError("plan_unknown")
        if self.ticks() >= plan["deadline"]:
            raise ProvisioningError("plan_expired")
        if self.store.blocked():
            raise ProvisioningError("job_unresolved")
        if fingerprint(self._facts()) != plan["facts"]:
            raise ProvisioningError("plan_changed")
        job = dict(
            request_id=payload["request_id"],
            plan_id=payload["plan_id"],
            instance_id=self.store.instance_id,
            operation="provision",
            simulated=True,
            profile=PROFILE,
            target_id=plan["public"]["target_id"],
            candidate_id=plan["public"]["candidate_id"],
            artifact_sha256=plan["public"]["artifact_sha256"],
            version=plan["public"]["version"],
            target_identity=self._identity(),
            status="pending",
            stage="awaiting_bootloader",
            transfer_percent=None,
            mutation_possible=False,
            error=None,
            recovery=None,
            application_verified=False,
            network_ready=False,
            setup_required=True,
        )
        try:
            self.store.admit(payload, job)
        except JournalFull:
            raise ProvisioningError("journal_full") from None
        return job

    def _job(self, request_id):
        """Read an admitted job without reconstructing or replaying any operation.

        @param request_id Opaque durable job/request identifier.
        """
        if not identifier(request_id):
            raise ProvisioningError("invalid_request")
        result = self.store.lookup(request_id)
        if result is None:
            raise ProvisioningError("job_unknown")
        return result[1]

    def _pending(self, request_id, stage):
        """Require an exact worker phase, including after restart or duplicate events.

        @param request_id Model worker's admitted job identifier.
        @param stage Exact predecessor stage permitted for this event.
        """
        job = self._job(request_id)
        if job["status"] != "pending" or job["stage"] != stage:
            raise ProvisioningError("invalid_transition")
        return job

    def _continuity(self, job, mode):
        """Recheck modeled identity and owner across the expected mode transition.

        @param job Original admitted target identity, never replaced by discovery.
        @param mode Exact expected current fixture mode.
        """
        self._target_check()
        if self._identity() != job["target_identity"]:
            raise ProvisioningError("target_changed")
        if self.target["mode"] != mode:
            raise ProvisioningError("wrong_target_mode")

    def before_transfer(self, request_id):
        """Commit the possible-mutation boundary; only a test worker consumes it.

        @param request_id Pending model job, explicitly confirmed by a test client.
        @return Detached checkpoint; exceptions mean no transfer may begin.
        """
        job = self._pending(request_id, "awaiting_bootloader")
        self._continuity(job, "bootloader")
        facts = self._facts()
        if any(facts[key] != job[key] for key in ("candidate_id", "artifact_sha256")):
            raise ProvisioningError("artifact_changed")
        job.update(stage="transferring", mutation_possible=True, transfer_percent=0)
        self.store.save(job)
        return deepcopy(job)

    def progress(self, request_id, percent):
        """Record measured fake transfer progress without establishing success.

        @param request_id Model job currently transferring.
        @param percent Finite, nondecreasing numeric percentage between zero and 100.
        """
        job = self._pending(request_id, "transferring")
        if (
            type(percent) not in (int, float)
            or not math.isfinite(percent)
            or not job["transfer_percent"] <= percent <= 100
        ):
            raise ProvisioningError("invalid_progress")
        job["transfer_percent"] = percent
        self.store.save(job)

    def transferred(self, request_id):
        """Finish modeled transfer while leaving the job pending verification.

        @param request_id Model worker's transferring job.
        """
        job = self._pending(request_id, "transferring")
        job.update(stage="verifying_application")
        self.store.save(job)

    def verify(self, request_id, evidence):
        """Confirm only exact modeled application identity with RF still disabled.

        @param request_id Model job that has completed transfer.
        @param evidence Exact fake VERSION/CAPS/STATUS projection, not an API input.
        """
        job = self._pending(request_id, "verifying_application")
        try:
            self._continuity(job, "application")
            expected = dict(
                board=PROFILE,
                fw_version=job["version"],
                proto="3",
                baud="460800",
                armed="0",
                ack="0",
            )
            if evidence != expected:
                raise ProvisioningError("application_mismatch")
        except ProvisioningError as error:
            self.fail(request_id, str(error))
            return
        job.update(
            status="confirmed", stage="application_verified", application_verified=True
        )
        self.store.save(job)

    def fail(self, request_id, code):
        """Retain uncertainty after possible writes, without implementing recovery.

        @param request_id Pending model job that cannot safely continue.
        @param code Stable bounded failure identifier, never raw helper output.
        """
        job = self._job(request_id)
        if job["status"] != "pending" or not identifier(code):
            raise ProvisioningError("invalid_transition")
        job.update(
            status="unknown" if job["mutation_possible"] else "rejected",
            stage="failed",
            error=code,
            recovery="recovery_required" if job["mutation_possible"] else None,
        )
        self.store.save(job)
