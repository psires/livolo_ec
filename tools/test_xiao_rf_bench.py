"""Exercise the actual bounded RF runner without importing serial or opening ports."""

from copy import deepcopy
from types import SimpleNamespace
from pathlib import Path
import json
import sys
import tempfile
import unittest
from unittest.mock import patch

import xiao_rf_bench as bench

NONCE = "12345678"


class Clock:
    """Deterministic monotonic time for bounded failure tests."""

    def __init__(self):
        """Start a private simulated clock."""
        self.now = 0

    def time(self):
        """Return simulated monotonic seconds."""
        return self.now

    def sleep(self, delay):
        """Advance time without blocking the test process.

        @param delay Requested nonnegative interval.
        """
        self.now += delay


def rx_line(payload, included=True, corrupt=False):
    """Render the frozen RF RX contract for a synthetic or unrelated packet.

    @param payload MAC bytes excluding FCS.
    @param included Whether actual FCS bytes are available.
    @param corrupt Whether those FCS bytes should fail independent checking.
    @return Complete ASCII event line, without newline.
    """
    raw = (
        payload + (b"\x00\x00" if corrupt else bench.fcs(payload))
        if included
        else payload
    )
    return (
        f"RF RX len={len(payload)} fcs={'included' if included else 'stripped'} "
        f"crc=1 rssi=-42 lqi=80 ticks=1234 data={raw.hex()}"
    )


class Port:
    """Fragmented, backpressured UART fixture and paired synthetic RF receiver."""

    def __init__(self, name):
        """Create a zero-RF fixture with configurable failure injection.

        @param name Canonical short bench board name.
        """
        self.name = name
        self.peer = None
        self.input = bytearray(b"ROM boot noise\n")
        self.output = bytearray()
        self.commands = []
        self.active = 0
        self.tx = self.rx = self.dropped = 0
        self.write_limit = 5
        self.zero_writes = 2
        self.silent = set()
        self.lose_start_reply = False
        self.capture = True
        self.bad_board = False
        self.bad_mode = False
        self.stop_stuck = False
        self.inject_ack = False

    @property
    def in_waiting(self):
        """Expose fragmented input so every event spans multiple polls."""
        return min(17, len(self.input))

    def read(self, size):
        """Consume at most the bounded requested input count.

        @param size Requested byte count.
        @return Next input fragment.
        """
        result = bytes(self.input[:size])
        del self.input[:size]
        return result

    def emit(self, line):
        """Append one complete fixture response or event.

        @param line ASCII protocol record.
        """
        self.input.extend((line + "\n").encode())

    def write(self, data):
        """Accept partial writes and implement the fixed command contract.

        @param data Remaining encoded command bytes, never an automatic retry.
        @return Bytes accepted by this call.
        """
        if self.zero_writes:
            self.zero_writes -= 1
            return 0
        size = min(len(data), self.write_limit)
        self.output.extend(data[:size])
        if b"\n" not in self.output:
            return size
        wire, _, self.output = self.output.partition(b"\n")
        ident, text = wire.decode().split(" ", 1)
        self.commands.append(text)
        command = text.split()[0]
        if command == "PING":
            reply = "OK EC-GATEWAY 3"
        elif command in ("VERSION", "CAPS"):
            board = "wrong-board" if self.bad_board else bench.BOARDS[self.name][0]
            reply = (
                f"OK board={board} proto=3 update=none experimental=1 "
                "gateway=0 ack_mask=00 peers=0 rf_diag=synthetic_v1 "
                "radio_tx=synthetic_only fw_version=0.14"
            )
        elif command == "RFSTATUS":
            reply = (
                f"OK active={self.active} channel=26 power_dbm=0 auto_ack=0 retries=0 "
                f"tx_started={self.tx} tx_done={self.tx} tx_failed=0 rx={self.rx} "
                f"dropped={self.dropped} queued=0 busy=0 ticks_hz=1000000 timestamp=callback"
                " fault=0 bad_crc=unavailable"
            )
        elif command == "RFSTART":
            self.active = 1
            self.tx = self.rx = 0
            reply = "OK active=1 channel=26"
        elif command == "RFSTOP":
            if not self.stop_stuck:
                self.active = 0
            reply = "OK active=0"
        elif command == "RFTX":
            _, nonce, seq, length, cca, ackreq = text.split()
            payload = bench.frame(nonce, int(seq), int(length), int(cca), int(ackreq))
            self.tx += 1
            self.emit(f"RF TX seq={seq} status=done")
            if self.capture:
                included = self.peer.name == "mg24"
                if self.peer.bad_mode:
                    included = not included
                self.peer.emit(rx_line(payload, included))
                self.peer.rx += 1
            if self.inject_ack and int(ackreq):
                self.emit(rx_line(bytes((0x02, 0, int(seq))), self.name == "mg24"))
                self.rx += 1
            reply = f"OK queued=1 seq={seq}"
        else:
            raise AssertionError(text)
        if command not in self.silent and not (
            command == "RFSTART" and self.lose_start_reply
        ):
            self.emit(f"RSP {ident} {reply}")
        return size


class AnalysisTests(unittest.TestCase):
    """Independent payload and CRC evidence must be stricter than TX completion."""

    def test_frame_layout_and_crc_vector(self):
        """Pin known CRC and exact safe header/nonce/sequence/length bytes."""
        self.assertEqual(bench.fcs(b"123456789"), bytes.fromhex("8921"))
        payload = bench.frame(NONCE, 7, 39)
        self.assertEqual(
            payload[:23].hex(), "018807efbe2222efbe1111585246311234567807270000"
        )
        self.assertEqual(len(payload), 39)
        self.assertEqual(payload[23], (23 * 17 + 7) & 255)
        for nonce in ("00000000", "ABCDEF01", "12"):
            with self.assertRaises(ValueError):
                bench.frame(nonce, 0, 39)

    def test_missing_duplicate_order_corruption_and_foreign_run(self):
        """Entire payload equality excludes wrong nonce, corruption and unrelated traffic."""
        expected = {i: bench.frame(NONCE, i, 39) for i in range(3)}
        received = [bench.event(rx_line(expected[i])) for i in (1, 0, 0)]
        received += [bench.event(rx_line(expected[2], corrupt=True))]
        received += [bench.event(rx_line(bench.frame("87654321", 2, 39)))]
        transmitted = [dict(kind="tx", seq=i, status="done") for i in expected]
        result = bench.analyze(expected, received, transmitted)
        self.assertEqual(result["missing_sequences"], [2])
        self.assertEqual(result["duplicate_sequences"], {"0": 1})
        self.assertEqual(result["out_of_order"], 1)
        self.assertEqual(result["invalid_software_crc"], 1)
        self.assertEqual(result["unrelated_frames"], 1)
        self.assertTrue(result["tx_completion_complete"])
        self.assertFalse(result["capture_complete"])

    def test_stripped_crc_is_hardware_evidence_only(self):
        """C6 payload-only capture cannot claim an independently calculated FCS."""
        payload = bench.frame(NONCE, 0, 41)
        event = bench.event(rx_line(payload, included=False))
        self.assertIsNone(event["software_crc_valid"])
        result = bench.analyze({0: payload}, [event], [])
        self.assertTrue(result["capture_complete"])
        self.assertFalse(result["tx_completion_complete"])
        self.assertEqual(result["hardware_crc_only"], 1)
        self.assertEqual(result["independently_crc_checked"], 0)
        self.assertEqual(bench.event(rx_line(b"x"))["length"], 1)

    def test_event_contract_rejects_ambiguous_or_unbounded_data(self):
        """Malformed lengths, duplicate fields and unknown TX states cannot become evidence."""
        good = rx_line(bench.frame(NONCE, 0, 39))
        for bad in (
            good + " len=39",
            good.replace("len=39", "len=41"),
            good.replace("crc=1", "crc=0"),
            good.replace("ticks=1234", "ticks=4294967296"),
            "RF TX seq=0 status=maybe",
        ):
            with self.subTest(line=bad), self.assertRaises(ValueError):
                bench.event(bad)

    def test_ack_observations_are_not_inferred_from_ar_or_tx_done(self):
        """Only captured classic ACKs count; sequence coincidences remain unattributed."""
        events = [
            bench.event(rx_line(bytes.fromhex("020007"))),
            bench.event(rx_line(bytes.fromhex("120007"), included=False)),
            bench.event(rx_line(bytes.fromhex("020063"))),
            bench.event(rx_line(bytes.fromhex("020008"), corrupt=True)),
            bench.event(rx_line(bytes.fromhex("030007"))),
            bench.event(rx_line(bytes.fromhex("020207"))),
            bench.event(rx_line(bench.frame(NONCE, 7, 39, ackreq=1))),
            dict(kind="tx", seq=7, status="done"),
        ]
        result = bench.ack_observations(events, {7, 8})
        self.assertEqual(result["observed"], 3)
        self.assertEqual(result["sequence_counts"], {"7": 2, "99": 1})
        self.assertEqual(result["matching_sequence_observed"], 2)
        self.assertEqual(result["unmatched_sequence_observed"], 1)
        self.assertEqual(result["matching_requested_sequences"], [7])
        self.assertIn("unproven", result["attribution"])
        self.assertEqual(bench.ack_observations(events[-2:], {7})["observed"], 0)


class RunnerTests(unittest.TestCase):
    """Run real command framing and sequencing against simulated endpoints."""

    def setUp(self):
        """Prepare both endpoint fixtures and a finite virtual clock."""
        self.ports = [Port("mg24"), Port("c6")]
        self.ports[0].peer, self.ports[1].peer = self.ports[1], self.ports[0]
        self.links = [bench.Link(port, port.name) for port in self.ports]
        self.clock = Clock()
        self.evidence = {}

    def run_bench(self, mode="baseline"):
        """Exercise the production runner with no wall-clock delay or serial module.

        @param mode Explicit bounded experiment; baseline remains the default.
        """
        bench.run(
            self.links,
            NONCE,
            self.evidence,
            self.clock.time,
            self.clock.sleep,
            mode=mode,
        )

    def test_explicit_cca_ack_mode_has_four_frames_and_separate_outcomes(self):
        """Opt-in mode sends only the two lengths for each flag, at bounded spacing."""
        times = {name: [] for name in bench.BOARDS}
        original = bench.command

        def observe(link, text, *args, **kwargs):
            """Record RF request times while preserving the real one-shot command path.

            @param link Destination endpoint.
            @param text Actual diagnostic command.
            @param args Remaining transport arguments.
            @param kwargs Optional transport settings.
            @return Original command result.
            """
            if text.startswith("RFTX "):
                times[link.name].append(self.clock.time())
            return original(link, text, *args, **kwargs)

        with patch.object(bench, "command", side_effect=observe):
            self.run_bench("cca-ack")
        self.assertTrue(bench.passed(self.evidence))
        self.assertEqual(len(self.evidence["requests"]), 8)
        for index, port in enumerate(self.ports):
            commands = [c.split() for c in port.commands if c.startswith("RFTX ")]
            self.assertEqual(
                [tuple(map(int, c[3:])) for c in commands],
                [(39, 1, 0), (41, 1, 0), (39, 0, 1), (41, 0, 1)],
            )
            self.assertEqual(
                [int(c[2]) for c in commands], list(range(index * 32, index * 32 + 4))
            )
            stamps = times[port.name]
            self.assertTrue(
                all(b - a >= 0.2 - 1e-9 for a, b in zip(stamps, stamps[1:]))
            )
            self.assertEqual(self.evidence["final"][port.name]["tx_started"], "4")
            self.assertEqual(
                self.evidence["ack_observations"][port.name]["observed"], 0
            )
            for case in ("cca", "ackreq"):
                outcome = self.evidence["case_outcomes"][port.name][case]
                self.assertEqual(outcome["expected"], 2)
                self.assertTrue(outcome["capture_complete"])
                self.assertTrue(outcome["tx_completion_complete"])

    def test_unexpected_ack_fails_negative_auto_ack_check_not_payload_capture(self):
        """An independently observed ACK is separate from complete synthetic reception."""
        self.ports[0].inject_ack = True
        self.run_bench("cca-ack")
        self.assertTrue(self.evidence["directions"]["mg24"]["capture_complete"])
        self.assertEqual(self.evidence["ack_observations"]["mg24"]["observed"], 2)
        self.assertEqual(
            self.evidence["ack_observations"]["mg24"]["matching_requested_sequences"],
            [2, 3],
        )
        self.assertFalse(bench.passed(self.evidence))
        self.assertEqual(self.evidence["ack_check"]["status"], "inconclusive")

    def test_cca_ack_lost_reply_has_no_retry_or_baseline_expansion(self):
        """Follow-up mode retains the one-command uncertainty stop and receiver cleanup."""
        self.ports[0].silent.add("RFTX")
        with self.assertRaises(TimeoutError):
            self.run_bench("cca-ack")
        self.assertEqual(len(self.evidence["requests"]), 1)
        self.assertTrue(all(port.active == 0 for port in self.ports))
        self.assertFalse(bench.passed(self.evidence))

    def test_cca_ack_requires_capture_and_board_specific_fcs(self):
        """No observed ACK cannot excuse a missing payload or unexpected CRC representation."""
        for failure in ("missing_capture", "wrong_fcs"):
            self.setUp()
            if failure == "missing_capture":
                self.ports[0].capture = False
            else:
                self.ports[1].bad_mode = True
            self.run_bench("cca-ack")
            with self.subTest(failure=failure):
                self.assertEqual(
                    self.evidence["ack_check"]["status"], "no_classic_ack_observed"
                )
                self.assertFalse(bench.passed(self.evidence))

    def test_unknown_mode_is_rejected_before_serial_commands(self):
        """No misspelled mode may silently select a broader RF experiment."""
        with self.assertRaises(ValueError):
            self.run_bench("unknown")
        self.assertTrue(all(not port.commands for port in self.ports))

    def test_both_directions_fragmented_backpressure_and_independent_crc(self):
        """Forty one-shot frames match on the other endpoint and both radios stop."""
        self.run_bench()
        self.assertLess(self.clock.time(), 30)
        self.assertEqual(len(self.evidence["requests"]), 40)
        self.assertEqual(self.evidence["cleanup_errors"], [])
        self.assertTrue(self.evidence["fcs_modes_valid"])
        self.assertTrue(bench.passed(self.evidence))
        for name, result in self.evidence["directions"].items():
            self.assertTrue(result["capture_complete"])
            self.assertTrue(result["tx_completion_complete"])
            self.assertEqual(self.evidence["final"][name]["active"], "0")
        self.assertEqual(
            self.evidence["directions"]["c6"]["independently_crc_checked"], 20
        )
        self.assertEqual(self.evidence["directions"]["mg24"]["hardware_crc_only"], 20)

    def test_tx_done_without_air_capture_is_not_success(self):
        """A successful transmitter alone cannot prove the receiver heard any packet."""
        self.ports[0].capture = False
        self.run_bench()
        result = self.evidence["directions"]["mg24"]
        self.assertTrue(result["tx_completion_complete"])
        self.assertFalse(result["capture_complete"])
        self.assertEqual(len(result["missing_sequences"]), 20)
        self.assertFalse(bench.passed(self.evidence))

    def test_counter_loss_overflow_and_busy_cannot_pass(self):
        """Missing USB events, queue overflow and uncertain shutdown defeat acceptance."""
        self.run_bench()
        for key, value in (
            ("rx", "21"),
            ("dropped", "1"),
            ("queued", "1"),
            ("busy", "1"),
            ("fault", "1"),
            ("tx_done", "19"),
        ):
            broken = deepcopy(self.evidence)
            broken["final"]["mg24"][key] = value
            with self.subTest(key=key):
                self.assertFalse(bench.passed(broken))

    def test_lost_start_reply_is_not_replayed_and_stops_owned_window(self):
        """Uncertain START still owns cleanup, but never another START attempt."""
        self.ports[0].lose_start_reply = True
        with self.assertRaises(TimeoutError):
            self.run_bench()
        self.assertEqual(
            sum(c.startswith("RFSTART") for c in self.ports[0].commands), 1
        )
        self.assertEqual(self.ports[0].active, 0)
        self.assertNotIn("RFSTOP", self.ports[1].commands)

    def test_lost_tx_reply_never_replays_and_saves_observed_capture(self):
        """TX admission uncertainty preserves receiver evidence without repeating RF."""
        self.ports[0].silent.add("RFTX")
        with self.assertRaises(TimeoutError):
            self.run_bench()
        self.assertEqual(sum(c.startswith("RFTX") for c in self.ports[0].commands), 1)
        self.assertEqual(
            self.evidence["directions"]["mg24"]["independently_received"], 1
        )
        self.assertTrue(all(port.active == 0 for port in self.ports))

    def test_active_or_wrong_board_refuses_without_mutating(self):
        """A pre-existing RF session or wrong firmware never receives START/STOP/TX."""
        for attribute in ("active", "bad_board"):
            self.setUp()
            setattr(self.ports[1], attribute, 1)
            with self.assertRaises(ValueError):
                self.run_bench()
            for port in self.ports:
                self.assertFalse(
                    any(
                        c.startswith(("RFSTART", "RFSTOP", "RFTX"))
                        for c in port.commands
                    )
                )

    def test_stop_and_fcs_contract_drift_are_visible(self):
        """A STOP acknowledgement is insufficient when radio status still says active."""
        self.ports[0].stop_stuck = True
        self.ports[1].bad_mode = True
        self.run_bench()
        self.assertTrue(self.evidence["cleanup_errors"])
        self.assertFalse(self.evidence["fcs_modes_valid"])

    def test_stream_bounds_and_incomplete_write_prevent_followup_commands(self):
        """Memory limits and a truncated request cannot be bypassed by cleanup writes."""
        self.ports[0].input = bytearray(b"x" * (bench.MAX_LINE + 1))
        with self.assertRaises(ValueError):
            for _ in range(100):
                self.links[0].poll()
        self.setUp()
        self.ports[0].write_limit = 1
        with self.assertRaises(TimeoutError):
            bench.command(
                self.links[0],
                "PING",
                self.links,
                0.005,
                self.clock.time,
                self.clock.sleep,
            )
        self.assertTrue(self.links[0].partial_write)
        with self.assertRaisesRegex(RuntimeError, "incomplete"):
            bench.command(self.links[0], "RFSTOP", self.links)

    def test_write_exception_marks_unknown_partial_request(self):
        """An OS write timeout can hide partial delivery, so no later command is safe."""

        def write(data):
            """Emit a prefix before simulating an unreported partial OS write.

            @param data Requested wire bytes.
            """
            self.ports[0].output.extend(data[:3])
            raise TimeoutError("write")

        with patch.object(self.ports[0], "write", side_effect=write) as writer:
            with self.assertRaises(TimeoutError):
                bench.command(self.links[0], "PING", self.links)
            self.assertTrue(self.links[0].partial_write)
            with self.assertRaisesRegex(RuntimeError, "incomplete"):
                bench.command(self.links[0], "RFSTOP", self.links)
            self.assertEqual(writer.call_count, 1)

    def test_uncertain_tx_write_keeps_counterpart_cleanup_working(self):
        """A poisoned sender gets no appended STOP while the receiver is still stopped."""
        sender = self.ports[0]
        sender.write_limit = 4096
        sender.zero_writes = 0
        original = sender.write
        uncertain_calls = []

        def write(data):
            """Simulate a USB exception after accepting the TX request prefix.

            @param data Complete next write from the real runner.
            @return Accepted count for ordinary commands.
            """
            if b" RFTX " in data:
                uncertain_calls.append(data)
                sender.output.extend(data[:5])
                raise TimeoutError("unknown USB prefix accepted")
            return original(data)

        with patch.object(sender, "write", side_effect=write):
            with self.assertRaises(TimeoutError):
                self.run_bench()
        self.assertEqual(len(uncertain_calls), 1)
        self.assertNotIn("RFSTOP", sender.commands)
        self.assertIn("RFSTOP", self.ports[1].commands)
        self.assertEqual(self.ports[1].active, 0)
        self.assertTrue(self.evidence["cleanup_errors"])
        self.assertFalse(bench.passed(self.evidence))

    def test_main_sets_control_lines_before_open_and_closes_on_failure(self):
        """Only the two explicitly selected ports open with their observed DTR policy."""
        opened = []
        closed = []

        def serial_factory(**kwargs):
            """Record serial configuration without importing or opening hardware.

            @param kwargs Production pyserial constructor parameters.
            @return Fake port recording state at open time.
            """
            self.assertIsNone(kwargs["port"])
            self.assertTrue(kwargs["exclusive"])
            self.assertEqual(kwargs["write_timeout"], 0.25)
            port = SimpleNamespace(in_waiting=0)
            port.open = lambda: opened.append((port.port, port.dtr, port.rts))
            port.close = lambda: closed.append(port.port)
            return port

        modules = {
            "serial": SimpleNamespace(Serial=serial_factory),
            "serial.tools": SimpleNamespace(
                list_ports=SimpleNamespace(comports=lambda: [])
            ),
        }
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "receipt.json"
            argv = [
                "bench",
                "--mg24-port",
                "/dev/serial/by-id/mg24",
                "--mg24-serial",
                "mg",
                "--c6-port",
                "/dev/serial/by-id/c6",
                "--c6-serial",
                "esp",
                "--output",
                str(output),
            ]
            with (
                patch.dict(sys.modules, modules),
                patch.object(sys, "argv", argv),
                patch.object(
                    bench,
                    "verify_endpoint",
                    side_effect=["/dev/ttyACM1", "/dev/ttyACM2"],
                ),
                patch.object(
                    bench, "run", side_effect=ValueError("test-only stop before RF")
                ),
                patch("builtins.print"),
            ):
                self.assertEqual(bench.main(), 1)
            self.assertIn("test-only", json.loads(output.read_text())["error"])
        self.assertEqual(
            opened,
            [
                ("/dev/serial/by-id/mg24", True, False),
                ("/dev/serial/by-id/c6", False, False),
            ],
        )
        self.assertEqual(len(closed), 2)

    def test_endpoint_identity_rejects_auto_selection_and_serial_mismatch(self):
        """Only the explicitly inventoried persistent endpoint may be opened later."""
        with self.assertRaises(ValueError):
            bench.verify_endpoint("/dev/ttyACM0", "expected", "mg24", [])
        metadata = SimpleNamespace(
            device="/dev/ttyACM8", vid=0x2886, pid=0x0062, serial_number="wrong"
        )
        with patch.object(
            bench.Path, "resolve", return_value=bench.Path("/dev/ttyACM8")
        ):
            with self.assertRaises(ValueError):
                bench.verify_endpoint(
                    "/dev/serial/by-id/explicit", "expected", "mg24", [metadata]
                )


if __name__ == "__main__":
    unittest.main()
