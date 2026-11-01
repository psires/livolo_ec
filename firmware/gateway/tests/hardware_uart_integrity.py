#!/usr/bin/env python3
"""Check long UART commands and replies on a configured, disarmed adapter.

Stop the daemon first. This test does not configure the network, arm the radio,
set outputs, or retry failed requests. Existing dry hardware_v3 checks leave a
suitable configured/disarmed adapter. The caller must select the exact port.
"""

import argparse
import json
from pathlib import Path
import secrets
import time

import serial


def fields(reply):
    """Read named fields from one UART response.

    @param reply Response body without its request envelope.
    """
    return dict(word.split("=", 1) for word in reply.split() if "=" in word)


class Link:
    def __init__(self, uart, log):
        """Use an already exclusively opened adapter and evidence log.

        @param uart PySerial connection owned by the caller.
        @param log Writable text stream recording received lines.
        """
        self.uart = uart
        self.log = log
        self.ident = secrets.randbelow(0xFFFFFF00) + 1
        self.pending = bytearray()

    def request(self, command):
        """Send exactly once and accept only the matching complete response.

        @param command UART command body without ID or newline.
        @return Response body, or None after one second without a matching reply.
        """
        self.ident = self.ident % 0xFFFFFFFF + 1
        prefix = f"RSP {self.ident} "
        wire = f"{self.ident} {command}\n".encode("ascii")
        self.log.write(f"{time.time()} > {wire.decode().rstrip()}\n")
        self.uart.write(wire)
        deadline = time.monotonic() + 1
        while time.monotonic() < deadline:
            if b"\n" not in self.pending:
                self.pending.extend(
                    self.uart.read(max(1, min(4096, self.uart.in_waiting)))
                )
                if len(self.pending) > 8192:
                    raise RuntimeError("UART framing overflow")
                continue
            raw, _, rest = self.pending.partition(b"\n")
            self.pending = bytearray(rest)
            line = raw.decode("ascii", errors="backslashreplace").rstrip("\r")
            self.log.write(f"{time.time()} < {line}\n")
            if line.startswith(prefix):
                return line[len(prefix) :]
        return None


def main():
    """Run bounded alternating CAPS/CHECK requests and preserve all failures."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", required=True)
    parser.add_argument(
        "--baud", type=int, choices=(115200, 230400, 460800, 921600), required=True
    )
    parser.add_argument("--samples", type=int, default=2000)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--log", type=Path, required=True)
    args = parser.parse_args()
    if not 2 <= args.samples <= 10000 or args.samples % 2:
        parser.error("samples must be an even number from 2 through 10000")
    if args.output.exists() or args.log.exists() or args.output == args.log:
        parser.error("choose distinct, new output and log paths")
    result = dict(
        started_at=time.time(),
        baud=args.baud,
        samples=args.samples,
        completed=0,
        mismatches=[],
        timeouts=[],
        passed=False,
    )
    try:
        with (
            serial.Serial(
                args.port,
                args.baud,
                timeout=0.02,
                write_timeout=1,
                exclusive=True,
                rtscts=False,
                dsrdtr=False,
            ) as uart,
            args.log.open("x") as log,
        ):
            uart.write(b"\n")
            uart.reset_input_buffer()
            link = Link(uart, log)
            caps = link.request("CAPS")
            status = link.request("STATUS")
            if caps is None or status is None:
                raise RuntimeError("Missing preflight response")
            before = fields(status)
            required = {
                "session",
                "tx",
                "ack_tx",
                "input_errors",
                "uart_drops",
                "rx_drops",
            }
            if not (
                caps.startswith("OK ")
                and fields(caps).get("proto") == "3"
                and fields(caps).get("max_raw") == "125"
                and fields(caps).get("baud") == str(args.baud)
                and required <= before.keys()
                and before.get("configured") == "1"
                and before.get("armed") == "0"
                and before.get("ack") == "0"
            ):
                raise RuntimeError("Require matching baud and configured/disarmed v3")
            result.update(caps=caps, before=before)
            for number in range(args.samples):
                raw = bytes((offset + number) % 256 for offset in range(125))
                command = "CAPS" if number % 2 == 0 else "CHECK " + raw.hex()
                expected = caps if number % 2 == 0 else "OK NO_ACK"
                actual = link.request(command)
                if actual is None:
                    result["timeouts"].append(number + 1)
                elif actual != expected:
                    result["mismatches"].append(dict(sample=number + 1, actual=actual))
                result["completed"] += 1
                time.sleep(0.005)
            status = link.request("STATUS")
            if status is None:
                raise RuntimeError("Missing final status")
            after = fields(status)
            if not status.startswith("OK ") or not before.keys() <= after.keys():
                raise RuntimeError("Incomplete final status")
            result["after"] = after
            counters = (
                "tx",
                "ack_tx",
                "input_errors",
                "uart_drops",
                "rx_drops",
                "radio_drops",
                "uart_rx_errors",
                "uart_rx_overruns",
                "uart_framing_errors",
                "uart_parity_errors",
                "uart_rx_dma_errors",
                "uart_rx_resyncs",
            )
            result["deltas"] = {
                key: int(after[key]) - int(before[key])
                for key in counters
                if key in before and key in after
            }
            result["passed"] = (
                not result["mismatches"]
                and not result["timeouts"]
                and all(value == 0 for value in result["deltas"].values())
                and after.get("session") == before.get("session")
            )
    except Exception as error:
        result["error"] = repr(error)
        raise
    finally:
        result["finished_at"] = time.time()
        args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(
        json.dumps(
            {k: v for k, v in result.items() if k not in ("caps", "before", "after")}
        )
    )
    if not result["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
