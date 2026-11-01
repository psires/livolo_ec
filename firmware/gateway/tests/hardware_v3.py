#!/usr/bin/env python3
"""Bounded v3 hardware checks; --air adds diagnostics and read-only spare queries.

Uses an exclusive UART owner. Always disarms on exit. Does not set outputs.
"""
import argparse
import json
from pathlib import Path
import secrets
import time
import serial
from test_uart_v3 import report, GW, DEV


class Client:
    def __init__(self, port, log, baudrate=115200):
        """Open an exclusive UART connection and a fresh timestamped log.

        @param port UART device path to open exclusively.
        @param log Path for a new timestamped UART log.
        @param baudrate Application baud matching the firmware build."""
        self.log = log.open("x")
        self.uart = serial.Serial(
            port, baudrate, timeout=0.05, write_timeout=1, exclusive=True
        )
        self.id = secrets.randbelow(0xFFFFFF00) + 1
        self.records = []
        self.pending = bytearray()

    def line(self, deadline):
        """Read and record one UART line before the supplied deadline.

        @param deadline Absolute monotonic time at which waiting must stop.

        @return The next decoded line, or None when the deadline expires."""
        while time.monotonic() < deadline:
            if b"\n" in self.pending:
                raw, _, rest = self.pending.partition(b"\n")
                self.pending = bytearray(rest)
                line = raw.decode("ascii", errors="backslashreplace").rstrip("\r")
                self.log.write(f"{time.time_ns()} < {line}\n")
                self.log.flush()
                self.records.append(line)
                return line
            self.pending.extend(self.uart.read(max(1, min(self.uart.in_waiting, 4096))))
            if len(self.pending) > 8192:
                raise RuntimeError("UART framing overflow")
        return None

    def command(self, text, expected="OK", ident=None):
        """Execute an identified UART request and verify its response prefix.

        @param text Command or response text to process.
        @param expected Required response prefix, or None to accept any response.
        @param ident Explicit request identifier, or None to allocate a fresh identifier.

        @return Response body without its request envelope."""
        if ident is None:
            ident = self.id
            self.id = (self.id % 0xFFFFFFFF) + 1
        line = f"{ident} {text}"
        # Opening a live UART can start mid-line. A malformed/lost first reply
        # is recovered by replaying the exact cached request, never a new ID.
        for attempt in range(2):
            self.log.write(f"{time.time_ns()} > {line}\n")
            self.log.flush()
            self.uart.write((line + "\n").encode())
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline:
                reply = self.line(deadline)
                if reply and reply.startswith(f"RSP {ident} "):
                    body = reply[len(f"RSP {ident} ") :]
                    if expected is not None and not body.startswith(expected):
                        raise AssertionError((text, expected, body))
                    return body
        raise TimeoutError(text)

    def listen(self, seconds):
        """Collect unsolicited UART records for a bounded duration.

        @param seconds Duration in seconds."""
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            self.line(deadline)


def fields(text):
    """Extract named status fields from a gateway response.

    @param text Command or response text to process.

    @return Mapping of field names to their string values."""
    return dict(word.split("=", 1) for word in text.split() if "=" in word)


def main():
    """Run the selected bounded hardware checks and save their results."""
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--port", required=True)
    p.add_argument(
        "--baud", type=int, choices=(115200, 230400, 460800, 921600), default=115200
    )
    p.add_argument("--log", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--air", action="store_true")
    p.add_argument("--boot-only", action="store_true")
    p.add_argument("--stale-session")
    args = p.parse_args()
    if args.output.exists() or args.air and args.boot_only:
        p.error("Choose new output and one mode")
    c = Client(args.port, args.log, args.baud)
    epoch = secrets.token_hex(8)
    result = {
        "session": epoch,
        "baudrate": args.baud,
        "checks": [],
        "air": args.air,
        "boot_only": args.boot_only,
    }

    def check(name, condition=True):
        """Record a successful hardware check or fail with its name.

        @param name Human-readable name of the hardware check.
        @param condition Whether the named check passed."""
        if not condition:
            raise AssertionError(name)
        result["checks"].append(name)

    def send(raw, repeat=False):
        """Send a diagnostic frame while checking replay behavior and CCA retries.

        @param raw Raw radio frame bytes.
        @param repeat Whether to replay the request and require the cached response."""
        for _ in range(4):
            ident = c.id
            c.id += 1
            response = c.command(
                f"SEND {epoch} " + raw.hex(), expected=None, ident=ident
            )
            if repeat:
                assert (
                    c.command(f"SEND {epoch} " + raw.hex(), expected=None, ident=ident)
                    == response
                )
            result.setdefault("air_sends", []).append(
                {"raw_hex": raw.hex(), "response": response}
            )
            if response == "OK TX 0":
                return
            if response != "ERR TX 2":
                raise AssertionError(response)
            c.listen(0.03)
        raise AssertionError("Persistent CCA busy")

    try:
        c.command("PING", "OK EC-GATEWAY 3")
        result["caps"] = fields(c.command("CAPS"))
        check(
            "v3 capabilities",
            result["caps"]["peers"] == "256"
            and result["caps"]["persistence"] == "none",
        )
        result["initial_status"] = fields(c.command("STATUS"))
        if args.boot_only:
            s = result["initial_status"]
            check(
                "reset clears network/table/session/lease",
                s["configured"] == "0"
                and s["peers"] == "0"
                and s["session"] == "0000000000000000"
                and s["armed"] == "0"
                and s["ack"] == "0",
            )
            if args.stale_session:
                raw = b"\x01\x88\x42" + bytes.fromhex(GW) * 2 + b"EC_V3_OLD_SESSION"
                c.command(
                    f"SEND {args.stale_session} " + raw.hex(), "ERR NOT_CONFIGURED"
                )
                check("old SEND rejected after physical reset")
        else:
            c.command("DISARM")
            c.command(f"NETWORK {GW} a7 {epoch}")
            for i in range(256):
                c.command(f"PEER SET {i} {0x10000000+i:08x} 1f")
            check("256 peers installed", fields(c.command("STATUS"))["peers"] == "256")
            for i in range(256):
                c.command(
                    "CHECK " + report(device=f"{0x10000000+i:08x}").hex(), "OK ACKHEX"
                )
            check("ACK lookup for every table slot")
            c.command("PEER SET 256 12345678 1f", "ERR PEER")
            c.command("PEER SET 255 10000000 1f", "ERR DUPLICATE_PEER")
            c.command("PEER SET 255 ffffffff 1f", "ERR PEER")
            c.command("PEER SET 255 100000ff 20", "ERR PEER")
            check("table bounds, duplicate identity and invalid mask rejected")
            c.command("PEER SET 255 100000ff 00")
            c.command("CHECK " + report(device="100000ff").hex(), "OK NO_ACK")
            c.command("PEER SET 255 100000ff 11")
            c.command(
                "CHECK " + report(device="100000ff", broadcast=True).hex(), "OK ACKHEX"
            )
            c.command("CHECK " + report(device="100000ff", opcode=8).hex(), "OK NO_ACK")
            check("per-peer ACK mask controls actual C core")
            c.command("ARM 30", "ERR SESSION")
            c.command("ACK 1", "ERR SESSION")
            c.command("TX 018842" + GW + GW + "45435f5633", "ERR SESSION")
            c.command("ARM ffffffffffffffff 1", "ERR SESSION")
            c.command(f"ARM {epoch} 61", "ERR LEASE")
            c.command(f"ACK {epoch} 1", "ERR DISARMED")
            c.command(f"ARM {epoch} 1")
            c.command(f"ACK {epoch} 1")
            c.command("PEER CLEAR", "ERR ARMED")
            c.listen(1.2)
            s = fields(c.command("STATUS"))
            check(
                "lease expiry disables TX and ACK",
                s["armed"] == "0" and s["ack"] == "0",
            )
            check("session guards reject legacy and stale requests")
            c.command("META 1")
            c.listen(1)
            check(
                "callback metadata received",
                any(x.startswith("RXMETA ") for x in c.records),
            )
            c.command("META 0")
            ident = c.id
            c.id += 1
            c.command("PEER DEL 0", ident=ident)
            c.command("PEER DEL 0", ident=ident)
            c.command("PING", "ERR ID_REUSE", ident=ident)
            check(
                "duplicate mutation cached and ID reuse rejected",
                fields(c.command("STATUS"))["peers"] == "255",
            )
            c.command("PEER SET 0 10000000 1f")
            c.uart.write(b"999 PING" + b" " * 400 + b"\n")
            check(
                "overlong UART line rejected",
                int(fields(c.command("STATUS"))["input_errors"]) >= 1,
            )
            before = fields(c.command("STATUS"))
            check(
                "no RF transmissions during dry checks",
                before["tx"] == result["initial_status"]["tx"]
                and before["ack_tx"] == result["initial_status"]["ack_tx"],
            )
            if args.air:
                c.command(f"PEER SET 255 {DEV} 1f")
                c.command(f"ARM {epoch} 30")
                c.command(f"ACK {epoch} 1")
                c.command("META 1")
                for seq in (0x51, 0x52, 0x53):
                    send(
                        bytes((1, 0x88, seq))
                        + bytes.fromhex(GW) * 2
                        + b"EC_V3_DUP_TEST",
                        repeat=True,
                    )
                # Query an already paired spare; never change outputs.
                for seq, opcode in ((0x61, 0x10), (0x62, 0xAC), (0x63, 0xAC)):
                    raw = bytearray.fromhex(
                        f"0188{seq:02x}"
                        + DEV
                        + GW
                        + "00000000"
                        + DEV
                        + "a71e8c"
                        + f"{opcode:02x}"
                        + DEV
                        + "00020000000000000001"
                    )
                    raw += bytes((sum(raw[21:37]) & 255, 0, 0, 0))
                    send(bytes(raw))
                    c.listen(0.2)
                c.listen(12)
                after = fields(c.command("STATUS"))
                result["air_status"] = after
                check("real peer in last slot auto-ACKed", int(after["ack_ok"]) > 0)
            c.command("DISARM")
            c.command("PEER CLEAR")
            c.command(f"NETWORK {GW} a7 1111222233334444")
            raw = b"\x01\x88\x42" + bytes.fromhex(GW) * 2 + b"EC_V3_OLD_SESSION"
            c.command(f"SEND {epoch} " + raw.hex(), "ERR SESSION")
            check("stale SEND rejected after session replacement")
            result["installed_session"] = "1111222233334444"
        result["passed"] = True
    finally:
        try:
            c.command("DISARM")
            result["final_status"] = fields(c.command("STATUS"))
            result["ack_records"] = [x for x in c.records if x.startswith("ACKTX ")]
        finally:
            c.uart.close()
            c.log.close()
            args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result), flush=True)


if __name__ == "__main__":
    main()
