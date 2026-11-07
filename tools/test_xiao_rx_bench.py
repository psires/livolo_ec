"""Passive-only runner, structured Livolo identity and USB interruption regressions."""

import binascii
from copy import deepcopy
import unittest
from unittest.mock import patch

from decode_ec import ROOT, read_pcapng, decode_frame
import xiao_rf_bench as rf
import xiao_rx_bench as rx
from test_xiao_rf_bench import Clock, Port as BasePort, rx_line


def native(source="46715621", subject=None, suffix=None):
    """Build a nontransmitted fixture in the established native 39-byte layout.

    @param source Actual MAC source field, distinct from incidental payload bytes.
    @param subject Optional application subject override.
    @param suffix Optional suffix override.
    @return Checksum-valid synthetic report bytes, used only by offline tests.
    """
    packet = bytearray(39)
    packet[:3] = bytes.fromhex("018801")
    packet[3:7] = bytes.fromhex("ffffffff")
    packet[7:11] = bytes.fromhex(source)
    packet[19:23] = bytes.fromhex("ff009e07")
    packet[23:27] = bytes.fromhex(subject or source)
    packet[27:29] = bytes.fromhex(suffix or rx.TARGETS.get(source, "0002"))
    packet[37:39] = binascii.crc_hqx(packet[21:37], 0xFFFF).to_bytes(2, "big")
    return bytes(packet)


class Port(BasePort):
    """Serial fixture supporting only the passive tool's allowed commands."""

    def __init__(self, name):
        """Prepare packet and USB-error injection without importing serial.

        @param name Exact fixture endpoint key.
        """
        super().__init__(name)
        self.frames = [native(target) for target in rx.TARGETS]
        self.rx = 0
        self.diag = "rx_diag"
        self.bad_fcs = False
        self.eio_after_start = False
        self.pending_frames = False
        self.start_reads = 0

    @property
    def in_waiting(self):
        """Inject disconnect only after START/status so counterpart cleanup is exercised."""
        if self.eio_after_start and self.active and self.start_reads > 30:
            raise OSError(5, "USB endpoint vanished")
        return min(17, len(self.input))

    def read(self, size):
        """Deliver bounded fragments and enqueue RF observations once during capture.

        @param size Bounded read size.
        @return Next UART fragment.
        """
        if self.active:
            self.start_reads += 1
        if self.pending_frames and not self.input:
            self.pending_frames = False
            for packet in self.frames:
                included = self.name == "mg24"
                self.emit(rx_line(packet, included, self.bad_fcs))
                self.rx += 1
        return super().read(size)

    def write(self, data):
        """Respond to read-only diagnostics and fixed passive start/stop only.

        @param data Remaining bytes from the production command writer.
        @return Accepted fragment size.
        """
        size = min(len(data), self.write_limit)
        self.output.extend(data[:size])
        if b"\n" not in self.output:
            return size
        line, _, self.output = self.output.partition(b"\n")
        ident, command = line.decode().split(" ", 1)
        self.commands.append(command)
        name = command.split()[0]
        if name == "PING":
            reply = "OK EC-GATEWAY 3"
        elif name in ("VERSION", "CAPS"):
            board = "wrong" if self.bad_board else rf.BOARDS[self.name][0]
            reply = f"OK proto=3 board={board} experimental=1 update=none gateway=0 peers=0 ack_mask=00 {self.diag}=passive_v1"
        elif name == "RFLISTEN":
            self.active, self.mode, self.channel = 1, "passive", 11
            self.rx = 0
            self.pending_frames = True
            reply = "OK active=1 channel=11 mode=passive"
        elif name == "RFSTOP":
            if not self.stop_stuck:
                self.active = 0
            reply = "OK active=0"
        elif name == "RFSTATUS":
            reply = f"OK active={self.active} channel={self.channel} mode={self.mode} power_dbm=0 auto_ack=0 retries=0 tx_started={self.tx} tx_done={self.tx} tx_failed=0 rx={self.rx} dropped={self.dropped} queued=0 busy=0 fault=0 ticks_hz=1000000 timestamp=callback bad_crc=unavailable"
        else:
            raise AssertionError("Prohibited command " + command)
        if name not in self.silent:
            self.emit(f"RSP {ident} {reply}")
        return size


class DecodeTests(unittest.TestCase):
    """Target identity requires valid structure, checksums and actual source fields."""

    def summarize(self, frames):
        """Create independently FCS-valid receiver events for fixture bytes.

        @param frames MAC payloads excluding radio FCS.
        @return Production receiver analysis.
        """
        events = [
            dict(rf.event(rx_line(packet)), host_monotonic=index)
            for index, packet in enumerate(frames)
        ]
        return rx.summarize(events, "mg24")

    def test_valid_sources_and_original_capture_decoder(self):
        """Both original-capture sources are recognized by the vetted EC decoder."""
        # Same captured one-gang announcement pinned in test_device_profile.py.
        originals = {
            "66780945": bytes.fromhex(
                "018825ffffffff667809450000000000000000ff009e01667809450001000101053401040014ad"
            )
        }
        for path in (ROOT / "captured").glob("*.pcapng"):
            for _, _, packet, _ in read_pcapng(path):
                try:
                    info = decode_frame(packet)
                except ValueError:
                    continue
                source = info["source_wire"]
                if (
                    source in rx.TARGETS
                    and info["family"] == "9e"
                    and info["subject_wire"] == source
                    and info["subject_suffix"] == rx.TARGETS[source]
                    and info["checksum_ok"]
                ):
                    originals[source] = packet
        self.assertEqual(set(originals), set(rx.TARGETS))
        result = self.summarize(originals.values())
        self.assertEqual(result["target_counts"], dict.fromkeys(rx.TARGETS, 1))

    def test_incidental_id_invalid_checksum_and_subject_cannot_match(self):
        """Payload/address lookalikes and corrupt EC data remain unmatched observations."""
        incidental = bytearray(native("12345678"))
        incidental[29:33] = bytes.fromhex("46715621")
        incidental[37:39] = binascii.crc_hqx(incidental[21:37], 0xFFFF).to_bytes(
            2, "big"
        )
        bad_checksum = bytearray(native())
        bad_checksum[30] ^= 1
        frames = [
            bytes(incidental),
            bytes(bad_checksum),
            native(subject="66780945"),
            native(suffix="0001"),
            bytes.fromhex("020001"),
        ]
        result = self.summarize(frames)
        self.assertEqual(result["target_counts"], dict.fromkeys(rx.TARGETS, 0))
        self.assertEqual(result["unmatched_frames"], len(frames))
        self.assertEqual(len(result["observations"]), len(frames))

    def test_repeats_and_sequence_changes_do_not_become_loss_counts(self):
        """Repeated payloads and sparse MAC sequences are descriptive evidence only."""
        first = native()
        later = bytearray(first)
        later[2] = 10
        result = self.summarize([first, first, bytes(later)])
        self.assertEqual(result["mac_sequence_steps"]["46715621"], [0, 9])
        self.assertEqual(sum(result["identical_payload_repeats"].values()), 1)
        self.assertIn("not packet-loss", result["gap_interpretation"])


class PassiveTests(unittest.TestCase):
    """The real receive-only runner must never issue radio transmit operations."""

    def setUp(self):
        """Build two independent receiver fixtures and a deterministic host clock."""
        self.clock = Clock()
        self.ports = [Port(name) for name in rf.BOARDS]
        self.links = [rx.Link(port, port.name, self.clock.time) for port in self.ports]
        self.evidence = {}

    def run_capture(self):
        """Execute a short passive session with the production transport and parser."""
        rx.capture(
            self.links, "12345678", 1, self.evidence, self.clock.time, self.clock.sleep
        )

    def test_passive_both_sources_both_receivers_and_raw_timestamps(self):
        """Both receivers independently decode both switches with zero TX commands."""
        self.run_capture()
        self.assertEqual(self.evidence["assessment"], "passed")
        self.assertEqual(len(self.evidence["shared_target_payload_sha256"]), 2)
        for port in self.ports:
            self.assertFalse(port.active)
            self.assertTrue(
                all(
                    c.split()[0]
                    in ("PING", "VERSION", "CAPS", "RFSTATUS", "RFLISTEN", "RFSTOP")
                    for c in port.commands
                )
            )
            report = self.evidence["receivers"][port.name]
            self.assertEqual(report["target_counts"], dict.fromkeys(rx.TARGETS, 1))
            self.assertIn(
                "ROM boot noise",
                [line["line"] for line in self.evidence["raw_lines"][port.name]],
            )
            self.assertTrue(
                all(
                    "host_monotonic" in line
                    for line in self.evidence["raw_lines"][port.name]
                )
            )

    def test_missing_target_is_inconclusive_even_with_clean_counters(self):
        """Silence, unknown traffic or one missing source cannot qualify reception."""
        self.ports[0].frames = [native("12345678")]
        self.run_capture()
        self.assertEqual(self.evidence["assessment"], "inconclusive")

    def test_invalid_fcs_overflow_or_counter_gap_is_failed_not_silent_pass(self):
        """Capture corruption and lost delivery remain distinct from absent switches."""
        self.run_capture()
        for key, value in (("dropped", "1"), ("rx", "3"), ("tx_started", "1")):
            broken = deepcopy(self.evidence)
            broken["final"]["mg24"][key] = value
            self.assertEqual(rx.assess(broken), "failed")
        self.setUp()
        self.ports[0].bad_fcs = True
        self.run_capture()
        self.assertEqual(self.evidence["assessment"], "failed")

    def test_wrong_capability_or_active_peer_never_starts_listening(self):
        """No alias or existing RF session can authorize passive mode implicitly."""
        for attribute, value in (
            ("diag", "rf_listen"),
            ("active", 1),
            ("bad_board", True),
        ):
            self.setUp()
            setattr(self.ports[1], attribute, value)
            with self.assertRaises(ValueError):
                self.run_capture()
            self.assertTrue(
                all(
                    not any(c.startswith(("RFLISTEN", "RFSTOP")) for c in port.commands)
                    for port in self.ports
                )
            )

    def test_commands_and_duration_fail_before_wire_access(self):
        """No arbitrary channel, TX, reset, pairing or network command passes the guard."""
        for command in (
            "RFSTART 12345678",
            "RFTX 12345678 0 39 0 0",
            "NETWORK a b c",
            "ARM a 30",
            "BOOTLOADER",
            "RFLISTEN 12345678 11",
        ):
            with self.assertRaises(ValueError):
                rx.receive_command(self.links[0], command, self.links)
        for duration in (0, 46, True):
            with self.assertRaises(ValueError):
                rx.capture(
                    self.links,
                    "12345678",
                    duration,
                    {},
                    self.clock.time,
                    self.clock.sleep,
                )
        self.assertTrue(all(not port.commands for port in self.ports))

    def test_lost_listen_reply_never_replays_and_stops_owned_window(self):
        """An uncertain passive START still requires bounded cleanup without replay."""
        self.ports[0].silent.add("RFLISTEN")
        with self.assertRaises(TimeoutError):
            self.run_capture()
        self.assertEqual(
            sum(c.startswith("RFLISTEN") for c in self.ports[0].commands), 1
        )
        self.assertFalse(self.ports[0].active)
        self.assertEqual(self.evidence["assessment"], "failed")

    def test_maximum_window_and_insufficient_lease_headroom(self):
        """A long requested capture completes only with enough time left to stop."""
        rx.capture(
            self.links, "12345678", 45, self.evidence, self.clock.time, self.clock.sleep
        )
        self.assertEqual(self.evidence["assessment"], "passed")
        self.setUp()
        original = rx.receive_command

        def delayed(link, text, *args, **kwargs):
            """Model slow admissions without dropping or replaying a command.

            @param link Destination receiver.
            @param text Requested command.
            @param args Transport timing arguments.
            @param kwargs Optional transport settings.
            @return Original matching response.
            """
            reply = original(link, text, *args, **kwargs)
            if text.startswith("RFLISTEN") or (text == "RFSTATUS" and link.port.active):
                self.clock.sleep(1.9)
            return reply

        with patch.object(rx, "receive_command", side_effect=delayed):
            with self.assertRaisesRegex(TimeoutError, "lease budget"):
                rx.capture(
                    self.links,
                    "12345678",
                    45,
                    self.evidence,
                    self.clock.time,
                    self.clock.sleep,
                )
        self.assertTrue(all(not port.active for port in self.ports))
        self.assertFalse(self.evidence["window_completed"])

    def test_eio_during_other_endpoint_preflight_never_reopens_or_starts(self):
        """Re-enumeration errors fail closed even while querying the other receiver."""
        with patch.object(
            self.ports[1], "read", side_effect=OSError(5, "USB disconnected")
        ):
            with self.assertRaises(OSError):
                self.run_capture()
        self.assertTrue(
            all(
                not any(c.startswith("RFLISTEN") for c in port.commands)
                for port in self.ports
            )
        )
        self.assertEqual(self.evidence["assessment"], "failed")
        self.assertIn("USB disconnected", self.evidence["error"])

    def test_eio_after_start_keeps_counterpart_stop_and_partial_evidence(self):
        """A vanished endpoint cannot prevent stopping the other owned receiver."""
        self.ports[1].eio_after_start = True
        with self.assertRaises(OSError):
            self.run_capture()
        self.assertIn("RFSTOP", self.ports[0].commands)
        self.assertFalse(self.ports[0].active)
        self.assertEqual(self.evidence["assessment"], "failed")
        self.assertTrue(self.evidence["raw_lines"]["mg24"])


if __name__ == "__main__":
    unittest.main()
