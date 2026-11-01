#!/usr/bin/env python3
"""Correlate Nordic gateway transactions with an independent Sonoff capture.

Run from the repository root. --partial permits a copied in-progress capture;
final qualification also requires the completed ten-minute live record.
"""
import argparse
import bisect
import collections
import json
from pathlib import Path
import statistics
import sys

from analyze_query_timeouts import load_capture
from analyze_stable_uart_rf import acknowledgement_summary
from analyze_ec_pulse import stats
from decode_ec import decode_frame, read_pcapng

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "host"))
from livolod.protocol import decode as host_decode


def main():
    """Validate retained results, retry bounds, RF grid and observed stability."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--capture",
        type=Path,
        default=Path("captured/development/nrf52840-012-20261101-sonoff.pcapng"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("analysis/development/nrf52840-012-20261101.json"),
    )
    parser.add_argument("--partial", action="store_true")
    args = parser.parse_args()
    root = Path("analysis/development/nrf52840-012-20261101")
    live = json.loads((root / "live.json").read_text())
    if not args.partial:
        assert live["completed"] and live["finished_at"] - live["started_at"] >= 600
    end = live.get("finished_at", live["samples"][-1]["time"])
    rows, metadata = load_capture(str(args.capture))
    beats = [
        p
        for p in rows
        if p["source_wire"] == "2a992b12" and p["heartbeat_counter"] != ""
    ]
    times = [p["timestamp"] for p in beats]
    # Derive relative clock rate from consecutive ten-second heartbeats. This
    # corrects the sniffer/gateway oscillator difference, not individual slots.
    intervals = [b - a for a, b in zip(times, times[1:]) if 9.9 < b - a < 10.1]
    period = statistics.median(intervals) / 50
    commands = []
    for q in live["requests"]:
        if "result" not in q:
            continue
        t = q["transaction"]
        result = q["result"]
        token = result["token"]
        data = bytes((1, 0, q["mask"], q["value"], 0, 0, 0, token)).hex()
        packets = [
            p
            for p in rows
            if q["started_at"] - 0.25 <= p["timestamp"] <= q["finished_at"] + 0.25
            and p["source_wire"] == "2a992b12"
            and p["subject_wire"] == q["device"]
            and p["family"] == "8c"
            and p["opcode"] == "02"
            and p["data_hex"] == data
        ]
        reports = [
            p
            for p in rows
            if q["started_at"] - 0.25 <= p["timestamp"] <= q["finished_at"] + 0.25
            and p["raw_hex"] == t["report"]
        ]
        assert t["output"] == t["grid"] == "1"
        assert (int(t["deadline"]) - int(t["started"])) & 0xFFFFFFFF == 4000000
        assert len(packets) <= int(t["sent"]) <= int(t["attempts"]) <= 6
        assert int(result["output_attempts"]) == int(t["attempts"])
        if result["status"] == "confirmed":
            r = decode_frame(bytes.fromhex(t["report"]))
            assert r["source_wire"] == r["subject_wire"] == q["device"]
            assert r["network_tag"] == "a7" and r["token"] == token
            assert r["mask_candidate"] == q["mask"]
            assert r["state_bits"] & q["mask"] == q["value"]
            assert ((int(t["rx_ticks"]) - int(t["entry_ticks"])) & 0xFFFFFFFF) < 4000000
            if reports:
                assert not any(
                    p["timestamp"] > reports[0]["timestamp"] for p in packets
                )
        errors = []
        for p in packets:
            index = bisect.bisect_right(times, p["timestamp"]) - 1
            if index >= 0 and p["timestamp"] - times[index] < 10.05:
                age = p["timestamp"] - times[index]
                errors.append(((age + period / 2) % period - period / 2) * 1000)
        commands.append(
            dict(
                **q,
                packets=packets,
                retained_report_packets=reports,
                grid_errors_ms=errors,
                retry_gaps_ms=[
                    (b["timestamp"] - a["timestamp"]) * 1000
                    for a, b in zip(packets, packets[1:])
                ],
            )
        )
    controls = [q for q in commands if q["cycle"] != "restore"]
    assert len(controls) == 24 and live["comparison_count"] == 24
    samples = (
        [live["before"]]
        + live["samples"]
        + ([live["after"]] if "after" in live else [])
    )
    assert len({s["diagnostics"]["adapter"]["session"] for s in samples}) == 1
    assert all(s["ready"] and s["mqtt_connected"] for s in samples)
    first, last = (
        samples[0]["diagnostics"]["adapter"],
        samples[-1]["diagnostics"]["adapter"],
    )
    names = (
        "rx_drops",
        "uart_drops",
        "meta_drops",
        "input_errors",
        "radio_drops",
        "radio_runtime_errors",
        "radio_tx_timeouts",
        "uart_rx_errors",
        "uart_rx_overruns",
    )
    deltas = {k: int(last[k]) - int(first[k]) for k in names}
    assert all(v == 0 for v in deltas.values()), deltas
    host = {
        k: samples[-1]["diagnostics"][k] - samples[0]["diagnostics"][k]
        for k in ("rx_gaps", "invalid_rx", "ack_failures")
    }
    assert host["rx_gaps"] == host["ack_failures"] == 0
    window = [p for p in rows if live["started_at"] <= p["timestamp"] <= end]
    hb = [p for p in beats if live["started_at"] <= p["timestamp"] <= end]
    summary = dict(
        total=len(controls),
        confirmed=sum(q["result"]["status"] == "confirmed" for q in controls),
        attempts=dict(
            collections.Counter(str(q["result"]["output_attempts"]) for q in controls)
        ),
        captured=sum(len(q["packets"]) for q in controls),
        reported_sent=sum(int(q["transaction"]["sent"]) for q in controls),
        retained_reports_captured=sum(
            bool(q["retained_report_packets"]) for q in controls
        ),
        retry_ms=stats([g for q in controls for g in q["retry_gaps_ms"]]),
        grid_abs_ms=stats([abs(g) for q in controls for g in q["grid_errors_ms"]]),
        heartbeat_ms=stats(
            [(b["timestamp"] - a["timestamp"]) * 1000 for a, b in zip(hb, hb[1:])]
        ),
        live_seconds=end - live["started_at"],
        live_counter_deltas=deltas,
        host_counter_deltas=host,
        air_error_deltas={
            k: int(last[k]) - int(first[k])
            for k in ("radio_rx_errors", "radio_crc_errors", "radio_filter_errors")
        },
        original_ec_heartbeats=sum(
            p["source_wire"] == "9bf4ea38" and p["heartbeat_counter"] != ""
            for p in window
        ),
    )
    evidence = dict(
        partial=args.partial,
        summary=summary,
        capture=metadata,
        commands=commands,
        acknowledgements=acknowledgement_summary(window, "2a992b12"),
        live=live,
    )
    decoder_counts = collections.Counter()
    for _, timestamp, raw, _ in read_pcapng(args.capture):
        if live["started_at"] <= timestamp <= end:
            try:
                host_decode(raw)
                decoder_counts["accepted"] += 1
            except ValueError as error:
                decoder_counts[str(error)] += 1
    evidence["capture_host_decode_window"] = dict(decoder_counts)
    for label in (
        "image-final",
        "final-dry",
        "final-scheduler-dry",
        "final-usb-integrity",
        "final-dfu-check",
    ):
        evidence[label] = json.loads((root / (label + ".json")).read_text())
    assert (
        evidence["final-usb-integrity"]["passed"]
        and evidence["final-dfu-check"]["passed"]
    )
    if not args.partial:
        evidence["deployment"] = json.loads((root / "final.json").read_text())
        preparation = json.loads((root / "preparation.json").read_text())
        assert (
            evidence["deployment"]["diagnostics"]["query_timing"]
            == preparation["before"]["diagnostics"]["query_timing"]
        )
        assert evidence["deployment"]["only_env_change_is_radio"]
        assert all(evidence["deployment"]["preserved_files"].values())
        evidence["other_radios"] = json.loads((root / "other-radios.json").read_text())
        evidence["sniffer"] = json.loads((root / "sniffer.json").read_text())
        assert all(
            v == 0
            for radio in evidence["other_radios"].values()
            for v in radio["transmission_deltas"].values()
        )
        assert all(v == 0 for v in evidence["sniffer"]["counter_deltas"].values())
        assert (
            evidence["sniffer"]["crc_errors"]
            == evidence["sniffer"]["sequence_gaps"]
            == 0
        )
        assert {
            d["id"]: d["state"]["state_bits"] for d in live["after"]["devices"]
        } == live["initial_bits"]
    args.output.write_text(json.dumps(evidence, indent=2) + "\n")
    print(
        json.dumps(
            dict(
                summary=summary,
                acks={
                    k: v
                    for k, v in evidence["acknowledgements"].items()
                    if k != "packets"
                },
            ),
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
