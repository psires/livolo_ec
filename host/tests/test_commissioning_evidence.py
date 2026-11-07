"""Capture-backed commissioning boundaries, independent of radios and state machines."""

import json
from pathlib import Path
import unittest

from livolod.device_profile import pairing_profile
from livolod.protocol import decode, frame


class CommissioningEvidenceTests(unittest.TestCase):
    """Preserve exact wire evidence that host commissioning must distinguish."""

    def setUp(self):
        """Load portable capture excerpts without requiring development pcaps."""
        self.evidence = json.loads(
            (Path(__file__).parent / "fixtures/commissioning_capture.json").read_text()
        )
        self.rows = {row["label"]: row for row in self.evidence["packets"]}
        self.packets = {
            label: decode(bytes.fromhex(row["raw_hex"]))
            for label, row in self.rows.items()
        }

    def test_exact_requests_identify_only_verified_profiles(self):
        """Both physical layouts classify; retransmissions remain one profile."""
        gateway = self.evidence["gateway"]
        for label, outputs in (("one_request", 1), ("two_request", 2)):
            profile = pairing_profile(self.packets[label], gateway=gateway)
            self.assertEqual(profile["status"], "detected")
            self.assertEqual(profile["outputs"], outputs)
        first = pairing_profile(self.packets["one_request"], gateway=gateway)
        for label in ("one_request_repeat", "one_request_latest"):
            self.assertEqual(
                pairing_profile(self.packets[label], gateway=gateway), first
            )
        self.assertIsNone(
            pairing_profile(self.packets["relayed_request"], gateway=gateway)
        )

    def test_admission_ack_echoes_latest_request_not_profile_constant(self):
        """Captured FA preserves the changing request byte and direct routing layout."""
        request = self.packets["one_request_latest"]
        ack = self.packets["admission_ack"]
        self.assertNotEqual(request.data[6], self.packets["one_request"].data[6])
        self.assertEqual(ack.data, b"\x01" + request.data[:7])
        self.assertEqual(ack.source, self.evidence["gateway"])
        self.assertEqual(ack.destination, request.source)
        self.assertEqual(ack.subject, request.source)
        self.assertEqual(ack.suffix, request.suffix)
        raw = bytes.fromhex(self.rows["admission_ack"]["raw_hex"])
        self.assertEqual(
            frame(ack.source, ack.tag, ack.subject, ack.suffix, raw[2], 0xFA, ack.data),
            raw,
        )

    def test_adopted_tag_precedes_report_to_our_gateway(self):
        """A valid same-tag state addressed to EC must not prove local commissioning."""
        early = self.packets["tag_adopted_old_destination"]
        local = self.packets["local_state"]
        self.assertEqual(early.source, local.source)
        self.assertEqual(early.subject, local.subject)
        self.assertEqual(early.tag, self.evidence["tag"])
        self.assertEqual(local.tag, early.tag)
        self.assertEqual(early.destination, "9bf4ea38")
        self.assertEqual(local.destination, self.evidence["gateway"])
        self.assertEqual((local.family, local.opcode, local.suffix), (0x9E, 5, "0001"))
        self.assertEqual(local.data, bytes.fromhex("0100010100000500"))
        times = [
            self.rows[label]["relative_seconds"]
            for label in ("admission_ack", "tag_adopted_old_destination", "local_state")
        ]
        self.assertEqual(times, sorted(times))

    def test_queries_properties_and_gateway_acks_are_not_native_state(self):
        """Actual traffic separates requests, tag adoption and report acknowledgement."""
        for label in ("admission_ack", "query_10", "query_ac", "local_state_ack"):
            self.assertEqual(self.packets[label].family, 0x8C)
        self.assertEqual(self.packets["local_property"].opcode, 0x15)
        report = self.packets["local_state"]
        ack = self.packets["local_state_ack"]
        self.assertEqual(ack.data, b"\x05" + report.data[:7])
        self.assertEqual(ack.destination, report.source)
        self.assertEqual(self.packets["unregistered_state_to_ec"].tag, "ff")
