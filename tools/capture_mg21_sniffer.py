#!/usr/bin/env python3
"""Capture ESN1 passive MG21 records using radio sync-word-end timestamps.

No command or RF transmission is performed. PC time supplies only a constant
wall-clock offset; all packet spacing comes from the extended radio clock.
"""
import argparse
import binascii
import json
from pathlib import Path
import struct
import time

from capture_cc26xx import block, packet

HEADER = struct.Struct("<4sHBBIQ")
COUNTERS = (
    "rf_overflows",
    "uart_dropped_bytes",
    "bad_packets",
    "timestamp_errors",
    "calibration_errors",
    "uart_rx_errors",
    "uart_dma_errors",
)


class Decoder:
    """Decode bounded CRC-protected records across arbitrary serial chunks."""

    def __init__(self):
        """Initialize framing, loss and sequence tracking."""
        self.buffer = bytearray()
        self.discarded = 0
        self.crc_errors = 0
        self.sequence_gaps = 0
        self.previous = None

    def feed(self, data):
        """Return complete records and retain only a bounded incomplete suffix.

        @param data Newly received binary bytes.
        @return List of validated records; reset/backward sequence raises ValueError.
        """
        self.buffer.extend(data)
        records = []
        while len(self.buffer) >= HEADER.size:
            if self.buffer[:4] != b"ESN1":
                pos = self.buffer.find(b"ESN1", 1)
                count = pos if pos >= 0 else len(self.buffer) - 3
                self.discarded += count
                del self.buffer[:count]
                continue
            _, length, kind, flags, sequence, timestamp = HEADER.unpack_from(
                self.buffer
            )
            if length > 129 or kind not in (1, 2) or flags != 1:
                self.discarded += 1
                del self.buffer[0]
                continue
            size = HEADER.size + length + 4
            if len(self.buffer) < size:
                break
            wire = bytes(self.buffer[:size])
            if binascii.crc32(wire[:-4]) != struct.unpack_from("<I", wire, size - 4)[0]:
                self.crc_errors += 1
                self.discarded += 1
                del self.buffer[0]
                continue
            del self.buffer[:size]
            if self.previous is not None:
                gap = (sequence - self.previous - 1) & 0xFFFFFFFF
                if gap >= 0x80000000:
                    raise ValueError(
                        "Radio reset, duplicate or backward record sequence"
                    )
                self.sequence_gaps += gap
            self.previous = sequence
            body = wire[HEADER.size : -4]
            row = dict(kind=kind, sequence=sequence, timestamp_us=timestamp)
            if kind == 1:
                if not 5 <= length <= 129:
                    raise ValueError("Invalid RX body length")
                rssi, lqi, channel, fcs = struct.unpack_from("<bBBB", body)
                if channel != 11 or fcs != 0:
                    raise ValueError("Unsupported channel/FCS encoding")
                row.update(rssi=rssi, lqi=lqi, raw=body[4:])
            else:
                if length < 37:
                    raise ValueError("Invalid status body length")
                major, minor, baud, *values = struct.unpack_from("<HH8I", body)
                row.update(
                    version=f"{major}.{minor}",
                    baud=baud,
                    counters=dict(zip(COUNTERS, values)),
                    identity=body[36:].rstrip(b"\0").decode("ascii"),
                )
            records.append(row)
        return records


def main():
    """Capture a bounded session into new pcapng, raw UART and metadata files."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", required=True)
    parser.add_argument("--baud", type=int, default=921600)
    parser.add_argument("--duration", type=float, default=180)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not 0 < args.duration <= 3600 or args.output.suffix != ".pcapng":
        parser.error("Use a .pcapng path and duration in (0,3600] seconds")
    binary = args.output.with_suffix(".uart.bin")
    metadata = args.output.with_suffix(".json")
    if any(p.exists() for p in (args.output, binary, metadata)):
        parser.error("Output files must be new")
    import serial

    decoder = Decoder()
    result = dict(
        started_at=time.time(),
        port=args.port,
        baud=args.baud,
        timestamp_position="sync_word_end",
        timestamp_hz=1000000,
        wall_clock_offset_source="first_record_host_receipt",
        packets=0,
        statuses=[],
    )
    offset = None
    try:
        with (
            serial.Serial(
                args.port,
                args.baud,
                timeout=0.1,
                exclusive=True,
                rtscts=False,
                dsrdtr=False,
            ) as uart,
            args.output.open("xb") as out,
            binary.open("xb") as rawlog,
        ):
            uart.reset_input_buffer()
            out.write(block(0x0A0D0D0A, struct.pack("<IHHq", 0x1A2B3C4D, 1, 0, -1)))
            out.write(block(1, struct.pack("<HHI", 283, 0, 256)))
            deadline = time.monotonic() + args.duration
            while time.monotonic() < deadline:
                chunk = uart.read(max(1, min(8192, uart.in_waiting)))
                received = time.time_ns() // 1000
                rawlog.write(chunk)
                for row in decoder.feed(chunk):
                    if offset is None:
                        offset = received - row["timestamp_us"]
                        result["epoch_offset_us"] = offset
                    if row["kind"] == 1:
                        out.write(
                            packet(
                                offset + row["timestamp_us"],
                                row["raw"],
                                row["rssi"],
                                row["lqi"],
                            )
                        )
                        result["packets"] += 1
                    else:
                        if (
                            row["baud"] != args.baud
                            or row["identity"] != "EC-MG21-BOARD=sonoff-zbdongle-e"
                        ):
                            raise ValueError("Unexpected sniffer board/baud")
                        result["statuses"].append(
                            dict(host_received_us=received, **row)
                        )
            if not result["statuses"] or not result["packets"]:
                raise RuntimeError("No validated status/packet records")
            first, last = result["statuses"][0], result["statuses"][-1]
            result["counter_deltas"] = {
                k: (last["counters"][k] - first["counters"][k]) & 0xFFFFFFFF
                for k in COUNTERS
            }
    except BaseException as error:
        result["error"] = repr(error)
        raise
    finally:
        result.update(
            finished_at=time.time(),
            sequence_gaps=decoder.sequence_gaps,
            crc_errors=decoder.crc_errors,
            resync_bytes=decoder.discarded,
            incomplete_tail_bytes=len(decoder.buffer),
        )
        metadata.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({k: v for k, v in result.items() if k != "statuses"}, indent=2))


if __name__ == "__main__":
    main()
