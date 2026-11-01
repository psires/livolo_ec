#!/usr/bin/env python3
"""Offline RF comparison of the stable-UART Sonoff run and original EC captures.

No radio access, query-policy changes, or dependencies outside the standard library.
Run from any directory; results retain capture packet numbers and input hashes.
Optional local serial logs corroborate RX observations and are hashed when present.
"""
import bisect
import collections
import hashlib
import json
import statistics
from pathlib import Path

from analyze_query_timeouts import load_capture
from decode_ec import decode_frame, read_pcapng

ROOT = Path(__file__).resolve().parents[1]
BASE = "captured/development/"
CAPTURE = BASE + "sonoff-uart-dma-20261025-nrf52840.pcapng"
EVIDENCE = "analysis/development/sonoff-uart-dma-20261025.json"
OUTPUT = "analysis/development/sonoff-stable-uart-rf-20261025.json"
GATEWAY, DEVICE = "2a992b12", "46715621"


def stats(values):
    """Describe the spread of observed samples without inferring reliability.

    @param values Numeric samples sharing one unit; may be empty.
    @return Sample count and, when nonempty, minimum, median and maximum.
    """
    return (
        dict(
            count=len(values),
            min=min(values),
            median=statistics.median(values),
            max=max(values),
        )
        if values
        else dict(count=0)
    )


def phase(row):
    """Interpret a gateway timing trailer as heartbeat-relative seconds.

    @param row Decoded gateway packet containing the three-byte trailer_hex field.
    @return Modeled elapsed seconds using the observed 65500-unit fraction.
    """
    data = bytes.fromhex(row["trailer_hex"])
    return data[2] + int.from_bytes(data[:2], "little") / 65500


def packet(row):
    """Preserve packet evidence needed to audit a transaction comparison.

    @param row Decoded capture record with EC fields and receiver metadata.
    @return Selected fields, preserving capture timestamps in seconds.
    """
    keys = (
        "frame",
        "timestamp",
        "source_wire",
        "destination_wire",
        "subject_wire",
        "family",
        "opcode",
        "data_hex",
        "raw_hex",
        "rssi_dbm",
        "lqi",
        "mac_sequence",
        "report_counter",
        "token",
        "state_bits",
        "route_flags",
        "route_a_wire",
        "route_b_wire",
        "subject_suffix",
    )
    return {k: row[k] for k in keys}


def serial_rx(folder):
    """Recover matching peer observations from an optional local UART log.

    @param folder Experiment directory name below the repository's .local folder.
    @return Decoded peer records and log provenance, or an unavailable marker.
    """
    path = ROOT / ".local" / folder / "trial.uart.log"
    if not path.exists():
        return [], dict(available=False)
    rx = []
    for line in path.read_text().splitlines():
        fields = line.split()
        if len(fields) != 9 or fields[1:3] != ["<", "RX"]:
            continue
        try:
            decoded = decode_frame(bytes.fromhex(fields[-1]))
        except ValueError:
            continue
        if decoded["source_wire"] == DEVICE and decoded["network_tag"] == "a7":
            rx.append(dict(timestamp=float(fields[0]), **decoded))
    return rx, dict(
        available=True,
        path=str(path.relative_to(ROOT)),
        sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
        peer_rx=len(rx),
    )


def acknowledgement_summary(rows, gateway):
    """Measure observed gateway acknowledgements against heartbeat and request times.

    @param rows Chronologically ordered decoded capture records.
    @param gateway Gateway identity as lowercase wire-order hexadecimal.
    @return Packet evidence and millisecond phase-error/response-delay summaries.
    """
    beats = [
        r for r in rows if r["source_wire"] == gateway and r["heartbeat_counter"] != ""
    ]
    samples = []
    for r in rows:
        if (
            r["source_wire"] != gateway
            or r["opcode"] not in ("08", "fa")
            or r["heartbeat_counter"] != ""
        ):
            continue
        previous = [h for h in beats if h["timestamp"] < r["timestamp"]]
        h = previous[-1] if previous else None
        # A missed heartbeat must not be interpreted as a ten-second phase error.
        age = r["timestamp"] - h["timestamp"] if h else None
        eligible = age is not None and age < 10.05
        prior = [
            p
            for p in rows
            if p["family"] == "9e"
            and p["source_wire"] == r["destination_wire"]
            and p["network_tag"] == r["network_tag"]
            and p["mac_sequence"] == r["mac_sequence"]
            and p["opcode"] == ("08" if r["opcode"] == "08" else r["ack_of_opcode"])
            and 0 < r["timestamp"] - p["timestamp"] < 0.05
        ]
        samples.append(
            dict(
                frame=r["frame"],
                opcode=r["opcode"],
                trailer=r["trailer_hex"],
                heartbeat=h["frame"] if h else None,
                phase_error_ms=(phase(r) - age) * 1000 if eligible else None,
                response_delay_ms=(
                    (r["timestamp"] - prior[-1]["timestamp"]) * 1000 if prior else None
                ),
            )
        )
    return dict(
        count=len(samples),
        zero_trailers=sum(s["trailer"] == "000000" for s in samples),
        phase_error_ms=stats(
            [s["phase_error_ms"] for s in samples if s["phase_error_ms"] is not None]
        ),
        response_delay_ms=stats(
            [
                s["response_delay_ms"]
                for s in samples
                if s["response_delay_ms"] is not None
            ]
        ),
        packets=samples,
    )


def original_comparison(relative):
    """Characterize reply candidates around original-gateway queries.

    @param relative Capture path relative to the repository root.
    @return Bounded observation windows and ACK evidence, not logical outcomes.
    """
    rows, meta = load_capture(relative)
    queries = [
        r
        for r in rows
        if r["source_wire"] == "9bf4ea38"
        and r["family"] == "8c"
        and r["opcode"] in ("10", "ac")
    ]
    measured = []
    assert all(
        q["data_hex"] == "0000000000000001"
        and q["route_flags"] == "1e"
        and q["destination_wire"] == q["subject_wire"]
        for q in queries
    )
    for q in queries:
        later = [
            p
            for p in queries
            if p["subject_wire"] == q["subject_wire"]
            and p["timestamp"] > q["timestamp"]
        ]
        stop = min(q["timestamp"] + 4, later[0]["timestamp"] if later else float("inf"))
        ps = [
            p
            for p in rows
            if q["timestamp"] < p["timestamp"] < stop
            and p["network_tag"] == q["network_tag"]
            and p["family"] == "9e"
            and p["subject_wire"] == q["subject_wire"]
            and p["destination_wire"] in ("ffffffff", q["source_wire"])
        ]
        ack = [
            p for p in ps if p["opcode"] == "fa" and p["ack_of_opcode"] == q["opcode"]
        ]
        state = [p for p in ps if p["opcode"] == "05"]
        measured.append(
            dict(
                query=packet(q),
                window_end=stop,
                replies=[packet(p) for p in ack],
                states=[packet(p) for p in state],
                forwarded_state_packets=[
                    p["frame"] for p in state if p["source_wire"] != p["subject_wire"]
                ],
                first_state_delay_ms=(
                    (state[0]["timestamp"] - q["timestamp"]) * 1000 if state else None
                ),
                phase_grid_distance_ms=abs(phase(q) - round(phase(q) / 0.2) * 0.2)
                * 1000,
            )
        )
    return dict(
        capture=meta,
        query_counts=dict(collections.Counter(q["opcode"] for q in queries)),
        candidate_windows_with_reply=sum(bool(m["replies"]) for m in measured),
        candidate_windows_with_state=sum(bool(m["states"]) for m in measured),
        candidate_windows_with_neither=sum(
            not m["states"] and not m["replies"] for m in measured
        ),
        queries=measured,
        acknowledgements=acknowledgement_summary(rows, "9bf4ea38"),
    )


def main():
    """Publish the stable-UART experiment's RF evidence and baseline comparison."""
    rows, meta = load_capture(CAPTURE)
    source = json.loads((ROOT / EVIDENCE).read_text())
    by_frame = {r["frame"]: r for r in rows}
    peer = [r for r in rows if r["source_wire"] == DEVICE and r["network_tag"] == "a7"]
    beats = [
        r for r in rows if r["source_wire"] == GATEWAY and r["heartbeat_counter"] != ""
    ]
    announcements = [r for r in peer if r["opcode"] == "07"]
    all_frames = [(n, t) for n, t, raw, metadata in read_pcapng(ROOT / CAPTURE)]
    times = [t for n, t in all_frames]
    entries = []
    log_metadata = []
    for run, folder in zip(
        source["runs"],
        (
            "sonoff-uart-dma-20261025",
            "sonoff-uart-load-20261025",
            "sonoff-uart-load-repeat-20261025",
            "sonoff-uart-load-full-20261025",
        ),
    ):
        rx, info = serial_rx(folder)
        log_metadata.append(dict(run=run["label"], **info))
        for ent, check in zip(run["trial"]["requests"], run["checks"]):
            entries.append(
                (run["label"], ent, check["packets"], check["cca_retries"], rx)
            )
    final = source["final_query"]["evidence"]["requests"][0]
    entries.append(
        (
            "final_health",
            dict(final, number=1, variant="paired"),
            source["final_query"]["captured_query_packets"],
            None,
            [],
        )
    )
    entries.sort(key=lambda v: v[1]["started_at"])
    transactions, attempts = [], []
    for idx, (label, ent, numbers, cca, rx) in enumerate(entries):
        qs = [by_frame[n] for n in numbers]
        start, end = ent["started_at"] - 0.25, ent["finished_at"] + 0.25
        ps = [p for p in peer if start <= p["timestamp"] <= end]
        serial = [p for p in rx if start <= p["timestamp"] <= end]
        state = [p for p in ps if p["opcode"] == "05"]
        fa = [p for p in ps if p["opcode"] == "fa"]
        good = ent["result"]["status"] == "confirmed"
        assert bool(state) == good
        if not good:
            assert not any(p["ack_of_opcode"] == "ac" for p in fa)
            assert any(p["opcode"] in ("07", "08") for p in ps)
        assert all(
            q["checksum_ok"]
            and q["source_wire"] == GATEWAY
            and q["network_tag"] == "a7"
            and q["destination_wire"] == q["subject_wire"] == DEVICE
            and q["subject_suffix"] == "0002"
            and q["route_flags"] == "1e"
            and q["data_hex"] == "0000000000000001"
            for q in qs
        )
        if not good:
            assert not any(
                p["opcode"] == "05"
                or (p["opcode"] == "fa" and p["ack_of_opcode"] == "ac")
                for p in serial
            )
        next_start = (
            entries[idx + 1][1]["started_at"] - 0.25
            if idx + 1 < len(entries)
            else times[-1]
        )
        late = [
            p["frame"]
            for p in peer
            if end < p["timestamp"] < next_start and p["opcode"] == "05"
        ]
        tx = dict(
            run=label,
            number=ent["number"],
            variant=ent["variant"],
            status=ent["result"]["status"],
            query_packets=numbers,
            cca_retries=cca,
            replies=[packet(p) for p in fa],
            states=[packet(p) for p in state],
            other_peer_packets=[
                packet(p) for p in ps if p["opcode"] not in ("05", "fa")
            ],
            serial_peer_packets=[
                dict(
                    timestamp=p["timestamp"],
                    opcode=p["opcode"],
                    ack_of_opcode=p["ack_of_opcode"],
                    raw_hex=p["raw_hex"],
                )
                for p in serial
            ],
            later_state_packets_before_next_test=late,
        )
        transactions.append(tx)
        for qidx, q in enumerate(qs):
            if q["opcode"] != "ac":
                continue
            following = [p for p in qs[qidx + 1 :] if p["opcode"] == "ac"]
            stop = min(
                q["timestamp"] + 4.012,
                following[0]["timestamp"] if following else float("inf"),
                end,
            )
            candidates = [p for p in ps if q["timestamp"] < p["timestamp"] < stop]
            replies = [
                p
                for p in candidates
                if p["opcode"] == "fa" and p["ack_of_opcode"] == "ac"
            ]
            states = [p for p in candidates if p["opcode"] == "05"]
            h = [h for h in beats if h["timestamp"] < q["timestamp"]][-1]
            ann = min(announcements, key=lambda a: abs(a["timestamp"] - q["timestamp"]))
            neighbors = all_frames[
                bisect.bisect_left(times, q["timestamp"] - 0.005) : bisect.bisect_right(
                    times, q["timestamp"] + 0.005
                )
            ]
            neighbor_ids = [n for n, t in neighbors if n != q["frame"]]
            attempts.append(
                dict(
                    run=label,
                    number=ent["number"],
                    variant=ent["variant"],
                    query=packet(q),
                    has_state=bool(states),
                    reply_packets=[p["frame"] for p in replies],
                    state_packets=[p["frame"] for p in states],
                    first_reply_delay_ms=(
                        (replies[0]["timestamp"] - q["timestamp"]) * 1000
                        if replies
                        else None
                    ),
                    first_state_delay_ms=(
                        (states[0]["timestamp"] - q["timestamp"]) * 1000
                        if states
                        else None
                    ),
                    heartbeat_frame=h["frame"],
                    heartbeat_age_ms=(q["timestamp"] - h["timestamp"]) * 1000,
                    heartbeat_phase_ms=((q["timestamp"] - h["timestamp"]) % 0.2) * 1000,
                    phase_error_ms=(phase(q) - (q["timestamp"] - h["timestamp"]))
                    * 1000,
                    announcement_frame=ann["frame"],
                    announcement_distance_s=q["timestamp"] - ann["timestamp"],
                    announcement_phase_ms=((q["timestamp"] - ann["timestamp"]) % 0.2)
                    * 1000,
                    other_frames_within_5ms=neighbor_ids,
                    original_ec_frames_within_5ms=[
                        n
                        for n in neighbor_ids
                        if n in by_frame and by_frame[n]["network_tag"] == "b1"
                    ],
                )
            )
    assert (
        len(transactions) == 73
        and sum(t["status"] == "confirmed" for t in transactions) == 62
    )
    assert len(attempts) == 130 and sum(a["has_state"] for a in attempts) == 62
    failed = [t for t in transactions if t["status"] == "unconfirmed"]
    assert len(failed) == 11 and all(
        not t["later_state_packets_before_next_test"] for t in failed
    )
    native_states = [p for p in peer if p["opcode"] == "05"]
    unique = []
    for p in native_states:
        if not unique or p["data_hex"] != unique[-1]["data_hex"]:
            unique.append(p)
    assert len(unique) == 62 and {p["token"] for p in unique} == {0x54}
    assert all(
        (b["report_counter"] - a["report_counter"]) % 256 == 1
        for a, b in zip(unique, unique[1:])
    )
    outcome_stats = {}
    for outcome in (True, False):
        selected = [a for a in attempts if a["has_state"] == outcome]
        outcome_stats["state_observed" if outcome else "no_state_observed"] = dict(
            attempts=len(selected),
            query_rssi_at_nordic=stats([a["query"]["rssi_dbm"] for a in selected]),
            phase_error_ms=stats([a["phase_error_ms"] for a in selected]),
            with_neighbor_within_5ms=sum(
                bool(a["other_frames_within_5ms"]) for a in selected
            ),
            with_original_ec_neighbor_within_5ms=sum(
                bool(a["original_ec_frames_within_5ms"]) for a in selected
            ),
            reply_delay_ms=stats(
                [
                    a["first_reply_delay_ms"]
                    for a in selected
                    if a["first_reply_delay_ms"] is not None
                ]
            ),
            state_delay_ms=stats(
                [
                    a["first_state_delay_ms"]
                    for a in selected
                    if a["first_state_delay_ms"] is not None
                ]
            ),
        )
    bins = []
    for lo in range(0, 200, 20):
        selected = [a for a in attempts if lo <= a["heartbeat_phase_ms"] < lo + 20]
        bins.append(
            dict(
                phase_ms=[lo, lo + 20],
                attempts=len(selected),
                state_observed=sum(a["has_state"] for a in selected),
            )
        )
    drift = []
    for a, b in zip(announcements, announcements[1:]):
        if 1792928180 < b["timestamp"] < 1792928300:
            h = [h for h in beats if h["timestamp"] < b["timestamp"]][-1]
            drift.append(
                dict(
                    frame=b["frame"],
                    interval_s=b["timestamp"] - a["timestamp"],
                    heartbeat_phase_ms=((b["timestamp"] - h["timestamp"]) % 0.2) * 1000,
                    data=b["data_hex"],
                )
            )
    originals = [
        original_comparison(p)
        for p in (
            BASE + "ec-edge-gateway-off-20261025-nrf52840.pcapng",
            BASE + "gateway-pairing-20261017T182031Z-nrf52840.pcapng",
            "captured/ec_device_pairing_sequence.pcapng",
            BASE + "original-ec-live-20261025-nrf52840.pcapng",
        )
    ]
    result = dict(
        capture=meta,
        evidence=dict(
            path=EVIDENCE,
            sha256=hashlib.sha256((ROOT / EVIDENCE).read_bytes()).hexdigest(),
        ),
        methodology="Each local AC is matched to native replies until the next AC, 4.012 seconds, or transaction end +250ms, whichever is earliest. Transactions include all harness outcomes and the final health query. Original windows end at next same-peer query or four seconds and are candidates, not logical transactions.",
        unchanged_policy=source["limits"],
        unchanged_source_hashes=source["unchanged_timing_source_sha256"],
        serial_logs=log_metadata,
        summary=dict(
            transactions=73,
            confirmed=62,
            timeouts=11,
            ac_attempts=130,
            query_frames_in_timed_out_transactions=sum(
                len(t["query_packets"]) for t in failed
            ),
            all_timeouts_have_peer_activity=True,
            no_late_state_after_timeout_before_next_test=True,
            state_counters=[p["report_counter"] for p in unique],
            state_counter_gaps=0,
            timed_out_transactions_with_10_reply=[
                dict(
                    run=t["run"],
                    number=t["number"],
                    packets=[p["frame"] for p in t["replies"]],
                )
                for t in failed
                if t["replies"]
            ],
            outcomes=outcome_stats,
            heartbeat_phase_bins=bins,
        ),
        transactions=transactions,
        ac_attempts=attempts,
        announcement_drift=drift,
        local_acknowledgements=acknowledgement_summary(rows, GATEWAY),
        concurrent_original_acknowledgements=acknowledgement_summary(rows, "9bf4ea38"),
        original_captures=originals,
        limitations=[
            "Receive silence is not proof the switch failed to receive; the reply could be lost at both observers.",
            "Sniffer RSSI is not signal strength at the switch. Five-ms proximity is not collision detection, and hidden/interfering traffic may be absent.",
            "Repeated attempts and adjacent transactions are correlated; phase bins are descriptive, not an independently validated receive-window model.",
            "Nearest announcement may follow a query; it is a retrospective phase proxy, not a usable guaranteed scheduling anchor.",
            "Original EC has six peers and forwarding, different devices and workload; its candidate windows cannot establish a comparable logical timeout rate.",
            "Automatic ACK trailer mismatch is observed; causality requires a separate controlled firmware experiment.",
        ],
    )
    assert all(
        hashlib.sha256((ROOT / p).read_bytes()).hexdigest() == h
        for p, h in result["unchanged_source_hashes"].items()
    )
    (ROOT / OUTPUT).write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result["summary"], indent=2))


if __name__ == "__main__":
    main()
