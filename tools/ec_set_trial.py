#!/usr/bin/env python3
"""Bounded output control for the spare switch already paired to our local gateway.

Only a checksum-valid state report with our transaction token confirms success.
The firmware handles report ACKs. This tool disarms on exit; it is not a daemon.
"""
import argparse
import json
from pathlib import Path
import secrets
import time
from decode_ec import decode_frame
from ec_gateway import GatewayClient, ec_frame, ec_heartbeat
from ec_join_trial import transmit

GATEWAY = "2a992b12"
DEVICE = "46715621"
TAG = 0xA7


def matching_report(packet, mask, value, token):
    """Decide whether a report confirms this trial's requested output state.

    @param packet Decoded EC packet including checksum and addressing metadata.
    @param mask Output-selection bit mask to confirm.
    @param value Requested output bits within the mask.
    @param token Transaction token expected in the state report.
    @return True only for a valid report matching the configured peer and command.
    """
    return (
        packet["checksum_ok"]
        and packet["family"] == "9e"
        and packet["opcode"] == "05"
        and packet["source_wire"] == DEVICE
        and packet["subject_wire"] == DEVICE
        and packet["subject_suffix"] == "0002"
        and packet["destination_wire"] in (GATEWAY, "ffffffff")
        and packet["network_tag"] == "a7"
        and packet["token"] == token
        and packet["mask_candidate"] == mask
        and packet["state_bits"] & mask == value
    )


def main():
    """Request a bounded output change and require a matching state confirmation."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", required=True)
    parser.add_argument("--mask", type=int, choices=(1, 2, 3), required=True)
    parser.add_argument("--value", type=int, choices=(0, 1, 2, 3), required=True)
    parser.add_argument("--log", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.value & ~args.mask or args.output.exists():
        parser.error("Value must fit the selected mask, and output must be new")
    token = secrets.randbelow(255) + 1
    sequence = secrets.randbelow(256)
    result = {
        "gateway": GATEWAY,
        "device": DEVICE,
        "tag": "a7",
        "mask": args.mask,
        "value": args.value,
        "token": token,
        "confirmed": False,
        "events": [],
        "transmissions": [],
    }
    started = time.monotonic()
    with GatewayClient(args.port, args.log) as client:
        try:
            client.command(f"CONFIG {GATEWAY} a7 {DEVICE}")
            client.command("ARM 30")
            client.command("ACK 1")
            # Consume old UART records before the new transaction.
            client.listen(0.15)
            client.records.clear()
            heartbeat = ec_heartbeat(
                GATEWAY, TAG, sequence, int(time.monotonic()) & 0xFFFFFFFF, 1
            )
            result["heartbeat_response"] = transmit(client, heartbeat)
            sequence = (sequence + 1) & 255
            data = bytes((1, 0, args.mask, args.value, 0, 0, 0, token)).hex()
            next_send = time.monotonic()
            deadline = next_send + 4
            attempts = 0
            while time.monotonic() < deadline:
                while client.records:
                    parts = client.records.popleft().split()
                    if len(parts) != 7 or parts[0] != "RX":
                        continue
                    try:
                        packet = decode_frame(bytes.fromhex(parts[-1]))
                    except ValueError:
                        continue
                    if (
                        packet["source_wire"] == DEVICE
                        and packet["subject_wire"] == DEVICE
                    ):
                        packet["relative_host_seconds"] = time.monotonic() - started
                        result["events"].append(packet)
                        if matching_report(packet, args.mask, args.value, token):
                            if not result["confirmed"]:
                                result["confirmed"] = True
                                result["confirmed_report"] = packet
                                # Keep ACK handling active for repeated reports.
                                deadline = time.monotonic() + 1
                now = time.monotonic()
                if not result["confirmed"] and attempts < 3 and now >= next_send:
                    raw = ec_frame(GATEWAY, DEVICE, TAG, sequence, 2, data)
                    sequence = (sequence + 1) & 255
                    response = transmit(client, raw)
                    result["transmissions"].append(
                        {
                            "raw_hex": raw.hex(),
                            "response": response,
                            "relative_host_seconds": now - started,
                        }
                    )
                    attempts += 1
                    next_send = time.monotonic() + 0.2
                wake = min(deadline, time.monotonic() + 0.05)
                client.read_line(wake)
        finally:
            try:
                client.command("DISARM")
                result["final_status"] = client.command("STATUS")
            finally:
                result["elapsed_seconds"] = time.monotonic() - started
                args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(
        json.dumps(
            {
                key: result[key]
                for key in (
                    "mask",
                    "value",
                    "token",
                    "confirmed",
                    "elapsed_seconds",
                    "final_status",
                )
            }
            | {
                "state_bits": result.get("confirmed_report", {}).get("state_bits"),
                "reports": len(result["events"]),
            }
        ),
        flush=True,
    )
    if not result["confirmed"]:
        raise SystemExit("Output command was not confirmed by a matching state report")


if __name__ == "__main__":
    main()
