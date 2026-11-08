"""Validated physical-bootstrap client, separate from normal firmware update admission."""

import asyncio
import json
import math
import re

import aiohttp

from . import AuthenticationError, IncompatibleError, LivoloError

OPERATIONS = {"enter", "initialize", "prepare", "install", "handoff", "cancel"}
STATUSES = {"pending", "confirmed", "rejected", "unknown"}
PREFIX = "/api/v1/provisioning/"
MAX_RESPONSE_BYTES = 128 * 1024
SNIFFER_ENTRY_METHOD = "sonoff_sniffer_0_12"


class BootstrapError(LivoloError):
    """A bounded server rejection, distinct from a missing or lost response."""

    def __init__(self, code):
        """Retain a stable public rejection code without raw helper output.

        @param code Validated server error identifier.
        """
        self.code = code
        super().__init__(code)


def ident(value, optional=False):
    """Accept opaque identifiers without paths, URLs or Markdown syntax.

    @param value Candidate identifier, version, code or service identity.
    @param optional Whether null is allowed.
    """
    if value is None and optional:
        return None
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,256}", value):
        raise IncompatibleError("Invalid bootstrap identifier")
    return value


def network(value):
    """Validate a real network identity or explicit lack of configured network.

    @param value Lowercase gateway/tag identity or None.
    """
    if value is not None and (
        not isinstance(value, str)
        or not re.fullmatch(r"[0-9a-f]{8}:[0-9a-f]{2}", value)
    ):
        raise IncompatibleError("Invalid bootstrap network identity")
    if value is not None and (
        value[:8] in {"00000000", "ffffffff"} or value[-2:] in {"00", "ff"}
    ):
        raise IncompatibleError("Reserved bootstrap network identity")
    return value


def digest(value):
    """Require a complete immutable artifact digest.

    @param value SHA-256 hexadecimal digest advertised by the service.
    """
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
        raise IncompatibleError("Invalid bootstrap artifact digest")
    return value


def engineering_only(value):
    """Preserve explicit engineering qualification without accepting truthy substitutes.

    @param value Public metadata mapping; older services may omit the annotation.
    """
    field = value.get("engineering_only", False)
    if type(field) is not bool:
        raise IncompatibleError("Invalid bootstrap engineering qualification")
    return field


def reconciliation_data(value, setup_session):
    """Validate narrowly scoped authority for new preparation without rewriting an old result.

    @param value Optional public reconciliation record from authenticated service metadata.
    @param setup_session Current server-owned setup session, or None for legacy services.
    """
    if value is None:
        return None
    fields = {
        "reconciliation_id",
        "request_id",
        "kind",
        "plan_id",
        "target_id",
        "resolution",
        "permits",
        "setup_session",
    }
    if not isinstance(value, dict) or set(value) != fields:
        raise IncompatibleError("Invalid bootstrap reconciliation")
    result = {key: ident(value[key]) for key in fields}
    if (
        result["kind"] != "install"
        or result["resolution"] != "verified_no_flash"
        or result["permits"] != "prepare_only"
        or result["setup_session"] != setup_session
    ):
        raise IncompatibleError("Unsupported or stale bootstrap reconciliation")
    return result


def plan_data(value, instance_id):
    """Allowlist an expiring reviewed plan bound to the current physical service.

    @param value Decoded plan mapping.
    @param instance_id Expected persistent service identity.
    """
    if not isinstance(value, dict) or value.get("instance_id") != instance_id:
        raise IncompatibleError("Bootstrap plan instance changed")
    result = {
        key: ident(value.get(key))
        for key in (
            "plan_id",
            "instance_id",
            "target_id",
            "candidate_id",
            "profile",
            "version",
        )
    }
    result["artifact_sha256"] = digest(value.get("artifact_sha256"))
    expiry = value.get("expires_at")
    if type(expiry) not in (int, float) or not math.isfinite(expiry) or expiry <= 0:
        raise IncompatibleError("Invalid bootstrap plan expiry")
    if value.get("requires_confirmation") is not True:
        raise IncompatibleError("Bootstrap plan lacks explicit confirmation")
    return dict(
        result,
        expires_at=expiry,
        requires_confirmation=True,
        engineering_only=engineering_only(value),
    )


def operation_data(value, instance_id, request_id=None):
    """Validate durable operation state without interpreting transfer as completion.

    @param value Operation mapping returned by POST or result lookup.
    @param instance_id Expected persistent service identity.
    @param request_id Expected request ID when observing a particular intent.
    """
    if not isinstance(value, dict):
        raise IncompatibleError("Invalid bootstrap operation")
    result = {
        key: ident(value.get(key)) for key in ("request_id", "kind", "status", "stage")
    }
    if (
        result["kind"] not in OPERATIONS
        or result["status"] not in STATUSES
        or request_id is not None
        and result["request_id"] != request_id
        or value.get("instance_id", instance_id) != instance_id
    ):
        raise IncompatibleError("Bootstrap operation identity changed")
    result["instance_id"] = instance_id
    result["engineering_only"] = engineering_only(value)
    for key in (
        "error",
        "recovery",
        "target_id",
        "candidate_id",
        "plan_id",
        "profile",
        "version",
        "install_request_id",
    ):
        result[key] = ident(value.get(key), optional=True)
    result["artifact_sha256"] = (
        digest(value["artifact_sha256"]) if value.get("artifact_sha256") else None
    )
    result["plan"] = (
        plan_data(value["plan"], instance_id) if value.get("plan") is not None else None
    )
    result["expected_network_id"] = network(value.get("expected_network_id"))
    chosen = value.get("network")
    if chosen is not None:
        if (
            not isinstance(chosen, dict)
            or set(chosen) != {"gateway", "tag"}
            or any(not isinstance(chosen[key], str) for key in ("gateway", "tag"))
        ):
            raise IncompatibleError("Invalid initialized network")
        network(f"{chosen['gateway']}:{chosen['tag']}")
        result["network"] = {key: chosen[key] for key in ("gateway", "tag")}
    else:
        result["network"] = None
    for key in (
        "application_verified",
        "network_ready",
        "setup_required",
        "cancelled_unconfigured",
        "entry_attempted",
    ):
        field = value.get(key, False)
        if type(field) is not bool:
            raise IncompatibleError("Invalid bootstrap readiness")
        result[key] = field
    percent = value.get("transfer_percent")
    if percent is not None and (
        type(percent) not in (int, float)
        or not math.isfinite(percent)
        or not 0 <= percent <= 100
    ):
        raise IncompatibleError("Invalid bootstrap transfer progress")
    result["transfer_percent"] = percent
    if (
        result["kind"] == "prepare"
        and result["status"] == "confirmed"
        and result["plan"] is None
    ):
        raise IncompatibleError("Prepared operation has no review plan")
    if (
        result["kind"] == "install"
        and result["status"] == "confirmed"
        and not result["application_verified"]
    ):
        raise IncompatibleError("Installation has no application verification")
    return result


class BootstrapClient:
    """Use the native client's connection, never retrying a physical POST implicitly."""

    def __init__(self, client):
        """Reuse a caller-owned authenticated HTTP transport.

        @param client Existing native Client; contains session, endpoint and authorization.
        """
        self.client = client

    async def _request(
        self, method, suffix, body=None, instance_id=None, optional=False
    ):
        """Read a bounded physical-service envelope and reject fake capabilities.

        @param method HTTP method, GET or explicit POST.
        @param suffix Fixed API route and validated opaque request handle.
        @param body Optional exact physical operation request.
        @param instance_id Expected service identity after first connection.
        @param optional Whether a missing info endpoint means unsupported.
        """
        try:
            async with self.client.session.request(
                method,
                self.client.url + PREFIX + suffix,
                json=body,
                headers=self.client._headers,
                timeout=aiohttp.ClientTimeout(total=10),
                allow_redirects=False,
            ) as response:
                if response.status in (401, 403):
                    raise AuthenticationError("Bootstrap authentication failed")
                if response.status == 404 and optional:
                    return None
                if (
                    response.content_length is not None
                    and response.content_length > MAX_RESPONSE_BYTES
                ):
                    raise IncompatibleError("Bootstrap response too large")
                body_bytes = bytearray()
                async for chunk in response.content.iter_chunked(16384):
                    body_bytes.extend(chunk)
                    if len(body_bytes) > MAX_RESPONSE_BYTES:
                        raise IncompatibleError("Bootstrap response too large")
                value = json.loads(body_bytes)
                if (
                    not isinstance(value, dict)
                    or type(value.get("schema")) is not int
                    or value["schema"] != 1
                    or value.get("simulated") is not False
                    or value.get("operation") != "provision"
                ):
                    raise IncompatibleError("Physical bootstrap API required")
                identity = ident(value.get("instance_id"))
                if instance_id is not None and identity != instance_id:
                    raise IncompatibleError("Bootstrap service instance changed")
                if value.get("ok") is False:
                    raise BootstrapError(ident(value.get("error")))
                if (
                    response.status != 200
                    or value.get("ok") is not True
                    or not isinstance(value.get("data"), dict)
                ):
                    raise IncompatibleError("Invalid bootstrap response")
                return identity, value["data"]
        except (
            aiohttp.ClientError,
            asyncio.TimeoutError,
            ValueError,
            RecursionError,
        ) as error:
            raise LivoloError("Bootstrap connection or response failed") from error

    async def info(self, instance_id=None):
        """Read capability and resumable request references without radio access.

        @param instance_id Expected service identity, if already bound.
        """
        response = await self._request(
            "GET", "info", instance_id=instance_id, optional=True
        )
        if response is None:
            return None
        identity, value = response
        if value.get("mode") not in ("gateway", "bootstrap"):
            raise IncompatibleError("Invalid bootstrap mode")
        result = dict(instance_id=identity, mode=value["mode"])
        result["engineering_only"] = engineering_only(value)
        for key in (
            "physical_install_supported",
            "prepare_supported",
            "enter_supported",
            "initialize_supported",
            "cancel_supported",
            "baseline_available",
            "manual_reinspection_supported",
        ):
            field = value.get(key, False)
            if type(field) is not bool:
                raise IncompatibleError("Invalid bootstrap capability")
            result[key] = field
        for key in (
            "profile",
            "active_request_id",
            "latest_request_id",
            "blocked_reason",
            "setup_session",
        ):
            result[key] = ident(value.get(key), optional=True)
        result["expected_network_id"] = network(value.get("expected_network_id"))
        result["reconciliation"] = reconciliation_data(
            value.get("reconciliation"), result["setup_session"]
        )
        return result

    async def targets(self, instance_id):
        """Read static exact-board choices; never infer a PCB from USB identification.

        @param instance_id Expected physical service identity.
        """
        _, value = await self._request("GET", "targets", instance_id=instance_id)
        targets = value.get("targets")
        if not isinstance(targets, list) or len(targets) > 16:
            raise IncompatibleError("Invalid bootstrap target list")
        result = []
        for item in targets:
            if not isinstance(item, dict):
                raise IncompatibleError("Invalid bootstrap target")
            safe = {
                key: ident(item.get(key))
                for key in ("target_id", "board", "candidate_id", "version")
            }
            safe["artifact_sha256"] = digest(item.get("artifact_sha256"))
            safe["engineering_only"] = engineering_only(item)
            label = item.get("label")
            if (
                not isinstance(label, str)
                or not 1 <= len(label) <= 128
                or any(ord(c) < 32 for c in label)
            ):
                raise IncompatibleError("Invalid bootstrap target label")
            safe["label"] = label
            aliases = item.get("port_aliases", [])
            if (
                not isinstance(aliases, list)
                or len(aliases) > 16
                or any(
                    not isinstance(path, str)
                    or not path.startswith("/dev/")
                    or len(path) > 512
                    or ".." in path.split("/")
                    or any(ord(char) < 32 for char in path)
                    for path in aliases
                )
            ):
                raise IncompatibleError("Invalid radio port identity hints")
            safe["port_aliases"] = aliases
            if item.get("requires_bootloader") is not True:
                raise IncompatibleError("Unsupported bootstrap target entry method")
            safe["requires_bootloader"] = True
            methods = item.get("entry_methods", ["manual"])
            if (
                not isinstance(methods, list)
                or not 1 <= len(methods) <= 2
                or any(
                    method not in ("manual", SNIFFER_ENTRY_METHOD) for method in methods
                )
                or len(set(methods)) != len(methods)
                or "manual" not in methods
                or SNIFFER_ENTRY_METHOD in methods
                and safe["board"] != "sonoff-zbdongle-e"
            ):
                raise IncompatibleError("Invalid bootstrap entry methods")
            safe["entry_methods"] = list(methods)
            result.append(safe)
        if len({item["target_id"] for item in result}) != len(result):
            raise IncompatibleError("Ambiguous bootstrap targets")
        return dict(
            targets=result,
            blocked_reason=ident(value.get("blocked_reason"), optional=True),
        )

    async def operation(self, request_id, instance_id):
        """Resolve an existing request without replaying its operation.

        @param request_id Durable request identifier.
        @param instance_id Expected physical service identity.
        """
        _, value = await self._request(
            "GET", "operations/" + ident(request_id), instance_id=instance_id
        )
        return operation_data(value, instance_id, request_id)

    async def submit(self, operation, body, instance_id):
        """Send exactly one explicit request; its caller persists intent first.

        @param operation Enter, prepare, install or handoff, never an arbitrary path.
        @param body Exact validated request including request_id and instance_id.
        @param instance_id Expected physical service identity.
        """
        fields = {
            "enter": {"request_id", "instance_id", "confirmed"},
            "initialize": {"request_id", "instance_id", "confirmed"},
            "cancel": {"request_id", "instance_id", "confirmed"},
            "prepare": {
                "request_id",
                "instance_id",
                "target_id",
                "candidate_id",
                "dedicated",
                "board_confirmed",
            },
            "install": {"request_id", "instance_id", "plan_id", "confirmed"},
            "handoff": {
                "request_id",
                "instance_id",
                "install_request_id",
                "confirmed",
                "network",
            },
        }
        if operation == "prepare" and (
            "entry_method" in body or "entry_confirmed" in body
        ):
            fields["prepare"] |= {"entry_method", "entry_confirmed"}
            if (
                body.get("entry_method") != SNIFFER_ENTRY_METHOD
                or body.get("entry_confirmed") is not True
            ):
                raise ValueError(
                    "Explicit supported software entry confirmation required"
                )
        if (
            operation not in fields
            or set(body) != fields[operation]
            or body.get("instance_id") != instance_id
        ):
            raise ValueError("Invalid bootstrap submission")
        for key in fields[operation] - {
            "confirmed",
            "network",
            "dedicated",
            "board_confirmed",
            "entry_confirmed",
        }:
            ident(body[key])
        if operation == "prepare" and (
            body["dedicated"] is not True or body["board_confirmed"] is not True
        ):
            raise ValueError("Dedicated exact-board confirmation required")
        if operation != "prepare" and body["confirmed"] is not True:
            raise ValueError("Explicit confirmation required")
        if operation == "handoff" and body["network"] is not None:
            chosen = body["network"]
            if not isinstance(chosen, dict) or set(chosen) != {"gateway", "tag"}:
                raise ValueError("Invalid bootstrap network configuration")
            if any(not isinstance(chosen[key], str) for key in ("gateway", "tag")):
                raise ValueError("Network identity fields must be strings")
            network(f"{chosen['gateway']}:{chosen['tag']}")
        _, value = await self._request("POST", operation, body, instance_id)
        result = operation_data(value, instance_id, body["request_id"])
        if result["kind"] != operation:
            raise IncompatibleError("Bootstrap operation kind changed")
        return result
