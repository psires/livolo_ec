import asyncio
import binascii
from dataclasses import replace
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from livolod.config import load, parse
from livolod.protocol import decode, frame, heartbeat, rx_record
from livolod.service import Service
from livolod.simulator import Simulator
from livolod.storage import Store
from livolod.uart import CommandError, Gateway, LinkError

ROOT = Path(__file__).resolve().parents[2]
EXAMPLE = ROOT / "host/config.example.json"


def report(
    device="46715621",
    gateway="2a992b12",
    tag="a7",
    mask=3,
    bits=1,
    counter=1,
    token=7,
    sequence=1,
    opcode=5,
):
    """Build a checksum-valid device state report for protocol and service tests.

    @param device Four-byte device wire identity as hexadecimal.
    @param gateway Four-byte gateway wire identity as hexadecimal.
    @param tag Network tag as hexadecimal.
    @param mask Output-selection bit mask.
    @param bits Reported output-state bits.
    @param counter Device report counter byte.
    @param token Transaction token byte.
    @param sequence MAC sequence byte.
    @param opcode Application operation byte.
    """
    raw = bytearray(
        frame(
            device,
            tag,
            device,
            "0002",
            sequence,
            opcode,
            bytes((1, 0, mask, bits, 0, 0, counter, token)),
        )[:39]
    )
    raw[3:7] = bytes.fromhex(gateway)
    raw[21] = 0x9E
    raw[37:39] = binascii.crc_hqx(raw[21:37], 0xFFFF).to_bytes(2, "big")
    return f"RX {sequence} 0 -42 99 39 {raw.hex()}"


class Broker:
    def __init__(self):
        """Prepare the simulated collaborator with the requested test behavior."""
        self.messages = []

    def publish(self, suffix, value, retain=False):
        """Record or deliver a publication for assertions without a production broker.

        @param suffix Topic suffix for the recorded publication.
        @param value Payload or value supplied to the simulated collaborator.
        @param retain Whether the publication requests broker retention.
        """
        self.messages.append((suffix, value, retain))

    publish_topic = publish

    def results(self, request_id):
        """Select recorded bridge results belonging to one command request.

        @param request_id Command identifier used to correlate requests and results.
        """
        return [
            v
            for topic, v, _ in self.messages
            if topic == "bridge/result" and v["request_id"] == request_id
        ]


class ConfigurationTests(unittest.TestCase):
    def test_example_and_rejections(self):
        """Verify example and rejections."""
        self.assertEqual(load(EXAMPLE).devices[0].mask, 3)
        for change in (
            lambda c: c["network"].update(tag="ff"),
            lambda c: c["devices"].append(c["devices"][0]),
            lambda c: c["devices"][0].update(outputs=True),
            lambda c: c["devices"][0].update(slot=256),
            lambda c: c["mqtt"].update(base_topic="livolo/#"),
            lambda c: c.update(typo=1),
        ):
            config = json.loads(EXAMPLE.read_text())
            change(config)
            with self.assertRaises(ValueError):
                parse(config)


class ProtocolTests(unittest.TestCase):
    def test_commands_match_captured_frames(self):
        # Original stock gateway control capture, decoded independently by tools/decode_ec.py.
        """Verify commands match captured frames."""
        import csv

        count = 0
        with (ROOT / "analysis/decoded_packets.csv").open() as stream:
            for row in csv.DictReader(stream):
                if row["family"] != "8c" or row["source_wire"] != "9bf4ea38":
                    continue
                if row["opcode"] == "02":
                    raw = frame(
                        row["source_wire"],
                        row["network_tag"],
                        row["subject_wire"],
                        row["subject_suffix"],
                        int(row["mac_sequence"]),
                        2,
                        bytes.fromhex(row["data_hex"]),
                    )
                    # The fallback flags vary; compare the confirmed application body.
                    self.assertEqual(raw[21:38], bytes.fromhex(row["raw_hex"])[21:38])
                    count += 1
                if row["heartbeat_counter"] != "":
                    raw = heartbeat(
                        row["source_wire"],
                        row["network_tag"],
                        int(row["mac_sequence"]),
                        int(row["heartbeat_counter"]),
                        int(row["heartbeat_parameter"]),
                    )
                    self.assertEqual(raw[:38], bytes.fromhex(row["raw_hex"])[:38])
        self.assertGreater(count, 10)

    def test_checksum_and_length_are_enforced(self):
        """Verify checksum and length are enforced."""
        line = report()
        self.assertEqual(rx_record(line)[-1].data[3], 1)
        with self.assertRaises(ValueError):
            rx_record(line.replace("99 39", "99 41"))
        raw = bytearray.fromhex(line.split()[-1])
        raw[32] ^= 1
        with self.assertRaises(ValueError):
            decode(raw)


class StoreTests(unittest.TestCase):
    def test_output_tokens_avoid_captured_checksum_collision_and_adjacent_values(self):
        """Verify output tokens avoid captured checksum collision and adjacent values."""

        def raw(token, value):
            """Build the output frame used to examine token and checksum collisions.

            @param token Transaction token byte.
            @param value Payload or value supplied to the simulated collaborator.
            """
            return frame(
                "2a992b12",
                "a7",
                "46715621",
                "0002",
                1,
                2,
                bytes((1, 0, 1, value, 0, 0, 0, token)),
            )

        # Reproduce the actual trial-03 collision under consecutive allocation.
        self.assertEqual(raw(6, 1)[37], raw(7, 0)[37])
        with tempfile.TemporaryDirectory() as directory:
            store = Store(directory + "/state.db", "network")
            try:
                on = store.token("peer", 4, mask=1, value=1)
                off = store.token("peer", on, mask=1, value=0)
                self.assertNotEqual(raw(on, 1)[37], raw(off, 0)[37])
                self.assertNotEqual(on >> 1, off >> 1)
                self.assertNotIn((off - on) & 255, (0, 1, 255))
            finally:
                store.close()

    def test_output_reservation_survives_crash_before_command_result(self):
        """Verify output reservation survives crash before command result."""
        with tempfile.TemporaryDirectory() as directory:
            path = directory + "/state.db"
            with patch("livolod.storage.time.time", return_value=1000):
                store = Store(path, "network")
                first = store.token("peer", mask=1, value=1)
                store.close()  # No command result was written.
                store = Store(path, "network")
                second = store.token("peer", mask=1, value=0)
                self.assertNotEqual((2 + first) & 255, (1 + second) & 255)
                # Reservations are per device.
                self.assertEqual(store.token("other", mask=1, value=1), first)
                store.close()

    def test_output_tokens_bootstrap_old_ledger_including_unconfirmed(self):
        """Verify output tokens bootstrap old ledger including unconfirmed."""
        with tempfile.TemporaryDirectory() as directory:
            store = Store(directory + "/state.db", "network")
            try:
                store.begin(
                    "old", {"action": "set", "device": "peer", "mask": 1, "value": 1}
                )
                store.finish("old", {"status": "unconfirmed", "token": 6})
                # Force token 7 (adjacent/colliding) and 8 (safe) as the only
                # unquarantined choices, as a migrated database might contain.
                with store.db:
                    store.db.executemany(
                        "INSERT INTO tokens VALUES (?,?,?)",
                        [
                            ("peer", n, time.time())
                            for n in range(1, 256)
                            if n not in (7, 8)
                        ],
                    )
                chosen = store.token("peer", 6, mask=1, value=0)
                self.assertEqual(chosen, 8)
            finally:
                store.close()

    def test_output_tokens_preserve_quarantine_and_exhaustion(self):
        """Verify output tokens preserve quarantine and exhaustion."""
        with tempfile.TemporaryDirectory() as directory:
            store = Store(directory + "/state.db", "network")
            try:
                with store.db:
                    store.db.executemany(
                        "INSERT INTO tokens VALUES (?,?,?)",
                        [("peer", n, 1000) for n in range(1, 256)],
                    )
                with patch("livolod.storage.time.time", return_value=1059):
                    with self.assertRaises(ValueError):
                        store.token("peer", mask=1, value=0)
                with patch("livolod.storage.time.time", return_value=1060):
                    self.assertIn(store.token("peer", mask=1, value=0), range(1, 256))
            finally:
                store.close()

    def test_checksum_guard_is_independent_of_token_spacing_and_handles_state_alias(
        self,
    ):
        """Verify checksum guard is independent of token spacing and handles state alias."""
        with tempfile.TemporaryDirectory() as directory:
            store = Store(directory + "/state.db", "network")
            try:
                store.begin("old", {"action": "set", "device": "peer", "state": "ON"})
                store.finish("old", {"status": "confirmed", "token": 6})
                with store.db:
                    store.db.executemany(
                        "INSERT INTO tokens VALUES (?,?,?)",
                        [
                            ("peer", n, time.time())
                            for n in range(1, 256)
                            if n not in (11, 12)
                        ],
                    )
                # Prior all-output ON: 3+3+6 == candidate 1+0+11.
                # Token 11 differs in upper bits and is not adjacent to 6.
                self.assertEqual(
                    store.token("peer", 6, mask=1, value=0, full_mask=3), 12
                )
            finally:
                store.close()

    def test_durable_deduplication_unknown_restart_and_ownership(self):
        """Verify durable deduplication unknown restart and ownership."""
        with tempfile.TemporaryDirectory() as directory:
            path = directory + "/state.db"
            store = Store(path, "network")
            self.assertIsNone(store.begin("abc", {"value": 1}))
            with self.assertRaises(ValueError):
                store.begin("abc", {"value": 2})
            with self.assertRaises(RuntimeError):
                Store(path, "network")
            token = store.token("peer")
            store.close()
            store = Store(path, "network")
            self.assertEqual(store.begin("abc", {"value": 1})["status"], "unknown")
            self.assertNotEqual(store.token("peer"), token)
            store.close()
            with self.assertRaises(ValueError):
                Store(path, "another-network")


class ServiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        """Prepare isolated state and asynchronous collaborators for the next test."""
        self.temp = tempfile.TemporaryDirectory()
        self.config = replace(load(EXAMPLE), database=self.temp.name + "/state.db")
        self.store = Store(self.config.database, "network")
        self.broker = Broker()
        self.service = Service(self.config, self.store, self.broker, Simulator)
        self.service.gateway = Simulator()
        await self.service.restore()
        self.tasks = []

    async def asyncTearDown(self):
        """Stop asynchronous collaborators and release temporary test state."""
        for task in self.tasks:
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        await self.service.gateway.close()
        self.store.close()
        self.temp.cleanup()

    def start_workers(self):
        """Start the test service's radio and command-processing workers."""
        self.tasks = [
            asyncio.create_task(self.service.events()),
            asyncio.create_task(self.service.command_worker()),
        ]

    async def wait_result(self, request_id, status):
        """Wait for the expected command-ledger status in the test service.

        @param request_id Command identifier used to correlate requests and results.
        @param status Expected command status.
        """

        async def wait():
            """Wait until the enclosing test's readiness or result condition is satisfied."""
            while not any(
                v["status"] == status for v in self.broker.results(request_id)
            ):
                await asyncio.sleep(0.001)

        await asyncio.wait_for(wait(), 1)

    def request(self, request_id, **fields):
        """Build or submit a command request using this fixture's defaults.

        @param request_id Command identifier used to correlate requests and results.
        @param fields Command fields overriding the fixture defaults.
        """
        self.service.receive(
            "livolo/46715621/set",
            json.dumps(dict(request_id=request_id, **fields)).encode(),
            False,
        )

    async def test_restore_verifies_full_table_before_arming_and_rotates_session(self):
        """Verify restore verifies full table before arming and rotates session."""
        commands = self.service.gateway.commands
        self.assertLess(
            commands.index("PEER GET 0"),
            next(i for i, c in enumerate(commands) if c.startswith("ARM ")),
        )
        old = self.service.session
        await self.service.restore()
        self.assertNotEqual(self.service.session, old)
        self.assertEqual(len(self.service.gateway.peers), 1)

    async def test_end_to_end_set_duplicate_delivery_and_restart_dedup(self):
        """Verify end to end set duplicate delivery and restart dedup."""
        self.start_workers()
        self.request("one", state="ON")
        await self.wait_result("one", "confirmed")
        self.assertEqual(self.service.states["46715621"]["state_bits"], 3)
        count = sum(c.startswith("SEND ") for c in self.service.gateway.commands)
        self.request("one", state="ON")
        await asyncio.sleep(0.01)
        self.assertEqual(
            count, sum(c.startswith("SEND ") for c in self.service.gateway.commands)
        )
        self.request("one", state="OFF")
        self.assertEqual(self.broker.results("one")[-1]["status"], "rejected")
        self.assertEqual(self.store.command_result("one")["status"], "confirmed")
        self.service.submit("remove", {"request_id": "forget", "device": "46715621"})
        self.request("one", state="ON")
        self.assertEqual(self.broker.results("one")[-1]["status"], "confirmed")

    async def test_manual_changes_with_reused_token_are_published(self):
        """Verify manual changes with reused token are published."""
        now = time.monotonic()
        self.service.handle_rx(now, report(bits=1, counter=1, token=55))
        self.service.handle_rx(
            now + 0.01, report(bits=3, counter=2, token=55, sequence=2)
        )
        states = [
            value
            for topic, value, _ in self.broker.messages
            if topic == "46715621/state" and value
        ]
        self.assertEqual([s["state_bits"] for s in states], [1, 3])
        self.assertEqual(self.store.states()["46715621"]["state_bits"], 3)

    async def test_simulator_scheduled_report_reads_current_state(self):
        """Verify simulator scheduled report reads current state."""
        self.start_workers()
        self.request("fast-set", state="ON")
        await self.wait_result("fast-set", "confirmed")
        await asyncio.sleep(0.08)
        self.assertEqual(self.service.states["46715621"]["state_bits"], 3)

    async def test_set_prelude_preserves_epoch_and_blocks_periodic_heartbeat(self):
        # Maintenance decides it is due while the set prelude is in flight.
        # Its second due check must suppress a heartbeat between prelude and set.
        """Verify set prelude preserves epoch and blocks periodic heartbeat."""
        self.start_workers()
        entered, release = asyncio.Event(), asyncio.Event()
        original = self.service.gateway.command

        async def command(text, retries=1):
            """Provide the scenario-specific UART response or injected failure.

            @param text Command or encoded protocol text for this exchange.
            @param retries Additional UART attempts allowed by the caller.
            """
            if (
                text.startswith("SEND ")
                and decode(bytes.fromhex(text.split()[-1])).opcode == 8
            ):
                entered.set()
                await release.wait()
            return await original(text, retries)

        self.service.gateway.command = command
        self.service.started = time.monotonic() - 120
        before = int(time.monotonic() - self.service.started)
        self.request("prelude", mask=1, value=1)
        await asyncio.wait_for(entered.wait(), 1)
        self.service.handle_rx(
            time.monotonic(), report(mask=1, bits=1, counter=2, token=1)
        )
        self.assertIsNone(self.service.pending)
        maintenance = asyncio.create_task(self.service.emit_heartbeat(only_if_due=True))
        release.set()
        await self.wait_result("prelude", "confirmed")
        await asyncio.wait_for(maintenance, 1)
        raw = [
            bytes.fromhex(c.split()[-1])
            for c in self.service.gateway.commands
            if c.startswith("SEND ")
        ]
        self.assertEqual([decode(r).opcode for r in raw], [8, 2])
        counter = int.from_bytes(decode(raw[0]).data[:4], "little")
        self.assertLessEqual(before, counter)
        self.assertLessEqual(counter, int(time.monotonic() - self.service.started))
        self.assertEqual(
            raw[0],
            heartbeat(self.config.gateway, self.config.tag, raw[0][2], counter, 1),
        )
        self.assertEqual(raw[1][-3:], bytes(3))
        self.assertEqual(decode(raw[1]).data[2:4], bytes((1, 1)))

    async def test_retry_stops_on_second_attempt_with_one_token(self):
        """Verify retry stops on second attempt with one token."""
        attempts = []

        async def transmit(raw):
            """Simulate the transmission outcome and report timing required by this test.

            @param raw MAC frame bytes for this scenario.
            """
            packet = decode(raw)
            if packet.opcode == 2:
                attempts.append((time.monotonic(), raw))
                if len(attempts) == 2:
                    self.service.handle_rx(
                        time.monotonic(),
                        report(mask=1, bits=1, counter=44, token=packet.data[7]),
                    )
            return True

        self.service._transmit = transmit
        self.store.begin(
            "long-window",
            {"action": "set", "device": "46715621", "mask": 1, "value": 1},
        )
        await self.service.set_outputs("long-window", "46715621", 1, 1)
        self.assertEqual(
            self.store.command_result("long-window")["status"], "confirmed"
        )
        self.assertEqual(len(attempts), 2)
        self.assertGreaterEqual(attempts[-1][0] - attempts[0][0], 0.19)
        self.assertEqual(len({decode(raw).data for _, raw in attempts}), 1)
        self.assertEqual(len({raw[2] for _, raw in attempts}), 2)
        self.assertEqual(
            self.store.db.execute("SELECT count(*) FROM tokens").fetchone()[0], 1
        )
        self.assertFalse(self.service.radio_lock.locked())

    async def test_three_attempts_share_absolute_deadline_and_late_report_only_updates_state(
        self,
    ):
        """Verify three attempts share absolute deadline and late report only updates state."""
        attempts = []
        late = []

        async def transmit(raw):
            """Simulate the transmission outcome and report timing required by this test.

            @param raw MAC frame bytes for this scenario.
            """
            packet = decode(raw)
            if packet.opcode == 2:
                attempts.append(raw)
                await asyncio.sleep(0.02)  # UART latency must count in the budget.
                if len(attempts) == 3:
                    self.service.handle_rx(
                        self.service.pending["deadline"] + 0.001,
                        report(mask=1, bits=1, counter=44, token=packet.data[7]),
                    )
                    late.append(self.service.pending["future"].done())
            return True

        self.service._transmit = transmit
        self.store.begin(
            "deadline", {"action": "set", "device": "46715621", "mask": 1, "value": 1}
        )
        started = time.monotonic()
        with (
            patch("livolod.service.SET_CONFIRM_TIMEOUT", 0.8),
            patch("livolod.service.SET_RETRY_INTERVAL", 0.05),
        ):
            await self.service.set_outputs("deadline", "46715621", 1, 1)
        elapsed = time.monotonic() - started
        self.assertGreaterEqual(elapsed, 0.79)
        self.assertLess(elapsed, 1.2)
        self.assertEqual(len(attempts), 3)
        self.assertEqual(late, [False])
        self.assertEqual(self.store.command_result("deadline")["status"], "unconfirmed")
        self.assertEqual(self.service.states["46715621"]["state_bits"], 1)

    async def test_deadline_during_send_raises_unknown_link_outcome_without_further_retries(
        self,
    ):
        """Verify deadline during send raises unknown link outcome without further retries."""
        attempts = []

        async def transmit(raw):
            """Simulate the transmission outcome and report timing required by this test.

            @param raw MAC frame bytes for this scenario.
            """
            if decode(raw).opcode == 2:
                attempts.append(raw)
                await asyncio.sleep(10)
            return True

        self.service._transmit = transmit
        with patch("livolod.service.SET_CONFIRM_TIMEOUT", 0.05):
            with self.assertRaisesRegex(LinkError, "RF outcome unknown"):
                await self.service.set_outputs("stalled", "46715621", 1, 1)
        self.assertEqual(len(attempts), 1)
        self.assertIsNone(self.service.pending)
        self.assertFalse(self.service.heartbeat_reserved)
        self.assertFalse(self.service.radio_lock.locked())

    async def test_busy_prelude_does_not_send_outputs(self):
        """Verify busy prelude does not send outputs."""
        self.start_workers()
        original = self.service.gateway.command
        transmitted = []

        async def busy(text, retries=1):
            """Reject the tested send with a definite radio-busy response.

            @param text Command or encoded protocol text for this exchange.
            @param retries Additional UART attempts allowed by the caller.
            """
            if text.startswith("SEND "):
                transmitted.append(decode(bytes.fromhex(text.split()[-1])).opcode)
                raise CommandError("ERR TX 2")
            return await original(text, retries)

        self.service.gateway.command = busy
        self.request("busy-prelude", state="ON")
        await self.wait_result("busy-prelude", "busy")
        self.assertEqual(transmitted, [8, 8, 8, 8])
        self.assertFalse(self.service.heartbeat_reserved)
        self.assertFalse(self.service.radio_lock.locked())

    async def test_set_waits_for_normal_cadence_without_blocking_lease_commands(self):
        """Verify set waits for normal cadence without blocking lease commands."""
        self.start_workers()
        last = time.monotonic() - 9.9
        self.service.last_heartbeat = last
        self.service.next_heartbeat = (
            0  # The last successful beat still enforces spacing.
        )
        sends = []
        original = self.service.gateway.command

        async def command(text, retries=1):
            """Provide the scenario-specific UART response or injected failure.

            @param text Command or encoded protocol text for this exchange.
            @param retries Additional UART attempts allowed by the caller.
            """
            if text.startswith("SEND "):
                sends.append(
                    (time.monotonic(), decode(bytes.fromhex(text.split()[-1])).opcode)
                )
            return await original(text, retries)

        self.service.gateway.command = command
        self.request("cadence", state="ON")
        await asyncio.sleep(0.02)
        self.assertTrue(self.service.heartbeat_reserved)
        self.assertFalse(self.service.radio_lock.locked())
        self.assertIsNone(self.service.pending)
        await self.service.emit_heartbeat()  # Cannot force an early beat while reserved.
        await self.service.gateway.command(f"ARM {self.service.session} 30")
        self.assertFalse(sends)
        await self.wait_result("cadence", "confirmed")
        self.assertEqual([opcode for _, opcode in sends], [8, 2])
        self.assertGreaterEqual(sends[0][0] - last, 10)
        self.assertFalse(self.service.heartbeat_reserved)

    async def test_cancelling_wait_releases_reservation_without_actuation(self):
        """Verify cancelling wait releases reservation without actuation."""
        self.start_workers()
        self.service.last_heartbeat = time.monotonic()
        self.request("cancel-wait", state="ON")
        await asyncio.sleep(0.02)
        self.assertTrue(self.service.heartbeat_reserved)
        self.tasks[1].cancel()
        await asyncio.gather(self.tasks[1], return_exceptions=True)
        self.assertFalse(self.service.heartbeat_reserved)
        self.assertFalse(self.service.radio_lock.locked())
        self.assertIsNone(self.service.pending)
        self.assertEqual(
            self.service.command_result("cancel-wait")["status"], "unknown"
        )
        self.assertFalse(
            any(c.startswith("SEND ") for c in self.service.gateway.commands)
        )

    async def test_session_change_during_wait_cannot_transmit(self):
        """Verify session change during wait cannot transmit."""
        self.start_workers()
        self.service.last_heartbeat = time.monotonic() - 9.95
        self.request("session-wait", state="ON")
        await asyncio.sleep(0.01)
        self.service.session = "changed-session"
        await self.wait_result("session-wait", "unknown")
        self.assertFalse(self.service.heartbeat_reserved)
        self.assertFalse(
            any(c.startswith("SEND ") for c in self.service.gateway.commands)
        )

    async def test_reconnect_cancels_wait_and_never_replays_old_intent(self):
        """Verify reconnect cancels wait and never replays old intent."""
        await self.service.gateway.close()
        gateways = []

        def factory():
            """Create a simulated gateway with the reconnect behavior under test."""
            gateway = Simulator()
            gateways.append(gateway)
            return gateway

        self.service.gateway_factory = factory
        self.service.ready = False
        task = asyncio.create_task(self.service.run())

        async def wait_until(predicate):
            """Wait for the supplied test condition within the scenario deadline.

            @param predicate Callable that becomes true when the expected observation is available.
            """
            while not predicate():
                await asyncio.sleep(0.01)

        try:
            await asyncio.wait_for(
                wait_until(
                    lambda: self.service.ready
                    and self.service.last_heartbeat is not None
                ),
                2,
            )
            old = self.service.session
            self.request("reconnect-wait", state="ON")
            await asyncio.wait_for(
                wait_until(lambda: self.service.heartbeat_reserved), 1
            )
            self.service.gateway.fault.set_result(LinkError("simulated disconnect"))
            await self.wait_result("reconnect-wait", "unknown")
            await asyncio.wait_for(
                wait_until(lambda: self.service.ready and self.service.session != old),
                3,
            )
            self.assertFalse(self.service.heartbeat_reserved)
            self.assertEqual(len(gateways), 2)
            self.assertFalse(
                any(
                    decode(bytes.fromhex(c.split()[-1])).opcode == 2
                    for gateway in gateways
                    for c in gateway.commands
                    if c.startswith("SEND ")
                )
            )
        finally:
            self.service.stop.set()
            await asyncio.wait_for(task, 2)
            self.service.gateway = Simulator()

    async def test_stale_queue_intent_is_not_promoted_to_phase_wait(self):
        """Verify stale queue intent is not promoted to phase wait."""
        self.service.receive(
            "livolo/46715621/set", b'{"request_id":"stale","state":"ON"}', False
        )
        queued, session, request_id, action, values = self.service.commands.get_nowait()
        self.service.commands.put_nowait(
            (queued - 6, session, request_id, action, values)
        )
        self.start_workers()
        await self.wait_result("stale", "rejected")
        self.assertFalse(self.service.heartbeat_reserved)
        self.assertFalse(
            any(c.startswith("SEND ") for c in self.service.gateway.commands)
        )

    async def test_phase_wait_and_lock_contention_are_bounded(self):
        """Verify phase wait and lock contention are bounded."""
        self.start_workers()
        self.service.next_heartbeat = time.monotonic() + 100
        with patch("livolod.service.HEARTBEAT_WAIT_TIMEOUT", 0.03):
            self.request("window-expired", state="ON")
            await self.wait_result("window-expired", "rejected")
            self.assertFalse(self.service.heartbeat_reserved)
            self.assertFalse(self.service.radio_lock.locked())
            self.service.next_heartbeat = 0
            await self.service.radio_lock.acquire()
            try:
                self.request("lock-expired", state="ON")
                await self.wait_result("lock-expired", "rejected")
                self.assertFalse(self.service.heartbeat_reserved)
                self.assertTrue(
                    self.service.radio_lock.locked()
                )  # Still owned by this test.
            finally:
                self.service.radio_lock.release()
        self.assertFalse(
            any(c.startswith("SEND ") for c in self.service.gateway.commands)
        )

    async def test_refresh_uses_only_queries_and_requires_fresh_report(self):
        """Verify refresh uses only queries and requires fresh report."""
        self.start_workers()
        self.service.handle_rx(time.monotonic() - 10, report())
        original = self.service.gateway.command

        async def queried(text, retries=1):
            """Record query traffic and arrange a fresh simulated device report.

            @param text Command or encoded protocol text for this exchange.
            @param retries Additional UART attempts allowed by the caller.
            """
            result = await original(text, retries)
            if text.startswith("SEND "):
                packet = decode(bytes.fromhex(text.split()[-1]))
                self.assertIn(packet.opcode, (0x10, 0xAC))
                if packet.opcode == 0xAC:
                    self.service.gateway.report("46715621", mask=3, bits=1)
            return result

        self.service.gateway.command = queried
        self.service.submit("refresh", {"request_id": "refresh1", "device": "46715621"})
        await self.wait_result("refresh1", "confirmed")
        self.assertEqual(
            self.service.command_result("refresh1")["state"]["state_bits"], 1
        )

    async def test_foreign_and_invalid_reports_do_not_set_state(self):
        """Verify foreign and invalid reports do not set state."""
        for line in (
            report(device="deadbeef"),
            report(tag="b1"),
            report(gateway="deadbeef"),
            report(bits=4),
        ):
            self.service.handle_rx(time.monotonic(), line)
        self.assertFalse(self.service.states)

    async def test_only_matching_fresh_report_confirms_pending(self):
        """Verify only matching fresh report confirms pending."""
        now = time.monotonic()
        future = asyncio.get_running_loop().create_future()
        self.service.pending = dict(
            device="46715621",
            mask=3,
            value=1,
            token=77,
            started=now,
            baseline=(0, 1, 77),
            future=future,
        )
        self.service.handle_rx(now - 1, report(token=77))
        self.service.handle_rx(now, report(token=76))
        self.service.handle_rx(now, report(token=77, bits=0))
        self.service.handle_rx(now, report(token=77, mask=0))
        self.assertEqual(self.service.states["46715621"]["mask"], 0)
        self.assertFalse(future.done())
        self.service.handle_rx(now, report(token=77, counter=2))
        self.assertTrue(future.done())

    async def test_retained_offline_and_old_session_commands_do_not_transmit(self):
        """Verify retained offline and old session commands do not transmit."""
        self.start_workers()
        self.service.receive(
            "livolo/46715621/set", b'{"request_id":"retained","state":"ON"}', True
        )
        self.service.ready = False
        self.request("offline", state="ON")
        self.assertEqual(self.broker.results("offline")[-1]["status"], "rejected")
        self.service.ready = True
        self.request("old", state="ON")
        self.service.session = "new-session"
        await self.wait_result("old", "rejected")
        self.assertFalse(
            any(c.startswith("SEND ") for c in self.service.gateway.commands)
        )

    async def test_bounded_targeted_pairing_and_tag_adoption(self):
        """Verify bounded targeted pairing and tag adoption."""
        self.start_workers()
        self.service.receive(
            "livolo/bridge/request/pair",
            b'{"request_id":"pair1","device":"46715621","seconds":30}',
            False,
        )
        await self.wait_result("pair1", "pairing")
        self.service.handle_rx(
            time.monotonic(), report(opcode=1, tag="ff", device="deadbeef")
        )
        self.assertFalse(self.service.schedule)
        self.service.handle_rx(time.monotonic(), report(opcode=1, tag="ff"))
        self.assertEqual(len(self.service.schedule), 5)
        self.service.handle_rx(time.monotonic(), report())
        await self.wait_result("pair1", "confirmed")
        self.assertIsNone(self.service.pairing)
        self.assertFalse(self.service.schedule)

    async def test_expired_lease_requires_recovery(self):
        """Verify expired lease requires recovery."""
        self.service.gateway.armed = False
        from livolod.protocol import key_values

        with self.assertRaises(LinkError):
            self.service.check_status(
                key_values(await self.service.gateway.command("STATUS"))
            )

    async def test_uart_unknown_outcome_does_not_retry_rf(self):
        """Verify uart unknown outcome does not retry rf."""
        count = 0

        async def command(text):
            """Provide the scenario-specific UART response or injected failure.

            @param text Command or encoded protocol text for this exchange.
            """
            nonlocal count
            count += 1
            raise LinkError("uncertain")

        self.service.gateway.command = command
        with self.assertRaises(LinkError):
            await self.service.transmit(b"x")
        self.assertEqual(count, 1)
        self.service.gateway = Simulator()  # teardown

    async def test_radio_busy_has_bounded_retry(self):
        """Verify radio busy has bounded retry."""
        count = 0

        async def command(text):
            """Provide the scenario-specific UART response or injected failure.

            @param text Command or encoded protocol text for this exchange.
            """
            nonlocal count
            count += 1
            raise CommandError("ERR TX 2")

        self.service.gateway.command = command
        self.assertFalse(await self.service.transmit(b"x"))
        self.assertEqual(count, 4)
        self.service.gateway = Simulator()

    async def test_registry_changes_survive_restart_and_do_not_reseed_removed_devices(
        self,
    ):
        """Verify registry changes survive restart and do not reseed removed devices."""
        result = self.service.submit(
            "rename", {"request_id": "rename1", "device": "46715621", "name": "Kitchen"}
        )
        self.assertEqual(result["status"], "confirmed")
        self.assertEqual(self.service.devices["46715621"].name, "Kitchen")
        result = self.service.submit(
            "add",
            {"request_id": "add1", "device": "12345678", "name": "Hall", "outputs": 1},
        )
        self.assertEqual(result["status"], "confirmed")
        self.assertEqual(self.service.devices["12345678"].slot, 1)
        self.assertFalse(self.service.ready)
        self.assertTrue(self.service.reconfigure.is_set())
        self.service.submit("remove", {"request_id": "remove1", "device": "46715621"})
        self.assertEqual(
            {d.id for d in self.store.devices(self.config.devices)}, {"12345678"}
        )
        self.assertEqual(
            self.service.submit(
                "remove", {"request_id": "remove1", "device": "46715621"}
            )["status"],
            "confirmed",
        )
        self.assertEqual(self.service.command_result("add1")["status"], "confirmed")
        self.assertEqual(self.store.removed_devices(), ["46715621"])
        self.broker.messages.clear()
        self.service.snapshot()
        self.assertIn(("46715621/state", "", True), self.broker.messages)
        self.assertIn(("46715621/availability", "", True), self.broker.messages)

    async def test_offline_snapshot_and_registry_conflicts(self):
        """Verify offline snapshot and registry conflicts."""
        self.service.ready = False
        self.assertEqual(
            self.service.submit("snapshot", {"request_id": "snap"})["status"],
            "confirmed",
        )
        self.assertEqual(
            self.service.submit(
                "add",
                {
                    "request_id": "conflict",
                    "device": "12345678",
                    "name": "Hall",
                    "outputs": 1,
                    "slot": 0,
                },
            )["status"],
            "rejected",
        )
        self.assertEqual(self.service.command_result("conflict")["status"], "rejected")

    async def test_manager_rebuilds_table_with_fresh_session(self):
        # Run the actual reconnect supervisor, not just restore() in isolation.
        """Verify manager rebuilds table with fresh session."""
        await self.service.gateway.close()
        task = asyncio.create_task(self.service.run())

        async def wait_until(predicate):
            """Wait for the supplied test condition within the scenario deadline.

            @param predicate Callable that becomes true when the expected observation is available.
            """
            while not predicate():
                await asyncio.sleep(0.01)

        try:
            self.service.ready = False
            await asyncio.wait_for(wait_until(lambda: self.service.ready), 2)
            old = self.service.session
            self.service.submit(
                "add",
                {
                    "request_id": "new",
                    "device": "12345678",
                    "name": "Hall",
                    "outputs": 1,
                },
            )
            await asyncio.wait_for(
                wait_until(lambda: self.service.ready and self.service.session != old),
                3,
            )
            self.assertEqual(len(self.service.gateway.peers), 2)
            self.assertEqual(self.service.command_result("new")["status"], "confirmed")
        finally:
            self.service.stop.set()
            await asyncio.wait_for(task, 2)
            self.service.gateway = Simulator()

    async def test_gap_counter_does_not_count_rejected_frames_as_uart_loss(self):
        """Verify gap counter does not count rejected frames as uart loss."""
        self.service.handle_rx(time.monotonic(), report(sequence=1))
        invalid = report(sequence=2)
        invalid = invalid[:-2] + ("ff" if invalid[-2:] != "ff" else "00")
        self.service.handle_rx(time.monotonic(), invalid)
        self.service.handle_rx(time.monotonic(), report(sequence=3))
        self.assertEqual(self.service.diagnostics["rx_gaps"], 0)
        self.assertEqual(self.service.diagnostics["invalid_rx"], 1)


class FakeSerial:
    """Actual pipe readiness, with scripted firmware responses on write."""

    def __init__(self):
        """Prepare the simulated collaborator with the requested test behavior."""
        self.reader, self.writer = os.pipe()
        os.set_blocking(self.reader, False)
        self.writes = []
        self.on_write = None

    def fileno(self):
        """Expose the test transport descriptor for event-loop registration."""
        return self.reader

    @property
    def in_waiting(self):
        """Report unread bytes currently buffered by the fake serial port."""
        return 4096

    def read(self, length):
        """Consume up to the requested number of bytes from the fake serial port.

        @param length Maximum byte count to consume.
        """
        return os.read(self.reader, length)

    def write(self, value):
        """Record a serial write and trigger the configured fake response.

        @param value Payload or value supplied to the simulated collaborator.
        """
        self.writes.append(value)
        if self.on_write:
            self.on_write(value)
        return len(value)

    def emit(self, text):
        """Deliver an unsolicited UART line to the fake serial reader.

        @param text Command or encoded protocol text for this exchange.
        """
        os.write(self.writer, text.encode())

    def close(self):
        """Release resources owned by the simulated collaborator."""
        os.close(self.reader)
        os.close(self.writer)


class UartTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        """Prepare isolated state and asynchronous collaborators for the next test."""
        self.serial = FakeSerial()
        self.gateway = Gateway(self.serial, timeout=0.02)

    async def asyncTearDown(self):
        """Stop asynchronous collaborators and release temporary test state."""
        self.serial.on_write = lambda value: self.serial.emit(
            f"RSP {value.split()[0].decode()} OK DISARMED\n"
        )
        await self.gateway.close()

    async def test_lost_reply_retries_exact_request_while_receiving_events(self):
        """Verify lost reply retries exact request while receiving events."""

        def reply(value):
            """Inject the UART reply pattern required by the surrounding transport test.

            @param value Payload or value supplied to the simulated collaborator.
            """
            request_id = value.split()[0].decode()
            self.serial.emit(report() + "\nRSP 0 OK unrelated\n")
            if len(self.serial.writes) == 2:
                self.serial.emit(f"RSP {request_id} OK TX 0\n")

        self.serial.on_write = reply
        self.assertEqual(await self.gateway.command("SEND session frame"), "OK TX 0")
        self.assertEqual(self.serial.writes[0], self.serial.writes[1])
        self.assertEqual(self.gateway.events.qsize(), 2)

    async def test_timeout_poisoning_prevents_new_send(self):
        """Verify timeout poisoning prevents new send."""
        with self.assertRaises(LinkError):
            await self.gateway.command("SEND oldsession frame")
        with self.assertRaises(LinkError):
            await self.gateway.command("SEND newsession frame")
        self.assertEqual(len(self.serial.writes), 2)

    async def test_reset_during_send_prevents_retry_across_sessions(self):
        """Verify reset during send prevents retry across sessions."""
        self.serial.on_write = lambda value: self.serial.emit(
            "EC-GATEWAY 3 tx=disabled\n"
        )
        with self.assertRaises(LinkError):
            await self.gateway.command("SEND session frame")
        self.assertEqual(len(self.serial.writes), 1)

    async def test_fragmented_response_and_serialized_concurrent_requests(self):
        """Verify fragmented response and serialized concurrent requests."""

        def reply(value):
            """Inject the UART reply pattern required by the surrounding transport test.

            @param value Payload or value supplied to the simulated collaborator.
            """
            request_id = value.split()[0].decode()
            self.serial.emit("RSP " + request_id + " O")
            asyncio.get_running_loop().call_later(0.003, self.serial.emit, "K done\n")

        self.serial.on_write = reply
        self.assertEqual(
            await asyncio.gather(
                self.gateway.command("PING"), self.gateway.command("STATUS")
            ),
            ["OK done", "OK done"],
        )


if __name__ == "__main__":
    unittest.main()
