"""HA-independent client for the authenticated Livolo EC native API v1."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
import re
import time
from typing import Any
from urllib.parse import urlsplit
import uuid

import aiohttp

API_VERSION = 1


class LivoloError(Exception):
    """An API request failed without proving a radio operation succeeded."""


class AuthenticationError(LivoloError):
    """The API credential was rejected."""


class IncompatibleError(LivoloError):
    """The endpoint or its response cannot be used by this client."""


class CommandFailed(LivoloError):
    """A durable command did not finish with confirmed state."""

    def __init__(self, result: dict[str, Any]):
        """Retain the outcome so callers can display uncertainty accurately.

        @param result Final or locally uncertain command outcome.
        """
        self.result = result
        super().__init__(f"Command {result.get('request_id')}: {result.get('status')}")


def normalize_url(url: str) -> str:
    """Validate a credential-free API base URL, preserving optional path prefixes.

    @param url HTTP(S) base URL supplied by the operator.
    @return Normalized base without a trailing slash.
    """
    if not isinstance(url, str) or any(c.isspace() for c in url):
        raise ValueError("API URL must not contain whitespace")
    parsed = urlsplit(url)
    if (
        parsed.scheme not in ("http", "https")
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("Use an HTTP(S) URL without credentials, query or fragment")
    _ = parsed.port
    return url.rstrip("/")


def network_id(info: dict[str, Any]) -> str:
    """Validate and return the stable logical EC network identity.

    @param info An info response or snapshot data mapping.
    @return Wire-order gateway and tag separated by a colon.
    """
    gateway, tag = info.get("gateway"), info.get("tag")
    if not isinstance(gateway, str) or not re.fullmatch(r"[0-9a-f]{8}", gateway):
        raise IncompatibleError("Invalid gateway identity")
    if not isinstance(tag, str) or not re.fullmatch(r"[0-9a-f]{2}", tag):
        raise IncompatibleError("Invalid network tag")
    return f"{gateway}:{tag}"


def snapshot_data(envelope: dict[str, Any]) -> dict[str, Any]:
    """Validate a full snapshot envelope before publishing it to consumers.

    @param envelope Decoded API WebSocket or HTTP snapshot response.
    @return The validated data mapping.
    """
    if (
        envelope.get("type") != "snapshot"
        or envelope.get("api_version") != API_VERSION
        or not isinstance(envelope.get("epoch"), str)
        or type(envelope.get("sequence")) is not int
        or envelope["sequence"] < 0
        or not isinstance(envelope.get("data"), dict)
    ):
        raise IncompatibleError("Invalid snapshot envelope")
    data = envelope["data"]
    network_id(data)
    if any(
        type(data.get(key)) is not bool
        for key in ("ready", "controls_enabled", "simulated")
    ):
        raise IncompatibleError("Invalid snapshot readiness fields")
    if not isinstance(data.get("devices"), list):
        raise IncompatibleError("Missing device registry")
    identities = set()
    for device in data["devices"]:
        if (
            not isinstance(device, dict)
            or not isinstance(device.get("id"), str)
            or not re.fullmatch(r"[0-9a-f]{8}", device["id"])
            or type(device.get("outputs")) is not int
            or device["outputs"] not in (1, 2)
            or device["id"] in identities
        ):
            raise IncompatibleError("Unsupported or duplicate device profile")
        identities.add(device["id"])
    return data


class Client:
    """Use a caller-owned aiohttp session without creating background tasks."""

    def __init__(self, session: aiohttp.ClientSession, url: str, token: str):
        """Configure API access; ownership of the session remains with the caller.

        @param session Existing asynchronous HTTP client session.
        @param url HTTP(S) API base URL.
        @param token Bearer credential; never placed in a URL.
        """
        self.session = session
        self.url = normalize_url(url)
        if not token or "\n" in token or "\r" in token:
            raise ValueError("An API token is required")
        self._headers = {"Authorization": "Bearer " + token}

    async def _request(self, method: str, path: str, body=None):
        """Perform one request without redirects or implicit command retries.

        @param method HTTP method.
        @param path API-relative path.
        @param body Optional JSON command envelope.
        @return Decoded JSON mapping, or None for a missing result.
        """
        try:
            async with self.session.request(
                method,
                self.url + path,
                json=body,
                headers=self._headers,
                timeout=aiohttp.ClientTimeout(total=10),
                allow_redirects=False,
            ) as response:
                if response.status in (401, 403):
                    raise AuthenticationError("API authentication failed")
                if response.status == 404 and path.startswith("/api/v1/commands/"):
                    return None
                if response.status == 404 and path == "/api/v1/firmware/install":
                    raise IncompatibleError(
                        "Daemon does not support firmware installation"
                    )
                if response.status != 200:
                    raise LivoloError(f"API returned HTTP {response.status}")
                value = await response.json()
                if not isinstance(value, dict):
                    raise IncompatibleError("API did not return an object")
                return value
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as error:
            raise LivoloError("API connection or response failed") from error

    async def info(self) -> dict[str, Any]:
        """Check API compatibility and logical network identity."""
        info = await self._request("GET", "/api/v1/info")
        if info.get("api_version") != API_VERSION:
            raise IncompatibleError("Native API v1 is required")
        network_id(info)
        return info

    async def snapshot(self) -> dict[str, Any]:
        """Read one validated authoritative full snapshot."""
        envelope = await self._request("GET", "/api/v1/snapshot")
        snapshot_data(envelope)
        return envelope

    async def events(self) -> AsyncIterator[dict[str, Any]]:
        """Yield validated snapshots until disconnect; the owner chooses reconnect policy."""
        try:
            async with self.session.ws_connect(
                self.url + "/api/v1/events",
                headers=self._headers,
                heartbeat=20,
                max_msg_size=2**20,
            ) as socket:
                async for message in socket:
                    if message.type == aiohttp.WSMsgType.TEXT:
                        envelope = message.json()
                        if not isinstance(envelope, dict):
                            raise IncompatibleError("Invalid event")
                        snapshot_data(envelope)
                        yield envelope
                    elif message.type == aiohttp.WSMsgType.ERROR:
                        raise LivoloError("Event connection failed")
        except aiohttp.WSServerHandshakeError as error:
            if error.status in (401, 403):
                raise AuthenticationError("API authentication failed") from error
            raise LivoloError("Event handshake failed") from error
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as error:
            raise LivoloError("Event connection failed") from error

    async def result(self, request_id: str):
        """Look up a durable outcome without replaying the command.

        @param request_id Previously submitted command ID.
        @return Stored result or None if not found.
        """
        if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", request_id):
            raise ValueError("Invalid request ID")
        return await self._request("GET", "/api/v1/commands/" + request_id)

    async def command(self, action: str, payload: dict[str, Any], timeout=25):
        """Submit exactly once, then resolve the same ID until a bounded deadline.

        @param action Service action such as set, refresh, add or pair.
        @param payload Validated service arguments; optional caller request_id.
        @param timeout Maximum completion wait in seconds including submission.
        @return Confirmed result, or accepted pairing-window result.
        """
        request = dict(payload)
        request.setdefault("request_id", str(uuid.uuid4()))
        request_id = request["request_id"]
        return await self._submit_and_wait(
            "/api/v1/commands",
            {"action": action, "payload": request},
            request_id,
            timeout,
            ("confirmed", "pairing") if action == "pair" else ("confirmed",),
        )

    async def install_firmware(self, candidate_id, request_id=None, timeout=600):
        """Request one daemon-owned firmware job and await its verified outcome.

        @param candidate_id Opaque validated candidate identifier advertised by the daemon.
        @param request_id Optional caller-owned durable ID; generated once if omitted.
        @param timeout Maximum seconds to observe the job, including submission.
        @return Confirmed job result; failures/uncertainty raise CommandFailed.
        """
        if not isinstance(candidate_id, str) or not re.fullmatch(
            r"[A-Za-z0-9_.:-]{1,256}", candidate_id
        ):
            raise ValueError("Invalid firmware candidate ID")
        request_id = str(uuid.uuid4()) if request_id is None else request_id
        return await self._submit_and_wait(
            "/api/v1/firmware/install",
            {"request_id": request_id, "candidate_id": candidate_id},
            request_id,
            timeout,
            ("confirmed",),
        )

    async def _submit_and_wait(self, path, body, request_id, timeout, accepted):
        """Submit once and observe the same durable ID without replaying work.

        Cancellation or a local deadline only stops observation, never the daemon job.

        @param path Native API submission endpoint.
        @param body Exact JSON submission envelope.
        @param request_id Validated durable identifier to recover after a lost reply.
        @param timeout Total observation deadline in seconds.
        @param accepted Tuple of successful terminal statuses for this operation.
        @return Durable accepted outcome, or raise CommandFailed on failure/uncertainty.
        """
        if not isinstance(request_id, str) or not re.fullmatch(
            r"[A-Za-z0-9_.:-]{1,128}", request_id
        ):
            raise ValueError("Invalid request ID")
        if timeout <= 0:
            raise ValueError("Command timeout must be positive")
        deadline = time.monotonic() + timeout
        result = None
        try:
            result = await asyncio.wait_for(
                self._request("POST", path, body),
                timeout,
            )
        except (AuthenticationError, IncompatibleError):
            raise
        except (LivoloError, asyncio.TimeoutError):
            # The server may have committed the submission before the reply was lost.
            pass
        while True:
            if result is not None:
                if result.get("request_id") != request_id:
                    raise IncompatibleError("Command result ID mismatch")
                status = result.get("status")
                if status in accepted:
                    return result
                if status != "pending":
                    raise CommandFailed(result)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise CommandFailed({"request_id": request_id, "status": "unknown"})
            await asyncio.sleep(min(0.25, remaining))
            try:
                result = await asyncio.wait_for(
                    self.result(request_id), max(0.01, deadline - time.monotonic())
                )
            except AuthenticationError:
                raise
            except (LivoloError, asyncio.TimeoutError):
                result = None
