"""Explicit browser ingress trust boundary; never trust forwarded client addresses.

The authenticated HA proxy strips the external prefix before forwarding requests.
Only the socket peer grants proxy trust. Public origins are separately allowlisted.
"""

import ipaddress
import re
from urllib.parse import urlsplit


def origin(value):
    """Validate and canonicalize one HTTP(S) origin without paths or credentials.

    @param value Exact HTTP(S) origin supplied in configuration or a header.
    @return Normalized origin, or raise ValueError for ambiguous/invalid input.
    """
    if not isinstance(value, str) or any(c.isspace() for c in value):
        raise ValueError("Ingress origin must be an exact HTTP(S) origin")
    parsed = urlsplit(value)
    if (
        parsed.scheme not in ("http", "https")
        or not parsed.netloc
        or parsed.path
        or parsed.query
        or parsed.fragment
        or any(c in value for c in "\\%@?#")
    ):
        raise ValueError("Ingress origin must be an exact HTTP(S) origin")
    host, port = parsed.hostname, parsed.port
    if not host or (port is not None and not 1 <= port <= 65535):
        raise ValueError("Invalid ingress origin host or port")
    if ":" in host:
        host = "[" + str(ipaddress.IPv6Address(host)) + "]"
    elif not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?", host):
        raise ValueError("Invalid ingress origin host")
    # Reject parser ambiguities, including a trailing empty port.
    authority = host + (":" + str(port) if port is not None else "")
    if parsed.netloc.lower() != authority:
        raise ValueError("Invalid ingress origin authority")
    if port == {"http": 80, "https": 443}[parsed.scheme]:
        authority = host
    return parsed.scheme + "://" + authority


def validate_ingress(raw=None):
    """Validate web.ingress; enabled mode requires proxy and public-origin lists.

    @param raw Optional ingress configuration mapping; None disables ingress.
    @return JSON-compatible normalized configuration for config.py and WebServer.
    """
    if raw is None:
        raw = {}
    if not isinstance(raw, dict) or set(raw) - {
        "enabled",
        "trusted_proxies",
        "allowed_origins",
    }:
        raise ValueError("Invalid or unknown fields in web.ingress")
    enabled = raw.get("enabled", False)
    if type(enabled) is not bool:
        raise ValueError("web.ingress.enabled must be boolean")
    result = {"enabled": enabled, "trusted_proxies": [], "allowed_origins": []}
    for key in ("trusted_proxies", "allowed_origins"):
        entries = raw.get(key, [])
        if not isinstance(entries, list) or (enabled and not entries):
            raise ValueError(
                "web.ingress." + key + " must be a nonempty list when enabled"
            )
        for entry in entries:
            if not isinstance(entry, str):
                raise ValueError("web.ingress." + key + " entries must be strings")
            if key == "trusted_proxies":
                network = ipaddress.ip_network(entry, strict=True)
                if network.prefixlen == 0:
                    raise ValueError("Ingress cannot trust all addresses")
                result[key].append(str(network))
            else:
                result[key].append(origin(entry))
    return result


def single_header(headers, name):
    """Require exactly one nonempty header; reject proxy chains and duplicates.

    @param headers HTTP message headers preserving repeated fields.
    @param name Case-insensitive header name required for this request.
    @return Single header value, or raise ValueError for missing/ambiguous input.
    """
    values = headers.get_all(name, [])
    if len(values) != 1 or not values[0] or "," in values[0]:
        raise ValueError("Missing or ambiguous ingress header")
    return values[0]


def request_context(settings, peer, headers):
    """Return (public origin, base path) only for a trusted, valid ingress request.

    X-Forwarded-For and remote-user headers never authorize a request. The proxy
    must authenticate the user before forwarding any asset or browser API route.

    @param settings Validated ingress proxy and origin allowlists.
    @param peer Numeric address of the actual TCP socket peer.
    @param headers HTTP message headers preserving repeated fields.
    @return Public origin and stripped prefix, or raise ValueError on rejection.
    """
    address = ipaddress.ip_address(peer)
    if not any(
        address in ipaddress.ip_network(net) for net in settings["trusted_proxies"]
    ):
        raise ValueError("Ingress accepts connections only from its trusted proxy")
    # Host may be the proxy's internal target or the preserved public host.
    origin("http://" + single_header(headers, "Host"))
    public = origin(
        single_header(headers, "X-Forwarded-Proto")
        + "://"
        + single_header(headers, "X-Forwarded-Host")
    )
    if public not in settings["allowed_origins"]:
        raise ValueError("Ingress public origin is not allowed")
    path = single_header(headers, "X-Ingress-Path")
    if len(path) > 1024 or not re.fullmatch(
        r"/(?:[A-Za-z0-9_.-]+/)*[A-Za-z0-9_.-]+/?", path
    ):
        raise ValueError("Invalid ingress base path")
    if any(segment in (".", "..") for segment in path.split("/")):
        raise ValueError("Invalid ingress base path")
    path = path.rstrip("/")
    # Validate Origin on reads too when supplied. All browser writes require it.
    if headers.get_all("Origin") and origin(single_header(headers, "Origin")) != public:
        raise ValueError("Ingress Origin does not match the public origin")
    return public, path
