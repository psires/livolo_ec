"""Compare labelled output requests against independent Nordic transmissions."""

import bisect
import collections
import json
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path.cwd() / "tools"))
from analyze_query_timeouts import load_capture
from analyze_stable_uart_rf import acknowledgement_summary

root = Path("analysis/development/output-grid-20261101")
trial = json.loads((root / "trial.json").read_text())
rows, meta = load_capture("captured/development/output-grid-20261101-nrf52840.pcapng")
controller = json.loads((root / "controller.json").read_text())
capture = json.loads((root / "grid.json").read_text())
assert controller["trial_exit_code"] == 0
assert all(
    controller[k] for k in ("source_unchanged", "config_unchanged", "override_removed")
)
assert (
    capture["started_at"]
    < trial["started_at"]
    < trial["finished_at"]
    < capture["finished_at"]
)
assert trial["attempt_limit"] == 6 and trial["confirmation_deadline_seconds"] == 4
assert trial["comparison_count"] == 48
assert {d["id"]: d["state"]["state_bits"] for d in trial["after"]["devices"]} == trial[
    "initial_bits"
]
assert controller["restored"]["diagnostics"]["host_version"] == "0.12"
assert controller["restored"]["diagnostics"]["firmware"]["running_version"] == "0.12"
uart = []
for line in (root / "trial.uart.log").read_text().splitlines():
    parts = line.split()
    if len(parts) == 6 and parts[1] == ">" and parts[3] == "SEND_PHASE":
        uart.append((float(parts[0]), bytes.fromhex(parts[5])))
beats = [
    p["timestamp"]
    for p in rows
    if p["source_wire"] == "2a992b12" and p["heartbeat_counter"] != ""
]
period = (
    statistics.median([b - a for a, b in zip(beats, beats[1:]) if 9.9 < b - a < 10.1])
    / 50
)
commands = []
for q in trial["requests"]:
    if q["block"] == "restore":
        continue
    token = q["result"].get("token")
    packets = [
        p
        for p in rows
        if q["started_at"] - 0.05 <= p["timestamp"] <= q["finished_at"] + 0.05
        and p["source_wire"] == "2a992b12"
        and p["subject_wire"] == q["device"]
        and p["family"] == "8c"
        and p["opcode"] == "02"
        and p["data_hex"] == bytes((1, 0, q["mask"], q["value"], 0, 0, 0, token)).hex()
    ]
    native = [
        p
        for p in rows
        if q["started_at"] - 0.05 <= p["timestamp"] <= q["finished_at"] + 0.05
        and p["source_wire"] == p["subject_wire"] == q["device"]
        and p["opcode"] == "05"
    ]
    hbs = [
        p
        for p in rows
        if q["started_at"] <= p["timestamp"] <= q["finished_at"]
        and p["source_wire"] == "9bf4ea38"
        and p["opcode"] == "08"
        and p["subject_wire"] == "00000000"
    ]
    gaps = [
        (b["timestamp"] - a["timestamp"]) * 1000 for a, b in zip(packets, packets[1:])
    ]
    attempts = [
        t
        for t, raw in uart
        if q["started_at"] - 0.05 <= t <= q["finished_at"] + 0.05
        and raw[23:27].hex() == q["device"]
        and raw[29:37] == bytes((1, 0, q["mask"], q["value"], 0, 0, 0, token))
    ]
    count = (
        len(attempts)
        if q["variant"] == "unaligned"
        else int(q["transaction"]["attempts"])
    )
    assert 1 <= count <= 6, q["request_id"]
    errors = []
    for p in packets:
        i = bisect.bisect_right(beats, p["timestamp"]) - 1
        if i >= 0 and p["timestamp"] - beats[i] < 10.05:
            age = p["timestamp"] - beats[i]
            errors.append(((age + period / 2) % period - period / 2) * 1000)
    matching = [
        p
        for p in native
        if p["family"] == "9e"
        and p["network_tag"] == "a7"
        and p["destination_wire"] in ("2a992b12", "ffffffff")
        and p["token"] == token
        and int(p["data_hex"][4:6], 16) == q["mask"]
        and p["state_bits"] & q["mask"] == q["value"]
    ]
    if q["result"]["status"] == "confirmed":
        assert matching, q["request_id"]
    if q["variant"] == "aligned":
        t = q["transaction"]
        assert (
            t["output"] == "1"
            and t["grid"] == "1"
            and t["rounds"] == "0"
            and t["retries"] == "0"
        )
        assert ((int(t["deadline"]) - int(t["started"])) & 0xFFFFFFFF) == 4000000
        assert len(packets) <= int(t["sent"]) <= count
        if matching:
            assert not any(p["timestamp"] > matching[0]["timestamp"] for p in packets)
    commands.append(
        dict(
            **q,
            captured_transmissions=packets,
            state_reports=native,
            retry_gaps_ms=gaps,
            original_ec_heartbeats=len(hbs),
            uart_attempt_times=attempts,
            attempt_count=count,
            grid_errors_ms=errors,
        )
    )


def stats(seq):
    """Describe the spread of observed timing samples.

    @param seq Numeric samples in a common unit; may be empty.
    @return Sample count and, when nonempty, minimum, median and maximum.
    """
    return (
        dict(count=len(seq), min=min(seq), median=statistics.median(seq), max=max(seq))
        if seq
        else {"count": 0}
    )


summary = {}
for interval in ("unaligned", "aligned"):
    subset = [q for q in commands if q["variant"] == interval]
    gaps = [g for q in subset for g in q["retry_gaps_ms"]]

    def counts(seq):
        """Report confirmed output requests independently of capture completeness.

        @param seq Labelled request records with daemon command results.
        @return Total request count and number confirmed by the daemon.
        """
        return dict(
            total=len(seq),
            confirmed=sum(q["result"]["status"] == "confirmed" for q in seq),
        )

    summary[str(interval)] = dict(
        **counts(subset),
        channels={
            f"{device}/{mask}": counts(
                [q for q in subset if q["device"] == device and q["mask"] == mask]
            )
            for device, mask in (("66780945", 1), ("46715621", 1), ("46715621", 2))
        },
        direction={
            direction: counts([q for q in subset if bool(q["value"]) == bool(value)])
            for direction, value in (("ON", 1), ("OFF", 0))
        },
        latency_seconds=stats(
            [
                q["finished_at"] - q["started_at"]
                for q in subset
                if q["result"]["status"] == "confirmed"
            ]
        ),
        retry_gaps_ms=stats(gaps),
        # Exclude doubled gaps attributable to a missed packet/CCA slot; keep all above.
        adjacent_gap_cluster_ms=stats([g for g in gaps if 160 < g < 300]),
        grid_error_abs_ms=stats([abs(e) for q in subset for e in q["grid_errors_ms"]]),
        within_1ms=sum(abs(e) <= 1 for q in subset for e in q["grid_errors_ms"]),
        captured_output_packets=sum(len(q["captured_transmissions"]) for q in subset),
    )
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
statuses = []
errors = []
sent_ids = []
received_ids = []
for line in (root / "trial.uart.log").read_text().splitlines():
    parts = line.split()
    if len(parts) > 3 and parts[1] == ">":
        sent_ids.append(parts[2])
    if len(parts) > 4 and parts[1:3] == ["<", "RSP"]:
        received_ids.append(parts[3])
        if parts[4] == "ERR":
            errors.append(line)
    if "uart_rx_overruns=" in line:
        statuses.append(dict(w.split("=", 1) for w in parts if "=" in w))
assert statuses
counter_maxima = {k: max(int(s[k]) for s in statuses) for k in COUNTERS}
assert all(v == 0 for v in counter_maxima.values()), counter_maxima
window = [
    p
    for p in rows
    if trial["requests"][0]["started_at"] <= p["timestamp"] <= trial["finished_at"]
]
acks = acknowledgement_summary(window, "2a992b12")
result = dict(
    firmware="0.12",
    image=json.loads((root / "image.json").read_text()),
    deployment=json.loads((root / "deployment.json").read_text()),
    capture_metadata=json.loads((root / "grid.json").read_text()),
    ack_baseline=json.loads((root / "ack-baseline.json").read_text()),
    capture=meta,
    uart=dict(
        requests=len(sent_ids),
        missing_replies=len(set(sent_ids) - set(received_ids)),
        errors=errors,
        counter_maxima=counter_maxima,
    ),
    acknowledgements=acks,
    summary=summary,
    commands=commands,
    original_ec_heartbeats_in_capture=sum(
        p["source_wire"] == "9bf4ea38"
        and p["opcode"] == "08"
        and p["subject_wire"] == "00000000"
        for p in rows
    ),
    six_attempt_bound_verified=True,
    final_queries={
        label: json.loads((root / ("final-query-" + label + ".json")).read_text())
        for label in ("onegang", "twogang")
    },
    original_ec_pulse=json.loads(
        Path("analysis/development/ec-pulse-20261101.json").read_text()
    ),
    controller=json.loads((root / "controller.json").read_text()),
    trial=trial,
    caveats=[
        "Small sequential ABBA experiment, not randomized or statistically conclusive.",
        "A larger observed packet gap may include missed transmissions or busy-channel attempts.",
        "Alignment and MCU ownership change together; this is not an isolated phase-only experiment.",
        "Same-state restoration requests are included; confirmation requires a fresh matching report.",
    ],
)
Path("analysis/development/output-grid-20261101.json").write_text(
    json.dumps(result, indent=2) + "\n"
)
print(json.dumps(summary, indent=2))
