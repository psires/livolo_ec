import unittest
from decode_ec import ROOT, analyze
from ec_gateway import GatewayError, ec_frame, ec_heartbeat
from ec_join_trial import transmit
from ec_set_trial import matching_report
from unittest.mock import Mock


class TrialTransmit(unittest.TestCase):
    def test_known_collision_retries_with_new_command(self):
        """Verify known collision retries with new command."""
        client = Mock()
        client.command.side_effect = [GatewayError("ERR TX 2"), "OK TX 0"]
        self.assertEqual(transmit(client, b"abc"), "OK TX 0")
        self.assertEqual(client.command.call_count, 2)
        client.listen.assert_called_once_with(0.025)

    def test_persistent_busy_is_bounded(self):
        """Verify persistent busy is bounded."""
        client = Mock()
        client.command.side_effect = GatewayError("ERR TX 2")
        self.assertEqual(transmit(client, b"abc"), "SKIPPED CCA_BUSY")
        self.assertEqual(client.command.call_count, 4)

    def test_unknown_outcome_is_not_retried(self):
        """Verify unknown outcome is not retried."""
        for message in ("ERR TX 1", "No response; outcome unknown"):
            client = Mock()
            client.command.side_effect = GatewayError(message)
            with self.assertRaises(GatewayError):
                transmit(client, b"abc")
            self.assertEqual(client.command.call_count, 1)


class GatewayBuilders(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        """Prepare shared capture or compiled-library fixtures for this test group."""
        cls.rows, _ = analyze(
            ROOT
            / "captured/development/gateway-pairing-20261017T182031Z-nrf52840.pcapng",
            True,
        )

    def test_command_and_registration_envelopes_match_capture(self):
        """Verify command and registration envelopes match capture."""
        checked = 0
        for row in self.rows:
            if row["source_wire"] != "9bf4ea38" or row["subject_wire"] != "46715621":
                continue
            if row["opcode"] not in ("02", "10", "ac") and row["ack_of_opcode"] != "01":
                continue
            generated = ec_frame(
                row["source_wire"],
                row["subject_wire"],
                0xB1,
                row["mac_sequence"],
                int(row["opcode"], 16),
                row["data_hex"],
            )
            self.assertEqual(generated[:38], bytes.fromhex(row["raw_hex"])[:38])
            checked += 1
        self.assertEqual(checked, 13)

    def test_heartbeat_matches_capture(self):
        """Verify heartbeat matches capture."""
        for row in self.rows:
            if row["heartbeat_counter"] == "":
                continue
            raw = ec_heartbeat(
                row["source_wire"],
                int(row["network_tag"], 16),
                row["mac_sequence"],
                row["heartbeat_counter"],
                row["heartbeat_parameter"],
            )
            self.assertEqual(raw[:38], bytes.fromhex(row["raw_hex"])[:38])

    def test_control_confirmation_requires_peer_domain_token_and_state(self):
        """Verify control confirmation requires peer domain token and state."""
        report = next(
            dict(row)
            for row in self.rows
            if row["source_wire"] == "46715621"
            and row["family"] == "9e"
            and row["opcode"] == "05"
            and row["token"] != 0
        )
        report.update(destination_wire="2a992b12", network_tag="a7")
        mask, token = report["mask_candidate"], report["token"]
        value = report["state_bits"] & mask
        self.assertTrue(matching_report(report, mask, value, token))
        self.assertTrue(
            matching_report(
                dict(report, destination_wire="ffffffff"), mask, value, token
            )
        )
        for field, bad in (
            ("source_wire", "deadbeef"),
            ("subject_wire", "deadbeef"),
            ("destination_wire", "9bf4ea38"),
            ("network_tag", "b1"),
            ("checksum_ok", False),
            ("token", (token + 1) & 255),
            ("state_bits", report["state_bits"] ^ mask),
            ("mask_candidate", 0),
            ("opcode", "15"),
        ):
            with self.subTest(field=field):
                self.assertFalse(
                    matching_report(dict(report, **{field: bad}), mask, value, token)
                )


if __name__ == "__main__":
    unittest.main()
