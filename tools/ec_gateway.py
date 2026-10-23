#!/usr/bin/env python3
"""Client for EC-GATEWAY UART v2. Commands, captures, and logs share one owner.

One request at a time. A timeout retries the same ID and bytes; firmware caches
the last reply so a lost UART response does not transmit a command twice.
Context-manager exit disarms a lease acquired by this client. Hardware also
expires it independently. UART ownership is exclusive; stop capture clients first.
"""
import argparse
from collections import deque
import json
from pathlib import Path
import secrets
import time


class GatewayError(RuntimeError):
    pass


class GatewayClient:
    def __init__(self, port, log=None, timeout=2.0):
        """Acquire exclusive UART ownership for bounded gateway requests.

        @param port Serial device path.
        @param log Optional new transcript file path.
        @param timeout Response deadline in seconds for each request attempt.
        """
        import serial

        self.uart = serial.Serial(
            port,
            115200,
            timeout=0.1,
            write_timeout=1,
            exclusive=True,
            rtscts=False,
            dsrdtr=False,
        )
        self.timeout = timeout
        self.next_id = secrets.randbelow(0xFFFFFF00) + 1
        self.pending = bytearray()
        self.records = deque(maxlen=4096)
        self.armed_by_us = False
        self.uncertain = False
        try:
            self.log = Path(log).open("x") if log else None
        except OSError:
            self.uart.close()
            raise

    def __enter__(self):
        """Expose the owned UART client for the context lifetime.

        @return This GatewayClient instance.
        """
        return self

    def __exit__(self, typ, value, traceback):
        """Release the UART and log, attempting to disarm any acquired lease.

        @param typ Exception type from the context, or None.
        @param value Exception instance from the context, or None.
        @param traceback Context exception traceback, or None.
        """
        try:
            if self.armed_by_us:
                try:
                    # An unresolved request forbids another ordinary command,
                    # but an emergency DISARM is always permitted.
                    self.command("DISARM", retries=0)
                except (GatewayError, OSError):
                    pass  # The MCU lease still expires without this link.
        finally:
            self.uart.close()
            if self.log:
                self.log.close()

    def _record(self, direction, line):
        """Persist a timestamped UART exchange when logging is enabled.

        @param direction Transcript direction marker for sent or received traffic.
        @param line Decoded UART line without a newline.
        """
        if self.log:
            self.log.write(f"{time.time_ns()} {direction} {line}\n")
            self.log.flush()

    def read_line(self, deadline):
        """Receive one complete UART record within an absolute deadline.

        @param deadline Monotonic time in seconds after which reception stops.
        @return A decoded UART record, or None if the deadline expires.
        """
        while time.monotonic() < deadline:
            if b"\n" in self.pending:
                raw, _, rest = self.pending.partition(b"\n")
                self.pending = bytearray(rest)
                line = raw.decode("ascii", errors="backslashreplace").rstrip("\r")
                self.records.append(line)
                self._record("<", line)
                return line
            data = self.uart.read(max(1, min(self.uart.in_waiting, 4096)))
            self.pending.extend(data)
            if len(self.pending) > 8192:
                raise GatewayError("UART line exceeds protocol limit")
        return None

    def command(self, text, retries=1):
        """Obtain one command result without duplicating an uncertain RF action.

        @param text Printable ASCII command without a request ID or newline.
        @param retries Additional UART attempts using the same ID and command bytes.
        @return The successful OK reply, excluding the RSP request envelope.
        """
        if self.uncertain and text != "DISARM":
            raise GatewayError("Previous request outcome unknown; disarm and reconnect")
        if not text or any(not 32 <= ord(c) <= 126 for c in text):
            raise ValueError("One ASCII command is required")
        request_id = self.next_id
        self.next_id = (self.next_id % 0xFFFFFFFF) + 1
        request = f"{request_id} {text}"
        if len(request) >= 320:
            raise ValueError("Command exceeds MCU input buffer")
        # Treat a possibly lost ARM response as an acquired lease for cleanup.
        if text.startswith("ARM "):
            self.armed_by_us = True
        for attempt in range(retries + 1):
            self._record(">", request)
            try:
                self.uart.write((request + "\n").encode("ascii"))
            except OSError as error:
                self.uncertain = True
                raise GatewayError("UART write failed; outcome unknown") from error
            deadline = time.monotonic() + self.timeout
            while time.monotonic() < deadline:
                line = self.read_line(deadline)
                if line is None:
                    break
                prefix = f"RSP {request_id} "
                if not line.startswith(prefix):
                    continue
                result = line[len(prefix) :]
                if result.startswith("ERR "):
                    raise GatewayError(result)
                if result != "OK" and not result.startswith("OK "):
                    raise GatewayError(f"Malformed response: {line}")
                if text == "DISARM" or text.startswith("CONFIG "):
                    self.armed_by_us = False
                if text == "DISARM":
                    self.uncertain = False
                return result
        self.uncertain = True
        raise GatewayError(f"No response to request {request_id}; outcome unknown")

    def listen(self, seconds):
        """Collect unsolicited UART events for a bounded interval.

        @param seconds Maximum observation duration in seconds.
        """
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            self.read_line(deadline)


def ec_frame(gateway, device, tag, sequence, opcode, data, route=True):
    """Build an observed family-8c command with a zero timing trailer.

    @param gateway Four-byte gateway identity as eight wire-order hexadecimal digits.
    @param device Four-byte destination identity as eight wire-order hexadecimal digits.
    @param tag Network tag byte.
    @param sequence MAC sequence byte.
    @param opcode Application operation byte.
    @param data Eight application-data bytes encoded as hexadecimal.
    @param route Whether to include the destination in the observed route field.
    @return The 41-byte command frame without a MAC FCS.
    """
    gateway, device, data = (
        bytes.fromhex(gateway),
        bytes.fromhex(device),
        bytes.fromhex(data),
    )
    if len(gateway) != 4 or len(device) != 4 or len(data) != 8:
        raise ValueError("Four-byte identities and eight-byte data required")
    if not all(0 <= n <= 255 for n in (tag, sequence, opcode)):
        raise ValueError("Tag, sequence and opcode must fit a byte")
    raw = bytearray(41)
    raw[:3] = bytes((1, 0x88, sequence))
    raw[3:7], raw[7:11] = device, gateway
    if route:
        raw[15:19] = device
    raw[19:23] = bytes((tag, 0x1E if route else 0x0E, 0x8C, opcode))
    raw[23:29] = device + b"\0\x02"
    raw[29:37] = data
    raw[37] = sum(raw[21:37]) & 255
    return bytes(raw)


def ec_heartbeat(gateway, tag, sequence, seconds, parameter):
    """Build a broadcast heartbeat advertising the gateway time and mode.

    @param gateway Four-byte gateway identity as wire-order hexadecimal.
    @param tag Network tag byte.
    @param sequence MAC sequence byte.
    @param seconds Unsigned 32-bit heartbeat counter in seconds.
    @param parameter Unsigned 32-bit heartbeat mode parameter.
    @return The 41-byte broadcast heartbeat frame without a MAC FCS.
    """
    raw = bytearray(
        ec_frame(
            gateway,
            "ffffffff",
            tag,
            sequence,
            8,
            (seconds.to_bytes(4, "little") + parameter.to_bytes(4, "little")).hex(),
            route=False,
        )
    )
    raw[23:29] = bytes(6)
    raw[37] = sum(raw[21:37]) & 255
    return bytes(raw)


def main():
    """Run the requested UART v2 status, disarm, validation or send operation."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", required=True)
    parser.add_argument("--log", type=Path)
    sub = parser.add_subparsers(dest="action", required=True)
    sub.add_parser("status")
    sub.add_parser("disarm")
    for name in ("check", "send"):
        cmd = sub.add_parser(name)
        cmd.add_argument("--gateway", required=True)
        cmd.add_argument("--tag", required=True, help="Two hex digits")
        cmd.add_argument("--device", required=True)
        cmd.add_argument("--frame", required=True, help="Raw MAC hex without FCS")
    args = parser.parse_args()
    with GatewayClient(args.port, args.log) as client:
        if args.action in ("status", "disarm"):
            result = client.command(args.action.upper())
        else:
            client.command(f"CONFIG {args.gateway} {args.tag} {args.device}")
            if args.action == "send":
                client.command("ARM 5")
            result = client.command(
                f'{"CHECK" if args.action == "check" else "TX"} {args.frame}'
            )
        print(json.dumps({"result": result}))


if __name__ == "__main__":
    main()
