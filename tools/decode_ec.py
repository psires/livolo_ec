#!/usr/bin/env python3
"""Offline Livolo EC capture decoder. No dependencies and no radio transmission.

Offsets refer to the captured MAC frame after the IEEE 802.15.4 TAP header.
This deliberately recognizes only the two EC layouts found in this repository.
Semantic names are hypotheses where noted in analysis/protocol.md.
"""

import argparse
import binascii
import collections
import csv
import datetime
import hashlib
import json
from pathlib import Path
import struct


ROOT = Path(__file__).resolve().parents[1]
GATEWAY = "9bf4ea38"  # Wire order, NOT a standard 32-bit MAC address.
SWITCH = "46715621"
OPCODES = {
    0x01: "registration_candidate",
    0x02: "set_outputs",
    0x05: "state_report",
    0x07: "announcement",
    0x08: "gateway_heartbeat",
    0x09: "announcement_variant",
    0x0A: "poll_candidate",
    0x0C: "discovery_candidate",
    0x10: "query_10",
    0x11: "maintenance_candidate",
    0x15: "property_report",
    0xA0: "capabilities_candidate",
    0xAC: "query_ac",
    0xFA: "ack_response",
}


def options(data, endian):
    """Yield pcapng or TAP option values, rejecting truncated records.

    @param data Encoded option bytes including alignment padding.
    @param endian Struct byte-order prefix: < for little-endian or > for big-endian.
    @return An iterator of numeric option types and unpadded value bytes.
    """
    pos = 0
    while pos < len(data):
        if pos + 4 > len(data):
            raise ValueError("Truncated option header")
        kind, size = struct.unpack_from(endian + "HH", data, pos)
        pos += 4
        if pos + size > len(data):
            raise ValueError("Truncated option value")
        yield kind, data[pos : pos + size]
        pos += (size + 3) & ~3


def read_pcapng(path):
    """Yield numbered radio frames, timestamps and metadata from a TAP capture.

    @param path Path to a pcapng file using IEEE 802.15.4 TAP without captured FCS.
    @return An iterator of frame number, Unix timestamp in seconds, frame bytes,
        and TAP metadata dictionaries; frame numbers start at one.
    """
    data = Path(path).read_bytes()
    pos, number, endian, interfaces = 0, 0, None, []
    while pos < len(data):
        if pos + 12 > len(data):
            raise ValueError("Truncated pcapng block")
        if data[pos : pos + 4] == b"\x0a\x0d\x0d\x0a":
            bom = data[pos + 8 : pos + 12]
            endian = {b"\x4d\x3c\x2b\x1a": "<", b"\x1a\x2b\x3c\x4d": ">"}.get(bom)
            if endian is None:
                raise ValueError("Invalid pcapng byte order")
            interfaces = []
        if endian is None:
            raise ValueError("Missing pcapng section header")
        kind, size = struct.unpack_from(endian + "II", data, pos)
        if size < 12 or size % 4 or pos + size > len(data):
            raise ValueError("Invalid pcapng block length")
        if struct.unpack_from(endian + "I", data, pos + size - 4)[0] != size:
            raise ValueError("Inconsistent pcapng block length")
        body = data[pos + 8 : pos + size - 4]
        pos += size
        if kind == 1:
            linktype, _, snaplen = struct.unpack_from(endian + "HHI", body)
            opts = dict(options(body[8:], endian))
            resolution = opts.get(9, b"\x06")[0]
            unit = (2 if resolution & 128 else 10) ** -(resolution & 127)
            offset = struct.unpack(endian + "q", opts.get(14, b"\0" * 8))[0]
            interfaces.append((linktype, unit, offset))
        elif kind == 6:
            number += 1
            interface, hi, lo, caplen, wirelen = struct.unpack_from(endian + "5I", body)
            linktype, unit, offset = interfaces[interface]
            if linktype != 283:
                raise ValueError(
                    f"Unsupported linktype {linktype}; expected IEEE802.15.4 TAP"
                )
            if caplen != wirelen or len(body) < 20 + caplen:
                raise ValueError("Truncated packet")
            packet = body[20 : 20 + caplen]
            version, _, header_len = struct.unpack_from("<BBH", packet)
            if version != 0 or not 4 <= header_len <= len(packet):
                raise ValueError("Unsupported TAP header")
            tlvs = dict(options(packet[4:header_len], "<"))
            # TAP's absent FCS TLV means no captured MAC FCS.
            if tlvs.get(0, b"\0")[0] != 0:
                raise ValueError(
                    "Captured MAC FCS unsupported; do not confuse it with EC CRC"
                )
            channel = struct.unpack_from("<H", tlvs[3])[0] if 3 in tlvs else None
            page = tlvs[3][2] if 3 in tlvs else None
            rssi = struct.unpack("<f", tlvs[1])[0] if 1 in tlvs else None
            lqi = tlvs[10][0] if 10 in tlvs else None
            yield number, ((hi << 32) | lo) * unit + offset, packet[header_len:], {
                "channel": channel,
                "channel_page": page,
                "rssi_dbm": rssi,
                "lqi": lqi,
                "tap_length": header_len,
            }


def decode_frame(raw):
    """Describe an observed EC frame and report its checksum validity.

    @param raw Complete 39- or 41-byte MAC frame without FCS.
    @return Decoded fields and checksum status; invalid checksums are reported
        rather than rejected, while unsupported layouts raise ValueError.
    """
    if raw[:2] != b"\x01\x88" or len(raw) not in (39, 41):
        raise ValueError("Unrecognized EC frame layout")
    family = raw[21]
    if (family, len(raw)) not in ((0x8C, 41), (0x9E, 39)):
        raise ValueError("Unrecognized EC family/length")
    calculated = (
        sum(raw[21:37]) & 255
        if family == 0x8C
        else binascii.crc_hqx(raw[21:37], 0xFFFF)
    )
    stored = raw[37] if family == 0x8C else int.from_bytes(raw[37:39], "big")
    result = {
        "length": len(raw),
        "mac_sequence": raw[2],
        "destination_wire": raw[3:7].hex(),
        "source_wire": raw[7:11].hex(),
        "destination_pan": f"{int.from_bytes(raw[3:5], 'little'):04x}",
        "destination_short": f"{int.from_bytes(raw[5:7], 'little'):04x}",
        "source_pan": f"{int.from_bytes(raw[7:9], 'little'):04x}",
        "source_short": f"{int.from_bytes(raw[9:11], 'little'):04x}",
        "route_a_wire": raw[11:15].hex(),
        "route_b_wire": raw[15:19].hex(),
        "network_tag": f"{raw[19]:02x}",
        "route_flags": f"{raw[20]:02x}",
        "family": f"{family:02x}",
        "opcode": f"{raw[22]:02x}",
        "operation": OPCODES.get(raw[22], "unknown"),
        "subject_wire": raw[23:27].hex(),
        "subject_suffix": raw[27:29].hex(),
        "body_hex": raw[21:37].hex(),
        "data_hex": raw[29:37].hex(),
        "checksum_kind": "sum8" if family == 0x8C else "crc16_ccitt_false",
        "checksum_stored": stored,
        "checksum_calculated": calculated,
        "checksum_ok": calculated == stored,
        "trailer_hex": raw[38:41].hex() if family == 0x8C else "",
        "mask_candidate": "",
        "state_bits": "",
        "report_counter": "",
        "token": "",
        "ack_of_opcode": f"{raw[29]:02x}" if raw[22] == 0xFA else "",
        "heartbeat_counter": "",
        "heartbeat_parameter": "",
        "raw_hex": raw.hex(),
    }
    if (family, raw[22]) in ((0x8C, 2), (0x9E, 5)):
        result.update(mask_candidate=raw[31], state_bits=raw[32], token=raw[36])
        if family == 0x9E:
            result["report_counter"] = raw[35]
    if raw[22] == 8:
        if family == 0x9E:
            result["operation"] = "device_keepalive_candidate"
        elif raw[23:29] == b"\0" * 6:
            result.update(
                heartbeat_counter=int.from_bytes(raw[29:33], "little"),
                heartbeat_parameter=int.from_bytes(raw[33:37], "little"),
            )
        else:
            # Live pairing observation: a device-specific 8c/08 answers 9e/08.
            # Its zero body is not a broadcast heartbeat counter/parameter.
            result["operation"] = "keepalive_response_candidate"
    return result


def analyze(path, skip_unknown=False):
    """Correlate EC traffic and summarize the evidence in one capture.

    @param path Path object naming the source pcapng capture.
    @param skip_unknown Count unsupported layouts instead of rejecting a mixed capture.
    @return Decoded packet rows and an aggregate capture summary.
    """
    rows = []
    frames_seen, skipped = 0, 0
    capture_start, capture_end = None, None
    for number, timestamp, raw, metadata in read_pcapng(path):
        frames_seen += 1
        if capture_start is None:
            capture_start = timestamp
        capture_end = timestamp
        try:
            decoded = decode_frame(raw)
        except ValueError:
            if not skip_unknown:
                raise
            skipped += 1
            continue
        row = {
            "capture": path.name,
            "frame": number,
            "timestamp": timestamp,
            "relative_seconds": 0.0,
            **metadata,
            **decoded,
            "relay_of_frame": "",
            "ack_of_frame": "",
            "related_delay_ms": "",
        }
        row["relative_seconds"] = round(timestamp - capture_start, 6)
        # Match only short, observed relationships within this capture.
        for prior in reversed(rows):
            delay = timestamp - prior["timestamp"]
            if delay > 1:
                break
            if (
                row["family"] == "8c"
                and row["source_wire"] != GATEWAY
                and prior["source_wire"] == GATEWAY
            ):
                if (
                    row["mac_sequence"] == prior["mac_sequence"]
                    and row["body_hex"] == prior["body_hex"]
                ):
                    row.update(
                        relay_of_frame=prior["frame"],
                        related_delay_ms=round(delay * 1000, 6),
                    )
                    break
            if (
                row["family"] == "8c"
                and row["opcode"] == "fa"
                and prior["family"] == "9e"
            ):
                if (row["subject_wire"], row["subject_suffix"], row["data_hex"]) == (
                    prior["subject_wire"],
                    prior["subject_suffix"],
                    prior["opcode"] + prior["data_hex"][:14],
                ):
                    row.update(
                        ack_of_frame=prior["frame"],
                        related_delay_ms=round(delay * 1000, 6),
                    )
                    break
        rows.append(row)
    if not frames_seen:
        raise ValueError(f"No packets in {path}")
    summary = {
        "file": path.name,
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "packets": frames_seen,
        "decoded_ec_packets": len(rows),
        "skipped_unknown_packets": skipped,
        "start_utc": datetime.datetime.fromtimestamp(
            capture_start, datetime.timezone.utc
        ).isoformat(),
        "duration_seconds": round(capture_end - capture_start, 6),
        "channels": sorted(set(r["channel"] for r in rows)),
        "families": dict(collections.Counter(r["family"] for r in rows)),
        "opcodes": dict(sorted(collections.Counter(r["opcode"] for r in rows).items())),
        "sources_wire": dict(collections.Counter(r["source_wire"] for r in rows)),
        "checksums_valid": sum(r["checksum_ok"] for r in rows),
        "matched_relays": sum(r["relay_of_frame"] != "" for r in rows),
        "matched_gateway_acks": sum(r["ack_of_frame"] != "" for r in rows),
    }
    return rows, summary


def main():
    """Decode the selected capture corpus and optionally write CSV/JSON evidence."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("captures", nargs="*", type=Path)
    parser.add_argument(
        "--output-dir", type=Path, help="Write CSV packet index and JSON summary here"
    )
    parser.add_argument(
        "--skip-unknown",
        action="store_true",
        help="Count and skip unsupported frame layouts in mixed radio captures",
    )
    args = parser.parse_args()
    all_rows, summaries = [], []
    for path in args.captures or sorted((ROOT / "captured").glob("*.pcapng")):
        rows, summary = analyze(path, skip_unknown=args.skip_unknown)
        all_rows.extend(rows)
        summaries.append(summary)
        print(
            f"{path.name}: {summary['packets']} packets, {len(rows)} decoded EC, "
            f"{summary['skipped_unknown_packets']} skipped, {summary['checksums_valid']} valid checksums, "
            f"{summary['matched_relays']} relay matches"
        )
    if args.output_dir:
        args.output_dir.mkdir(parents=True, exist_ok=True)
        with (args.output_dir / "decoded_packets.csv").open("w", newline="") as output:
            writer = csv.DictWriter(
                output,
                fieldnames=list(all_rows[0]) if all_rows else ["capture", "frame"],
            )
            writer.writeheader()
            writer.writerows(all_rows)
        (args.output_dir / "summary.json").write_text(
            json.dumps(summaries, indent=2) + "\n"
        )
    if not all(row["checksum_ok"] for row in all_rows):
        raise SystemExit("Checksum failures detected")


if __name__ == "__main__":
    main()
