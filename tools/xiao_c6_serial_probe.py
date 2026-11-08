#!/usr/bin/env python3
"""Fixed RF-off C6 lifecycle comparison, requiring an explicitly selected arm."""

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import time

from xiao_c6_serial import C6Serial
import xiao_rf_bench as rf
from xiao_rx_bench import Link

ALLOWED = frozenset(("PING", "VERSION", "CAPS", "RFSTATUS", "RFHEALTH"))


def run(port, evidence, clock=time.monotonic, sleep=time.sleep):
    """Collect three opens with seven-second holds and two five-second closed gaps.

    @param port Closed, identity-checking C6Serial in one fixed comparison mode.
    @param evidence Mutable receipt retaining partial transcripts on any failure.
    @param clock Injectable monotonic time for deadlines and receipt timestamps.
    @param sleep Injectable scheduler yield; never used to retry a command.
    """
    evidence.update(completed=False, cycles=[], lifecycle=[])
    deadline = clock() + 75

    def stamp(action):
        """Retain timing at the boundary of each explicit lifecycle operation.

        @param action Open/close requested/returned or gap completion label.
        """
        evidence["lifecycle"].append(
            dict(
                action=action,
                host_monotonic=clock(),
                utc=datetime.now(timezone.utc).isoformat(),
            )
        )

    for index in range(3):
        cycle = dict(index=index, requests=[])
        evidence["cycles"].append(cycle)
        link = None
        try:
            stamp("open_requested")
            port.open()
            stamp("open_returned")
            link = Link(port, "c6", clock)
            cycle["transcript"] = link.timeline

            def request(command):
                """Record one bounded allowed request, without retries or RF writes.

                @param command Exact read-only diagnostic command.
                @return Matching successful response text.
                """
                if command not in ALLOWED or clock() >= deadline:
                    raise ValueError("Probe command or deadline rejected")
                entry = dict(command=command, requested=clock())
                cycle["requests"].append(entry)
                reply = rf.command(
                    link, command, [link], min(2, deadline - clock()), clock, sleep
                )
                entry.update(reply=reply, returned=clock())
                return reply

            if request("PING") != "OK EC-GATEWAY 3":
                raise ValueError("Wrong protocol")
            for command in ("VERSION", "CAPS"):
                data = rf.fields(request(command)[3:])
                expected = dict(
                    board="seeed-xiao-esp32c6",
                    proto="3",
                    experimental="1",
                    update="none",
                )
                if command == "VERSION":
                    expected["build"] = "xiao-rx-1"
                else:
                    expected.update(gateway="0", peers="0", ack_mask="00")
                if any(data.get(key) != value for key, value in expected.items()):
                    raise ValueError("Unexpected C6 identity/build/capabilities")

            def snapshot(label):
                """Require idle RF and preserve raw health, including reset reason.

                @param label Before or after the fixed same-open hold.
                """
                state = rf.status(request("RFSTATUS"), 0)
                if state["busy"] != "0" or state["queued"] != "0":
                    raise ValueError("C6 is not idle")
                health = rf.fields(request("RFHEALTH")[3:])
                if health.get("counter_bits") != "32":
                    raise ValueError("Unexpected health counter width")
                for key in (
                    "uptime_ms",
                    "reset_reason",
                    "task_wdt_events",
                    "read_error",
                    "write_error",
                    "pending",
                ):
                    rf.integer(health.get(key), 0, 0xFFFFFFFF)
                cycle[label] = dict(status=state, health=health, host_monotonic=clock())

            snapshot("before_hold")
            if clock() + 7 + 4 > deadline:
                raise TimeoutError("Insufficient fixed hold/diagnostic budget")
            rf.pump([link], clock() + 7, clock, sleep)
            snapshot("after_hold")
            if link.events:
                raise ValueError("Unexpected RF events during RF-off probe")
        finally:
            if link is not None:
                cycle["incomplete_line_hex"] = bytes(link.buffer).hex()
            stamp("close_requested")
            port.close()
            stamp("close_returned")
        if index < 2:
            if clock() + 5 >= deadline:
                raise TimeoutError("Insufficient fixed closed-gap budget")
            sleep(5)
            stamp("five_second_closed_gap_completed")
    evidence["completed"] = True


def main():
    """Write a new private receipt; completion is not a reset-free qualification."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", required=True)
    parser.add_argument("--serial", required=True)
    parser.add_argument("--mode", required=True, choices=("stock", "combined"))
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    evidence = dict(
        schema=1, mode=args.mode, scope="C6 RF-off lifecycle only", completed=False
    )
    with args.output.open("x") as output:
        try:
            port = C6Serial(args.port, args.serial, mode=args.mode)
            evidence.update(path=args.port, usb_serial=args.serial)
            run(port, evidence)
        except Exception as error:
            evidence["error"] = f"{type(error).__name__}: {error}"
        finally:
            json.dump(evidence, output, indent=2)
            output.write("\n")
    print(json.dumps(dict(completed=evidence["completed"], output=str(args.output))))
    return 0 if evidence["completed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
