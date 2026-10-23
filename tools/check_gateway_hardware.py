#!/usr/bin/env python3
"""Exercise UART v2 on the Pi. RF diagnostic transmission requires --transmit.

The RF test sends three short non-EC messages addressed to the module itself;
it never sends switch control or registration packets. Capture on Nordic before
running with --transmit to independently verify bytes and duplicate suppression.
"""
import argparse
import binascii
import json
from pathlib import Path
import statistics
import time
from ec_gateway import GatewayClient, GatewayError
from decode_ec import decode_frame


def fields(reply):
    """Read named status values from a UART reply.

    @param reply Whitespace-separated gateway reply containing key=value fields.
    @return A mapping of field names to their string values.
    """
    return dict(s.split("=", 1) for s in reply.split() if "=" in s)


def main():
    """Run bounded UART v2 acceptance checks, with optional diagnostic RF."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", required=True)
    parser.add_argument("--log", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--transmit", action="store_true")
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Choose a new output filename")
    report = {
        "transmit_test_requested": args.transmit,
        "checks": [],
        "diagnostic_frames": [],
    }
    with GatewayClient(args.port, args.log) as c:

        def check(name, condition):
            """Record a successful acceptance check or stop the hardware trial.

            @param name Human-readable acceptance condition.
            @param condition Whether the condition was satisfied.
            """
            if not condition:
                raise RuntimeError(f"Hardware check failed: {name}")
            report["checks"].append(name)

        def rejected(command, expected):
            """Require a command to fail with the expected firmware error.

            @param command UART command whose rejection is under test.
            @param expected Exact error response required for acceptance.
            """
            try:
                c.command(command)
            except GatewayError as e:
                check(command.split()[0] + " rejected: " + expected, str(e) == expected)
            else:
                raise RuntimeError("Unexpected command acceptance: " + command)

        def direct(request_id, body):
            """Exercise request replay with a caller-selected UART request ID.

            @param request_id Wire request identifier, including a reused ID for replay tests.
            @param body ASCII command without its request ID.
            @return The complete matching RSP record, including the request ID.
            """
            line = f"{request_id} {body}"
            c._record(">", line)
            c.uart.write((line + "\n").encode("ascii"))
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline:
                record = c.read_line(deadline)
                if record and record.startswith(f"RSP {request_id} "):
                    return record
            raise RuntimeError("Direct request timed out")

        check("protocol version", c.command("PING") == "OK EC-GATEWAY 2")
        before = fields(c.command("STATUS"))
        report["initial_status"] = before
        check("starts disarmed", before["armed"] == before["ack"] == "0")
        c.command("CONFIG 2a992b12 a7 deadbeef")
        diagnostic = bytes.fromhex("0188422a992b122a992b12") + b"EC_UART_TEST_V2"
        rejected("TX " + diagnostic.hex(), "ERR DISARMED")
        rejected("ACK 1", "ERR DISARMED")
        rejected("ARM 61", "ERR LEASE")

        # Re-map a captured report to a synthetic peer. This never transmits.
        raw = bytearray.fromhex(
            "0188359bf4ea38467156210000000000000000ff009e0546715621000201000202000002001abe"
        )
        raw[3:7] = bytes.fromhex("2a992b12")
        raw[7:11] = raw[23:27] = bytes.fromhex("deadbeef")
        raw[19] = 0xA7
        raw[37:39] = binascii.crc_hqx(raw[21:37], 0xFFFF).to_bytes(2, "big")
        response = c.command("CHECK " + raw.hex())
        check("MCU generates ACK without RF", response.startswith("OK ACKHEX "))
        ack = decode_frame(bytes.fromhex(response.split()[-1]))
        check(
            "MCU ACK content",
            ack["source_wire"] == "2a992b12"
            and ack["destination_wire"] == "deadbeef"
            and ack["network_tag"] == "a7"
            and ack["checksum_ok"]
            and ack["data_hex"] == "0501000202000002",
        )
        bad = bytearray(raw)
        bad[-1] ^= 1
        check(
            "MCU rejects bad checksum", c.command("CHECK " + bad.hex()) == "OK NO_ACK"
        )
        bad = bytearray(raw)
        bad[7] ^= 1
        check("MCU rejects wrong peer", c.command("CHECK " + bad.hex()) == "OK NO_ACK")

        c.command("ARM 1")
        c.command("ACK 1")
        c.listen(1.3)
        state = fields(c.command("STATUS"))
        check("lease expires and clears ACK", state["armed"] == state["ack"] == "0")
        check("no transmissions in dry-run checks", state["tx"] == before["tx"])

        # Long input containing a valid command prefix must be discarded whole.
        error_before = int(state["input_errors"])
        c.command("ARM 5")
        c.uart.write((f"999 TX {diagnostic.hex()}" + " " * 400 + "\n").encode("ascii"))
        c.listen(0.2)
        state = fields(c.command("STATUS"))
        check(
            "overlong command discarded",
            int(state["input_errors"]) == error_before + 1
            and state["tx"] == before["tx"],
        )
        wrong = bytearray(diagnostic)
        wrong[7] ^= 1
        rejected("TX " + wrong.hex(), "ERR FRAME_POLICY")
        c.command("DISARM")

        request_id = c.next_id
        c.next_id += 1
        one = direct(request_id, "PING")
        check("duplicate ID replays response", direct(request_id, "PING") == one)
        check(
            "same ID different body rejected",
            direct(request_id, "STATUS").endswith("ERR ID_REUSE"),
        )

        timings = []
        for _ in range(100):
            start = time.monotonic()
            check_result = c.command("PING")
            if check_result != "OK EC-GATEWAY 2":
                raise RuntimeError("PING stress response mismatch")
            timings.append((time.monotonic() - start) * 1000)
        report["ping_ms"] = {
            "median": statistics.median(timings),
            "max": max(timings),
            "count": 100,
        }
        report["checks"].append("100 sequential requests with interleaved RX logs")

        if args.transmit:
            c.command("ARM 10")
            tx_before = int(fields(c.command("STATUS"))["tx"])
            for sequence in (0x42, 0x43, 0x44):
                raw = bytearray(diagnostic)
                raw[2] = sequence
                request_id = c.next_id
                c.next_id += 1
                body = "TX " + raw.hex()
                response = direct(request_id, body)
                check(
                    f"RF diagnostic {sequence} succeeded", response.endswith("OK TX 0")
                )
                check(
                    f"RF diagnostic {sequence} duplicate cached",
                    direct(request_id, body) == response,
                )
                report["diagnostic_frames"].append(raw.hex())
                c.listen(0.15)
            state = fields(c.command("STATUS"))
            check("exactly three transmit attempts", int(state["tx"]) - tx_before == 3)
            c.command("DISARM")
        report["final_status"] = fields(c.command("STATUS"))
        check(
            "final state disarmed",
            report["final_status"]["armed"] == report["final_status"]["ack"] == "0",
        )
        check(
            "UART and RX logging intact",
            report["final_status"]["uart_drops"] == before["uart_drops"]
            and report["final_status"]["rx_drops"] == before["rx_drops"],
        )
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
