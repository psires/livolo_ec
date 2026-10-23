import asyncio
import importlib.util
from pathlib import Path
from types import SimpleNamespace
import unittest

from livolod.phase import PhaseClient
from livolod.protocol import frame, heartbeat
from livolod.uart import CommandError

spec = importlib.util.spec_from_file_location(
    "phase_harness", Path(__file__).parents[1] / "tools/check_phase_hardware.py"
)
harness = importlib.util.module_from_spec(spec)
spec.loader.exec_module(harness)
CAPS = dict(
    phase="tx_entry_v1",
    phase_scale="65500",
    phase_max_ms="12000",
    phase_probe="1",
    rtimer_hz="65536",
    rtimer_bits="32",
)


class Clock:
    def __init__(self):
        """Prepare the simulated collaborator with the requested test behavior."""
        self.value = 0.0

    def now(self):
        """Expose the deterministic test clock in monotonic seconds."""
        return self.value

    async def sleep(self, duration):
        """Advance the deterministic test clock by the requested delay.

        @param duration Elapsed seconds to add to the test clock.
        """
        self.value += duration


class Radio:
    def __init__(self, clock, heartbeat_errors=()):
        """Prepare the simulated collaborator with the requested test behavior.

        @param clock Deterministic clock used by the phase fixture.
        @param heartbeat_errors Sequence of heartbeat failures to inject.
        """
        self.clock = clock
        self.fault = asyncio.get_running_loop().create_future()
        self.anchor = None
        self.generation = 0
        self.commands = []
        self.emitted_probes = []
        self.heartbeats = []
        self.replays = 0
        self.errors = list(heartbeat_errors)

    async def command(self, text):
        """Provide the scenario-specific UART response or injected failure.

        @param text Command or encoded protocol text for this exchange.
        """
        self.commands.append(text)
        self.clock.value += 0.01
        words = text.split()
        if words[0] == "SEND":
            if self.errors:
                raise CommandError(self.errors.pop(0))
            self.anchor = int(self.clock.now() * 65536)
            self.heartbeats.append(self.clock.now())
            self.generation += 1
            return "OK TX 0"
        age = (
            None if self.anchor is None else int(self.clock.now() * 65536) - self.anchor
        )
        valid = age is not None and age < 12 * 65536
        if words[0] == "STATUS":
            return f"OK phase_valid={int(valid)} phase_anchor_ticks={self.anchor or 0} phase_anchor_gen={self.generation}"
        if words[0] != "PHASE_PROBE":
            raise AssertionError(text)
        if not valid:
            raise CommandError("ERR PHASE_REFERENCE")
        raw = (
            b"\x01\x88"
            + bytes.fromhex(words[2])
            + bytes.fromhex("2a992b12") * 2
            + b"EC_PHASE_PROBE_V1"
            + bytes.fromhex(words[3])
            + bytes(3)
        )
        raw += ((age % 65536) * 65500 // 65536).to_bytes(2, "little") + bytes(
            (age // 65536,)
        )
        self.emitted_probes.append(raw)
        entry = self.anchor + age
        return (
            f"OK TX 0 timestamp=tx_entry entry_ticks={entry} return_ticks={entry + 100} "
            f"anchor_ticks={self.anchor} anchor_gen={self.generation} age_ticks={age} raw={raw.hex()}"
        )

    async def replay_last_probe(self):
        """Expose the last cached probe reply for duplicate-request assertions."""
        if not self.commands[-1].startswith("PHASE_PROBE "):
            raise AssertionError("Replay was not immediate")
        self.commands.append(self.commands[-1])
        self.replays += 1


class HarnessTests(unittest.IsolatedAsyncioTestCase):
    async def run_measurement(self, errors=()):
        """Execute the phase harness with a deterministic clock and injected errors.

        @param errors Sequence of heartbeat failures to exercise.
        """
        clock = Clock()
        radio = Radio(clock, errors)
        client = PhaseClient(radio, "123456789abcdef0", CAPS, gateway_id="2a992b12")
        config = SimpleNamespace(gateway="2a992b12", tag="a7", heartbeat_parameter=1)
        results = []
        await harness.measurements(
            radio, client, config, results, now=clock.now, sleep=clock.sleep
        )
        return clock, radio, results

    async def test_finite_schedule_reference_rejections_and_immediate_replay(self):
        """Verify finite schedule reference rejections and immediate replay."""
        clock, radio, results = await self.run_measurement()
        self.assertEqual(len(radio.emitted_probes), 14)
        self.assertEqual(len({raw[28:32] for raw in radio.emitted_probes}), 14)
        self.assertEqual(radio.replays, 1)
        self.assertGreaterEqual(radio.heartbeats[1] - radio.heartbeats[0], 10)
        self.assertLess(clock.now(), 35)
        self.assertEqual(
            [r["check"] for r in results if r.get("rejected_without_send")],
            ["missing_reference", "expired_reference"],
        )
        self.assertEqual(
            [r["requested_phase"] for r in results if r["check"] == "probe"],
            list(harness.POINTS) * 2,
        )

    async def test_definite_busy_heartbeat_retries_are_bounded(self):
        """Verify definite busy heartbeat retries are bounded."""
        _, radio, _ = await self.run_measurement(("ERR TX 2", "ERR TX 2"))
        self.assertEqual(sum(c.startswith("SEND ") for c in radio.commands), 4)
        with self.assertRaisesRegex(CommandError, "ERR TX 2"):
            await self.run_measurement(("ERR TX 2",) * 3)

    async def test_uncertain_heartbeat_aborts(self):
        """Verify uncertain heartbeat aborts."""
        with self.assertRaisesRegex(CommandError, "ERR TX 1"):
            await self.run_measurement(("ERR TX 1",))

    def test_guard_refuses_all_device_output_and_query_sends(self):
        """Verify guard refuses all device output and query sends."""
        for opcode in (2, 0x10, 0xAC, 0xFA):
            raw = frame("2a992b12", "a7", "46715621", "0002", 1, opcode, bytes(8))
            with self.assertRaises(ValueError):
                harness.check_command(
                    f"SEND 123456789abcdef0 {raw.hex()}", "2a992b12", "a7"
                )
        with self.assertRaises(ValueError):
            harness.check_command("SEND_PHASE 123456789abcdef0 00", "2a992b12", "a7")
        raw = heartbeat("2a992b12", "a7", 1, 0, 1)
        harness.check_command(f"SEND 123456789abcdef0 {raw.hex()}", "2a992b12", "a7")

    async def test_exact_cached_replay_reuses_only_last_id_and_restores_next_id(self):
        """Verify exact cached replay reuses only last id and restores next id."""
        radio = object.__new__(harness.AuditedGateway)
        radio.transcript = [
            dict(
                request_id=100,
                command="PHASE_PROBE session 01 abcdef01",
                result="OK TX 0 cached",
            )
        ]
        radio.next_id, radio.pending = 101, None
        observed = []

        async def command(text, retries):
            """Provide the scenario-specific UART response or injected failure.

            @param text Command or encoded protocol text for this exchange.
            @param retries Additional UART attempts allowed by the caller.
            """
            observed.append((radio.next_id, text, retries))
            radio.next_id += 1
            return "OK TX 0 cached"

        radio.command = command
        await radio.replay_last_probe()
        self.assertEqual(observed, [(100, "PHASE_PROBE session 01 abcdef01", 0)])
        self.assertEqual(radio.next_id, 101)
