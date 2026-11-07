"""Explicit commissioning client for an already running native daemon API."""

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
import uuid


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        """Reject redirects so authenticated POSTs cannot move or change meaning.

        @param req Original authenticated request.
        @param fp Redirect response stream.
        @param code HTTP redirect status.
        @param msg HTTP reason phrase.
        @param headers Response headers.
        @param newurl Untrusted redirect target, never followed.
        """
        return None


def main(argv=None):
    """Submit one operation without retrying ambiguous radio-affecting requests.

    @param argv Optional argument list for embedding and offline command tests.
    @return Zero on accepted/read operations, one on refusal or uncertain transport.
    """
    parser = argparse.ArgumentParser(description="Bounded Livolo device commissioning")
    parser.add_argument("--url", default="http://127.0.0.1:8090")
    parser.add_argument("--token-env", default="LIVOLO_API_TOKEN")
    commands = parser.add_subparsers(dest="command", required=True)
    start = commands.add_parser("start", help="Open one explicit pairing window")
    start.add_argument(
        "--seconds", type=int, default=120, choices=range(10, 121), metavar="10..120"
    )
    start.add_argument("--request-id", default=None)
    status = commands.add_parser(
        "status", help="Read session or a durable command result"
    )
    status.add_argument("--request-id", default=None)
    cancel = commands.add_parser(
        "cancel", help="Close one named window; cannot unpair a switch"
    )
    cancel.add_argument("session_id")
    cancel.add_argument("--request-id", default=None)
    args = parser.parse_args(argv)
    token = os.environ.get(args.token_env)
    parsed = urllib.parse.urlsplit(args.url)
    if (
        not token
        or parsed.scheme not in ("http", "https")
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        parser.error(
            "Supply an HTTP(S) daemon URL and a nonempty token environment variable"
        )
    ident = args.request_id or str(uuid.uuid4())
    payload = None
    path = "/api/v1/snapshot"
    if args.command == "status" and args.request_id:
        path = "/api/v1/commands/" + urllib.parse.quote(args.request_id, safe="")
    elif args.command != "status":
        fields = {"request_id": ident}
        if args.command == "start":
            fields["seconds"] = args.seconds
        else:
            fields["session_id"] = args.session_id
        payload = json.dumps(
            {
                "action": (
                    "commission" if args.command == "start" else "commission_cancel"
                ),
                "payload": fields,
            }
        ).encode()
        path = "/api/v1/commands"
    request = urllib.request.Request(
        args.url.rstrip("/") + path,
        data=payload,
        headers={
            "Authorization": "Bearer " + token,
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.build_opener(NoRedirect()).open(
            request, timeout=10
        ) as response:
            result = json.load(response)
        if not isinstance(result, dict):
            raise ValueError("Invalid response")
        if args.command == "status" and not args.request_id:
            data = result.get("data")
            if (
                not isinstance(data, dict)
                or type(data.get("commissioning_supported")) is not bool
                or "commissioning" not in data
                or not isinstance(data["commissioning"], (dict, type(None)))
            ):
                raise ValueError("Invalid snapshot")
            result = {
                key: data[key] for key in ("commissioning_supported", "commissioning")
            }
        elif result.get("request_id") != ident or result.get("status") not in {
            "listening",
            "registering",
            "pairing",
            "confirmed",
            "rejected",
            "failed",
            "unknown",
            "conflict",
            "expired",
            "cancelled",
        }:
            raise ValueError("Invalid command result")
        print(json.dumps(result))
        if args.command == "status":
            return 0
        accepted = (
            {"confirmed"}
            if args.command == "cancel"
            else {"listening", "registering", "pairing", "confirmed"}
        )
        return int(result.get("status") not in accepted)
    except (OSError, ValueError, urllib.error.HTTPError):
        print(
            json.dumps(
                {
                    "request_id": ident if payload else args.request_id,
                    "status": "unknown",
                    "error": "API request failed; read status with this request ID before any new start",
                }
            ),
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
