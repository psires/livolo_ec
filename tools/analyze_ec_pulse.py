#!/usr/bin/env python3
"""Measure original EC timing without accessing hardware or changing protocol policy."""
import bisect
import collections
import json
import statistics
from pathlib import Path

from analyze_query_timeouts import load_capture

ROOT = Path(__file__).resolve().parents[1]
GW = "9bf4ea38"
FILES = [
    "captured/ec_no_connection_registered.pcapng",
    "captured/ec_gateway ready to pair.pcapng",
    "captured/ec_device_pairing_sequence.pcapng",
    "captured/ec_set_on_off_on_off.pcapng",
    "captured/ec_1gang_on_off.pcapng",
] + [
    "captured/development/" + name + "-nrf52840.pcapng"
    for name in (
        "gateway-pairing-20261017T182031Z",
        "original-ec-75710834-control-01",
        "original-ec-live-20261025",
        "ec-edge-gateway-off-20261025",
        "ec-edge-switches-off-20261025",
    )
]


def stats(values):
    """Summarize observed values without confidence or causality claims.

    @param values Numeric samples in a common unit; may be empty.
    @return Sample count and, when nonempty, extrema, median and 95th percentile.
    """
    if not values:
        return {"count": 0}
    seq = sorted(values)
    return dict(
        count=len(seq),
        min=seq[0],
        median=statistics.median(seq),
        p95=seq[round((len(seq) - 1) * 0.95)],
        max=seq[-1],
    )


def compact(row):
    """Retain packet identity and timing evidence for an analysis result.

    @param row Decoded capture record containing frame and EC payload fields.
    @return Selected fields, preserving the original capture timestamp in seconds.
    """
    return {
        k: row[k]
        for k in (
            "frame",
            "timestamp",
            "source_wire",
            "destination_wire",
            "subject_wire",
            "family",
            "opcode",
            "data_hex",
            "mac_sequence",
            "trailer_hex",
        )
    }


def summarize(samples):
    """Describe how closely observed packets follow the measured transmit grid.

    @param samples Packet observations with signed grid_error_ms measurements.
    @return Absolute-error statistics and counts within fixed millisecond bounds.
    """
    values = [abs(p["grid_error_ms"]) for p in samples]
    return dict(
        count=len(samples),
        absolute_grid_error_ms=stats(values),
        within_1ms=sum(x <= 1 for x in values),
        within_2ms=sum(x <= 2 for x in values),
        within_5ms=sum(x <= 5 for x in values),
    )


def inspect(path, gateway=GW):
    """Compare gateway traffic with its observed heartbeat timing reference.

    @param path Capture path accepted by load_capture, relative to the repository.
    @param gateway Gateway identity as lowercase wire-order hexadecimal.
    @return Packet evidence and timing summaries without causal claims.
    """
    rows, meta = load_capture(path)
    gw = [p for p in rows if p["source_wire"] == gateway and p["family"] == "8c"]
    beats = [p for p in gw if p["heartbeat_counter"] != ""]
    times = [p["timestamp"] for p in beats]
    gaps = [b - a for a, b in zip(times, times[1:])]
    regular = [g for g in gaps if 9.9 < g < 10.1]
    period = statistics.median(regular) / 50 if regular else 0.2
    samples = []
    seen = {}
    chains = []
    chain = []
    ack_pairs = []
    native_states = []
    for p in rows:
        if (
            p["family"] != "9e"
            or p["opcode"] != "05"
            or p["source_wire"] != p["subject_wire"]
            or p["network_tag"] != "b1"
        ):
            continue
        i = bisect.bisect_right(times, p["timestamp"]) - 1
        if i < 0 or not 0 <= p["timestamp"] - times[i] < 10.05:
            continue
        age = p["timestamp"] - times[i]
        native_states.append(
            dict(
                **compact(p),
                heartbeat_frame=beats[i]["frame"],
                grid_offset_ms=((age + period / 2) % period - period / 2) * 1000,
            )
        )
    for index, p in enumerate(rows):
        if p["source_wire"] != gateway or p["family"] != "8c":
            continue
        if p["heartbeat_counter"] != "":
            continue
        old = None
        if p["opcode"] == "02":
            key = (p["subject_wire"], p["data_hex"])
            old = seen.get(key)
            seen[key] = p
        i = bisect.bisect_right(times, p["timestamp"]) - 1
        if i < 0 or not 0 <= p["timestamp"] - times[i] < 10.05:
            continue
        age = p["timestamp"] - times[i]
        slot = round(age / period)
        trailer = bytes.fromhex(p["trailer_hex"])
        phase = trailer[2] + int.from_bytes(trailer[:2], "little") / 65500
        kind = (
            "output"
            if p["opcode"] == "02"
            else (
                "join_response"
                if p["opcode"] == "fa" and p["ack_of_opcode"] == "01"
                else (
                    "reactive_ack" if p["opcode"] in ("fa", "08") else "scheduled_other"
                )
            )
        )
        s = dict(
            **compact(p),
            kind=kind,
            heartbeat_frame=beats[i]["frame"],
            heartbeat_age_ms=age * 1000,
            slot=slot,
            grid_error_ms=(age - slot * period) * 1000,
            encoded_phase_seconds=phase,
            encoded_grid_error_ms=(phase - round(phase / 0.2) * 0.2) * 1000,
            encoded_minus_observed_ms=(phase - age) * 1000,
        )
        if kind == "output":
            s["first_observed_attempt"] = not (
                old and p["timestamp"] - old["timestamp"] < 4
            )
            s["previous_same_payload_frame"] = old["frame"] if old else None
            s["retry_gap_ms"] = (
                (p["timestamp"] - old["timestamp"]) * 1000
                if not s["first_observed_attempt"]
                else None
            )
            if s["first_observed_attempt"]:
                prior = rows[index - 1] if index else None
                s["previous_observed_packet"] = compact(prior) if prior else None
                s["previous_packet_gap_ms"] = (
                    (p["timestamp"] - prior["timestamp"]) * 1000 if prior else None
                )
                # These are absence observations at one receiver, not proof that
                # no cloud/app event or uncaptured RF packet triggered the command.
                s["packets_in_preceding_50ms"] = [
                    compact(q)
                    for q in rows[max(0, index - 100) : index]
                    if 0 < p["timestamp"] - q["timestamp"] < 0.05
                ]
        if kind == "reactive_ack":
            matches = [
                q
                for q in rows[max(0, index - 100) : index]
                if q["family"] == "9e"
                and q["source_wire"] == p["destination_wire"]
                and q["subject_wire"] == p["subject_wire"]
                and q["mac_sequence"] == p["mac_sequence"]
                and q["opcode"] == ("08" if p["opcode"] == "08" else p["ack_of_opcode"])
                and 0 < p["timestamp"] - q["timestamp"] < 0.1
            ]
            if matches:
                q = matches[-1]
                pair = dict(
                    request=compact(q),
                    reply=compact(p),
                    latency_ms=(p["timestamp"] - q["timestamp"]) * 1000,
                )
                ack_pairs.append(pair)
                s["matched_incoming_frame"] = q["frame"]
        samples.append(s)
    outputs = [s for s in samples if s["kind"] == "output"]
    # Preserve each same-payload repeat chain, including routed gateway originals.
    for p in [q for q in gw if q["opcode"] == "02"]:
        if chain and (
            (p["subject_wire"], p["data_hex"])
            != (chain[-1]["subject_wire"], chain[-1]["data_hex"])
            or p["timestamp"] - chain[-1]["timestamp"] >= 4
        ):
            if len(chain) > 1:
                chains.append(chain)
            chain = []
        chain.append(compact(p))
    if len(chain) > 1:
        chains.append(chain)
    for chain in chains:
        first = chain[0]["timestamp"]
        for p in chain:
            p["relative_200ms_slot"] = round((p["timestamp"] - first) / period)
    return dict(
        capture=meta,
        heartbeat=dict(
            count=len(beats),
            regular_gap_seconds=stats(regular),
            grid_period_ms=period * 1000,
            irregular_gaps_seconds=[g for g in gaps if not 9.9 < g < 10.1],
            packets=[compact(p) | {"counter": p["heartbeat_counter"]} for p in beats],
        ),
        by_kind={
            k: summarize([s for s in samples if s["kind"] == k])
            for k in sorted({s["kind"] for s in samples})
        },
        by_opcode={
            op: summarize([s for s in samples if s["opcode"] == op])
            for op in sorted({s["opcode"] for s in samples})
        },
        first_output_attempts=summarize(
            [s for s in outputs if s["first_observed_attempt"]]
        ),
        retry_output_attempts=summarize(
            [s for s in outputs if not s["first_observed_attempt"]]
        ),
        acknowledgements=dict(
            latency_ms=stats([a["latency_ms"] for a in ack_pairs]), pairs=ack_pairs
        ),
        native_state_grid=dict(
            offset_ms=stats([p["grid_offset_ms"] for p in native_states]),
            packets=native_states,
        ),
        repeated_output_chains=chains,
        samples=samples,
    )


def main():
    """Publish the fixed capture corpus's pulse analysis and local comparison."""
    captures = [inspect(f) for f in FILES]
    all_samples = [
        s | {"capture": c["capture"]["capture"]} for c in captures for s in c["samples"]
    ]
    outputs = [s for s in all_samples if s["kind"] == "output"]
    first = [s for s in outputs if s["first_observed_attempt"]]
    scheduled = [
        s
        for s in all_samples
        if s["kind"] in ("output", "scheduled_other", "join_response")
    ]
    local = inspect(
        "captured/development/output-retry400-20261101-nrf52840.pcapng", "2a992b12"
    )
    aggregate = dict(
        outputs=summarize(outputs),
        first_outputs=summarize(first),
        scheduled=summarize(scheduled),
        first_output_age_ms=stats([s["heartbeat_age_ms"] for s in first]),
        first_output_slots=sorted({s["slot"] for s in first}),
        first_outputs_without_observed_packet_in_50ms=sum(
            not s["packets_in_preceding_50ms"] for s in first
        ),
        ack_latency_ms=stats(
            [a["latency_ms"] for c in captures for a in c["acknowledgements"]["pairs"]]
        ),
        output_encoded_phase_grid_error_ms=stats(
            [abs(s["encoded_grid_error_ms"]) for s in outputs]
        ),
        output_encoded_phase_exact_grid=sum(
            abs(s["encoded_grid_error_ms"]) < 0.001 for s in outputs
        ),
    )
    result = dict(
        aggregate=aggregate,
        captures=captures,
        local_output_comparison=local,
        caveats=[
            "One receiver can miss packets; no empty slot proves radio silence.",
            "First observed attempts may exclude an earlier uncaptured attempt.",
            "The grid is measured relative to the preceding captured heartbeat; ages above 10.05 seconds are excluded.",
            "Per-capture period uses median regular heartbeat interval divided by 50 to account for clock drift.",
            "Slot alignment does not by itself prove a receiver duty cycle, exclusive timeslots, or causality.",
        ],
    )
    target = ROOT / "analysis/development/ec-pulse-20261101.json"
    target.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(aggregate, indent=2))
    print("Local outputs:", json.dumps(local["by_kind"].get("output")))


if __name__ == "__main__":
    main()
