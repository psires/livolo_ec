#!/usr/bin/env python3
"""Bounded channel-11 passive XIAO reception; no radio transmission commands."""

import argparse
from collections import Counter
from contextlib import ExitStack
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import secrets
import time

from decode_ec import decode_frame
import xiao_rf_bench as rf

TARGETS = {"46715621": "0002", "66780945": "0001"}


class Link(rf.Link):
    """Retain host delivery timestamps independently of MCU callback ticks."""

    def __init__(self, port, name, clock=time.monotonic):
        """Bind one prevalidated endpoint without opening it.

        @param port Nonblocking serial-like object.
        @param name Exact short board name.
        @param clock Injectable host monotonic clock.
        """
        super().__init__(port, name)
        self.clock = clock
        self.timeline = []

    def poll(self):
        """Record delivery time for every complete line, including ROM/unknown traffic."""
        lines, events = len(self.records), len(self.events)
        try:
            super().poll()
        finally:
            stamp = self.clock()
            self.timeline.extend(
                dict(host_monotonic=stamp, line=line) for line in self.records[lines:]
            )
            for item in self.events[events:]:
                item["host_monotonic"] = stamp


def receive_command(
    link, text, links, timeout=2, clock=time.monotonic, sleep=time.sleep
):
    """Permit only identity/status, fixed passive start and stop operations.

    @param link Destination endpoint.
    @param text Complete permitted command, never arbitrary TX or gateway control.
    @param links Both independent receivers to drain.
    @param timeout Finite command budget.
    @param clock Injectable monotonic clock.
    @param sleep Injectable scheduler yield.
    @return Matching successful numbered response.
    """
    if text not in ("PING", "VERSION", "CAPS", "RFSTATUS", "RFSTOP", "RFQUEUE"):
        match = re.fullmatch(r"RFLISTEN ([0-9a-f]{8})", text)
        if match is None or int(match[1], 16) == 0:
            raise ValueError("Passive harness rejects this command")
    return rf.command(link, text, links, timeout, clock, sleep)


def summarize(events, board):
    """Decode complete validated EC frames and retain every unmatched observation.

    @param events Structured RF events delivered by one receiver.
    @param board Short endpoint name determining the required FCS representation.
    @return Independent receiver observations, target counts and honest gap metrics.
    """
    rows = []
    target_counts = dict.fromkeys(TARGETS, 0)
    previous = {}
    sequence_steps = {target: [] for target in TARGETS}
    fingerprints = Counter()
    gaps = []
    last_delivery = None
    software_invalid = wrong_fcs = 0
    for item in events:
        if item["kind"] != "rx":
            continue
        row = dict(item, match=None, reason="unsupported_layout")
        if last_delivery is not None:
            gaps.append(item["host_monotonic"] - last_delivery)
        last_delivery = item["host_monotonic"]
        raw = bytes.fromhex(item["payload"])
        row["sha256"] = hashlib.sha256(raw).hexdigest()
        fingerprints[row["sha256"]] += 1
        try:
            decoded = decode_frame(raw)
            row["decoded"] = decoded
            source = decoded["source_wire"]
            if not decoded["checksum_ok"]:
                row["reason"] = "invalid_application_checksum"
            elif source not in TARGETS:
                row["reason"] = "other_source"
            elif (
                decoded["family"] != "9e"
                or decoded["subject_wire"] != source
                or decoded["subject_suffix"] != TARGETS[source]
            ):
                row["reason"] = "target_source_but_unverified_native_role"
            else:
                row["match"] = source
                row["reason"] = "validated_native_source_subject_suffix"
        except ValueError:
            pass
        if item["fcs"] != ("included" if board == "mg24" else "stripped"):
            wrong_fcs += 1
            row.update(match=None, reason="wrong_fcs_representation")
        elif item["software_crc_valid"] is False:
            software_invalid += 1
            row.update(match=None, reason="invalid_mac_fcs")
        if row["match"]:
            source = row["match"]
            target_counts[source] += 1
            seq = row["decoded"]["mac_sequence"]
            if source in previous:
                sequence_steps[source].append((seq - previous[source]) & 255)
            previous[source] = seq
        rows.append(row)
    return dict(
        observations=rows,
        target_counts=target_counts,
        rx_events=len(rows),
        unexpected_tx_events=sum(item["kind"] == "tx" for item in events),
        invalid_mac_fcs=software_invalid,
        wrong_fcs_representation=wrong_fcs,
        hardware_crc_only=sum(row["software_crc_valid"] is None for row in rows),
        independently_crc_valid=sum(row["software_crc_valid"] is True for row in rows),
        unmatched_frames=sum(row["match"] is None for row in rows),
        identical_payload_repeats={
            key: count - 1 for key, count in fingerprints.items() if count > 1
        },
        mac_sequence_steps=sequence_steps,
        max_host_delivery_gap_seconds=max(gaps, default=None),
        gap_interpretation="No receiver event sequence exists; silence, retries, modulo MAC sequence changes and host delivery gaps are not packet-loss measurements",
    )


def assess(evidence):
    """Separate complete observation from inconclusive silence and failed capture integrity.

    @param evidence Completed or interrupted passive receipt.
    @return passed, inconclusive or failed; never gateway or timing qualification.
    """
    if (
        evidence.get("error")
        or evidence.get("cleanup_errors")
        or not evidence.get("window_completed")
    ):
        return "failed"
    if set(evidence.get("final", {})) != set(rf.BOARDS):
        return "failed"
    for name in rf.BOARDS:
        final = evidence["final"][name]
        report = evidence["receivers"][name]
        for key, value in (
            ("active", "0"),
            ("channel", "11"),
            ("mode", "passive"),
            ("auto_ack", "0"),
            ("retries", "0"),
            ("tx_started", "0"),
            ("tx_done", "0"),
            ("tx_failed", "0"),
            ("dropped", "0"),
            ("queued", "0"),
            ("busy", "0"),
            ("fault", "0"),
        ):
            if final.get(key) != value:
                return "failed"
        if (
            int(final["rx"]) != report["rx_events"]
            or report["unexpected_tx_events"]
            or report["invalid_mac_fcs"]
            or report["wrong_fcs_representation"]
        ):
            return "failed"
    if not all(
        all(report["target_counts"].values())
        for report in evidence["receivers"].values()
    ):
        return "inconclusive"
    return "passed"


def capture(links, nonce, duration, evidence, clock=time.monotonic, sleep=time.sleep):
    """Listen concurrently on both exact endpoints, then stop only owned passive windows.

    @param links MG24 and C6 independent, exclusively opened links.
    @param nonce Fresh nonzero eight-hex window identity.
    @param duration Passive observation window, 1 through 45 seconds.
    @param evidence Mutable receipt retaining partial/error evidence.
    @param clock Injectable monotonic clock.
    @param sleep Injectable scheduler yield.
    """
    if type(duration) is not int or not 1 <= duration <= 45:
        raise ValueError("Capture duration must be 1..45 seconds")
    if not re.fullmatch(r"[0-9a-f]{8}", nonce) or int(nonce, 16) == 0:
        raise ValueError("Invalid passive nonce")
    evidence.update(
        nonce=nonce,
        duration_seconds=duration,
        baseline={},
        final={},
        cleanup_errors=[],
        window_completed=False,
        queue={},
    )
    started = []

    def send(link, text):
        """Use the passive-only command allowlist and bounded transport.

        @param link Destination endpoint.
        @param text Read-only diagnostic or fixed passive start command.
        @return Matching response.
        """
        return receive_command(link, text, links, 2, clock, sleep)

    try:
        for link in links:
            if send(link, "PING") != "OK EC-GATEWAY 3":
                raise ValueError("UART identity mismatch")
            version = rf.fields(send(link, "VERSION")[3:])
            caps = rf.fields(send(link, "CAPS")[3:])
            for data in (version, caps):
                for key, value in (
                    ("board", rf.BOARDS[link.name][0]),
                    ("proto", "3"),
                    ("experimental", "1"),
                    ("update", "none"),
                ):
                    if data.get(key) != value:
                        raise ValueError("Live identity mismatch")
            for key, value in (
                ("gateway", "0"),
                ("peers", "0"),
                ("ack_mask", "00"),
                ("rx_diag", "passive_v1"),
            ):
                if caps.get(key) != value:
                    raise ValueError("Missing passive-only diagnostic contract")
            evidence["queue"][link.name] = rf.queue_capability(caps)
            evidence["baseline"][link.name] = rf.status(send(link, "RFSTATUS"), 0)
            if evidence["baseline"][link.name]["busy"] != "0":
                raise ValueError("Inactive endpoint reports pending radio work")
        for link in links:
            link.events.clear()
        stop_by = clock() + 55
        for link in links:
            started.append(link)
            reply = rf.fields(send(link, f"RFLISTEN {nonce}")[3:])
            if reply != dict(active="1", channel="11", mode="passive"):
                raise ValueError("Passive admission mismatch")
            active = rf.status(send(link, "RFSTATUS"), 1, channel=11, mode="passive")
            if any(
                active[key] != "0"
                for key in ("tx_started", "tx_done", "tx_failed", "busy", "dropped")
            ):
                raise ValueError("Passive session contains TX, fault or dropped data")
        evidence["window_start_monotonic"] = clock()
        if clock() + duration + 4 > stop_by:
            raise TimeoutError("Insufficient passive lease budget for requested window")
        rf.pump(links, clock() + duration, clock, sleep)
        for link in links:
            rf.status(send(link, "RFSTATUS"), 1, channel=11, mode="passive")
        evidence["window_completed"] = True
    except Exception as error:
        evidence["error"] = f"{type(error).__name__}: {error}"
        raise
    finally:
        evidence["window_end_monotonic"] = clock()
        for link in reversed(started):
            try:
                receive_command(link, "RFSTOP", [link], 1, clock, sleep)
            except Exception as error:
                evidence["cleanup_errors"].append(f"{link.name}: {error}")
        if started:
            try:
                rf.pump(links, clock() + 0.2, clock, sleep)
            except Exception as error:
                evidence["cleanup_errors"].append(f"drain: {error}")
        for link in started:
            try:
                evidence["final"][link.name] = rf.status(
                    receive_command(link, "RFSTATUS", [link], 1, clock, sleep),
                    0,
                    channel=11,
                    mode="passive",
                )
                if evidence["queue"].get(link.name, {}).get("available"):
                    snapshot = rf.queue_status(
                        receive_command(link, "RFQUEUE", [link], 1, clock, sleep)
                    )
                    evidence["queue"][link.name]["counters"] = snapshot
                    rf.reconcile_queue(
                        snapshot,
                        evidence["final"][link.name],
                        sum(e["kind"] == "rx" for e in link.events),
                    )
            except Exception as error:
                evidence["cleanup_errors"].append(f"{link.name}: {error}")
        evidence["receivers"] = {
            link.name: summarize(link.events, link.name) for link in links
        }
        evidence["raw_lines"] = {link.name: link.timeline for link in links}
        evidence["incomplete_lines_hex"] = {
            link.name: bytes(link.buffer).hex() for link in links
        }
        evidence["unmatched_responses"] = {link.name: link.unmatched for link in links}
        sets = [
            set(
                row["sha256"]
                for row in evidence["receivers"][link.name]["observations"]
                if row["match"]
            )
            for link in links
        ]
        evidence["shared_target_payload_sha256"] = sorted(sets[0] & sets[1])
        evidence["assessment"] = assess(evidence)


def main():
    """Open only explicit inventoried XIAOs and preserve a private passive receipt."""
    parser = argparse.ArgumentParser(description=__doc__)
    for name in rf.BOARDS:
        parser.add_argument(f"--{name}-port", required=True)
        parser.add_argument(f"--{name}-serial", required=True)
    parser.add_argument("--seconds", type=int, choices=range(30, 46), default=30)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    with args.output.open("x") as output:
        evidence = dict(
            schema=1,
            assessment="failed",
            scope="passive channel11 only",
            targets=TARGETS,
            host_events=[],
        )

        def record(operation, name):
            """Anchor host lifecycle observations in UTC and monotonic time.

            @param operation Open/close phase, not an inferred hardware event.
            @param name Exact endpoint key.
            """
            evidence["host_events"].append(
                dict(
                    operation=operation,
                    board=name,
                    host_monotonic=time.monotonic(),
                    utc=datetime.now(timezone.utc).isoformat(),
                )
            )

        def close(port, name):
            """Record cleanup without asserting that OS close cannot affect USB state.

            @param port Open or partially opened serial object.
            @param name Exact endpoint key.
            """
            record("close_requested", name)
            port.close()
            record("close_returned", name)

        try:
            import serial
            from serial.tools import list_ports

            inventory = list(list_ports.comports())
            paths = [
                rf.verify_endpoint(
                    getattr(args, name + "_port"),
                    getattr(args, name + "_serial"),
                    name,
                    inventory,
                )
                for name in rf.BOARDS
            ]
            if len(set(paths)) != 2:
                raise ValueError("Two distinct endpoints required")
            evidence["endpoints"] = {
                name: dict(
                    path=getattr(args, name + "_port"),
                    serial=getattr(args, name + "_serial"),
                    resolved=paths[index],
                    dtr=rf.BOARDS[name][3],
                    rts=False,
                    baud=115200,
                )
                for index, name in enumerate(rf.BOARDS)
            }
            with ExitStack() as stack:
                links = []
                for name in rf.BOARDS:
                    port = rf.configured_port(
                        name,
                        getattr(args, name + "_port"),
                        getattr(args, name + "_serial"),
                        serial.Serial,
                    )
                    stack.callback(close, port, name)
                    record("open_requested", name)
                    port.open()
                    record("open_returned", name)
                    links.append(Link(port, name))
                capture(
                    links,
                    f"{secrets.randbelow(0xFFFFFFFF) + 1:08x}",
                    args.seconds,
                    evidence,
                )
        except Exception as error:
            evidence.update(
                error=f"{type(error).__name__}: {error}", assessment="failed"
            )
        finally:
            json.dump(evidence, output, indent=2)
            output.write("\n")
    print(json.dumps(dict(assessment=evidence["assessment"], output=str(args.output))))
    return (
        0
        if evidence["assessment"] == "passed"
        else 2 if evidence["assessment"] == "inconclusive" else 1
    )


if __name__ == "__main__":
    raise SystemExit(main())
