"""Durable physical setup operations; HTTP admission never performs serial work inline."""

import asyncio
from copy import deepcopy
import hashlib
import json
import logging
import secrets
import time
import uuid

from .config import identity, hex_value
from .firmware_bundle import BundleError
from .bootstrap_catalog import bootstrap_candidate
from .gecko_profiles import gecko_profile
from .sniffer_identity import ENTRY_METHOD

PROFILE = "zb-gw04-1v2"
PLAN_LIFETIME = 120
LOG = logging.getLogger(__name__)
ENTRY_RECOVERY = "manual_bootloader_inspection_required"


def failure_location(error):
    """Describe unexpected failure frames without messages, paths, source or locals.

    @param error Exception whose traceback remains private to this process.
    @return Bounded list of project module/function/line identifiers for server logs.
    """
    frames = []
    trace = error.__traceback__
    while trace is not None:
        module = trace.tb_frame.f_globals.get("__name__", "")
        function = trace.tb_frame.f_code.co_name
        if (
            isinstance(module, str)
            and module.startswith("livolod.")
            and all(
                c.isascii() and (c.isalnum() or c in "_.") for c in module + function
            )
        ):
            frames.append(f"{module[:80]}.{function[:80]}:{trace.tb_lineno}")
        trace = trace.tb_next
    return frames[-8:]


class BootstrapError(ValueError):
    """Stable public failure with an HTTP classification, never a helper traceback."""

    def __init__(self, code, status=409):
        """Bind a stable outcome to its response status.

        @param code Public error identifier.
        @param status HTTP error code.
        """
        super().__init__(code)
        self.code, self.status = code, status


def handle(value, maximum=128):
    """Accept bounded opaque handles, never path or URL syntax.

    @param value Client-provided identifier.
    @param maximum Maximum permitted ASCII length.
    """
    return (
        isinstance(value, str)
        and 0 < len(value) <= maximum
        and all(c.isascii() and (c.isalnum() or c in "_.:-") for c in value)
    )


def digest(value):
    """Bind JSON-compatible server-owned facts independently of object ordering.

    @param value Non-secret target/profile facts.
    """
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def network(value):
    """Validate an explicitly accepted wire network, without creating a live identity.

    @param value Exact gateway/tag mapping supplied or generated after consent.
    """
    if not isinstance(value, dict) or set(value) != {"gateway", "tag"}:
        raise BootstrapError("invalid_network", 400)
    try:
        result = dict(
            gateway=identity(value["gateway"]), tag=hex_value(value["tag"], 2, "tag")
        )
    except (ValueError, TypeError):
        raise BootstrapError("invalid_network", 400) from None
    if result["tag"] in ("00", "ff"):
        raise BootstrapError("invalid_network", 400)
    return result


class BootstrapController:
    """One serialized setup owner with worker and normal-runtime dependencies injected."""

    def __init__(self, store, settings, worker, runtime, clock=time.time):
        """Restore committed mode without automatically re-running physical operations.

        @param store Distinct physical BootstrapStore, never the model journal.
        @param settings Server-owned port/profile/catalog/deployment settings.
        @param worker Supervised async worker supporting run(kind, job, candidate, binding, checkpoint).
        @param runtime Async enter/handoff and readiness owner for normal Service.
        @param clock UTC seconds provider for durable plan expiry.
        """
        self.store, self.settings, self.worker, self.runtime, self.clock = (
            store,
            deepcopy(settings),
            worker,
            runtime,
            clock,
        )
        self.tasks = set()
        self.closed = False
        self.profile = gecko_profile(settings.get("profile", PROFILE))
        store.bind_profile(self.profile.board, PROFILE)
        self.target_id = (
            "zb-"
            + digest({"port": settings["serial_port"], "profile": self.profile.board})[
                :24
            ]
        )
        self.candidate = None
        self.blocked_reason = None
        self.engineering_only = bool(
            (settings.get("loader_policy") or {}).get("engineering")
        )
        self._deadlines = {}
        self.inspection = None
        self.session = store.meta("setup_session", store.epoch)
        store.set_meta("setup_session", self.session)
        self.mode = store.meta("mode", settings.get("mode", "gateway"))
        initial = settings.get("network")
        if initial is not None:
            initial = network(initial)
            saved = store.meta("network")
            if saved is not None and saved != initial:
                raise ValueError(
                    "Configured network differs from physical setup journal"
                )
            store.set_meta("network", initial)
        self.legacy_reason = (
            self.runtime.admission_reason()
            if hasattr(self.runtime, "admission_reason")
            else None
        )
        self.refresh_catalog()

    def refresh_catalog(self):
        """Refresh bounded local artifact metadata; never invoked by a status GET."""
        try:
            self.candidate = bootstrap_candidate(self.settings)
            self.blocked_reason = getattr(self.worker, "blocked_reason", None)
        except (BundleError, OSError, ValueError) as error:
            self.candidate = None
            self.blocked_reason = getattr(error, "code", "catalog_unavailable")

    def envelope(self, **fields):
        """Label the actual service contract distinctly from the model.

        @param fields Public result or stable error fields.
        """
        return dict(
            schema=1,
            instance_id=self.store.instance_id,
            simulated=False,
            operation="provision",
            **fields,
        )

    def _entry_reinspection(self):
        """Allow only a drained entry-only uncertainty to request fresh loader inspection."""
        unresolved = self.store.unresolved()
        return bool(
            unresolved
            and not self.closed
            and not self.legacy_reason
            and not self.blocked_reason
            and self.candidate
            and self.mode == "bootstrap"
            and self.runtime.quiesced is True
            and getattr(self.worker, "poisoned", False) is False
            and not self.store.meta("installation_attempted", False)
            and all(
                job["kind"] == "prepare"
                and job["status"] != "pending"
                and job.get("entry_attempted") is True
                and job.get("recovery") == ENTRY_RECOVERY
                and job.get("profile") == self.profile.board
                and job.get("target_id") == self.target_id
                for job in unresolved
            )
        )

    def info(self):
        """Return stored service/mode/capability facts without UART, probing or retries."""
        pending = self.store.unresolved()
        selected = self.store.meta("network")
        reason = (
            "recovery_required"
            if any(j.get("recovery") for j in pending)
            else self.legacy_reason or self.blocked_reason
        )
        eligible = bool(
            self.candidate and not reason and not pending and not self.closed
        )
        reinspection = self._entry_reinspection()
        maintenance = self.store.reconciliation()
        prepare_only = bool(
            maintenance and self.store.meta("reconciliation_requires_prepare")
        )
        if reinspection:
            reason = ENTRY_RECOVERY
        return dict(
            mode=self.mode,
            profile=self.profile.board,
            engineering_only=self.engineering_only,
            setup_session=self.session,
            reconciliation=(
                {
                    key: maintenance[key]
                    for key in (
                        "reconciliation_id",
                        "request_id",
                        "kind",
                        "plan_id",
                        "target_id",
                        "resolution",
                        "permits",
                        "setup_session",
                    )
                }
                if maintenance and maintenance["setup_session"] == self.session
                else None
            ),
            physical_install_supported=eligible
            and self.mode == "bootstrap"
            and not prepare_only,
            prepare_supported=(eligible and self.mode == "bootstrap") or reinspection,
            manual_reinspection_supported=reinspection,
            enter_supported=eligible
            and not self.legacy_reason
            and self.mode == "gateway",
            baseline_available=bool(self.store.meta("baseline")),
            cancel_supported=self.mode == "bootstrap"
            and not prepare_only
            and not pending
            and not self.store.meta("installation_attempted", False)
            and (
                not self.runtime.has_gateway_state()
                or bool(self.store.meta("baseline"))
            ),
            initialize_supported=self.mode == "bootstrap"
            and not prepare_only
            and selected is None
            and not pending,
            expected_network_id=(
                (selected["gateway"] + ":" + selected["tag"]) if selected else None
            ),
            active_request_id=next((j["request_id"] for j in pending), None),
            latest_request_id=self.store.meta("latest_request_id"),
            blocked_reason=reason,
            requires_dedicated_radio=True,
        )

    def targets(self):
        """Return only the configured profile and cached candidate, not a discovery scan."""
        candidate = self.candidate
        return dict(
            targets=(
                []
                if not candidate
                else [
                    dict(
                        target_id=self.target_id,
                        board=self.profile.board,
                        label=self.profile.label,
                        candidate_id=candidate["id"],
                        version=candidate["version"],
                        artifact_sha256=candidate["sha256"],
                        requires_bootloader=True,
                        entry_methods=(
                            ["manual", ENTRY_METHOD]
                            if self.profile.board == "sonoff-zbdongle-e"
                            and not self.store.unresolved()
                            and not self.store.meta("baseline")
                            else ["manual"]
                        ),
                        engineering_only=self.engineering_only,
                        port_aliases=self.settings.get(
                            "port_aliases", [self.settings["serial_port"]]
                        ),
                    )
                ]
            ),
            blocked_reason=self.blocked_reason,
        )

    def result(self, request_id):
        """Observe an existing result; missing state is not evidence of no write.

        @param request_id Opaque operation handle.
        """
        row = self.store.lookup(request_id) if handle(request_id) else None
        if row is None:
            raise BootstrapError("unknown_operation", 404)
        return row[1]

    def _artifact(self, candidate_id):
        """Revalidate the exact selected bytes at admission rather than trusting display cache.

        @param candidate_id Hash-bound candidate handle from the cached catalog.
        """
        try:
            candidate = bootstrap_candidate(self.settings, candidate_id)
        except (BundleError, OSError, ValueError) as error:
            raise BootstrapError(
                getattr(error, "code", "artifact_unavailable")
            ) from None
        if candidate["board"] != self.profile.board:
            raise BootstrapError("unsupported_profile")
        return candidate

    async def submit(self, kind, payload):
        """Commit explicit intent once, then schedule bounded work outside the request.

        @param kind Enter, initialize, prepare, install or handoff.
        @param payload Exact operation body with instance/request identity and consent.
        """
        fields = {
            "enter": {"confirmed"},
            "initialize": {"confirmed"},
            "cancel": {"confirmed"},
            "prepare": {"target_id", "candidate_id", "dedicated", "board_confirmed"},
            "install": {"plan_id", "confirmed"},
            "handoff": {"install_request_id", "confirmed", "network"},
        }
        if (
            kind == "prepare"
            and isinstance(payload, dict)
            and ("entry_method" in payload or "entry_confirmed" in payload)
        ):
            fields["prepare"] |= {"entry_method", "entry_confirmed"}
        if (
            kind not in fields
            or not isinstance(payload, dict)
            or set(payload) != fields[kind] | {"request_id", "instance_id"}
            or not handle(payload["request_id"])
            or not handle(payload["instance_id"])
        ):
            raise BootstrapError("invalid_request", 400)
        if payload["instance_id"] != self.store.instance_id:
            raise BootstrapError("instance_changed")
        if "entry_method" in payload and (
            payload["entry_method"] != ENTRY_METHOD
            or payload["entry_confirmed"] is not True
            or self.profile.board != "sonoff-zbdongle-e"
        ):
            raise BootstrapError("entry_confirmation_required", 400)
        for flag in (
            ("dedicated", "board_confirmed") if kind == "prepare" else ("confirmed",)
        ):
            if payload[flag] is not True:
                raise BootstrapError("confirmation_required", 400)
        request = dict(kind=kind, payload=deepcopy(payload))
        existing = self.store.lookup(payload["request_id"])
        if existing:
            if existing[0] != request:
                raise BootstrapError("request_conflict")
            return existing[1]
        job = dict(
            request_id=payload["request_id"],
            instance_id=self.store.instance_id,
            kind=kind,
            engineering_only=self.engineering_only,
            status="pending",
            stage="accepted",
            error=None,
            recovery=None,
            profile=self.profile.board,
            target_id=self.target_id,
            candidate_id=None,
            artifact_sha256=None,
            version=None,
            plan_id=None,
            plan=None,
            install_request_id=payload.get("install_request_id"),
            application_verified=False,
            network_ready=False,
            setup_required=True,
            transfer_percent=None,
            expected_network_id=self.info()["expected_network_id"],
            setup_session=self.session,
            entry_method=payload.get("entry_method", "manual"),
            entry_attempted=False,
        )
        try:
            candidate, binding, accepted_network = self._admission(kind, payload, job)
        except BootstrapError as error:
            # Matching, valid intent has a permanent result even when it cannot run.
            job.update(status="rejected", stage="rejected", error=error.code)
            self.store.admit(request, job)
            return deepcopy(job)
        self.store.admit(request, job, job["plan_id"] if kind == "install" else None)
        if kind == "enter":
            self.runtime.fence()
        elif kind == "prepare":
            self.inspection = None
        task = asyncio.create_task(
            self._execute(job, candidate, binding, accepted_network)
        )
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)
        return deepcopy(job)

    def _admission(self, kind, payload, job):
        """Validate current state before recording a durable admission or rejection.

        @param kind Allowlisted operation name.
        @param payload Structurally validated intent.
        @param job Public pending result populated with immutable bindings.
        """
        self.legacy_reason = (
            self.runtime.admission_reason()
            if hasattr(self.runtime, "admission_reason")
            else None
        )
        if self.legacy_reason:
            raise BootstrapError(self.legacy_reason)
        reinspection = (
            kind == "prepare"
            and job["entry_method"] == "manual"
            and self._entry_reinspection()
        )
        if self.closed or (self.store.unresolved() and not reinspection):
            raise BootstrapError("operation_unresolved")
        if self.store.meta("reconciliation_requires_prepare") and kind != "prepare":
            raise BootstrapError("fresh_preparation_required")
        if (kind == "enter") != (self.mode == "gateway"):
            raise BootstrapError("wrong_mode")
        candidate = binding = accepted_network = None
        if kind == "enter":
            if self.blocked_reason:
                raise BootstrapError(self.blocked_reason)
            check = getattr(self.worker, "precheck", None)
            reason = check() if check else None
            if isinstance(reason, str):
                raise BootstrapError(reason)
        elif kind == "prepare":
            if job["entry_method"] == ENTRY_METHOD and self.store.meta("baseline"):
                raise BootstrapError("entry_baseline_conflict")
            if reinspection:
                job["entry_reinspection"] = [
                    j["request_id"] for j in self.store.unresolved()
                ]
            if payload["target_id"] != self.target_id or not handle(
                payload["candidate_id"], 256
            ):
                raise BootstrapError("selection_changed")
            candidate = self._artifact(payload["candidate_id"])
        elif kind == "install":
            row = (
                self.store.plan(payload["plan_id"])
                if handle(payload["plan_id"])
                else None
            )
            if row is None:
                raise BootstrapError("plan_unknown")
            binding, consumed = row
            for key in ("plan_id", "candidate_id", "artifact_sha256", "version"):
                job[key] = binding[key]
            if consumed:
                raise BootstrapError("plan_used")
            if (
                binding["epoch"] != self.store.epoch
                or binding["expires_at"] <= self.clock()
                or self._deadlines.get(binding["plan_id"], 0) <= time.monotonic()
            ):
                raise BootstrapError("plan_expired")
            if (
                binding["settings_digest"] != digest(self.settings)
                or binding["setup_session"] != self.session
                or binding["profile"] != self.profile.board
                or binding["target_id"] != self.target_id
                or binding["instance_id"] != self.store.instance_id
            ):
                raise BootstrapError("plan_changed")
            candidate = self._artifact(binding["candidate_id"])
            if candidate["sha256"] != binding["artifact_sha256"]:
                raise BootstrapError("artifact_changed")
            job["plan_id"] = binding["plan_id"]
        elif kind == "initialize":
            if (
                self.store.meta("network") is not None
                or self.runtime.has_gateway_state()
            ):
                raise BootstrapError("network_exists")
        elif kind == "cancel":
            if self.store.meta("installation_attempted", False):
                raise BootstrapError("recovery_required")
            if (
                self.runtime.has_gateway_state()
                or self.store.meta("baseline") is not None
            ):
                baseline = self.store.meta("baseline")
                if not baseline:
                    raise BootstrapError("baseline_unavailable")
                binding = dict(baseline=baseline, mode="bootloader")
        elif kind == "handoff":
            install = self.result(payload["install_request_id"])
            if (
                install["kind"] != "install"
                or install["status"] != "confirmed"
                or install["stage"] != "application_verified"
                or install.get("setup_session") != self.session
            ):
                raise BootstrapError("application_not_verified")
            for key in ("candidate_id", "artifact_sha256", "version", "plan_id"):
                job[key] = install[key]
            job["application_verified"] = True
            accepted_network = self.store.meta("network")
            if payload["network"] is not None:
                proposed = network(payload["network"])
                if accepted_network is not None and proposed != accepted_network:
                    raise BootstrapError("network_changed")
                if accepted_network is None and self.runtime.has_gateway_state():
                    raise BootstrapError("network_exists")
                accepted_network = proposed
            if accepted_network is None:
                raise BootstrapError("network_required")
        if candidate:
            job.update(
                candidate_id=candidate["id"],
                artifact_sha256=candidate["sha256"],
                version=candidate["version"],
            )
        return candidate, binding, accepted_network

    async def resume_startup(self):
        """Resume only committed normal-start permission; never replay installation."""
        unresolved = self.store.unresolved()
        if any(job.get("recovery") for job in unresolved):
            self.mode = "bootstrap"
            return
        pending = [
            job
            for job in unresolved
            if job["kind"] == "handoff" and job["stage"] == "handoff_authorized"
        ]
        if len(unresolved) == len(pending) == 1:
            await self._execute(pending[0], None, None, self.store.meta("network"))
        elif not unresolved and self.mode == "gateway" and self.store.meta("network"):
            await self.runtime.start_gateway(self.store.meta("network"))

    async def _execute(self, job, candidate, binding, accepted_network):
        """Reconcile one admitted operation, never infer success from worker exit alone.

        @param job Durable pending operation.
        @param candidate Admitted immutable candidate bytes or None.
        @param binding Private inspected plan or None.
        @param accepted_network Explicitly selected handoff identity or None.
        """
        kind = job["kind"]
        try:
            if kind == "enter":
                job["stage"] = "quiescing"
                self.store.save(job, {"mode": "bootstrap"})
                baseline = await self.runtime.enter()
                self.session = uuid.uuid4().hex
                self.inspection = None
                self.store.save(
                    job,
                    {
                        "baseline": baseline,
                        "setup_session": self.session,
                        "installation_attempted": False,
                    },
                )
                job["setup_session"] = self.session
                self.mode = "bootstrap"
                job.update(status="confirmed", stage="setup_entered")
            elif kind == "initialize":
                proposed = {
                    "gateway": secrets.token_hex(4),
                    "tag": f"{secrets.randbelow(254)+1:02x}",
                }
                while proposed["gateway"] in ("00000000", "ffffffff"):
                    proposed["gateway"] = secrets.token_hex(4)
                proposed = network(proposed)
                job.update(
                    status="confirmed",
                    stage="network_initialized",
                    network=proposed,
                    expected_network_id=proposed["gateway"] + ":" + proposed["tag"],
                )
                self.store.save(job, {"network": proposed})
                return
            elif kind in ("prepare", "install", "cancel"):
                if kind == "cancel" and binding is None:
                    job.update(
                        status="confirmed",
                        stage="setup_cancelled",
                        cancelled_unconfigured=True,
                    )
                    self.session = uuid.uuid4().hex
                    self.inspection = None
                    self._deadlines.clear()
                    self.store.save(job, {"setup_session": self.session})
                    return

                async def checkpoint(stage):
                    """Persist a worker's phase before granting flash permission.

                    @param stage Bounded worker stage name.
                    """
                    job["stage"] = stage
                    metadata = (
                        {"installation_attempted": True} if stage == "flash" else None
                    )
                    if stage == "bootloader_entry":
                        job["entry_attempted"] = True
                        self.session = uuid.uuid4().hex
                        self.inspection = None
                        self._deadlines.clear()
                        job["setup_session"] = self.session
                        metadata = {"setup_session": self.session}
                    self.store.save(
                        job,
                        metadata,
                    )

                result = await self.worker.run(
                    kind, job, candidate, binding, checkpoint
                )
                if kind == "prepare":
                    if not isinstance(result, dict) or set(result) != {
                        "target",
                        "loader",
                    }:
                        raise BootstrapError("invalid_worker_result")
                    public = dict(
                        plan_id=uuid.uuid4().hex,
                        instance_id=self.store.instance_id,
                        target_id=self.target_id,
                        candidate_id=candidate["id"],
                        profile=self.profile.board,
                        version=candidate["version"],
                        artifact_sha256=candidate["sha256"],
                        expires_at=self.clock() + PLAN_LIFETIME,
                        requires_confirmation=True,
                        engineering_only=self.engineering_only,
                    )
                    private = dict(
                        public,
                        epoch=self.store.epoch,
                        setup_session=self.session,
                        settings_digest=digest(self.settings),
                        inspection=result,
                    )
                    self.inspection = deepcopy(result)
                    self._deadlines[public["plan_id"]] = (
                        time.monotonic() + PLAN_LIFETIME
                    )
                    job.update(
                        status="confirmed",
                        stage="prepared",
                        plan_id=public["plan_id"],
                        plan=public,
                    )
                    self.store.prepared(job, private, job.get("entry_reinspection", ()))
                    return
                if kind == "cancel":
                    from .preserved_gecko import check_application

                    check_application(
                        binding["baseline"]["version"], result, board=self.profile.board
                    )
                    try:
                        await self.runtime.handoff(self.store.meta("network"))
                    except BootstrapError as error:
                        if error.code != "normal_service_not_ready":
                            raise
                        job.update(
                            status="rejected",
                            stage="application_restored",
                            error="software_rollback_required",
                        )
                        self.store.save(job)
                        return
                    self.mode = "gateway"
                    job.update(
                        status="confirmed",
                        stage="network_ready",
                        network_ready=True,
                        setup_required=False,
                    )
                    self.store.save(
                        job,
                        {
                            "mode": "gateway",
                            "verified_target": None,
                            "normal_target": binding["baseline"]["target"],
                        },
                    )
                    self.runtime.normal_confirmed()
                    return
                if (
                    not isinstance(result, dict)
                    or result.get("application_verified") is not True
                    or result.get("board") != self.profile.board
                    or result.get("version") != job["version"]
                    or result.get("artifact_sha256") != job["artifact_sha256"]
                    or result.get("network_ready") is not False
                ):
                    raise BootstrapError("application_mismatch")
                job.update(
                    status="confirmed",
                    stage="application_verified",
                    application_verified=True,
                )
                self.store.save(
                    job, {"verified_target": binding["inspection"]["target"]}
                )
                return
            elif kind == "handoff":
                job.update(
                    stage="handoff_authorized",
                    expected_network_id=accepted_network["gateway"]
                    + ":"
                    + accepted_network["tag"],
                )
                self.store.save(
                    job,
                    {
                        "network": accepted_network,
                        "mode": "gateway",
                        "handoff_request_id": job["request_id"],
                    },
                )
                await self.runtime.handoff(accepted_network)
                self.mode = "gateway"
                job.update(
                    status="confirmed",
                    stage="network_ready",
                    network_ready=True,
                    setup_required=False,
                )
            self.store.save(
                job,
                (
                    {
                        "normal_target": self.store.meta("verified_target"),
                        "verified_target": None,
                    }
                    if kind == "handoff"
                    else None
                ),
            )
            if kind == "handoff":
                self.runtime.normal_confirmed()
        except BaseException as error:
            if not hasattr(error, "code") and not isinstance(
                error, asyncio.CancelledError
            ):
                # Never log exception text/args, source lines, absolute filenames or
                # locals: they can contain API credentials and private job contents.
                LOG.error(
                    "Unexpected setup failure kind=%s stage=%s request_id=%s type=%s frames=%s",
                    kind,
                    job["stage"],
                    job["request_id"],
                    type(error).__name__[:80],
                    failure_location(error),
                )
            uncertain = kind in ("install", "handoff", "enter") or (
                kind == "cancel" and job["stage"] != "accepted"
            )
            entry_uncertain = kind == "prepare" and job.get("entry_attempted")
            job.update(
                status="unknown" if uncertain or entry_uncertain else "rejected",
                error=getattr(error, "code", "operation_failed"),
                recovery=(
                    "recovery_required"
                    if uncertain
                    else ENTRY_RECOVERY if entry_uncertain else None
                ),
            )
            self.mode = "bootstrap" if uncertain else self.mode
            self.store.save(job, {"mode": self.mode})
            if isinstance(error, asyncio.CancelledError):
                raise

    async def close(self):
        """Stop new admission and supervise outstanding worker shutdown without replay."""
        self.closed = True
        for task in tuple(self.tasks):
            task.cancel()
        await asyncio.gather(*tuple(self.tasks), return_exceptions=True)
