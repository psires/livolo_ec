"""Small, same-origin HTTP UI; all service access stays on its asyncio loop."""

import asyncio
from concurrent.futures import TimeoutError as FutureTimeout
import hmac
import hashlib
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import inspect
import json
import logging
import os
import re
from pathlib import Path
import secrets
import socket
import threading
from urllib.parse import parse_qs, urlsplit

from .ingress import request_context, validate_ingress

LOG = logging.getLogger(__name__)
STATIC = Path(__file__).with_name("static")
ASSETS = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/style.css": ("style.css", "text/css; charset=utf-8"),
}
ACTIONS = {"add", "rename", "remove", "pair", "set", "commission", "commission_cancel"}


class WebServer:
    def __init__(self, service, host="127.0.0.1", port=8080, token=None, ingress=None):
        """Configure a same-origin UI server with optional bearer authentication.

        @param service Daemon service whose API runs on the owning asyncio loop.
        @param host Local address or hostname to bind.
        @param port HTTP port, or zero to select a free port.
        @param token Standalone bearer token, or None for loopback/ingress access.
        @param ingress Explicit trusted-proxy and public-origin configuration.
        """
        self.ingress = validate_ingress(ingress)
        if self.ingress["enabled"] and token is not None:
            raise ValueError(
                "Ingress proxy authentication and standalone bearer authentication are separate modes"
            )
        if (
            host not in ("127.0.0.1", "::1", "localhost")
            and not token
            and not self.ingress["enabled"]
        ):
            raise ValueError("A non-loopback web host requires an access token")
        if token is not None and (not token or not token.isascii()):
            raise ValueError("Access token must be nonempty ASCII")
        self.service, self.host, self.port = service, host, port
        self.token = secrets.token_urlsafe(32)
        self.auth_token = token
        self.httpd = self.thread = self.loop = None

    async def start(self):
        """Start the HTTP listener and route service work to the owner event loop."""
        self.loop = asyncio.get_running_loop()
        owner = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.0"

            def setup(self):
                """Bound the time an HTTP client may hold a request connection."""
                super().setup()
                self.connection.settimeout(5)

            def log_message(self, fmt, *args):
                """Record HTTP access details through the daemon logger.

                @param fmt BaseHTTPRequestHandler printf-style message format.
                @param args Values to interpolate into the log message.
                """
                LOG.debug("HTTP %s %s", self.address_string(), fmt % args)

            def send(
                self, status, body, content_type="application/json; charset=utf-8"
            ):
                """Return a bounded API or asset response with browser security headers.

                @param status HTTP response status code.
                @param body Raw bytes or a JSON-serializable response.
                @param content_type MIME type and optional character encoding.
                """
                if not isinstance(body, bytes):
                    body = json.dumps(body, ensure_ascii=False).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                public_origin = getattr(self, "ingress_origin", None)
                if public_origin is None:
                    self.send_header("X-Frame-Options", "DENY")
                # Trusted ingress pages are embedded by their exact public origin.
                # X-Frame-Options is omitted only here; CSP remains restrictive.
                ancestors = public_origin or "'none'"
                self.send_header("Referrer-Policy", "no-referrer")
                self.send_header(
                    "Content-Security-Policy",
                    f"default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors {ancestors}; base-uri 'none'; form-action 'self'",
                )
                self.send_header("Connection", "close")
                self.end_headers()
                self.wfile.write(body)

            def check_host(self):
                """Validate the ingress trust boundary or the standalone Host header.

                @return True for an accepted request authority; otherwise send an error
                    and return False.
                """
                if owner.ingress["enabled"]:
                    try:
                        public, path = request_context(
                            owner.ingress, self.client_address[0], self.headers
                        )
                    except ValueError:
                        self.send(
                            403, {"error": "Invalid ingress proxy, origin or base path"}
                        )
                        return False
                    self.ingress_origin, self.ingress_path = public, path
                    return True
                try:
                    hosts = self.headers.get_all("Host", [])
                    if len(hosts) != 1 or any(c in hosts[0] for c in "/\\@?#"):
                        raise ValueError()
                    parsed = urlsplit("http://" + hosts[0])
                    allowed = {
                        owner.host.lower(),
                        socket.gethostname().lower(),
                        self.connection.getsockname()[0].lower(),
                    }
                    if owner.host in ("127.0.0.1", "::1", "localhost"):
                        allowed.update(("localhost", "127.0.0.1", "::1"))
                    if (
                        parsed.hostname not in allowed
                        or (parsed.port or 80) != owner.port
                    ):
                        raise ValueError()
                    return True
                except ValueError:
                    self.send(
                        403,
                        {
                            "error": "Unrecognized Host header. Use the configured web host or the Pi's IP address."
                        },
                    )
                    return False

            def authorized(self):
                """Require the configured bearer credential before API access.

                @return True when access is allowed; otherwise send an error and return False.
                """
                if owner.auth_token and not hmac.compare_digest(
                    self.headers.get("Authorization", "").encode(),
                    ("Bearer " + owner.auth_token).encode(),
                ):
                    self.send(401, {"error": "Enter the web access token to connect."})
                    return False
                return True

            def csrf_token(self):
                """Prevent reuse of an ingress CSRF token across public origins or paths.

                @return Context-bound ingress token, or the standalone session token.
                """
                if not owner.ingress["enabled"]:
                    return owner.token
                context = self.ingress_origin + "\n" + self.ingress_path
                return hmac.new(
                    owner.token.encode(), context.encode(), hashlib.sha256
                ).hexdigest()

            def dispatch(self, action=None, payload=None):
                """Obtain a service result on the owner loop without cancelling timed-out work.

                @param action Service action, or None for a state snapshot.
                @param payload Action parameters or command ID for result lookup.
                @return The service response, or None after sending an HTTP error.
                """
                future = asyncio.run_coroutine_threadsafe(
                    owner.dispatch(action, payload), owner.loop
                )
                try:
                    return future.result(timeout=10)
                except FutureTimeout:
                    # Do not cancel: a command may already be durable or queued.
                    self.send(
                        504,
                        {
                            "error": "Daemon response timed out. Check activity before trying again."
                        },
                    )
                except (ValueError, KeyError, TypeError) as error:
                    self.send(400, {"error": str(error)})
                except Exception:
                    LOG.exception("Web request failed")
                    self.send(
                        500, {"error": "The daemon could not complete the request."}
                    )
                return None

            def do_GET(self):
                """Serve known UI assets, state snapshots, and command-result lookups."""
                if not self.check_host():
                    return
                if self.path.startswith("/api/") and not self.authorized():
                    return
                if self.path == "/api/state":
                    state = self.dispatch()
                    if state is not None:
                        state["csrf_token"] = self.csrf_token()
                        self.send(200, state)
                elif urlsplit(self.path).path == "/api/firmware/preflight":
                    query = parse_qs(urlsplit(self.path).query, keep_blank_values=True)
                    if (
                        set(query) - {"candidate_id"}
                        or len(query.get("candidate_id", [])) > 1
                    ):
                        self.send(400, {"error": "Invalid preflight query"})
                        return
                    value = self.dispatch(
                        "firmware_preflight", query.get("candidate_id", [None])[0]
                    )
                    if value is not None:
                        self.send(200, value)
                elif re.fullmatch(r"/api/commands/[A-Za-z0-9_.:-]{1,128}", self.path):
                    result = self.dispatch("result", self.path.rsplit("/", 1)[1])
                    if result is not None:
                        self.send(200, result)
                elif self.path in ASSETS:
                    filename, kind = ASSETS[self.path]
                    body = (STATIC / filename).read_bytes()
                    if filename == "index.html":
                        base = getattr(self, "ingress_path", "") + "/"
                        body = body.replace(
                            b"__LIVOLO_BASE_PATH__", escape(base, quote=True).encode()
                        )
                    self.send(200, body, kind)
                else:
                    self.send(404, {"error": "Not found"})

            def do_POST(self):
                """Accept protected commands, firmware installs and MQTT settings."""
                if not self.check_host():
                    return
                if not self.authorized():
                    return
                origin = self.headers.get("Origin")
                expected_origin = "http://" + self.headers["Host"]
                if owner.ingress["enabled"]:
                    # request_context has already checked the canonical origin.
                    if not origin:
                        self.send(403, {"error": "Ingress writes require Origin"})
                        return
                    expected_origin = origin
                if (origin and origin != expected_origin) or self.headers.get(
                    "Sec-Fetch-Site"
                ) == "cross-site":
                    self.send(403, {"error": "Cross-origin requests are not accepted"})
                    return
                if not hmac.compare_digest(
                    self.headers.get("X-Livolo-Token", "").encode(),
                    self.csrf_token().encode(),
                ):
                    self.send(
                        403,
                        {"error": "Session expired. Refresh the page and try again."},
                    )
                    return
                if self.path not in (
                    "/api/command",
                    "/api/settings/mqtt",
                    "/api/firmware/install",
                    "/api/commission",
                    "/api/commission_cancel",
                ):
                    self.send(404, {"error": "Not found"})
                    return
                try:
                    lengths = self.headers.get_all("Content-Length", [])
                    if len(lengths) != 1 or self.headers.get("Transfer-Encoding"):
                        raise ValueError("A single Content-Length is required")
                    length = int(lengths[0])
                    if not 0 < length <= 4096:
                        self.send(413, {"error": "Request must contain 1..4096 bytes"})
                        return
                    if self.headers.get_content_type() != "application/json":
                        self.send(
                            415, {"error": "Content-Type must be application/json"}
                        )
                        return
                    body = json.loads(self.rfile.read(length))
                    if self.path == "/api/settings/mqtt":
                        body = {"action": "mqtt_settings", "payload": body}
                    elif self.path in ("/api/commission", "/api/commission_cancel"):
                        body = {"action": self.path.rsplit("/", 1)[1], "payload": body}
                    elif self.path == "/api/firmware/install":
                        if (
                            not isinstance(body, dict)
                            or set(body) != {"request_id", "candidate_id"}
                            or any(
                                not isinstance(body[key], str)
                                or not re.fullmatch(
                                    rf"[A-Za-z0-9_.:-]{{1,{limit}}}", body[key]
                                )
                                for key, limit in (
                                    ("request_id", 128),
                                    ("candidate_id", 256),
                                )
                            )
                        ):
                            raise ValueError("Expected request_id and candidate_id")
                        body = {"action": "firmware_install", "payload": body}
                    if not isinstance(body, dict) or set(body) != {"action", "payload"}:
                        raise ValueError("Expected action and payload")
                    if (
                        not isinstance(body["action"], str)
                        or body["action"]
                        not in {
                            "/api/command": ACTIONS,
                            "/api/settings/mqtt": {"mqtt_settings"},
                            "/api/firmware/install": {"firmware_install"},
                            "/api/commission": {"commission"},
                            "/api/commission_cancel": {"commission_cancel"},
                        }[self.path]
                        or not isinstance(body["payload"], dict)
                    ):
                        raise ValueError("Invalid action or payload")
                except (ValueError, UnicodeError) as error:
                    self.send(400, {"error": str(error)})
                    return
                value = self.dispatch(body["action"], body["payload"])
                if value is not None:
                    self.send(200, value)

        class Server(ThreadingHTTPServer):
            address_family = socket.AF_INET6 if ":" in owner.host else socket.AF_INET

        self.httpd = Server((self.host, self.port), Handler)
        self.httpd.daemon_threads = True
        self.port = self.httpd.server_port
        self.thread = threading.Thread(
            target=self.httpd.serve_forever, name="livolod-http", daemon=True
        )
        self.thread.start()
        LOG.info("Web UI listening at http://%s:%d", self.host, self.port)

    async def dispatch(self, action=None, payload=None):
        """Resolve an API request on the service loop and detach its response data.

        @param action Service action, or None for a state snapshot.
        @param payload Action parameters or command ID for result lookup.
        @return A JSON-compatible response with the CSRF token included in snapshots.
        """
        if action is None:
            value = self.service.api_snapshot()
        elif action == "mqtt_settings":
            value = self.service.configure_mqtt(payload)
        elif action == "firmware_install":
            value = self.service.start_firmware_update(payload)
        elif action == "firmware_preflight":
            value = self.service.firmware_preflight(payload)
        elif action == "result":
            value = self.service.command_result(payload)
            if value is None:
                raise ValueError("Unknown request ID")
        else:
            value = self.service.submit(action, payload)
        if inspect.isawaitable(value):
            value = await value
        # Detach response data on the owner loop before the HTTP thread serializes it.
        value = json.loads(json.dumps(value))
        if action is None:
            value["csrf_token"] = self.token
        return value

    async def close(self):
        """Stop accepting HTTP requests and release the listener thread."""
        if self.httpd:
            await asyncio.to_thread(self.httpd.shutdown)
            self.httpd.server_close()
            await asyncio.to_thread(self.thread.join, 2)
            self.httpd = None


async def start_web(service, config):
    """Start the configured UI after validating any environment-provided access token.

    @param service Daemon service to expose through the UI.
    @param config Validated daemon configuration containing web settings.
    @return The running WebServer.
    """
    settings = config.web
    token = None
    ingress = validate_ingress(settings.get("ingress"))
    if ingress["enabled"] and settings.get("token_env"):
        raise ValueError("web.token_env must be omitted in ingress mode")
    if settings.get("token_env"):
        token = os.environ.get(settings["token_env"])
        if not token or not token.isascii():
            raise ValueError(
                "Web token environment variable must contain a nonempty ASCII token"
            )
    server = WebServer(
        service,
        settings.get("host", "127.0.0.1"),
        settings.get("port", 8080),
        token,
        ingress=ingress,
    )
    await server.start()
    return server
