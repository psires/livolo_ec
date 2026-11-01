"""Trusted ingress boundary, prefix rendering, and standalone regression tests."""

import asyncio
from email.message import Message
import http.client
import json
from types import SimpleNamespace
from pathlib import Path
import unittest

from livolod.ingress import origin, request_context, validate_ingress
from livolod.web import WebServer, start_web


SETTINGS = {
    "enabled": True,
    "trusted_proxies": ["127.0.0.1"],
    "allowed_origins": ["https://ha.example:8123", "http://ha.local:8123"],
}
HEADERS = {
    "Host": "addon.internal:8099",
    "X-Ingress-Path": "/api/hassio_ingress/session-one",
    "X-Forwarded-Host": "ha.example:8123",
    "X-Forwarded-Proto": "https",
}


class IngressValidationTests(unittest.TestCase):
    def test_configuration_rejects_ambiguous_or_open_trust(self):
        """Ingress configuration rejects ambiguous origins and unrestricted proxy trust."""
        for raw in (
            True,
            {"enabled": 1},
            {"enabled": True},
            dict(SETTINGS, trusted_proxies=["0.0.0.0/0"]),
            dict(SETTINGS, trusted_proxies=["proxy.local"]),
            dict(SETTINGS, trusted_proxies="127.0.0.1"),
            dict(SETTINGS, allowed_origins=["https://*.example"]),
            dict(SETTINGS, allowed_origins=["https://ha.example/path"]),
            dict(SETTINGS, allowed_origins=["https://user@ha.example"]),
            dict(SETTINGS, allowed_origins=["https://ha.example/"]),
            dict(SETTINGS, extra=True),
        ):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                validate_ingress(raw)
        self.assertFalse(validate_ingress()["enabled"])
        self.assertEqual(origin("https://HA.EXAMPLE:443"), "https://ha.example")
        self.assertEqual(origin("https://[::1]:8123"), "https://[::1]:8123")

    def test_config_hook_allows_only_explicit_validated_ingress(self):
        """Non-loopback web listeners require explicitly validated ingress settings."""
        from livolod.config import parse

        raw = json.loads(
            (Path(__file__).parents[1] / "config.example.json").read_text()
        )
        raw["web"] = {
            "enabled": True,
            "host": "0.0.0.0",
            "port": 8099,
            "ingress": SETTINGS,
        }
        self.assertEqual(parse(raw).web["ingress"], validate_ingress(SETTINGS))
        for ingress in (
            {"enabled": False},
            {"enabled": True},
            dict(SETTINGS, trusted_proxies=["0.0.0.0/0"]),
        ):
            raw["web"]["ingress"] = ingress
            with self.assertRaises(ValueError):
                parse(raw)

    def test_only_socket_peer_grants_trust_and_cidr_is_supported(self):
        """Only the socket peer can match a trusted IPv4 or IPv6 proxy network."""
        settings = validate_ingress(
            dict(SETTINGS, trusted_proxies=["172.30.32.2/32", "2001:db8::/64"])
        )
        headers = Message()
        for key, value in dict(HEADERS, **{"X-Forwarded-For": "172.30.32.2"}).items():
            headers[key] = value
        for peer in ("127.0.0.1", "172.30.32.3", "2001:db9::1"):
            with self.subTest(peer=peer), self.assertRaises(ValueError):
                request_context(settings, peer, headers)
        for peer in ("172.30.32.2", "2001:db8::10"):
            self.assertEqual(
                request_context(settings, peer, headers),
                ("https://ha.example:8123", HEADERS["X-Ingress-Path"]),
            )


class FakeService:
    def __init__(self):
        """Create a command ledger for the isolated ingress service stub."""
        self.calls = []

    def api_snapshot(self):
        """Expose a minimal broker-free state without a real radio or database."""
        return {"ready": True, "devices": [], "mqtt": {"enabled": False}}

    def submit(self, action, payload):
        """Record an accepted browser command and return a confirmed stub result.

        @param action Browser command action accepted by the web server.
        @param payload Validated command fields, including the optional request ID.
        @return Confirmed result for the submitted request.
        """
        self.calls.append((action, payload))
        return {"request_id": payload.get("request_id"), "status": "confirmed"}

    def command_result(self, request_id):
        """Return a deterministic result for browser command-status requests.

        @param request_id Command identifier requested by the browser.
        @return Confirmed stub result retaining the requested ID.
        """
        return {"request_id": request_id, "status": "confirmed"}


class IngressHttpTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        """Start an isolated loopback web server with trusted-proxy ingress enabled."""
        self.service = FakeService()
        self.server = WebServer(self.service, port=0, ingress=SETTINGS)
        await self.server.start()

    async def asyncTearDown(self):
        """Release the loopback listener and its request handlers after each test."""
        await self.server.close()

    async def request(self, path="/api/state", method="GET", headers=None, body=None):
        """Exercise the real HTTP parser while preserving duplicate header fields.

        @param path Request target relative to the isolated server.
        @param method HTTP request method.
        @param headers Header mapping or ordered pairs; None uses the trusted proxy fields.
        @param body Optional UTF-8 request body.
        @return HTTP status, response headers and decoded JSON or text body.
        """

        def exchange():
            """Run one blocking HTTP exchange off the event loop and always close it."""
            conn = http.client.HTTPConnection("127.0.0.1", self.server.port, timeout=5)
            try:
                # A list preserves duplicate fields for parser ambiguity tests.
                pairs = (
                    list(HEADERS.items())
                    if headers is None
                    else list(headers.items()) if isinstance(headers, dict) else headers
                )
                conn.putrequest(method, path, skip_host=True)
                for key, value in pairs:
                    conn.putheader(key, value)
                if body is not None:
                    conn.putheader("Content-Length", str(len(body.encode())))
                    conn.putheader("Content-Type", "application/json")
                conn.endheaders(body.encode() if body is not None else None)
                response = conn.getresponse()
                raw = response.read().decode()
                value = (
                    json.loads(raw)
                    if "application/json" in response.getheader("Content-Type", "")
                    else raw
                )
                return response.status, dict(response.headers), value
            finally:
                conn.close()

        return await asyncio.to_thread(exchange)

    async def test_trusted_prefix_assets_and_https_csrf_command(self):
        """Trusted ingress renders prefixed assets and accepts a valid HTTPS CSRF command."""
        status, headers, html = await self.request("/")
        self.assertEqual(status, 200)
        self.assertIn('content="/api/hassio_ingress/session-one/"', html)
        self.assertIn('src="/api/hassio_ingress/session-one/app.js"', html)
        self.assertIn('href="/api/hassio_ingress/session-one/style.css"', html)
        self.assertNotIn("<base", html)
        self.assertNotIn("__LIVOLO_BASE_PATH__", html)
        self.assertNotIn("X-Frame-Options", headers)
        self.assertIn(
            "frame-ancestors https://ha.example:8123;",
            headers["Content-Security-Policy"],
        )
        self.assertIn("base-uri 'none'", headers["Content-Security-Policy"])
        self.assertEqual(headers["Cache-Control"], "no-store")
        for asset in ("/app.js", "/style.css"):
            self.assertEqual((await self.request(asset))[0], 200)
        status, _, state = await self.request()
        self.assertEqual(status, 200)
        token = state["csrf_token"]
        self.assertNotEqual(token, self.server.token)
        fields = dict(
            HEADERS, Origin="https://ha.example:8123", **{"X-Livolo-Token": token}
        )
        body = json.dumps(
            {
                "action": "rename",
                "payload": {
                    "request_id": "test-1",
                    "device": "46715621",
                    "name": "Kitchen",
                },
            }
        )
        self.assertEqual(
            (await self.request("/api/command", "POST", fields, body))[0], 200
        )
        self.assertEqual(len(self.service.calls), 1)
        self.assertEqual(
            (await self.request("/api/commands/test-1"))[2]["status"], "confirmed"
        )

    async def test_untrusted_peer_cannot_spoof_ingress_or_bearer(self):
        """Forged forwarded identity and bearer headers cannot grant proxy trust."""
        self.server.ingress["trusted_proxies"] = ["172.30.32.2/32"]
        forged = dict(
            HEADERS,
            Authorization="Bearer test",
            **{"X-Forwarded-For": "172.30.32.2", "X-Remote-User-Id": "admin"},
        )
        for path in ("/", "/app.js", "/api/state", "/api/commands/test"):
            status, headers, body = await self.request(path, headers=forged)
            self.assertEqual(status, 403)
            self.assertEqual(headers["X-Frame-Options"], "DENY")
            self.assertNotIn("csrf_token", body)
        self.assertEqual(
            (await self.request("/api/command", "POST", forged, "{}"))[0], 403
        )
        self.assertFalse(self.service.calls)

    async def test_invalid_missing_or_duplicate_proxy_headers_fail_closed(self):
        """Missing, duplicated or malformed ingress metadata rejects requests before dispatch."""
        for key in ("Host", "X-Ingress-Path", "X-Forwarded-Host", "X-Forwarded-Proto"):
            missing = dict(HEADERS)
            missing.pop(key)
            self.assertEqual((await self.request(headers=missing))[0], 403)
            self.assertEqual(
                (
                    await self.request(
                        headers=list(HEADERS.items()) + [(key, HEADERS[key])]
                    )
                )[0],
                403,
            )
        for key, value in (
            ("X-Forwarded-Proto", "https,http"),
            ("X-Forwarded-Proto", "ftp"),
            ("X-Forwarded-Host", "evil.example"),
            ("X-Forwarded-Host", "ha.example:8123@evil.example"),
            ("X-Forwarded-Host", "ha.example:8123/path"),
            ("Host", "evil/path"),
            ("X-Ingress-Path", "//evil.example"),
            ("X-Ingress-Path", "/x/../y"),
            ("X-Ingress-Path", "/x/%2e%2e/y"),
            ("X-Ingress-Path", "/x//y"),
            ("X-Ingress-Path", "/x?y"),
            ("X-Ingress-Path", "/x#y"),
            ("X-Ingress-Path", "/x\\y"),
            ("X-Ingress-Path", '/x" onload="evil'),
            ("X-Ingress-Path", "/"),
            ("X-Ingress-Path", "/x" + "a" * 1024),
            ("Origin", "https://evil.example"),
            ("Origin", "null"),
        ):
            with self.subTest(key=key, value=value):
                self.assertEqual(
                    (await self.request(headers=dict(HEADERS, **{key: value})))[0], 403
                )
        self.assertFalse(self.service.calls)

    async def test_origin_csrf_and_path_bound_tokens(self):
        """Browser writes require matching origins and tokens bound to the ingress session."""
        token = (await self.request())[2]["csrf_token"]
        good = dict(
            HEADERS, Origin="https://ha.example:8123", **{"X-Livolo-Token": token}
        )
        body = json.dumps({"action": "rename", "payload": {}})
        no_origin = dict(good)
        no_origin.pop("Origin")
        cases = [
            no_origin,
            dict(good, Origin="http://ha.example:8123"),
            dict(good, **{"X-Livolo-Token": self.server.token}),
            dict(good, **{"X-Ingress-Path": "/api/hassio_ingress/session-two"}),
            dict(good, **{"Sec-Fetch-Site": "cross-site"}),
        ]
        for fields in cases:
            self.assertEqual(
                (await self.request("/api/command", "POST", fields, body))[0], 403
            )
        duplicate = list(good.items()) + [("Origin", good["Origin"])]
        self.assertEqual(
            (await self.request("/api/command", "POST", duplicate, body))[0], 403
        )
        # Explicitly configured HTTP origins work; the old HTTPS token does not.
        http = dict(
            HEADERS,
            **{"X-Forwarded-Proto": "http", "X-Forwarded-Host": "ha.local:8123"},
        )
        fresh = (await self.request(headers=http))[2]["csrf_token"]
        self.assertNotEqual(token, fresh)
        http.update(Origin="http://ha.local:8123", **{"X-Livolo-Token": fresh})
        self.assertEqual(
            (await self.request("/api/command", "POST", http, body))[0], 200
        )
        self.assertEqual(len(self.service.calls), 1)

    async def test_trailing_slash_and_unstripped_paths(self):
        """Equivalent ingress prefixes share tokens while unstripped request paths fail."""
        headers = dict(HEADERS, **{"X-Ingress-Path": HEADERS["X-Ingress-Path"] + "/"})
        self.assertEqual(
            (await self.request(headers=headers))[2]["csrf_token"],
            (await self.request())[2]["csrf_token"],
        )
        self.assertEqual(
            (await self.request(HEADERS["X-Ingress-Path"] + "/api/state"))[0], 404
        )

    async def test_start_web_hook_and_auth_modes_are_exclusive(self):
        """Startup requires one explicit authentication mode and rejects mixed ingress tokens."""
        with self.assertRaises(ValueError):
            WebServer(self.service, host="0.0.0.0")
        with self.assertRaises(ValueError):
            WebServer(self.service, ingress=SETTINGS, token="secret")
        config = SimpleNamespace(
            web={"enabled": True, "host": "127.0.0.1", "port": 0, "ingress": SETTINGS}
        )
        server = await start_web(self.service, config)
        await server.close()
        config.web["token_env"] = "BROWSER_TOKEN"
        with self.assertRaises(ValueError):
            await start_web(self.service, config)

    async def test_standalone_ignores_forwarded_identity_and_retains_guards(self):
        """Standalone mode keeps bearer, framing and same-origin protections behind proxy headers."""
        await self.server.close()
        self.server = WebServer(self.service, port=0, token="standalone-secret")
        await self.server.start()
        headers = dict(HEADERS, Host="127.0.0.1:" + str(self.server.port))
        status, response, html = await self.request("/", headers=headers)
        self.assertEqual(status, 200)
        self.assertIn('src="/app.js"', html)
        self.assertIn('content="/"', html)
        self.assertEqual(response["X-Frame-Options"], "DENY")
        self.assertIn("frame-ancestors 'none'", response["Content-Security-Policy"])
        self.assertEqual((await self.request(headers=headers))[0], 401)
        headers["Authorization"] = "Bearer standalone-secret"
        token = (await self.request(headers=headers))[2]["csrf_token"]
        self.assertEqual(token, self.server.token)
        body = json.dumps({"action": "rename", "payload": {}})
        headers.update(Origin="https://ha.example:8123", **{"X-Livolo-Token": token})
        self.assertEqual(
            (await self.request("/api/command", "POST", headers, body))[0], 403
        )
        headers["Origin"] = "http://" + headers["Host"]
        self.assertEqual(
            (await self.request("/api/command", "POST", headers, body))[0], 200
        )


if __name__ == "__main__":
    unittest.main()
