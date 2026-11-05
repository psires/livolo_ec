"""Experimental add uses only fresh, consistent server observations and reviewed tokens."""

import asyncio
import binascii
from dataclasses import replace
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from livolod.config import load
from livolod.native_api import native_snapshot
from livolod.service import Service
from livolod.storage import Store
from test_device_profile import ONE


def announcement(
    suffix="0001",
    body="0001010534010400",
    device="66780945",
    destination="ffffffff",
    tag="ff",
    opcode=1,
):
    """Construct a checksum-valid request with explicitly varied profile evidence.

    @param suffix Device profile suffix in wire order.
    @param body Eight application payload bytes encoded as hex.
    @param device Source and subject wire identity.
    @param destination Requested gateway or broadcast identity.
    @param tag Packet network tag.
    @param opcode Native application operation.
    @return Raw frame suitable for a synthetic UART RX record.
    """
    raw = bytearray(ONE)
    raw[3:7] = bytes.fromhex(destination)
    raw[7:11] = raw[23:27] = bytes.fromhex(device)
    raw[27:29] = bytes.fromhex(suffix)
    raw[29:37] = bytes.fromhex(body)
    raw[19] = int(tag, 16)
    raw[22] = opcode
    raw[37:] = binascii.crc_hqx(raw[21:37], 0xFFFF).to_bytes(2, "big")
    return bytes(raw)


class DiscoveryProfileTests(unittest.TestCase):
    def setUp(self):
        """Create an empty registry and a ready session without opening a radio."""
        # Python 3.9 asyncio primitives need a current loop even for synchronous
        # policy tests; prior IsolatedAsyncioTestCase tests remove that default.
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self.addCleanup(loop.close)
        self.addCleanup(asyncio.set_event_loop, None)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        config = replace(
            load(Path(__file__).parents[1] / "config.example.json"),
            devices=(),
            database=self.temp.name + "/state.sqlite3",
        )
        self.store = Store(config.database, "network")
        self.addCleanup(self.store.close)
        self.service = Service(config, self.store)
        self.service.ready = True
        self.service.session = "test-session"
        self.clock = patch("livolod.service.time.monotonic", return_value=100.0).start()
        self.addCleanup(patch.stopall)
        self.sequence = 0

    def observe(self, raw=ONE, received=None, retained=False):
        """Feed a synthetic RX record through checksum and discovery processing.

        @param raw Raw frame including its native checksum.
        @param received Optional monotonic arrival time; defaults to the test clock.
        @param retained Whether to simulate an old retained query result.
        """
        self.sequence += 1
        received = self.clock.return_value if received is None else received
        self.service.handle_rx(
            received,
            f"RX {self.sequence} 0 -42 99 {len(raw)} {raw.hex()}",
            retained=retained,
        )

    def profile(self):
        """Read the same dynamically evaluated profile used by browser and add validation."""
        return self.service.discovery_profile("66780945")

    def request(self, **changes):
        """Build an explicit auto-add request from the current reviewed profile.

        @param changes Request fields to override for validation boundary tests.
        """
        return dict(
            dict(
                request_id="auto-1",
                device="66780945",
                name="Hall",
                auto_detect=True,
                profile_token=self.profile()["token"],
            ),
            **changes,
        )

    def test_one_and_two_gang_profiles_derive_values_server_side(self):
        """Both captured signatures yield their own count and suffix without client overrides."""
        for suffix, body, count in (
            ("0001", "0001010534010400", 1),
            ("0002", "0002030534010400", 2),
        ):
            self.service.discovery_profiles.clear()
            self.observe(announcement(suffix, body))
            values = self.service.validate_action("add", self.request())
            self.assertEqual((values["outputs"], values["suffix"]), (count, suffix))

    def test_auto_add_persists_profile_and_duplicate_never_replays(self):
        """Accepted profiles persist as ordinary devices and requests keep existing dedupe."""
        self.observe()
        request = self.request()
        result = self.service.submit("add", request)
        self.assertEqual(result["status"], "confirmed")
        self.assertEqual(self.service.devices["66780945"].outputs, 1)
        self.assertEqual(self.store.devices(())[0].suffix, "0001")
        self.clock.return_value += 120
        self.assertEqual(self.service.submit("add", request), result)

    def test_original_manual_add_works_without_ready_radio_or_evidence(self):
        """The original explicit-output path and suffix default remain unchanged."""
        self.service.ready = False
        result = self.service.submit(
            "add", dict(request_id="manual", device="66780945", name="Hall", outputs=2)
        )
        self.assertEqual(result["status"], "confirmed")
        self.assertEqual(self.service.devices["66780945"].suffix, "0002")

    def test_manual_false_preserves_explicit_profile(self):
        """Explicit opt-out accepts the same manual count and suffix as the old API."""
        values = self.service.validate_action(
            "add",
            dict(
                device="66780945",
                name="Hall",
                outputs=1,
                suffix="0001",
                auto_detect=False,
            ),
        )
        self.assertEqual(values["outputs"], 1)

    def test_auto_rejects_manual_overrides_and_nonboolean_opt_in(self):
        """Experimental opt-in cannot mix trusted evidence with caller profile overrides."""
        self.observe()
        for changes in (
            dict(outputs=1),
            dict(suffix="0001"),
            dict(auto_detect=1),
            dict(auto_detect="true"),
        ):
            with self.assertRaises(ValueError):
                self.service.validate_action("add", self.request(**changes))
        with self.assertRaises(ValueError):
            self.service.validate_action(
                "add",
                dict(device="66780945", name="Hall", outputs=1, profile_token="x"),
            )

    def test_counter_changes_keep_review_token_stable(self):
        """Repeated same-profile requests refresh evidence without invalidating review."""
        self.observe()
        token = self.profile()["token"]
        self.clock.return_value += 1
        self.observe(announcement(body="0001010534012e00"))
        self.assertEqual(self.profile()["token"], token)
        self.assertEqual(self.profile()["status"], "detected")

    def test_conflicting_payload_inside_publish_throttle_blocks_add(self):
        """A rapid same-suffix contradiction is processed before the five-second throttle."""
        self.observe()
        request = self.request()
        self.clock.return_value += 0.1
        self.observe(announcement(body="0002030534010400"))
        self.assertEqual(self.profile()["status"], "ambiguous")
        self.assertIsNone(self.profile()["token"])
        self.assertEqual(self.service.submit("add", request)["status"], "rejected")
        self.assertFalse(self.service.devices)

    def test_unknown_suffix_conflicts_and_cannot_be_hidden_by_more_known_packets(self):
        """Unknown signatures poison a continuous evidence cohort until a quiet expiry."""
        self.observe()
        self.observe(announcement(suffix="0003"))
        self.observe()
        self.assertEqual(self.profile()["status"], "ambiguous")

    def test_unknown_payload_alone_has_no_detected_count(self):
        """A familiar suffix alone is insufficient for the automatic path."""
        self.observe(announcement(body="0000000000000000"))
        self.assertEqual(self.profile()["status"], "unknown")
        self.assertIsNone(self.profile()["outputs"])
        self.assertEqual(
            self.service.submit("add", self.request())["status"], "rejected"
        )

    def test_expiry_is_rechecked_at_snapshot_and_add(self):
        """A cached browser token cannot authorize an add after sixty seconds."""
        self.observe()
        request = self.request()
        self.clock.return_value += 60.1
        self.assertEqual(
            self.service.web_snapshot()["discoveries"][0]["profile"]["status"], "stale"
        )
        self.assertEqual(self.service.submit("add", request)["status"], "rejected")

    def test_expired_cohort_requires_new_review_token(self):
        """Fresh evidence after a quiet period cannot reuse an earlier reviewed token."""
        self.observe()
        request = self.request()
        self.clock.return_value += 61
        self.observe()
        self.assertNotEqual(self.profile()["token"], request["profile_token"])
        self.assertEqual(self.service.submit("add", request)["status"], "rejected")

    def test_disconnect_and_session_change_invalidate_profile(self):
        """Current-session evidence cannot be used while offline or after reconnect."""
        self.observe()
        request = self.request()
        self.service.ready = False
        self.assertEqual(self.profile()["status"], "stale")
        self.service.ready = True
        self.service.session = "new-session"
        self.assertEqual(self.service.submit("add", request)["status"], "rejected")
        self.observe()
        self.assertNotEqual(self.profile()["token"], request["profile_token"])

    def test_old_future_retained_bad_checksum_and_foreign_reports_cannot_authorize(
        self,
    ):
        """Only fresh validated current-stream pairing records authorize auto-add."""
        self.observe(received=39)
        self.observe(received=101)
        self.observe(retained=True)
        bad = bytearray(ONE)
        bad[-1] ^= 1
        self.observe(bytes(bad))
        for change in (dict(destination="9bf4ea38"), dict(tag="a7"), dict(opcode=5)):
            self.observe(announcement(**change))
        self.assertIsNone(self.profile()["token"])
        self.assertEqual(
            self.service.submit("add", self.request())["status"], "rejected"
        )

    def test_native_snapshot_copies_only_public_profile_fields(self):
        """Native discovery exports the reviewed profile without private aggregation state."""
        self.observe()
        snapshot = native_snapshot(self.service)
        profile = snapshot["discoveries"][0]["profile"]
        self.assertEqual(profile, self.profile())
        self.assertNotIn("signature", profile)
        self.assertNotIn("session", profile)
        profile["outputs"] = 9
        self.assertEqual(self.profile()["outputs"], 1)

    def test_discovery_memory_is_bounded_without_trusting_evicted_tokens(self):
        """Eviction drops old capability evidence and tokens alongside discovery entries."""
        self.observe()
        request = self.request()
        for n in range(256):
            self.observe(announcement(device=f"{n+1:08x}"))
        self.assertLessEqual(len(self.service.discoveries), 256)
        self.assertLessEqual(len(self.service.discovery_profiles), 256)
        self.assertEqual(self.service.submit("add", request)["status"], "rejected")
