"""Guarded adapter phase stamping for opt-in outputs and supported state queries.

tx_entry_v1 measures radio submission, not SFD. Never synthesize phase from host
time or fall back to raw SEND when this capability or its reference is absent.
"""

from dataclasses import dataclass

from .config import identity
from .protocol import decode, key_values
from .uart import CommandError, LinkError


@dataclass(frozen=True)
class PhaseCapabilities:
    hz: int
    bits: int
    scale: int = 65500
    max_ms: int = 12000

    @classmethod
    def parse(cls, caps):
        """Require the explicit adapter timing capabilities needed for phase experiments.

        @param caps String-valued adapter capability fields.
        @return Validated PhaseCapabilities; unsupported metadata raises LinkError.
        """
        try:
            if (
                caps["phase"] != "tx_entry_v1"
                or int(caps["phase_scale"]) != 65500
                or int(caps["phase_max_ms"]) != 12000
                or caps["phase_probe"] != "1"
            ):
                raise ValueError
            hz, bits = int(caps["rtimer_hz"]), int(caps["rtimer_bits"])
            if hz <= 0 or bits != 32 or hz * 12 >= 2**bits:
                raise ValueError
            return cls(hz, bits)
        except (KeyError, ValueError, TypeError):
            raise LinkError(
                "Explicit tx_entry_v1 phase/probe capability is required"
            ) from None

    def trailer(self, age_ticks):
        """Represent a valid reference age in the experimental EC timing trailer.

        @param age_ticks Nonnegative ticks elapsed since the adapter reference heartbeat.
        @return Three trailer bytes; expired ages raise LinkError.
        """
        if not 0 <= age_ticks * 1000 < self.max_ms * self.hz:
            raise LinkError("Phase metadata has an expired or invalid reference age")
        seconds, fraction = divmod(age_ticks, self.hz)
        return (fraction * self.scale // self.hz).to_bytes(2, "little") + bytes(
            (seconds,)
        )


class PhaseClient:
    """One guarded UART transaction per call; caller owns RF ordering/recovery.

    Device sends require a separate explicit opt-in. Probe-only mode is the
    default, so discovering this experimental capability cannot enable outputs.
    UART retry/cache policy remains in Gateway.command. No RF replay is added.
    """

    def __init__(self, gateway, session, caps, *, gateway_id, allow_outputs=False):
        """Bind guarded phase operations to one adapter session and gateway identity.

        @param gateway UART gateway providing asynchronous commands.
        @param session Active adapter session token.
        @param caps Adapter capability fields to validate.
        @param gateway_id Source gateway identity in wire-order hexadecimal.
        @param allow_outputs Explicit opt-in permitting device output transmissions.
        """
        self.gateway, self.session = gateway, session
        self.gateway_id = identity(gateway_id)
        self.caps = PhaseCapabilities.parse(caps)
        self.allow_outputs = allow_outputs
        self.query_supported = caps.get("phase_queries") == "10_ac_v1"
        self.adapter_sequence = caps.get("tx_sequence") == "adapter_when_hb"
        self.last_tx = None

    def reference_status(self, status):
        """Read STATUS without treating historical invalid anchor fields as live.

        @param status Adapter STATUS key/value fields.
        @return Validated reference metadata, or None when no live reference exists.
        """
        try:
            valid = status["phase_valid"]
            if valid not in ("0", "1"):
                raise ValueError
            if valid == "0":
                return None
            anchor, generation = int(status["phase_anchor_ticks"]), int(
                status["phase_anchor_gen"]
            )
            if not 0 <= anchor < 2**self.caps.bits or not 0 <= generation <= 0xFFFFFFFF:
                raise ValueError
            return {
                "anchor_ticks": anchor,
                "anchor_gen": generation,
                "timestamp": "tx_entry",
            }
        except (KeyError, ValueError, TypeError):
            raise LinkError("Invalid phase reference status") from None

    def validate_reply(self, reply, expected=None):
        """Verify a phase transmission outcome against its reference and requested frame.

        @param reply Complete OK TX or ERR TX adapter response.
        @param expected Requested frame bytes to compare, or None to omit content comparison.
        @return Validated timing metadata, also retained as last_tx.
        """
        parts = reply.split()
        if len(parts) < 3 or parts[:2] not in (["OK", "TX"], ["ERR", "TX"]):
            raise LinkError("Malformed phase transmission result")
        try:
            code = int(parts[2])
            meta = key_values(reply)
            raw = bytes.fromhex(meta["raw"])
            entry, returned, anchor, generation, age = (
                int(meta[k])
                for k in (
                    "entry_ticks",
                    "return_ticks",
                    "anchor_ticks",
                    "anchor_gen",
                    "age_ticks",
                )
            )
            limit = 2**self.caps.bits
            if (
                code not in (0, 1, 2, 3)
                or (parts[0] == "OK") != (code == 0)
                or meta["timestamp"] != "tx_entry"
                or not all(0 <= n < limit for n in (entry, returned, anchor, age))
                or not 0 <= generation <= 0xFFFFFFFF
                or ((entry - anchor) % limit) != age
                or (returned - entry) % limit >= self.caps.hz * 12
            ):
                raise ValueError
            if self.last_tx:
                old_generation = int(self.last_tx["anchor_gen"])
                step = (generation - old_generation) & 0xFFFFFFFF
                if step >= 0x80000000 or (
                    step == 0 and anchor != int(self.last_tx["anchor_ticks"])
                ):
                    raise ValueError
            if expected is not None and (
                len(raw) != len(expected)
                or raw[:2] != expected[:2]
                or raw[3:-3] != expected[3:-3]
                or (not self.adapter_sequence and raw[2] != expected[2])
            ):
                raise ValueError
            if len(raw) not in (38, 41) or raw[-3:] != self.caps.trailer(age):
                raise ValueError
        except (KeyError, ValueError, TypeError, OverflowError):
            raise LinkError(
                "Invalid phase timestamp or stamped-frame metadata"
            ) from None
        self.last_tx = dict(meta, result=code, timing_quality="tx_entry_not_sfd")
        return self.last_tx

    async def _command(self, text, expected=None):
        """Execute a phase request and validate timing evidence even for TX failures.

        @param text Complete phase UART operation and arguments.
        @param expected Requested frame bytes to compare, or None.
        @return Validated successful transmission metadata.
        """
        try:
            reply = await self.gateway.command(text)
        except CommandError as error:
            # PHASE_REFERENCE is a definite non-send. TX failures carry timing
            # metadata, but code 2 alone establishes a safely retryable RF failure.
            if str(error).startswith("ERR TX "):
                self.validate_reply(str(error), expected)
            raise
        return self.validate_reply(reply, expected)

    async def send(self, raw):
        """Submit an explicitly enabled direct output command with adapter phase stamping.

        @param raw Validated-layout 41-byte direct command with a zero timing trailer.
        @return Validated phase transmission metadata.
        """
        if not self.allow_outputs:
            raise ValueError("Phase output controls are disabled in probe-only mode")
        packet = decode(raw)
        if (
            len(raw) != 41
            or packet.family != 0x8C
            or packet.opcode != 2
            or packet.source != self.gateway_id
            or raw[20] != 0x1E
            or raw[11:15] != bytes(4)
            or raw[3:7] != raw[15:19]
            or raw[3:7] != raw[23:27]
            or raw[-3:] != bytes(3)
        ):
            raise ValueError(
                "Phase send requires a direct 8c02 frame with a zero input trailer"
            )
        return await self._command(f"SEND_PHASE {self.session} {raw.hex()}", raw)

    async def query(self, raw):
        """Phase-stamp only the observed read-only query form, without output permission.

        @param raw Direct 10/ac query with data 0000000000000001 and zero trailer.
        @return Validated timing metadata; absent capability or wrong form is rejected.
        """
        if not self.query_supported:
            raise LinkError("Adapter does not advertise phase_queries=10_ac_v1")
        packet = decode(raw)
        if (
            len(raw) != 41
            or packet.family != 0x8C
            or packet.opcode not in (0x10, 0xAC)
            or packet.data != bytes(7) + b"\x01"
            or packet.source != self.gateway_id
            or packet.destination in (self.gateway_id, "ffffffff")
            or raw[20] != 0x1E
            or raw[11:15] != bytes(4)
            or raw[3:7] != raw[15:19]
            or raw[3:7] != raw[23:27]
            or raw[-3:] != bytes(3)
        ):
            raise ValueError("Phase query requires the observed direct 10/ac frame")
        return await self._command(f"SEND_PHASE {self.session} {raw.hex()}", raw)

    async def probe(self, sequence, nonce):
        """Submit a non-actuating timing probe for the active gateway session.

        @param sequence Eight-bit probe radio sequence.
        @param nonce Four-byte probe identifier.
        @return Validated phase transmission metadata.
        """
        if type(sequence) is not int or not 0 <= sequence <= 255:
            raise ValueError("Probe sequence must be an 8-bit integer")
        if not isinstance(nonce, bytes) or len(nonce) != 4:
            raise ValueError("Probe nonce must be four bytes")
        expected = (
            bytes((1, 0x88, sequence))
            + bytes.fromhex(self.gateway_id) * 2
            + b"EC_PHASE_PROBE_V1"
            + nonce
            + bytes(6)
        )
        return await self._command(
            f"PHASE_PROBE {self.session} {sequence:02x} {nonce.hex()}", expected
        )
