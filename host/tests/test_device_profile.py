"""Captured pairing signatures are provisional profiles, never state-bit guesses."""

from dataclasses import replace
import unittest

from livolod.device_profile import pairing_profile
from livolod.protocol import decode

# Exact application frames, retaining their original CRCs. The first is frame
# 1691 of onegang-pairing-20261101-nrf52840.pcapng; the second is frame 136 of
# the supplied ec_device_pairing_sequence.pcapng. No capture dependency at runtime.
ONE = bytes.fromhex(
    "018825ffffffff667809450000000000000000ff009e01667809450001000101053401040014ad"
)
TWO = bytes.fromhex(
    "01880effffffff467156210000000000000000ff009e014671562100020002030534010400ffc6"
)
TWO_LATER = bytes.fromhex(
    "01886dffffffff467156210000000000000000ff009e014671562100020002030534013d0040cb"
)
ONE_LATER = bytes.fromhex(
    "0188cfffffffff667809450000000000000000ff039e016678094500010001010534012e00fd80"
)
GATEWAY = "2a992b12"
# Exact eight-byte payload retained by HA session aee36fdb-17af-4234-817d-1f2fa33f1097
# on 2026-11-07 from owner-confirmed two-gang 67181844. No raw envelope was retained;
# packets derived below are synthetic, not reconstructed claims of captured frames.
TWO_VARIANT_PAYLOAD = bytes.fromhex("000203042e003000")


class DeviceProfileTests(unittest.TestCase):
    """Require the known native pairing layout and preserve conflicting evidence."""

    def test_captured_profiles_match_physically_confirmed_gang_counts(self):
        """Both raw captured CRC-valid requests identify their tested output count."""
        for raw, count, suffix in ((ONE, 1, "0001"), (TWO, 2, "0002")):
            with self.subTest(outputs=count):
                result = pairing_profile(decode(raw), gateway=GATEWAY)
                self.assertEqual(result["outputs"], count)
                self.assertEqual(result["suffix"], suffix)
                self.assertEqual(result["status"], "detected")
                self.assertEqual(result["basis"], "pairing_suffix_and_payload")
                self.assertEqual(result["profile"], f"livolo-ec-suffix-{suffix}")
                self.assertTrue(result["experimental"])

    def test_empirical_two_output_variant_is_not_bound_to_a_device_id(self):
        """The observed exact prefix accepts synthetic identities and the existing counter rule."""
        for device in ("67181844", "12345678", "abcdef12"):
            for counter in range(256):
                payload = TWO_VARIANT_PAYLOAD[:6] + bytes((counter, 0))
                packet = replace(
                    decode(TWO), source=device, subject=device, data=payload
                )
                result = pairing_profile(packet, gateway=GATEWAY)
                self.assertEqual(result["status"], "detected")
                self.assertEqual(result["outputs"], 2)
                self.assertEqual(result["profile"], "livolo-ec-suffix-0002")
                self.assertEqual(result["signature"], "0002:000203042e00:00")
                self.assertTrue(result["experimental"])

    def test_empirical_variant_keeps_every_fixed_byte_suffix_and_length_gate(self):
        """No generalized generation mask or inferred one-gang variant is admitted."""
        packet = replace(decode(TWO), data=TWO_VARIANT_PAYLOAD)
        invalid = []
        for index in (0, 1, 2, 3, 4, 5, 7):
            data = bytearray(packet.data)
            data[index] ^= 1
            invalid.append(replace(packet, data=bytes(data)))
        invalid += [
            replace(packet, data=packet.data[:-1]),
            replace(packet, data=packet.data + b"\0"),
        ]
        invalid += [
            replace(packet, suffix=suffix)
            for suffix in ("0001", "0000", "0003", "ffff")
        ]
        invalid.append(
            replace(packet, suffix="0001", data=bytes.fromhex("000101042e003000"))
        )
        for altered in invalid:
            with self.subTest(packet=altered):
                result = pairing_profile(altered, gateway=GATEWAY)
                self.assertEqual(result["status"], "unknown")
                self.assertIsNone(result["outputs"])
        self.assertNotEqual(
            pairing_profile(packet, gateway=GATEWAY)["signature"],
            pairing_profile(decode(TWO), gateway=GATEWAY)["signature"],
        )

    def test_empirical_variant_preserves_native_direct_unregistered_envelope_gates(
        self,
    ):
        """The new payload cannot authorize relay, foreign, reserved or registered traffic."""
        packet = replace(decode(TWO), data=TWO_VARIANT_PAYLOAD)
        for change in (
            dict(source="00000000", subject="00000000"),
            dict(source="ffffffff", subject="ffffffff"),
            dict(source="12345678"),
            dict(subject="12345678"),
            dict(destination="12345678"),
            dict(tag="a7"),
            dict(family=0x8C),
            dict(opcode=5),
        ):
            with self.subTest(change=change):
                self.assertIsNone(
                    pairing_profile(replace(packet, **change), gateway=GATEWAY)
                )

    def test_capture_counter_and_route_changes_do_not_change_profile_signature(self):
        """Observed later request counters are excluded from aggregation identity."""
        for first, later in ((ONE, ONE_LATER), (TWO, TWO_LATER)):
            self.assertEqual(
                pairing_profile(decode(first), gateway=GATEWAY),
                pairing_profile(decode(later), gateway=GATEWAY),
            )

    def test_reserved_relay_foreign_and_registered_packets_are_not_discovery(self):
        """Only a direct native request for an unregistered identity qualifies."""
        packet = decode(ONE)
        for change in (
            dict(source="00000000", subject="00000000"),
            dict(source="ffffffff", subject="ffffffff"),
            dict(source="81422527"),
            dict(subject="46715621"),
            dict(destination="9bf4ea38"),
            dict(tag="a7"),
            dict(family=0x8C),
            dict(opcode=0xFA),
        ):
            with self.subTest(change=change):
                self.assertIsNone(
                    pairing_profile(replace(packet, **change), gateway=GATEWAY)
                )
        self.assertEqual(
            pairing_profile(replace(packet, destination=GATEWAY), gateway=GATEWAY)[
                "outputs"
            ],
            1,
        )

    def test_state_properties_and_passive_announcements_cannot_infer_outputs(self):
        """Mask/state bits and identical property values cannot prove gang count."""
        for opcode, body in (
            (0x05, "0100010100000100"),
            (0x05, "0100030300000100"),
            (0x15, "0125040000000100"),
            (0xA0, "0002030000000300"),
            (0x09, "0001010534010400"),
            (0x07, "f000082caa000000"),
        ):
            self.assertIsNone(
                pairing_profile(
                    replace(decode(ONE), opcode=opcode, data=bytes.fromhex(body)),
                    gateway=GATEWAY,
                )
            )

    def test_unknown_suffix_is_evidence_instead_of_silently_disappearing(self):
        """Unknown/legacy suffixes remain visible to the cohort conflict detector."""
        for suffix in ("0000", "0003", "0004", "0100", "ffff"):
            result = pairing_profile(
                replace(decode(ONE), suffix=suffix), gateway=GATEWAY
            )
            self.assertEqual(result["status"], "unknown")
            self.assertEqual(result["reason"], "unknown_pairing_suffix")
            self.assertEqual(result["suffix"], suffix)
            self.assertIsNone(result["outputs"])
            self.assertIsNone(result["profile"])

    def test_known_suffix_requires_matching_payload_and_preserves_ambiguity_key(self):
        """Every unverified payload byte except the changing counter blocks inference."""
        packet = decode(ONE)
        expected = pairing_profile(packet, gateway=GATEWAY)
        for index in (0, 1, 2, 3, 4, 5, 7):
            data = bytearray(packet.data)
            data[index] ^= 1
            result = pairing_profile(replace(packet, data=bytes(data)), gateway=GATEWAY)
            self.assertEqual(result["status"], "unknown")
            self.assertEqual(result["reason"], "unknown_pairing_payload")
            self.assertNotEqual(result["signature"], expected["signature"])
            self.assertIsNone(result["outputs"])
        mismatch = pairing_profile(replace(packet, suffix="0002"), gateway=GATEWAY)
        self.assertEqual(mismatch["status"], "unknown")

    def test_invalid_application_checksum_is_rejected_before_classification(self):
        """Corrupted radio evidence cannot reach the classifier through decode."""
        corrupted = bytearray(ONE)
        corrupted[28] ^= 3
        with self.assertRaises(ValueError):
            decode(bytes(corrupted))
