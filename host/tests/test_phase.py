import unittest

from livolod.phase import PhaseCapabilities, PhaseClient
from livolod.protocol import frame
from livolod.uart import CommandError, LinkError


CAPS = dict(
    phase="tx_entry_v1",
    phase_scale="65500",
    phase_max_ms="12000",
    phase_probe="1",
    rtimer_hz="65536",
    rtimer_bits="32",
)
RAW = frame("2a992b12", "a7", "46715621", "0002", 1, 2, bytes((1, 0, 1, 1, 0, 0, 0, 9)))


def response(raw=RAW, age=32768, anchor=0xFFFFFFF0, code=0):
    """Build a phase-send reply with controllable metadata for validation tests.

    @param raw MAC frame bytes for this scenario.
    @param age Reported phase-reference age in ticks.
    @param anchor Reported reference-anchor tick value.
    @param code Radio transmit result code.
    """
    caps = PhaseCapabilities.parse(CAPS)
    entry = (anchor + age) & 0xFFFFFFFF
    stamped = raw[:-3] + caps.trailer(age)
    return (
        f"{'OK' if code == 0 else 'ERR'} TX {code} timestamp=tx_entry "
        f"entry_ticks={entry} return_ticks={(entry + 100) & 0xffffffff} "
        f"anchor_ticks={anchor} anchor_gen=2 age_ticks={age} raw={stamped.hex()}"
    )


class GatewayStub:
    def __init__(self, reply):
        """Prepare the simulated collaborator with the requested test behavior.

        @param reply Reply string to return to the phase client.
        """
        self.reply, self.commands = reply, []

    async def command(self, text):
        """Provide the scenario-specific UART response or injected failure.

        @param text Command or encoded protocol text for this exchange.
        """
        self.commands.append(text)
        if self.reply.startswith("ERR"):
            raise CommandError(self.reply)
        return self.reply


class PhaseTests(unittest.IsolatedAsyncioTestCase):
    def test_capabilities_are_explicit_and_timestamp_scale_is_not_timer_frequency(self):
        """Verify capabilities are explicit and timestamp scale is not timer frequency."""
        caps = PhaseCapabilities.parse(CAPS)
        self.assertEqual(caps.trailer(65536), bytes.fromhex("000001"))
        self.assertEqual(caps.trailer(32768), (32750).to_bytes(2, "little") + b"\x00")
        self.assertEqual(caps.trailer(0), bytes(3))
        for age in (-1, 12 * 65536):
            with self.assertRaises(LinkError):
                caps.trailer(age)
        for key in CAPS:
            broken = dict(CAPS)
            broken.pop(key)
            with self.assertRaises(LinkError):
                PhaseCapabilities.parse(broken)

    async def test_probe_only_default_never_sends_output(self):
        """Verify probe only default never sends output."""
        gateway = GatewayStub(response())
        client = PhaseClient(gateway, "123456789abcdef0", CAPS, gateway_id="2a992b12")
        with self.assertRaisesRegex(ValueError, "probe-only"):
            await client.send(RAW)
        self.assertEqual(gateway.commands, [])

    def test_status_does_not_use_historical_anchor_when_reference_invalid(self):
        """Verify status does not use historical anchor when reference invalid."""
        client = PhaseClient(
            GatewayStub("OK"), "123456789abcdef0", CAPS, gateway_id="2a992b12"
        )
        self.assertIsNone(
            client.reference_status(
                dict(phase_valid="0", phase_anchor_ticks="100", phase_anchor_gen="2")
            )
        )
        self.assertEqual(
            client.reference_status(
                dict(phase_valid="1", phase_anchor_ticks="100", phase_anchor_gen="2")
            ),
            dict(anchor_ticks=100, anchor_gen=2, timestamp="tx_entry"),
        )
        for status in (
            {},
            dict(phase_valid="2"),
            dict(phase_valid="1", phase_anchor_ticks="-1", phase_anchor_gen="2"),
        ):
            with self.assertRaises(LinkError):
                client.reference_status(status)

    async def test_valid_stamped_send_checks_wraparound_metadata_and_preserved_body(
        self,
    ):
        """Verify valid stamped send checks wraparound metadata and preserved body."""
        gateway = GatewayStub(response())
        client = PhaseClient(
            gateway, "123456789abcdef0", CAPS, gateway_id="2a992b12", allow_outputs=True
        )
        metadata = await client.send(RAW)
        self.assertEqual(metadata["timing_quality"], "tx_entry_not_sfd")
        self.assertEqual(metadata["result"], 0)
        self.assertEqual(gateway.commands, [f"SEND_PHASE 123456789abcdef0 {RAW.hex()}"])

    async def test_reference_error_never_falls_back_or_replays(self):
        """Verify reference error never falls back or replays."""
        gateway = GatewayStub("ERR PHASE_REFERENCE")
        client = PhaseClient(
            gateway, "123456789abcdef0", CAPS, gateway_id="2a992b12", allow_outputs=True
        )
        with self.assertRaisesRegex(CommandError, "PHASE_REFERENCE"):
            await client.send(RAW)
        self.assertEqual(len(gateway.commands), 1)
        self.assertIsNone(client.last_tx)

    async def test_busy_metadata_is_recorded_without_automatic_rf_retry(self):
        """Verify busy metadata is recorded without automatic rf retry."""
        gateway = GatewayStub(response(code=2))
        client = PhaseClient(
            gateway, "123456789abcdef0", CAPS, gateway_id="2a992b12", allow_outputs=True
        )
        with self.assertRaisesRegex(CommandError, "ERR TX 2"):
            await client.send(RAW)
        self.assertEqual(client.last_tx["result"], 2)
        self.assertEqual(len(gateway.commands), 1)

    async def test_bad_metadata_and_mutated_application_body_are_rejected(self):
        """Verify bad metadata and mutated application body are rejected."""
        for bad in (
            response().replace("timestamp=tx_entry", "timestamp=sfd"),
            response().replace("age_ticks=32768", "age_ticks=32769"),
            response(raw=RAW[:29] + b"\x02" + RAW[30:]),
            "OK TX 0",
        ):
            client = PhaseClient(
                GatewayStub(bad),
                "123456789abcdef0",
                CAPS,
                gateway_id="2a992b12",
                allow_outputs=True,
            )
            with self.assertRaises(LinkError):
                await client.send(RAW)
        client = PhaseClient(
            GatewayStub(response()),
            "123456789abcdef0",
            CAPS,
            gateway_id="2a992b12",
            allow_outputs=True,
        )
        with self.assertRaises(ValueError):
            await client.send(RAW[:-3] + b"\x01\x00\x00")
        self.assertEqual(client.gateway.commands, [])

    async def test_probe_validates_identity_sequence_marker_nonce_and_reserved_bytes(
        self,
    ):
        """Verify probe validates identity sequence marker nonce and reserved bytes."""
        probe = (
            b"\x01\x88\x42"
            + bytes.fromhex("2a992b12") * 2
            + b"EC_PHASE_PROBE_V1"
            + bytes.fromhex("abcdef01")
            + bytes(6)
        )
        gateway = GatewayStub(response(probe))
        client = PhaseClient(gateway, "123456789abcdef0", CAPS, gateway_id="2a992b12")
        result = await client.probe(0x42, bytes.fromhex("abcdef01"))
        self.assertEqual(len(bytes.fromhex(result["raw"])), 38)
        self.assertEqual(gateway.commands, ["PHASE_PROBE 123456789abcdef0 42 abcdef01"])
        for index in (2, 3, 7, 11, 28, 32):
            mutated = bytearray(probe)
            mutated[index] ^= 1
            gateway.reply = response(bytes(mutated))
            with self.assertRaises(LinkError):
                await client.probe(0x42, bytes.fromhex("abcdef01"))

    async def test_reference_generation_and_clock_inconsistency_are_rejected(self):
        """Verify reference generation and clock inconsistency are rejected."""
        gateway = GatewayStub(response())
        client = PhaseClient(
            gateway, "123456789abcdef0", CAPS, gateway_id="2a992b12", allow_outputs=True
        )
        await client.send(RAW)
        for malformed in (
            response(anchor=100),
            response().replace("anchor_gen=2", "anchor_gen=1"),
            response().replace("return_ticks=32852", "return_ticks=32751"),
        ):
            gateway.reply = malformed
            with self.assertRaises(LinkError):
                await client.send(RAW)
