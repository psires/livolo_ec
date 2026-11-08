"""Authenticated physical setup routes attached to the native listener."""

import asyncio
import json

from aiohttp import web

from .bootstrap_controller import BootstrapError

PREFIX = "/api/v1/provisioning"


class BootstrapAPI:
    def __init__(self, controller):
        """Bind a service owner; authentication is supplied by NativeAPI middleware.

        @param controller Persistent physical bootstrap controller.
        """
        self.controller = controller

    def routes(self):
        """Declare fixed operations without accepting arbitrary backend command names."""
        return [
            web.get(PREFIX + "/info", self.info),
            web.get(PREFIX + "/targets", self.targets),
            web.get(PREFIX + "/operations/{request_id}", self.result),
            *[
                web.post(PREFIX + "/" + action, self.submit)
                for action in (
                    "enter",
                    "initialize",
                    "prepare",
                    "install",
                    "handoff",
                    "cancel",
                )
            ],
        ]

    def response(self, data):
        """Wrap a detached result with physical service identity.

        @param data Public operation or metadata mapping.
        """
        return web.json_response(self.controller.envelope(ok=True, data=data))

    async def info(self, request):
        """Read static stored mode and capability only.

        @param request Authenticated request; no query or body is accepted.
        """
        return self.response(self.controller.info())

    async def targets(self, request):
        """Read cached target/artifact metadata without serial access or scanning.

        @param request Authenticated request.
        """
        return self.response(self.controller.targets())

    async def result(self, request):
        """Observe an existing durable operation without replaying it.

        @param request Authenticated request containing one opaque request handle.
        """
        try:
            return self.response(
                self.controller.result(request.match_info["request_id"])
            )
        except BootstrapError as error:
            return web.json_response(
                self.controller.envelope(ok=False, error=error.code),
                status=error.status,
            )

    async def submit(self, request):
        """Durably admit one explicit bounded action before any worker is scheduled.

        @param request Authenticated JSON request to one fixed operation route.
        """
        try:
            if request.content_type != "application/json":
                raise BootstrapError("invalid_request", 400)
            body = await asyncio.wait_for(request.read(), 5)
            if len(body) > 4096:
                raise BootstrapError("request_too_large", 413)
            try:
                payload = json.loads(body)
            except (ValueError, UnicodeError, RecursionError):
                raise BootstrapError("invalid_request", 400) from None
            result = await self.controller.submit(
                request.path.rsplit("/", 1)[1], payload
            )
            return self.response(result)
        except asyncio.TimeoutError:
            return web.json_response(
                self.controller.envelope(ok=False, error="request_timeout"), status=408
            )
        except BootstrapError as error:
            return web.json_response(
                self.controller.envelope(ok=False, error=error.code),
                status=error.status,
            )
