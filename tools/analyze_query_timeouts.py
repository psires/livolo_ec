#!/usr/bin/env python3
"""Reproduce the offline MG21 query-timeout comparison; never access a radio."""

import hashlib
import json
from pathlib import Path

from decode_ec import decode_frame, read_pcapng


ROOT = Path(__file__).resolve().parents[1]
DEVICE = "46715621"
PRIMARY = "captured/development/mg21-query-retries-20261024-nrf52840.pcapng"
EVIDENCE = "analysis/development/mg21-query-retries-20261024.json"
OUTPUT = "analysis/development/mg21-query-timeout-comparison-20261024.json"
EXTRA_CAPTURES = (
    "captured/development/gateway-pairing-20261017T182031Z-nrf52840.pcapng",
    "captured/development/original-ec-75710834-control-01-nrf52840.pcapng",
    "captured/development/mg21-toggle-20261024-nrf52840.pcapng",
)


def span(values):
    """Describe a measured sample without inventing bounds for an empty sample.

    @param values Sequence of numerical measurements in the caller's units.
    """
    return (
        {"count": len(values), "min": min(values), "max": max(values)} if values else {}
    )


def load_capture(relative):
    """Decode supported frames and retain original packet numbers and RF timestamps.

    @param relative Repository-relative pcapng path with TAP metadata.
    """
    path = ROOT / relative
    rows = []
    total = 0
    for number, timestamp, raw, metadata in read_pcapng(path):
        total += 1
        try:
            packet = decode_frame(raw)
        except ValueError:
            continue
        rows.append(dict(frame=number, timestamp=timestamp, **metadata, **packet))
    assert all(p["checksum_ok"] for p in rows), relative
    return rows, {
        "capture": relative,
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "total_packets": total,
        "decoded_packets": len(rows),
        "unsupported_packets": total - len(rows),
        "bad_ec_checksums": 0,
    }


def responses(rows, query, opcode, window, acknowledged=None):
    """List candidate native responses; nearby repeated queries can share candidates.

    @param rows Decoded packets from one capture.
    @param query Gateway query packet to measure from.
    @param opcode Native response opcode as hexadecimal text.
    @param window Maximum positive response delay in seconds.
    @param acknowledged Required embedded query opcode, or None for state reports.
    """
    return [
        {
            "frame": p["frame"],
            "delay_ms": round((p["timestamp"] - query["timestamp"]) * 1000, 3),
            "data_hex": p["data_hex"],
        }
        for p in rows
        if p["family"] == "9e"
        and p["opcode"] == opcode
        and p["source_wire"] == p["subject_wire"] == query["subject_wire"]
        and p["network_tag"] == query["network_tag"]
        and p["destination_wire"] in (query["source_wire"], "ffffffff")
        and 0 < p["timestamp"] - query["timestamp"] < window
        and (acknowledged is None or p["ack_of_opcode"] == acknowledged)
    ]


def summarize_capture(relative):
    """Measure gateway queries, heartbeat cadence and exploratory announcement phase.

    @param relative Repository-relative path naming one independently timed capture.
    """
    rows, summary = load_capture(relative)
    gateway = "2a992b12" if "mg21-" in relative else "9bf4ea38"
    heartbeats = [
        p for p in rows if p["source_wire"] == gateway and p["heartbeat_counter"] != ""
    ]
    summary["heartbeats"] = [
        {k: p[k] for k in ("frame", "timestamp", "heartbeat_counter")}
        for p in heartbeats
    ]
    summary["heartbeat_intervals_seconds"] = [
        round(b["timestamp"] - a["timestamp"], 6)
        for a, b in zip(heartbeats, heartbeats[1:])
    ]
    summary["heartbeat_interval_range_seconds"] = span(
        summary["heartbeat_intervals_seconds"]
    )
    announcements = [
        p
        for p in rows
        if p["source_wire"] == DEVICE and p["family"] == "9e" and p["opcode"] == "07"
    ]
    queries = []
    for packet in rows:
        if not (
            packet["source_wire"] == gateway
            and packet["family"] == "8c"
            and packet["opcode"] in ("10", "ac")
        ):
            continue
        query = dict(packet)
        previous = [h for h in heartbeats if h["timestamp"] < packet["timestamp"]]
        trailer = bytes.fromhex(packet["trailer_hex"])
        encoded = trailer[2] + int.from_bytes(trailer[:2], "little") / 65500
        query["encoded_phase_seconds"] = encoded
        if previous:
            age = packet["timestamp"] - previous[-1]["timestamp"]
            query["heartbeat_age_seconds"] = round(age, 6)
            query["trailer_error_ms"] = round((encoded - age) * 1000, 3)
        query["reply_candidates"] = responses(rows, packet, "fa", 1.5, packet["opcode"])
        query["state_candidates"] = responses(rows, packet, "05", 2)
        if announcements and packet["subject_wire"] == DEVICE:
            nearest = min(
                announcements, key=lambda p: abs(p["timestamp"] - packet["timestamp"])
            )
            delta = packet["timestamp"] - nearest["timestamp"]
            query["exploratory_200ms_phase"] = {
                "announcement_frame": nearest["frame"],
                "announcement_distance_seconds": round(delta, 6),
                "phase_ms": round((delta % 0.2) * 1000, 3),
            }
        queries.append(query)
    summary["queries"] = queries
    return rows, summary


def main():
    """Save comparison evidence and assert the six-request capture accounting."""
    paths = sorted(
        p.relative_to(ROOT).as_posix() for p in (ROOT / "captured").glob("*.pcapng")
    )
    paths += list(EXTRA_CAPTURES) + [PRIMARY]
    captures = []
    primary_rows = None
    for path in paths:
        rows, summary = summarize_capture(path)
        captures.append(summary)
        if path == PRIMARY:
            primary_rows = rows
    primary = captures[-1]
    original_queries = [
        q for c in captures for q in c["queries"] if q["source_wire"] == "9bf4ea38"
    ]
    # Normalize only known network/session fields; do not ignore payload differences.
    allowed = {2, 7, 8, 9, 10, 19, 38, 39, 40}
    for query in primary["queries"]:
        reference = next(q for q in original_queries if q["opcode"] == query["opcode"])
        a, b = bytes.fromhex(query["raw_hex"]), bytes.fromhex(reference["raw_hex"])
        assert len(a) == len(b) == 41
        assert all(
            x == y or offset in allowed for offset, (x, y) in enumerate(zip(a, b))
        )
    trial = json.loads((ROOT / EVIDENCE).read_text())
    requests = []
    for request in trial["requests"]:
        during = [
            p
            for p in primary_rows
            if request["started_at"] <= p["timestamp"] <= request["finished_at"]
        ]
        queries = [
            q
            for q in primary["queries"]
            if request["started_at"] <= q["timestamp"] <= request["finished_at"]
        ]
        assert len(queries) == request["result"]["queries_sent"]
        native = [
            p for p in during if p["source_wire"] == DEVICE and p["family"] == "9e"
        ]
        if request["result"]["status"] == "unconfirmed":
            assert not any(p["opcode"] in ("fa", "05") for p in native)
        requests.append(
            {
                "number": request["number"],
                "status": request["result"]["status"],
                "query_frames": [q["frame"] for q in queries],
                "native_frames": [
                    {k: p[k] for k in ("frame", "timestamp", "opcode", "data_hex")}
                    for p in native
                ],
                "query_pair_gaps_ms": [
                    round((b["timestamp"] - a["timestamp"]) * 1000, 3)
                    for a, b in zip(queries[::2], queries[1::2])
                ],
                "round_start_gaps_ms": [
                    round((b["timestamp"] - a["timestamp"]) * 1000, 3)
                    for a, b in zip(queries[::2], queries[2::2])
                ],
            }
        )
    result = {
        "captures": captures,
        "requests": requests,
        "query_bytes_match_stock_except_sequence_gateway_tag_trailer": True,
        "adapter_counter_deltas": trial["nordic_verification"][
            "adapter_counter_deltas"
        ],
        "limitations": [
            "Response candidates are temporal associations, not unique transaction matches when stock queries repeat.",
            "The 200ms phase is a post-hoc model anchored to the nearest 07, not an established receive-window boundary.",
            "The earlier MG21 toggle capture includes a successful 10 near phase 1ms; do not hard-code the 169-177ms cluster from this trial.",
            "No receiver-side logs or controlled timing intervention prove why a captured query was not answered.",
            "Original 10/ac samples come from pairing captures, not a controlled stock idle-refresh experiment.",
        ],
    }
    (ROOT / OUTPUT).write_text(json.dumps(result, indent=2) + "\n")
    print(
        json.dumps(
            {
                "output": OUTPUT,
                "captures": len(captures),
                "stock_queries": len(original_queries),
                "mg21_queries": len(primary["queries"]),
                "verified_requests": len(requests),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
