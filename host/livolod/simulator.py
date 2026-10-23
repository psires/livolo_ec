"""In-process UART v3 adapter for development without radio hardware."""

import asyncio
import binascii
import time

from .protocol import decode
from .uart import CommandError


class Simulator:
    def __init__(self):
        """Create an isolated, initially disarmed UART adapter simulation."""
        self.events = asyncio.Queue()
        self.fault = asyncio.get_running_loop().create_future()
        self.network = None
        self.peers = {}
        self.states = {}
        self.armed = False
        self.ack = False
        self.rx_sequence = 0
        self.commands = []
        self.phase_anchor = None
        self.phase_generation = 0

    async def command(self, text, retries=1):
        """Emulate supported adapter operations and resulting device reports.

        @param text UART operation and arguments without transport framing.
        @param retries Unused retry count accepted for Gateway API compatibility.
        @return The simulated adapter OK reply; invalid requests raise CommandError.
        """
        self.commands.append(text)
        words = text.split()
        verb = words[0]
        if verb == "PING":
            return "OK EC-GATEWAY 3"
        if verb == "CAPS":
            return (
                "OK proto=3 peers=256 ack_mask=1f lease_max=60 session_bytes=8 channel=11 max_line=319 max_raw=125 "
                "phase=tx_entry_v1 phase_scale=65500 phase_max_ms=12000 phase_probe=1 rtimer_hz=65536 rtimer_bits=32"
            )
        if verb == "NETWORK":
            self.network = words[1:]
            self.peers.clear()
            self.armed = self.ack = False
            self.phase_anchor = None
            return "OK NETWORK DISARMED"
        if words[:2] == ["PEER", "SET"]:
            self.peers[int(words[2])] = words[3:]
            return "OK PEER"
        if words[:2] == ["PEER", "GET"]:
            device, mask = self.peers[int(words[2])]
            return f"OK PEER slot={words[2]} used=1 device={device} ack_mask={mask}"
        if verb == "DISARM":
            self.armed = self.ack = False
            self.phase_anchor = None
            return "OK DISARMED"
        if verb == "STATUS":
            gateway, tag, session = self.network
            age = (
                time.monotonic() - self.phase_anchor
                if self.phase_anchor is not None
                else 12
            )
            return (
                f"OK configured=1 gateway={gateway} tag={tag} session={session} peers={len(self.peers)} "
                f"armed={int(self.armed)} ack={int(self.ack)} rx_drops=0 uart_drops=0 input_errors=0 "
                f"phase_valid={int(age < 12)} phase_anchor_ticks={int((self.phase_anchor or 0) * 65536) & 0xffffffff} "
                f"phase_anchor_gen={self.phase_generation}"
            )
        if verb in ("ARM", "ACK", "SEND", "SEND_PHASE") and words[1] != self.network[2]:
            raise CommandError("ERR SESSION")
        if verb == "ARM":
            self.armed = True
            # Report after restore's command/reply drain, then again at each renewal.
            for device, _ in self.peers.values():
                asyncio.get_running_loop().call_later(0.05, self.current_state, device)
            return "OK ARMED 30"
        if verb == "ACK":
            self.ack = words[2] == "1"
            return "OK ACK_ON"
        if verb in ("SEND", "SEND_PHASE"):
            if not self.armed:
                raise CommandError("ERR DISARMED")
            raw = bytes.fromhex(words[2])
            packet = decode(raw)
            phase_reply = None
            if verb == "SEND_PHASE":
                from .phase import PhaseCapabilities

                now = time.monotonic()
                if self.phase_anchor is None or now - self.phase_anchor >= 12:
                    raise CommandError("ERR PHASE_REFERENCE")
                entry = int(now * 65536) & 0xFFFFFFFF
                anchor = int(self.phase_anchor * 65536) & 0xFFFFFFFF
                age = (entry - anchor) & 0xFFFFFFFF
                stamped = raw[:-3] + PhaseCapabilities(65536, 32).trailer(age)
                phase_reply = (
                    f"OK TX 0 timestamp=tx_entry entry_ticks={entry} return_ticks={entry} "
                    f"anchor_ticks={anchor} anchor_gen={self.phase_generation} age_ticks={age} raw={stamped.hex()}"
                )
            elif packet.opcode == 8:
                self.phase_anchor = time.monotonic()
                self.phase_generation += 1
            if packet.opcode == 2:
                mask, value, token = packet.data[2], packet.data[3], packet.data[7]
                old = self.states.get(packet.subject, 0)
                bits = (old & ~mask) | value
                self.states[packet.subject] = bits
                self.report(packet.subject, packet.suffix, mask, bits, token)
            return phase_reply or "OK TX 0"
        raise CommandError("ERR SYNTAX")

    def current_state(self, device):
        """Emit the simulated device state after adapter arming or lease renewal.

        @param device Device identity whose stored state should be reported.
        """
        self.report(device, mask=1, bits=self.states.get(device, 0))

    def report(self, device, suffix="0002", mask=3, bits=0, token=0):
        """Offer a simulated native state report to the daemon while armed.

        @param device Reporting device identity.
        @param suffix Device suffix in wire-order hexadecimal.
        @param mask Outputs described by this report.
        @param bits Complete simulated output state.
        @param token Transaction token echoed by the report.
        """
        if not self.armed:
            return
        self.rx_sequence += 1
        gateway, tag, _ = self.network
        raw = bytearray(39)
        raw[:3] = bytes((1, 0x88, self.rx_sequence & 255))
        raw[3:7] = bytes.fromhex(gateway)
        raw[7:11] = bytes.fromhex(device)
        raw[19:23] = bytes((int(tag, 16), 0, 0x9E, 5))
        raw[23:29] = bytes.fromhex(device + suffix)
        raw[29:37] = bytes((1, 0, mask, bits, 0, 0, self.rx_sequence & 255, token))
        raw[37:39] = binascii.crc_hqx(raw[21:37], 0xFFFF).to_bytes(2, "big")
        self.events.put_nowait(
            (time.monotonic(), f"RX {self.rx_sequence} 0 -45 100 39 {raw.hex()}")
        )

    async def close(self):
        """Disarm the simulated adapter at the end of a session."""
        await self.command("DISARM")
