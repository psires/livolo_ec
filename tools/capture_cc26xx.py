#!/usr/bin/env python3
"""Capture EC-RECEIVER UART output to IEEE 802.15.4 TAP pcapng.

Timestamps are host UART receipt times, not RF arrival times. Firmware tick and
sequence values remain in the accompanying UART log. No bytes are transmitted.
"""
import argparse
import json
from pathlib import Path
import struct
import time


def block(kind, body):
    """Encode one aligned pcapng block for a capture file.

    @param kind pcapng block type number.
    @param body Unpadded block payload bytes.
    @return Complete little-endian pcapng block bytes, including length fields.
    """
    body += b"\0" * (-len(body) % 4)
    size = 12 + len(body)
    return struct.pack("<II", kind, size) + body + struct.pack("<I", size)


def packet(timestamp_us, raw, rssi, lqi):
    # FCS omitted, channel 11/page 0, RSSI float, LQI byte.
    """Encode a received radio frame with its TAP metadata.

    @param timestamp_us Host receipt time in microseconds since the Unix epoch.
    @param raw MAC frame bytes without an FCS.
    @param rssi Received signal strength in dBm.
    @param lqi Link quality value from 0 through 255.
    @return An enhanced packet block with channel-11 TAP metadata and no FCS.
    """
    tap = struct.pack("<BBH", 0, 0, 36)
    tap += struct.pack("<HHB3x", 0, 1, 0)
    tap += struct.pack("<HHf", 1, 4, rssi)
    tap += struct.pack("<HHHBx", 3, 3, 11, 0)
    tap += struct.pack("<HHB3x", 10, 1, lqi)
    data = tap + raw
    body = (
        struct.pack(
            "<IIIII",
            0,
            timestamp_us >> 32,
            timestamp_us & 0xFFFFFFFF,
            len(data),
            len(data),
        )
        + data
    )
    return block(6, body)


def main():
    """Capture receiver UART records to new pcapng and diagnostic files."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", required=True)
    parser.add_argument("--duration", type=float, default=30)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    log = args.output.with_suffix(".uart.log")
    if args.output.suffix != ".pcapng" or any(p.exists() for p in (args.output, log)):
        parser.error("Choose a new .pcapng output filename")
    if not 0 < args.duration <= 3600:
        parser.error("Duration must be in (0, 3600] seconds")
    import serial

    args.output.parent.mkdir(parents=True, exist_ok=True)
    count = malformed = gaps = 0
    previous = None
    with (
        serial.Serial(args.port, 115200, timeout=0.25, exclusive=True) as uart,
        args.output.open("xb") as out,
        log.open("xb") as diagnostics,
    ):
        out.write(block(0x0A0D0D0A, struct.pack("<IHHq", 0x1A2B3C4D, 1, 0, -1)))
        out.write(block(1, struct.pack("<HHI", 283, 0, 256)))
        uart.reset_input_buffer()
        deadline = time.monotonic() + args.duration
        while time.monotonic() < deadline:
            line = uart.readline()
            timestamp = time.time_ns() // 1000
            if not line:
                continue
            diagnostics.write(line)
            if not line.startswith(b"RX "):
                continue
            try:
                _, sequence, ticks, rssi, lqi, size, hexdata = line.split()
                sequence, ticks, rssi, lqi, size = map(
                    int, (sequence, ticks, rssi, lqi, size)
                )
                raw = bytes.fromhex(hexdata.decode("ascii"))
                if (
                    len(raw) != size
                    or not 1 <= size <= 125
                    or not -128 <= rssi <= 127
                    or not 0 <= lqi <= 255
                ):
                    raise ValueError("Invalid RX record")
            except (ValueError, UnicodeError):
                malformed += 1
                continue
            if previous is not None and sequence != (previous + 1) & 0xFFFFFFFF:
                gaps += 1
            previous = sequence
            out.write(packet(timestamp, raw, rssi, lqi))
            count += 1
    print(
        json.dumps(
            {
                "packets": count,
                "malformed_records": malformed,
                "sequence_discontinuities": gaps,
                "output": str(args.output),
            }
        )
    )
    if not count or malformed or gaps:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
