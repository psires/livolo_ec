#!/usr/bin/env python3
"""Bounded live HTTP acceptance: read state, preserve outputs, verify command report.

Only uses the running daemon's HTTP API. Does not pair, forget, toggle, or open UART.
Requires explicit --device and --output; a report <=30s old before actuation.
"""
import argparse
import json
import os
from pathlib import Path
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen
import uuid


def check(args):
    """Verify live HTTP control by reasserting freshly observed output state.

    @param args Parsed URL, target device, mask, authentication and evidence options.
    """
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x") as stream:
        json.dump({"status": "started", "started_at": time.time()}, stream)
    token = os.environ.get(args.token_env) if args.token_env else None
    headers = {"Authorization": "Bearer " + token} if token else {}
    evidence = {
        "started_at": time.time(),
        "device": args.device,
        "purpose": "Reassert freshly observed state without requesting an output transition",
        "pairing_commands_sent": 0,
        "requests": [],
    }

    def request(path, payload=None, csrf=None):
        """Exchange JSON with the daemon HTTP API using trial credentials.

        @param path API path relative to the configured daemon URL.
        @param payload Optional JSON body; None selects a read request.
        @param csrf CSRF token required when sending a JSON command.
        @return The decoded JSON response; HTTP errors propagate to the caller.
        """
        extra = dict(headers)
        data = None
        if payload is not None:
            data = json.dumps(payload).encode()
            extra.update({"Content-Type": "application/json", "X-Livolo-Token": csrf})
        with urlopen(
            Request(args.url.rstrip("/") + path, data=data, headers=extra), timeout=10
        ) as response:
            return json.load(response)

    def usable(snapshot):
        """Require a live, online target with a recent state report before control.

        @param snapshot Daemon API state snapshot to validate.
        @return The configured target device with a state report at most 30 seconds old.
        """
        if (
            snapshot.get("simulated")
            or not snapshot.get("ready")
            or snapshot.get("pairing")
        ):
            raise ValueError("Requires a ready live radio with pairing closed")
        device = next((d for d in snapshot["devices"] if d["id"] == args.device), None)
        if not device or device["availability"] != "online" or not device.get("state"):
            raise ValueError(
                "Target must be registered, online and have a received state report"
            )
        if not 0 <= time.time() - device["state"]["last_seen"] <= 30:
            raise ValueError(
                "Target state report is older than 30 seconds; wait for a fresh report"
            )
        return device

    def sanitized(snapshot):
        """Prepare snapshot evidence without retaining its CSRF credential.

        @param snapshot Daemon API snapshot to copy without its CSRF token.
        @return A shallow snapshot copy excluding csrf_token.
        """
        return {k: v for k, v in snapshot.items() if k != "csrf_token"}

    def command(action, payload, csrf):
        """Submit a uniquely identified HTTP command and require confirmation.

        @param action Daemon command action.
        @param payload Command parameters for the selected device.
        @param csrf Current API CSRF token.
        @return The confirmed result matching both the request ID and target device.
        """
        request_id = "http-acceptance-" + uuid.uuid4().hex
        payload = dict(payload, request_id=request_id)
        evidence["requests"].append(
            {"action": action, "payload": payload, "at": time.time()}
        )
        result = request("/api/command", {"action": action, "payload": payload}, csrf)
        deadline = time.monotonic() + 20
        while result["status"] == "pending" and time.monotonic() < deadline:
            time.sleep(0.1)
            result = request("/api/commands/" + request_id)
        evidence["requests"][-1]["result"] = result
        if (
            result.get("request_id") != request_id
            or result.get("device") != args.device
        ):
            raise RuntimeError("Command result identity does not match request")
        if result["status"] != "confirmed":
            raise RuntimeError("Command not confirmed: " + json.dumps(result))
        return result

    try:
        before = request("/api/state")
        evidence["before"] = sanitized(before)
        device = usable(before)
        supported_mask = (1 << device["outputs"]) - 1
        mask = args.mask
        if mask & ~supported_mask:
            raise ValueError("Requested mask exceeds configured outputs")
        bits = device["state"]["state_bits"]
        if bits & ~supported_mask:
            raise ValueError("Observed bits do not fit configured outputs")
        result = command(
            "set",
            {"device": args.device, "mask": mask, "value": bits & mask},
            before["csrf_token"],
        )
        state = result.get("state", {})
        if (
            state.get("device") != args.device
            or type(state.get("token")) is not int
            or not 1 <= state["token"] <= 255
            or state.get("state_bits") != bits
            or state.get("mask") != mask
            or state.get("token") != result.get("token")
            or state.get("token") == device["state"].get("token")
        ):
            raise RuntimeError("No matching preserved-state report with a fresh token")
        if state.get("last_seen", 0) < evidence["requests"][0]["at"]:
            raise RuntimeError("Confirmation report predates this test")
        after = request("/api/state")
        evidence["after"] = sanitized(after)
        current = usable(after)
        if current["state"]["state_bits"] != bits:
            raise RuntimeError("Output state changed during verification")
        evidence["passed"] = True
        evidence["confirmed_state_bits"] = bits
        evidence["confirmed_token"] = state["token"]
    except Exception as error:
        evidence["passed"] = False
        evidence["error"] = str(error)
        try:
            evidence["after"] = sanitized(request("/api/state"))
        except Exception as read_error:
            evidence["after_error"] = str(read_error)
        raise
    finally:
        evidence["finished_at"] = time.time()
        Path(args.output).write_text(json.dumps(evidence, indent=2) + "\n")
    print(
        json.dumps(
            {
                "passed": True,
                "device": args.device,
                "state_bits": bits,
                "request_id": result["request_id"],
                "evidence": args.output,
            }
        )
    )


def main():
    """Parse HTTP acceptance options and verify confirmed state preservation."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8080")
    parser.add_argument("--device", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--token-env")
    parser.add_argument("--mask", type=int, choices=(1, 2, 3), default=1)
    args = parser.parse_args()
    try:
        check(args)
    except (ValueError, RuntimeError, OSError, HTTPError) as error:
        parser.exit(1, str(error) + "\n")


if __name__ == "__main__":
    main()
