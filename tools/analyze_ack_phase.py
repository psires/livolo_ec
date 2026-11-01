"""Validate ACK phase and unchanged query policy against the 0.10 baseline.

Run from the repository root with the preserved test bundle in
.local/sonoff-ack-phase-20261025. This script never accesses radios or services.
"""

import collections, hashlib, json, statistics, sys
from pathlib import Path

sys.path.insert(0, "tools")
from analyze_query_timeouts import load_capture
from analyze_stable_uart_rf import acknowledgement_summary, stats, phase, packet

ROOT = Path(".local/sonoff-ack-phase-20261025")
CAP = "captured/development/sonoff-ack-phase-20261025-nrf52840.pcapng"
rows, cap = load_capture(CAP)
trial = json.loads((ROOT / "query-trial.json").read_text())
controller = json.loads((ROOT / "controller.json").read_text())
meta = json.loads((ROOT / "nordic-matched-spacing.json").read_text())
assert (
    controller["trial_exit_code"] == 0
    and controller["source_unchanged"]
    and controller["config_unchanged"]
    and controller["override_removed"]
)
assert (
    meta["started_at"]
    < trial["started_at"]
    < trial["finished_at"]
    < meta["finished_at"]
)
assert trial["output_commands"] == 0 and len(trial["requests"]) == 72
hashes = json.loads((ROOT / "timing-source-before.json").read_text())
assert all(
    hashlib.sha256(Path(f).read_bytes()).hexdigest() == h for f, h in hashes.items()
)
beats = [
    p for p in rows if p["source_wire"] == "2a992b12" and p["heartbeat_counter"] != ""
]
COUNTERS = (
    "rx_drops",
    "uart_drops",
    "radio_drops",
    "input_errors",
    "uart_rx_errors",
    "uart_rx_overruns",
    "uart_framing_errors",
    "uart_parity_errors",
    "uart_rx_dma_errors",
    "uart_rx_resyncs",
)
status = []
tx = []
rsp = collections.defaultdict(list)
acktx = []
for line in (ROOT / "trial.uart.log").read_text().splitlines():
    t, direction, body = line.split(" ", 2)
    f = dict(w.split("=", 1) for w in body.split() if "=" in w)
    if direction == ">":
        args = body.split()
        tx.append(dict(time=float(t), id=args[0], command=args[1], args=args[2:]))
        assert args[1] not in ("SEND", "TX", "SEND_PHASE", "PHASE_PROBE")
        if args[1] in ("QUERY", "QUERY_GRID"):
            assert args[4:7] == ["235500", "4012000", "3"]
    if body.startswith("RSP "):
        _, ident, reply = body.split(" ", 2)
        rsp[ident].append(reply)
        assert not reply.startswith("ERR"), line
    if "uart_rx_overruns=" in body:
        status.append(f)
    if body.startswith("ACKTX "):
        acktx.append(
            dict(
                time=float(t),
                raw=body.split()[-1],
                result=int(f["result"]),
                work_ticks=int(f["work_ticks"]),
            )
        )
assert max(collections.Counter(t["id"] for t in tx).values()) == 1
assert all(t["id"] in rsp for t in tx)
assert status and all(int(s[k]) == 0 for s in status for k in COUNTERS)
assert {s["fw_version"] for s in status} == {"0.11"} and len(
    {s["session"] for s in status}
) == 1
checks = []
for ent in trial["requests"]:
    q = ent["query_transaction"]
    variant = ent["variant"]
    window = [
        p
        for p in rows
        if ent["started_at"] - 0.25 <= p["timestamp"] <= ent["finished_at"] + 0.25
        and p["network_tag"] == "a7"
    ]
    query = [
        p
        for p in window
        if p["source_wire"] == "2a992b12"
        and p["family"] == "8c"
        and p["opcode"] in ("10", "ac")
    ]
    states = [
        p for p in window if p["source_wire"] == "46715621" and p["opcode"] == "05"
    ]
    replies = [
        p for p in window if p["source_wire"] == "46715621" and p["opcode"] == "fa"
    ]
    assert all(
        p["destination_wire"] == p["subject_wire"] == "46715621"
        and p["subject_suffix"] == "0002"
        and p["data_hex"] == "0000000000000001"
        for p in query
    )
    assert not any(
        p["source_wire"] == "2a992b12" and p["opcode"] in ("01", "02") for p in window
    )
    assert 1 <= int(q["rounds"]) <= 3 and int(q["sent"]) <= (
        6 if variant == "paired" else 3
    )
    assert ((int(q["deadline"]) - int(q["started"])) & 0xFFFFFFFF) == 14000000
    assert int(q["attempts"]) <= 4 * (6 if variant == "paired" else 3)
    assert q["grid"] == str(int(variant == "aligned"))
    assert len(query) <= int(q["sent"])
    ac = [p for p in query if p["opcode"] == "ac"]
    pairs = [
        (a, b)
        for a, b in zip(query, query[1:])
        if a["opcode"] == "10" and b["opcode"] == "ac"
    ]
    phase_errors = []
    grid = []
    for p in query:
        hb = [h for h in beats if h["timestamp"] < p["timestamp"]][-1]
        age = p["timestamp"] - hb["timestamp"]
        phase_errors.append((phase(p) - age) * 1000)
        grid.append((age - round(age / 0.2) * 0.2) * 1000)
    if phase_errors:
        assert max(abs(v) for v in phase_errors) < 3
    if variant == "aligned" and grid:
        assert max(abs(v) for v in grid) < 3
    good = ent["result"]["status"] == "confirmed"
    if good:
        st = ent["result"]["state"]
        matched = [
            p
            for p in states
            if (p["state_bits"], p["report_counter"], p["token"])
            == (st["state_bits"], st["counter"], st["token"])
        ]
        assert matched and all(p["timestamp"] < matched[0]["timestamp"] for p in query)
    else:
        assert ent["result"]["status"] == "unconfirmed" and q["outcome"] == "2"
        assert not states and not any(p["ack_of_opcode"] == "ac" for p in replies)
        matched = []
    checks.append(
        dict(
            number=ent["number"],
            variant=variant,
            status=ent["result"]["status"],
            rounds=int(q["rounds"]),
            reported_sent=int(q["sent"]),
            captured=len(query),
            missing_from_capture=int(q["sent"]) - len(query),
            cca_retries=int(q["retries"]),
            packets=[p["frame"] for p in query],
            state_packets=[p["frame"] for p in states],
            confirmation_packets=[p["frame"] for p in matched],
            reply_packets=[packet(p) for p in replies],
            ac_to_ac_ms=[
                1000 * (b["timestamp"] - a["timestamp"]) for a, b in zip(ac, ac[1:])
            ],
            pair_ms=[1000 * (b["timestamp"] - a["timestamp"]) for a, b in pairs],
            phase_error_ms=phase_errors,
            grid_error_ms=grid if variant == "aligned" else [],
            last_retained=q,
        )
    )
window = [
    p
    for p in rows
    if trial["requests"][0]["started_at"] - 0.25
    <= p["timestamp"]
    <= trial["finished_at"] + 0.25
]
acks = acknowledgement_summary(window, "2a992b12")
assert acks["count"] and not acks["zero_trailers"]
# Use full capture for references before the first query.
fullacks = acknowledgement_summary(rows, "2a992b12")
selected = {p["frame"] for p in window}
ack_errors = [
    p["phase_error_ms"]
    for p in fullacks["packets"]
    if p["frame"] in selected and p["phase_error_ms"] is not None
]
assert ack_errors and max(abs(v) for v in ack_errors) < 3
ack_matches = []
for ack in acktx:
    if (
        trial["requests"][0]["started_at"] - 0.25
        <= ack["time"]
        <= trial["finished_at"] + 0.25
    ):
        matches = [
            p["frame"]
            for p in rows
            if p["raw_hex"] == ack["raw"] and abs(p["timestamp"] - ack["time"]) < 0.25
        ]
        ack_matches.append(dict(**ack, packets=matches))
base = json.loads(
    Path("analysis/development/sonoff-uart-dma-20261025.json").read_text()
)
basechecks = [c for run in base["runs"] for c in run["checks"]]


def summarize(cs):
    """Compare observed query outcomes and timing without hiding capture loss.

    @param cs Per-transaction records containing outcome, retry and timing evidence.
    @return Outcome counts, capture discrepancies and timing summaries in milliseconds.
    """
    return dict(
        transactions=len(cs),
        confirmed=sum(c["status"] == "confirmed" for c in cs),
        timeouts=[c["number"] for c in cs if c["status"] == "unconfirmed"],
        rounds=dict(collections.Counter(str(c["rounds"]) for c in cs)),
        cca_retries=sum(c["cca_retries"] for c in cs),
        captured=sum(c["captured"] for c in cs),
        missing_from_capture=sum(c["missing_from_capture"] for c in cs),
        pair_ms=stats([v for c in cs for v in c["pair_ms"]]),
        ac_to_ac_ms=stats([v for c in cs for v in c["ac_to_ac_ms"]]),
        ac_to_ac_without_cca_ms=stats(
            [
                v
                for c in cs
                if not c["cca_retries"] and not c["missing_from_capture"]
                for v in c["ac_to_ac_ms"]
            ]
        ),
        phase_error_ms=stats([v for c in cs for v in c["phase_error_ms"]]),
        grid_error_ms=stats([v for c in cs for v in c["grid_error_ms"]]),
    )


variants = {
    v: dict(
        baseline=summarize([c for c in basechecks if c["variant"] == v]),
        phase_ack=summarize([c for c in checks if c["variant"] == v]),
    )
    for v in ("unaligned", "aligned", "paired")
}
base_rf = json.loads(
    Path("analysis/development/sonoff-stable-uart-rf-20261025.json").read_text()
)
b = [
    h
    for h in beats
    if trial["requests"][0]["started_at"] < h["timestamp"] < trial["finished_at"]
]
result = dict(
    firmware="0.11",
    baud=460800,
    bootloader_baud=115200,
    capture=cap,
    capture_metadata=meta,
    trial=trial,
    controller=controller,
    checks=checks,
    variants=variants,
    summary=summarize(checks),
    unchanged_timing_source_sha256=hashes,
    limits=base["limits"],
    uart=dict(
        requests=len(tx),
        repeated_ids=0,
        error_replies=0,
        missing_replies=0,
        counter_maxima={k: max(int(s[k]) for s in status) for k in COUNTERS},
        rx_highwater=max(int(s["uart_rx_highwater"]) for s in status),
        rx_poll_gap_us=max(int(s["uart_rx_poll_gap_us"]) for s in status),
    ),
    acknowledgements=dict(
        current=acks,
        baseline=base_rf["local_acknowledgements"],
        phase_error_ms=stats(ack_errors),
        uart_matches=ack_matches,
        unmatched_uart_acks=sum(not a["packets"] for a in ack_matches),
    ),
    heartbeat_spacing_ms=stats(
        [1000 * (v["timestamp"] - u["timestamp"]) for u, v in zip(b, b[1:])]
    ),
    original_ec_frames=sum(p["network_tag"] == "b1" for p in window),
    image=json.loads((ROOT / "image.json").read_text()),
    deployment=json.loads((ROOT / "deployment.json").read_text()),
    limitations=[
        "Sequential comparison, not a randomized concurrent firmware A/B; the RF environment and switch synchronization state can change.",
        "The baseline paired tests used multiple daemon sessions and partly overlapped explicit UART load; this run uses one session without the 6000-request load.",
        "Nordic is one receiver and can miss frames; frame discrepancies are retained, never converted into logical failures.",
        "Cross-Pi matching allows 250ms receipt/clock margin; timing comparisons use Nordic timestamps only.",
    ],
)
final = json.loads((ROOT / "final-query.json").read_text())
entry = final["requests"][0]
window = [
    p
    for p in rows
    if entry["started_at"] - 0.25 <= p["timestamp"] <= entry["finished_at"] + 0.25
    and p["network_tag"] == "a7"
]
query = [
    p for p in window if p["source_wire"] == "2a992b12" and p["opcode"] in ("10", "ac")
]
states = [p for p in window if p["source_wire"] == "46715621" and p["opcode"] == "05"]
assert (
    entry["result"]["status"] == "confirmed"
    and entry["result"]["queries_sent"] == len(query) == 4
)
st = entry["result"]["state"]
assert any(
    (p["state_bits"], p["report_counter"], p["token"])
    == (st["state_bits"], st["counter"], st["token"])
    for p in states
)
assert all(int(final["after"]["diagnostics"]["adapter"][k]) == 0 for k in COUNTERS)
health = json.loads((ROOT / "final-health.json").read_text())
assert (
    health["ready"]
    and health["mqtt_connected"]
    and health["registry_and_mqtt_preserved"]
    and health["no_runtime_override"]
)
result["final_query"] = dict(
    evidence=final,
    query_packets=[p["frame"] for p in query],
    state_packets=[p["frame"] for p in states],
)
result["final_health"] = health
result["tests"] = dict(
    ack_and_mg21_scheduler=33, legacy_parser=40, scheduler=32, uart_dma=9, host=113
)
result["limitations"].append(
    "Six workload query frames and fifteen successful UART ACK records have no matching Nordic packet; one successful AC-only request has no captured query at all. MCU results and captured fresh reports are retained separately."
)
Path("analysis/development/sonoff-ack-phase-20261025.json").write_text(
    json.dumps(result, indent=2) + "\n"
)
print(
    json.dumps(
        {
            k: result[k]
            for k in (
                "summary",
                "variants",
                "uart",
                "heartbeat_spacing_ms",
                "original_ec_frames",
            )
        },
        indent=2,
    )
)
print(
    "ACK latency:",
    acks["response_delay_ms"],
    "phase error:",
    stats(ack_errors),
    "missing UART ACKs:",
    result["acknowledgements"]["unmatched_uart_acks"],
)
