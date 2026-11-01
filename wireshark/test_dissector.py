#!/usr/bin/env python3
"""Exercise the real Lua dissector through TShark, without transmitting traffic.

Run python3 -m unittest discover -s wireshark -p 'test_*.py' -v.
Set TSHARK to the executable path if it is not installed in a standard location.
"""

import csv
import io
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from decode_ec import decode_frame, read_pcapng  # noqa: E402

PLUGIN = ROOT / "wireshark/livolo_ec.lua"
FIELDS = [
    "frame.number",
    "livolo_ec.family",
    "livolo_ec.opcode",
    "livolo_ec.source",
    "livolo_ec.destination",
    "livolo_ec.subject",
    "livolo_ec.suffix",
    "livolo_ec.tag",
    "livolo_ec.flags",
    "livolo_ec.data",
    "livolo_ec.checksum.good",
    "livolo_ec.checksum.calculated",
    "livolo_ec.mask",
    "livolo_ec.state",
    "livolo_ec.token",
    "livolo_ec.counter",
    "livolo_ec.heartbeat.counter",
    "livolo_ec.heartbeat.parameter",
    "livolo_ec.operation",
    "livolo_ec.ack_opcode",
    "livolo_ec.property.key",
    "livolo_ec.property.value",
    "livolo_ec.phase.age",
    "livolo_ec.checksum.bad",
    "livolo_ec.truncated",
    "_ws.expert.message",
]


def tshark_path():
    """Return an installed TShark executable path, or None when unavailable."""
    candidates = [
        os.environ.get("TSHARK"),
        shutil.which("tshark"),
        "/Applications/Wireshark.app/Contents/MacOS/tshark",
    ]
    return next((p for p in candidates if p and Path(p).is_file()), None)


def write_pcap(path, records, linktype=230):
    """Write small raw-MAC fixtures with independent captured and reported lengths.

    @param path Destination temporary file.
    @param records Iterable of (captured bytes, reported packet length) tuples.
    @param linktype Pcap DLT; 230 is 802.15.4 without FCS, 195 includes FCS.
    """
    with path.open("wb") as output:
        output.write(struct.pack("<IHHIIII", 0xA1B2C3D4, 2, 4, 0, 0, 65535, linktype))
        for index, (packet, reported) in enumerate(records):
            output.write(struct.pack("<IIII", index + 1, 0, len(packet), reported))
            output.write(packet)


def mac_fcs(packet):
    """Compute IEEE 802.15.4's reflected CRC, independently of the EC checksum.

    @param packet MAC header and payload bytes without FCS.
    @return Two-byte FCS in transmitted order.
    """
    crc = 0
    for byte in packet:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ (0x8408 if crc & 1 else 0)
    return struct.pack("<H", crc)


class DissectorTests(unittest.TestCase):
    """Validate Wireshark integration, documented semantics and rejection bounds."""

    @classmethod
    def setUpClass(cls):
        """Locate the runtime and load independent examples from original captures."""
        cls.tshark = tshark_path()
        if not cls.tshark:
            raise unittest.SkipTest("Install Wireshark/TShark or set TSHARK")
        cls.originals = sorted((ROOT / "captured").glob("*.pcapng"))
        cls.raw = [raw for _, _, raw, _ in read_pcapng(cls.originals[0])]
        cls.gateway = next(raw for raw in cls.raw if raw[21] == 0x8C)
        cls.device = next(raw for raw in cls.raw if raw[21] == 0x9E)

    def dissect(self, path, two_pass=False, decode_as=False):
        """Run the actual plugin and return fields, failing on Lua runtime errors.

        @param path Input pcap/pcapng file.
        @param two_pass Whether to request TShark's two-pass analysis.
        @param decode_as Force EC for the original gateway's source PAN.
        @return Rows of string-valued Wireshark fields for all packets.
        """
        command = [self.tshark, "-n", "-X", f"lua_script:{PLUGIN}"]
        if two_pass:
            command.append("-2")
        if decode_as:
            command.extend(["-d", "wpan.panid==0xf49b,livolo_ec"])
        command.extend(["-r", str(path), "-T", "fields", "-E", "header=y"])
        for field in FIELDS:
            command.extend(["-e", field])
        # Isolate personal plugins/preferences so an installed copy cannot mask
        # the repository version or cause a duplicate protocol registration.
        with tempfile.TemporaryDirectory() as home:
            environment = dict(os.environ, HOME=home, XDG_CONFIG_HOME=home)
            result = subprocess.run(
                command, capture_output=True, text=True, timeout=120, env=environment
            )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("Lua Error", result.stderr)
        self.assertNotIn("Lua Error", result.stdout)
        rows = list(csv.DictReader(io.StringIO(result.stdout), delimiter="\t"))
        # TShark 4.6 prints unmasked Lua booleans as True/False, older builds
        # may print 1/0. Normalize only this presentation difference.
        for row in rows:
            value = row["livolo_ec.checksum.good"]
            if value in ("True", "False"):
                row["livolo_ec.checksum.good"] = "1" if value == "True" else "0"
        return rows

    def check_capture(self, path):
        """Compare every recognized packet with the independent Python decoder.

        @param path TAP capture path, optionally containing non-EC traffic.
        @return Number of recognized EC packets.
        """
        actual = self.dissect(path)
        packets = list(read_pcapng(path))
        self.assertEqual(len(actual), len(packets))
        count = 0
        for row, (number, _, raw, _) in zip(actual, packets):
            self.assertEqual(int(row["frame.number"]), number)
            try:
                expected = decode_frame(raw)
            except ValueError:
                self.assertEqual(
                    row["livolo_ec.family"], "", f"False positive #{number}"
                )
                continue
            count += 1
            with self.subTest(capture=path.name, frame=number):
                for field, key in (
                    ("family", "family"),
                    ("opcode", "opcode"),
                    ("tag", "network_tag"),
                    ("flags", "route_flags"),
                ):
                    self.assertEqual(
                        int(row[f"livolo_ec.{field}"], 0), int(expected[key], 16)
                    )
                for field, key in (
                    ("source", "source_wire"),
                    ("destination", "destination_wire"),
                    ("subject", "subject_wire"),
                    ("suffix", "subject_suffix"),
                    ("data", "data_hex"),
                ):
                    self.assertEqual(
                        row[f"livolo_ec.{field}"].replace(":", ""), expected[key]
                    )
                self.assertEqual(
                    row["livolo_ec.checksum.good"], str(int(expected["checksum_ok"]))
                )
                self.assertEqual(
                    int(row["livolo_ec.checksum.calculated"], 0),
                    expected["checksum_calculated"],
                )
                for field, key in (
                    ("mask", "mask_candidate"),
                    ("state", "state_bits"),
                    ("token", "token"),
                    ("counter", "report_counter"),
                    ("heartbeat.counter", "heartbeat_counter"),
                    ("heartbeat.parameter", "heartbeat_parameter"),
                ):
                    if expected[key] != "":
                        self.assertEqual(
                            int(row[f"livolo_ec.{field}"], 0), expected[key]
                        )
        return count

    def test_original_corpus(self):
        """All 1,141 original packets agree with the independent offline decoder."""
        self.assertEqual(sum(self.check_capture(path) for path in self.originals), 1141)

    def test_mixed_capture(self):
        """The mixed bringup capture exposes exactly 11 EC packets and 110 others."""
        self.assertEqual(
            self.check_capture(
                ROOT / "captured/development/2026-10-17-bringup-channel11.pcapng"
            ),
            11,
        )

    def test_corrupt_truncated_and_unknown(self):
        """Bad checksums remain visible while unsupported framing is not claimed."""
        records = []
        for packet in (self.gateway, self.device):
            corrupt = bytearray(packet)
            corrupt[37] ^= 1
            records.append((bytes(corrupt), len(corrupt)))
        records.append((self.gateway[:35], len(self.gateway)))
        for length in (0, 10, 20, 22, 30, 38, 40, 42):
            packet = (self.gateway + b"\0")[:length]
            records.append((packet, len(packet)))
        unknown = bytearray(self.gateway)
        unknown[21] = 0x77
        records.append((bytes(unknown), len(unknown)))
        wrong_header = bytearray(self.gateway)
        wrong_header[1] = 0x98  # Same addressing lengths, unsupported MAC version.
        records.append((bytes(wrong_header), len(wrong_header)))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bounds.pcap"
            write_pcap(path, records)
            rows = self.dissect(path)
        for row in rows[:2]:
            self.assertEqual(row["livolo_ec.checksum.good"], "0")
            self.assertTrue(row["livolo_ec.checksum.bad"])
        self.assertTrue(rows[2]["livolo_ec.truncated"])
        self.assertTrue(all(not row["livolo_ec.family"] for row in rows[3:]))

    def test_fcs_and_decode_as(self):
        """Native MAC FCS handling preserves EC checksums for both families."""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "fcs.pcap"
            packets = [self.gateway, self.device]
            write_pcap(path, [(p + mac_fcs(p), len(p) + 2) for p in packets], 195)
            for decode_as in (False, True):
                rows = self.dissect(path, decode_as=decode_as)
                self.assertEqual(
                    [r["livolo_ec.checksum.good"] for r in rows], ["1", "1"]
                )

    def test_recent_relay_capture_when_available(self):
        """Forwarded native reports retain distinct source and subject identities."""
        path = ROOT / "captured/development/original-ec-live-20261025-nrf52840.pcapng"
        if not path.exists():
            self.skipTest("Optional development capture not distributed")
        self.assertGreater(self.check_capture(path), 0)
        relay = self.dissect(path)[1700]
        self.assertEqual(relay["livolo_ec.source"], "75710834")
        self.assertEqual(relay["livolo_ec.subject"].replace(":", ""), "67181844")
        self.assertEqual(int(relay["livolo_ec.state"], 0), 0)

    def test_keepalive_property_ack_and_redissection(self):
        """Keepalives stay distinct from heartbeat counters; ACKs omit state tokens."""
        requests = [
            bytes.fromhex(value)
            for value in (
                "01888f9bf4ea38467156210000000000000000ff009e08467156210002000031280c000000c940",
                "01888f467156219bf4ea380000000000000000b10e8c084671562100020000000000000000c44a3801",
            )
        ]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "keepalive.pcap"
            write_pcap(path, [(p, len(p)) for p in requests])
            rows = self.dissect(path)
        self.assertEqual(
            [r["livolo_ec.operation"] for r in rows],
            ["Device keepalive candidate", "Keepalive response candidate"],
        )
        self.assertTrue(all(not r["livolo_ec.heartbeat.counter"] for r in rows))
        path = ROOT / "captured/ec_device_pairing_sequence.pcapng"
        rows = self.dissect(path)
        self.assertEqual(rows, self.dissect(path, two_pass=True))
        self.assertEqual(int(rows[58]["livolo_ec.property.key"], 0), 0x24)
        self.assertEqual(int(rows[58]["livolo_ec.property.value"], 0), 2)
        state_acks = [
            r
            for r in rows
            if r["livolo_ec.ack_opcode"] == "0x05" and r["livolo_ec.family"] == "0x8c"
        ]
        self.assertTrue(state_acks)
        self.assertTrue(
            all(r["livolo_ec.counter"] and not r["livolo_ec.token"] for r in state_acks)
        )


if __name__ == "__main__":
    unittest.main()
