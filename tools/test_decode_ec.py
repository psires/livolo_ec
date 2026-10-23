"""Regression evidence for the protocol conclusions, using original captures."""

import binascii
import collections
import unittest

from decode_ec import ROOT, analyze, decode_frame, read_pcapng


class CaptureEvidence(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        """Prepare shared capture or compiled-library fixtures for this test group."""
        cls.captures = {
            path.name: analyze(path)[0] for path in (ROOT / "captured").glob("*.pcapng")
        }
        cls.rows = [row for rows in cls.captures.values() for row in rows]

    def test_complete_corpus_and_metadata(self):
        """Verify complete corpus and metadata."""
        self.assertEqual(
            {name: len(rows) for name, rows in self.captures.items()},
            {
                "ec_no_connection_registered.pcapng": 220,
                "ec_gateway ready to pair.pcapng": 494,
                "ec_device_pairing_sequence.pcapng": 322,
                "ec_set_on_off_on_off.pcapng": 85,
                "ec_1gang_on_off.pcapng": 20,
            },
        )
        self.assertEqual(
            {(r["channel"], r["channel_page"], r["tap_length"]) for r in self.rows},
            {(11, 0, 28)},
        )
        self.assertEqual(
            collections.Counter(r["family"] for r in self.rows), {"8c": 1095, "9e": 46}
        )

    def test_every_application_checksum(self):
        """Verify every application checksum."""
        self.assertEqual(binascii.crc_hqx(b"123456789", 0xFFFF), 0x29B1)
        self.assertTrue(all(r["checksum_ok"] for r in self.rows))
        # Corrupt a state-data byte in an independently captured frame of each family.
        rows = self.captures["ec_set_on_off_on_off.pcapng"]
        for number in (15, 16):
            raw = bytearray.fromhex(rows[number - 1]["raw_hex"])
            raw[32] ^= 1
            self.assertFalse(decode_frame(raw)["checksum_ok"])

    def test_mac_fields_are_not_32_bit_standard_addresses(self):
        """Verify mac fields are not 32 bit standard addresses."""
        command = self.captures["ec_set_on_off_on_off.pcapng"][14]
        self.assertEqual(
            (command["source_pan"], command["source_short"]), ("f49b", "38ea")
        )
        self.assertEqual(
            (command["destination_pan"], command["destination_short"]), ("7146", "2156")
        )

    def test_four_app_operations_and_retries(self):
        """Verify four app operations and retries."""
        rows = self.captures["ec_set_on_off_on_off.pcapng"]
        expected = [
            ([15], 16, 3, 0xA4),
            ([25, 26], 27, 0, 0x57),
            ([47, 48], 49, 3, 0x5E),
            ([55, 56], 57, 0, 0xFB),
        ]
        for commands, report_number, state, token in expected:
            report = rows[report_number - 1]
            self.assertEqual(
                (report["opcode"], report["state_bits"], report["token"]),
                ("05", state, token),
            )
            for number in commands:
                command = rows[number - 1]
                self.assertEqual(
                    (
                        command["opcode"],
                        command["mask_candidate"],
                        command["state_bits"],
                        command["token"],
                    ),
                    ("02", 3, state, token),
                )
            self.assertEqual(rows[report_number]["ack_of_frame"], report_number)

    def test_physical_label_contains_gateway_commands(self):
        """Verify physical label contains gateway commands."""
        rows = self.captures["ec_1gang_on_off.pcapng"]
        for command_number, report_number, state, token in [
            (8, 9, 0, 0xA8),
            (13, 15, 1, 0xA6),
            (17, 19, 0, 0x93),
        ]:
            command, report = rows[command_number - 1], rows[report_number - 1]
            self.assertEqual(command["source_wire"], "9bf4ea38")
            self.assertEqual(
                (
                    command["opcode"],
                    command["mask_candidate"],
                    command["state_bits"],
                    command["token"],
                ),
                ("02", 1, state, token),
            )
            self.assertEqual((report["state_bits"], report["token"]), (state, token))

    def test_two_bit_state_transitions(self):
        """Verify two bit state transitions."""
        rows = self.captures["ec_device_pairing_sequence.pcapng"]
        self.assertEqual(
            [
                (rows[n - 1]["mask_candidate"], rows[n - 1]["state_bits"])
                for n in (76, 98, 144, 173)
            ],
            [(2, 2), (1, 3), (2, 1), (2, 3)],
        )
        native = [r for r in rows if r["family"] == "9e"]
        self.assertTrue(
            all(r["network_tag"] == "ff" for r in native if r["frame"] < 144)
        )
        self.assertTrue(
            all(r["network_tag"] == "b1" for r in native if r["frame"] >= 144)
        )

    def test_all_gateway_acks_have_observed_originals(self):
        """Verify all gateway acks have observed originals."""
        acks = [r for r in self.rows if r["family"] == "8c" and r["opcode"] == "fa"]
        self.assertEqual(len(acks), 37)
        self.assertTrue(all(r["ack_of_frame"] != "" for r in acks))

    def test_forwarding_and_destination_rewrite(self):
        """Verify forwarding and destination rewrite."""
        relays = [r for r in self.rows if r["relay_of_frame"] != ""]
        self.assertEqual(len(relays), 121)
        self.assertTrue(all(6 < r["related_delay_ms"] < 16 for r in relays))
        rows = self.captures["ec_device_pairing_sequence.pcapng"]
        original, relay = rows[136], rows[137]
        self.assertEqual(relay["relay_of_frame"], 137)
        self.assertEqual(
            (original["destination_wire"], relay["destination_wire"]),
            ("ffffffff", "67181844"),
        )
        self.assertNotEqual(original["source_wire"], relay["source_wire"])
        self.assertEqual(original["body_hex"], relay["body_hex"])

    def test_live_mixed_capture_preserves_frame_numbers(self):
        """Verify live mixed capture preserves frame numbers."""
        path = ROOT / "captured/development/2026-10-17-bringup-channel11.pcapng"
        with self.assertRaises(ValueError):
            analyze(path)
        rows, summary = analyze(path, skip_unknown=True)
        self.assertEqual(
            (
                summary["packets"],
                summary["decoded_ec_packets"],
                summary["skipped_unknown_packets"],
            ),
            (121, 11, 110),
        )
        self.assertEqual(summary["checksums_valid"], 11)
        self.assertEqual(rows[0]["frame"], 2)
        self.assertGreater(rows[0]["relative_seconds"], 0)
        self.assertEqual(len(summary["sources_wire"]), 7)

    def test_device_08_exchange_is_not_broadcast_heartbeat(self):
        # Independent UART observations during the 2026-10-17 pairing capture.
        """Verify device 08 exchange is not broadcast heartbeat."""
        request = decode_frame(
            bytes.fromhex(
                "01888f9bf4ea38467156210000000000000000ff009e08467156210002000031280c000000c940"
            )
        )
        response = decode_frame(
            bytes.fromhex(
                "01888f467156219bf4ea380000000000000000b10e8c084671562100020000000000000000c44a3801"
            )
        )
        self.assertTrue(request["checksum_ok"] and response["checksum_ok"])
        self.assertEqual(request["operation"], "device_keepalive_candidate")
        self.assertEqual(response["operation"], "keepalive_response_candidate")
        for row in (request, response):
            self.assertEqual(row["heartbeat_counter"], "")
            self.assertEqual(row["heartbeat_parameter"], "")
        broadcasts = [r for r in self.rows if r["opcode"] == "08"]
        self.assertTrue(broadcasts)
        self.assertTrue(all(r["operation"] == "gateway_heartbeat" for r in broadcasts))
        self.assertTrue(
            all(isinstance(r["heartbeat_counter"], int) for r in broadcasts)
        )


if __name__ == "__main__":
    unittest.main()
