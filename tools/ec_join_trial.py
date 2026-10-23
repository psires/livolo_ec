#!/usr/bin/env python3
"""Bounded experimental EC admission using a distinct gateway identity/tag.

Requires the named spare switch to have been removed from the stock gateway.
The caller starts Nordic capture first, then puts the switch into pairing mode.
This sends registration ACKs, observed queries, and gateway heartbeats. It does
not send output-control commands. MCU report ACKs require the configured peer,
tag and a valid checksum, with direct or supported broadcast destinations.
All transmit leases expire on exit/loss.
"""
import argparse
import heapq
import json
from pathlib import Path
import time
from ec_gateway import GatewayClient, GatewayError, ec_frame, ec_heartbeat
from decode_ec import decode_frame


def transmit(client, raw):
    """Send a trial frame, retrying only definite CCA collisions.

    @param client Open GatewayClient owning the configured UART session.
    @param raw Complete outgoing MAC frame bytes without FCS.
    @return The adapter reply, or SKIPPED CCA_BUSY after four busy results.
    """
    for attempt in range(4):
        try:
            return client.command("TX " + raw.hex())
        except GatewayError as error:
            if str(error) != "ERR TX 2":
                raise
            if attempt == 3:
                return "SKIPPED CCA_BUSY"
            client.listen(0.025 * (attempt + 1))


def main():
    """Run bounded admission or monitoring of the explicitly selected spare switch."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", required=True)
    parser.add_argument("--gateway", default="2a992b12")
    parser.add_argument("--tag", default="a7")
    parser.add_argument("--device", default="46715621")
    parser.add_argument("--duration", type=float, default=180)
    parser.add_argument(
        "--monitor-only",
        action="store_true",
        help="Maintain/ACK the already paired peer; never admit or query it",
    )
    parser.add_argument("--log", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not 10 <= args.duration <= 3600 or args.output.exists():
        parser.error("Use a new output file and a duration between 10 and 3600 seconds")
    if args.gateway.lower() == "9bf4ea38" or int(args.tag, 16) == 0xB1:
        parser.error(
            "This trial must use an identity and tag distinct from the stock EC network"
        )
    tag = int(args.tag, 16)
    result = {
        "gateway": args.gateway,
        "tag": args.tag,
        "device": args.device,
        "monitor_only": args.monitor_only,
        "joined_observed": False,
        "events": [],
        "transmissions": [],
    }
    started = time.monotonic()
    sequence = 0x80
    schedule = []
    admission_pending = False
    latest_registration = None
    next_admission = 0.0

    with GatewayClient(args.port, args.log) as client:
        client.command(f"CONFIG {args.gateway} {args.tag} {args.device}")
        client.command("ARM 30")
        client.command("ACK 1")
        next_lease = time.monotonic() + 10
        next_beacon = time.monotonic()
        print(
            (
                "Local gateway ready for physical-button monitoring."
                if args.monitor_only
                else "Local gateway armed for the named spare switch; ready for pairing."
            ),
            flush=True,
        )
        try:
            while time.monotonic() - started < args.duration:
                now = time.monotonic()
                if now >= next_lease:
                    client.command("ARM 30")
                    next_lease = time.monotonic() + 10
                if now >= next_beacon:
                    raw = ec_heartbeat(
                        args.gateway,
                        tag,
                        sequence,
                        int(now - started),
                        int(args.monitor_only or result["joined_observed"]),
                    )
                    sequence = (sequence + 1) & 255
                    response = transmit(client, raw)
                    result["transmissions"].append(
                        {
                            "relative_seconds": now - started,
                            "kind": "heartbeat",
                            "raw_hex": raw.hex(),
                            "response": response,
                        }
                    )
                    next_beacon = time.monotonic() + 10

                while schedule and schedule[0][0] <= time.monotonic():
                    _, kind = heapq.heappop(schedule)
                    if kind.startswith("registration"):
                        opcode = 0xFA
                        data = "01" + latest_registration["data_hex"][:14]
                    else:
                        opcode = 0x10 if kind == "query_10" else 0xAC
                        data = "0000000000000001"
                    raw = ec_frame(
                        args.gateway, args.device, tag, sequence, opcode, data
                    )
                    sequence = (sequence + 1) & 255
                    response = transmit(client, raw)
                    event = {
                        "relative_seconds": time.monotonic() - started,
                        "kind": kind,
                        "raw_hex": raw.hex(),
                        "response": response,
                    }
                    result["transmissions"].append(event)
                    print(json.dumps(event), flush=True)
                    if not schedule:
                        admission_pending = False

                while client.records:
                    line = client.records.popleft()
                    if not line.startswith("RX "):
                        continue
                    parts = line.split()
                    if len(parts) != 7:
                        continue
                    try:
                        packet = decode_frame(bytes.fromhex(parts[-1]))
                    except ValueError:
                        continue
                    if (
                        not packet["checksum_ok"]
                        or packet["source_wire"] != args.device.lower()
                        or packet["subject_wire"] != args.device.lower()
                    ):
                        continue
                    packet["relative_host_seconds"] = time.monotonic() - started
                    result["events"].append(packet)
                    if packet["family"] == "9e" and packet["opcode"] == "05":
                        print(
                            json.dumps(
                                {
                                    key: packet[key]
                                    for key in (
                                        "relative_host_seconds",
                                        "destination_wire",
                                        "network_tag",
                                        "mask_candidate",
                                        "state_bits",
                                        "report_counter",
                                        "token",
                                    )
                                }
                            ),
                            flush=True,
                        )
                    if (
                        packet["family"] == "9e"
                        and packet["network_tag"] == args.tag.lower()
                        and packet["destination_wire"] == args.gateway.lower()
                    ):
                        if not result["joined_observed"]:
                            print(
                                "JOIN OBSERVED: native switch packet uses the local network tag.",
                                flush=True,
                            )
                        result["joined_observed"] = True
                    if (
                        not args.monitor_only
                        and packet["family"] == "9e"
                        and packet["opcode"] == "01"
                        and packet["network_tag"] == "ff"
                        and packet["subject_suffix"] == "0002"
                        and not result["joined_observed"]
                    ):
                        latest_registration = packet
                        if not admission_pending and time.monotonic() >= next_admission:
                            begin = time.monotonic()
                            # Reproduce the observed deferred ACK and query sequence.
                            for delay, kind in (
                                (0.6, "registration_1"),
                                (0.8, "registration_2"),
                                (1.0, "query_10"),
                                (1.2, "query_ac_1"),
                                (1.4, "query_ac_2"),
                            ):
                                heapq.heappush(schedule, (begin + delay, kind))
                            admission_pending = True
                            next_admission = begin + 5
                            print(
                                "Registration received; scheduling admission response.",
                                flush=True,
                            )
                wake = min(
                    started + args.duration,
                    next_lease,
                    next_beacon,
                    schedule[0][0] if schedule else float("inf"),
                    time.monotonic() + 0.2,
                )
                client.read_line(wake)
        except KeyboardInterrupt:
            result["stop_reason"] = "operator_interrupt"
        finally:
            # Save partial evidence even if a link error or interrupt ends the trial.
            try:
                client.command("DISARM")
                result["final_status"] = client.command("STATUS")
            finally:
                result["elapsed_seconds"] = time.monotonic() - started
                args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(
        json.dumps(
            {
                "joined_observed": result["joined_observed"],
                "events": len(result["events"]),
                "output": str(args.output),
            }
        ),
        flush=True,
    )


if __name__ == "__main__":
    main()
