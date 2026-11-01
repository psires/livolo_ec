"""Validate TI CC2652P control timing and transport evidence without live hardware."""

import bisect, collections, json, statistics, sys
from pathlib import Path

sys.path.insert(0, "tools")
sys.path.insert(0, "host")
from analyze_query_timeouts import load_capture
from decode_ec import decode_frame, read_pcapng
from livolod.protocol import decode as host_decode
from analyze_stable_uart_rf import acknowledgement_summary
from analyze_ec_pulse import stats

root = Path("analysis/development/ti-012-20261101")
live = json.loads((root / "live.json").read_text())
repeat = json.loads((root / "repeat.json").read_text())
integrity = json.loads((root / "integrity.json").read_text())
assert integrity["passed"] and integrity["samples"] == integrity["completed"] == 6000
assert live["completed"] and live["finished_at"] - live["started_at"] >= 600
rows, meta = load_capture("captured/development/ti-012-20261101-nrf52840.pcapng")
beats = [
    p for p in rows if p["source_wire"] == "2a992b12" and p["heartbeat_counter"] != ""
]
times = [p["timestamp"] for p in beats]
period = (
    statistics.median([b - a for a, b in zip(times, times[1:]) if 9.9 < b - a < 10.1])
    / 50
)
commands = []
for q in live["requests"] + repeat["requests"]:
    run = "initial" if q in live["requests"] else "settled"
    token = q["result"]["token"]
    data = bytes((1, 0, q["mask"], q["value"], 0, 0, 0, token)).hex()
    ps = [
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
        and p["source_wire"] == p["subject_wire"] == q["device"]
        and p["family"] == "9e"
        and p["opcode"] == "05"
        and p["network_tag"] == "a7"
        and p["destination_wire"] in ("ffffffff", "2a992b12")
        and p["token"] == token
        and int(p["data_hex"][4:6], 16) == q["mask"]
        and p["state_bits"] & q["mask"] == q["value"]
    ]
    t = q["transaction"]
    assert int(q["result"]["output_attempts"]) <= 6
    if run in ("initial", "settled"):
        assert t["output"] == t["grid"] == "1"
        assert ((int(t["deadline"]) - int(t["started"])) & 0xFFFFFFFF) == 4 * 65536
        assert len(ps) <= int(t["sent"]) <= int(t["attempts"]) <= 6
        if q["result"]["status"] == "confirmed":
            retained = decode_frame(bytes.fromhex(t["report"]))
            assert retained["source_wire"] == retained["subject_wire"] == q["device"]
            assert retained["token"] == token
            assert retained["mask_candidate"] == q["mask"]
            assert retained["state_bits"] & q["mask"] == q["value"]
            assert (
                0
                <= ((int(t["rx_ticks"]) - int(t["entry_ticks"])) & 0xFFFFFFFF)
                < 4 * 65536
            )
            # Nordic may hear a fresh report that TI did not receive. Compare
            # against the exact raw report retained by the MCU, when captured.
            received = [p for p in reports if p["raw_hex"] == t["report"]]
            if received:
                assert not any(p["timestamp"] > received[0]["timestamp"] for p in ps)
    errors = []
    for p in ps:
        i = bisect.bisect_right(times, p["timestamp"]) - 1
        if i >= 0 and p["timestamp"] - times[i] < 10.05:
            age = p["timestamp"] - times[i]
            errors.append(((age + period / 2) % period - period / 2) * 1000)
    native = [
        p
        for p in rows
        if q["started_at"] - 0.25 <= p["timestamp"] <= q["finished_at"] + 0.25
        and p["source_wire"] == p["subject_wire"] == q["device"]
        and p["family"] == "9e"
        and p["opcode"] == "05"
    ]
    commands.append(
        dict(
            **q,
            run=run,
            packets=ps,
            reports=reports,
            native_reports=native,
            retained_report_packets=[p for p in reports if p["raw_hex"] == t["report"]],
            grid_errors_ms=errors,
            retry_gaps_ms=[
                (b["timestamp"] - a["timestamp"]) * 1000 for a, b in zip(ps, ps[1:])
            ],
        )
    )
cs = [q for q in commands if q["cycle"] != "restore"]
assert len(cs) == 48 and live["comparison_count"] == repeat["comparison_count"] == 24
assert repeat["completed"]
counter_names = (
    "rx_drops",
    "uart_drops",
    "meta_drops",
    "radio_drops",
    "input_errors",
    "uart_rx_errors",
    "uart_rx_overruns",
    "uart_framing_errors",
    "uart_parity_errors",
    "uart_rx_dma_errors",
    "uart_rx_resyncs",
)
samples = [live["before"]] + live["samples"] + [live["after"]]
sessions = {s["diagnostics"]["adapter"]["session"] for s in samples}
assert len(sessions) == 1
assert all(s["ready"] and s["mqtt_connected"] for s in samples)
before = live["before"]["diagnostics"]["adapter"]
after = live["after"]["diagnostics"]["adapter"]
deltas = {
    k: int(after[k]) - int(before[k])
    for k in counter_names
    if k in before and k in after
}
assert all(v == 0 for v in deltas.values()), deltas
host_deltas = {
    k: live["after"]["diagnostics"][k] - live["before"]["diagnostics"][k]
    for k in ("rx_gaps", "invalid_rx", "ack_failures")
}
assert host_deltas["rx_gaps"] == host_deltas["ack_failures"] == 0, host_deltas
window = [
    p for p in rows if live["started_at"] <= p["timestamp"] <= live["finished_at"]
]
acks = acknowledgement_summary(window, "2a992b12")
hb = [p for p in beats if live["started_at"] <= p["timestamp"] <= live["finished_at"]]


def counts(seq):
    """Count confirmations without treating missing capture frames as failures.

    @param seq Labelled output request records with daemon command results.
    @return Total request count and number confirmed by the daemon.
    """
    return dict(
        total=len(seq), confirmed=sum(q["result"]["status"] == "confirmed" for q in seq)
    )


summary = dict(
    **counts(cs),
    runs={
        label: counts([q for q in cs if q["run"] == label])
        for label in ("initial", "settled")
    },
    channels={
        f"{d}/{m}": counts([q for q in cs if q["device"] == d and q["mask"] == m])
        for d, m in (("66780945", 1), ("46715621", 1), ("46715621", 2))
    },
    attempts=dict(collections.Counter(str(q["result"]["output_attempts"]) for q in cs)),
    retry_ms=stats([g for q in cs for g in q["retry_gaps_ms"]]),
    grid_abs_ms=stats([abs(e) for q in cs for e in q["grid_errors_ms"]]),
    captured=sum(len(q["packets"]) for q in cs),
    reported_sent=sum(int(q["transaction"]["sent"]) for q in cs),
    heartbeat_ms=stats(
        [(b["timestamp"] - a["timestamp"]) * 1000 for a, b in zip(hb, hb[1:])]
    ),
    live_counter_deltas=deltas,
    host_counter_deltas=host_deltas,
    live_seconds=live["finished_at"] - live["started_at"],
)
r = dict(
    summary=summary,
    capture=meta,
    commands=commands,
    live=live,
    repeat=repeat,
    integrity=integrity,
    acknowledgements=acks,
    original_ec_heartbeats=sum(
        p["source_wire"] == "9bf4ea38" and p["heartbeat_counter"] != "" for p in window
    ),
    update=json.loads((root / "update.json").read_text()),
    image=json.loads((root / "image.json").read_text()),
    scheduler_dry=json.loads((root / "scheduler-dry.json").read_text()),
    dry=json.loads((root / "dry.json").read_text()),
    limitations=[
        "User asked to leave buttons and app controls untouched during the controlled command sequence.",
        "TI exposes software loss counters but lacks MG21 hardware UART/radio error diagnostics.",
        "Bounded ten-minute qualification on this board; not a long-term or many-peer guarantee.",
        "Nordic can miss frames; capture gaps remain distinct from firmware results.",
        "State-query reliability remains tier 3 and was not the acceptance criterion.",
    ],
)
r["run_metrics"] = {}
for label in ("initial", "settled"):
    selected = [q for q in cs if q["run"] == label]
    r["run_metrics"][label] = dict(
        **counts(selected),
        captured=sum(len(q["packets"]) for q in selected),
        reported_sent=sum(int(q["transaction"]["sent"]) for q in selected),
        attempts=dict(
            collections.Counter(str(q["result"]["output_attempts"]) for q in selected)
        ),
        retry_ms=stats([g for q in selected for g in q["retry_gaps_ms"]]),
        grid_abs_ms=stats([abs(e) for q in selected for e in q["grid_errors_ms"]]),
    )
r["final"] = json.loads((root / "final.json").read_text())
r["other_radios"] = json.loads((root / "other-radios.json").read_text())
assert all(
    v == 0
    for radio in r["other_radios"].values()
    for v in radio["transmission_deltas"].values()
)
assert r["final"]["only_env_change_is_radio"] and all(
    r["final"]["preserved_files"].values()
)
assert {
    d["id"]: d["state"]["state_bits"] for d in repeat["after"]["devices"]
} == repeat["initial_bits"]
rejected = collections.Counter()
accepted = 0
for _, timestamp, raw, _ in read_pcapng(
    Path("captured/development/ti-012-20261101-nrf52840.pcapng")
):
    if not live["started_at"] <= timestamp <= live["finished_at"]:
        continue
    try:
        host_decode(raw)
        accepted += 1
    except ValueError as error:
        rejected[str(error)] += 1
r["capture_host_decode_window"] = dict(accepted=accepted, rejected=dict(rejected))
r["limitations"].append(
    "Host invalid_rx includes unsupported frames and malformed RX envelopes; independent Nordic traffic supports the presence of unsupported frames but cannot attribute every TI rejection."
)
Path("analysis/development/ti-012-20261101.json").write_text(
    json.dumps(r, indent=2) + "\n"
)
print(json.dumps(summary, indent=2))
