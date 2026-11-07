"""Offline commissioning policy and real service-loop reconciliation tests."""

import asyncio
from dataclasses import replace
import json
from pathlib import Path
import sqlite3
import tempfile
import time
import unittest
from unittest.mock import patch

from livolod.commissioning import Commissioning
from livolod.config import Device, load
from livolod.native_api import native_snapshot
from livolod.protocol import decode
from livolod.service import Service
from livolod.simulator import Simulator
from livolod.storage import Store
from livolod.uart import CommandError, LinkError
from test_discovery_profiles import announcement


class CommissioningTests(unittest.TestCase):
    def setUp(self):
        """Create a ready owned session with no hardware, sockets or initial peers."""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self.addCleanup(loop.close)
        self.addCleanup(asyncio.set_event_loop, None)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.config = replace(
            load(Path(__file__).parents[1] / "config.example.json"),
            devices=(),
            database=self.temp.name + "/state.db",
            mqtt={"enabled": False},
        )
        self.store = Store(self.config.database, "test")
        self.addCleanup(self.store.close)
        self.service = Service(self.config, self.store)
        self.service.ready = True
        self.service.session = "owned"
        self.service.peer_capacity = 256
        self.manager = self.service.commissioning
        self.clock = patch(
            "livolod.commissioning.time.monotonic", return_value=100.0
        ).start()
        self.addCleanup(patch.stopall)
        self.sequence = 0

    def start(self, **values):
        """Submit an explicit bounded window through the normal command ledger.

        @param values Optional request fields overriding the stable test ID.
        """
        return self.service.submit("commission", dict(request_id="start", **values))

    def receive(self, raw=None, received=None, retained=False):
        """Deliver a checksum-valid synthetic UART record through Service.handle_rx.

        @param raw Optional raw frame; defaults to the captured one-gang signature.
        @param received Optional monotonic arrival time.
        @param retained Whether to emulate an old retained query report.
        """
        raw = announcement() if raw is None else raw
        self.sequence += 1
        self.service.handle_rx(
            self.clock.return_value if received is None else received,
            f"RX {self.sequence} 0 -42 99 {len(raw)} {raw.hex()}",
            retained=retained,
        )

    def prepare(self):
        """Select one supported device, then model only the intentional reconciliation."""
        self.start()
        self.receive()
        self.clock.return_value += 1
        self.manager.tick()
        self.assertEqual(self.manager.state["status"], "registering")
        self.manager.radio_ended(expected=True)
        self.service.session = "reconciled"
        self.service.ready = True
        self.service.reconfigure.clear()
        self.manager.restored()

    def state_report(self, **changes):
        """Create a native report addressed to the configured gateway.

        @param changes Explicit packet field overrides for negative evidence tests.
        """
        fields = dict(
            body="0100010100000500", tag="a7", opcode=5, destination="2a992b12"
        )
        fields.update(changes)
        return announcement(**fields)

    def test_explicit_fresh_window_and_duplicate_request(self):
        """Cached and retained announcements cannot enroll; duplicate starts cannot extend."""
        self.receive()
        self.assertEqual(self.start()["status"], "listening")
        self.receive(received=99)
        self.receive(retained=True)
        self.manager.tick()
        self.assertIsNone(self.manager.candidate)
        self.clock.return_value += 5
        self.assertEqual(self.start()["status"], "listening")
        self.assertEqual(self.manager.deadline, 220)
        self.assertEqual(self.manager.snapshot()["seconds_remaining"], 115)

    def test_strict_request_validation_and_busy(self):
        """Reject unknown fields, malformed durations, offline radio and overlapping work."""
        for seconds in (True, 9, 121, "120", 12.5):
            self.assertEqual(self.start(seconds=seconds)["status"], "rejected")
        self.assertEqual(self.start(device="66780945")["status"], "rejected")
        self.service.ready = False
        self.assertEqual(self.start()["status"], "rejected")
        self.service.ready = True
        self.assertEqual(
            self.service.submit("commission", {"request_id": "new"})["status"],
            "listening",
        )
        self.assertEqual(
            self.service.submit("commission", {"request_id": "busy"})["status"],
            "rejected",
        )
        self.assertEqual(
            self.service.submit(
                "add", dict(request_id="add", device="12345678", name="x", outputs=1)
            )["error"],
            "commissioning_active",
        )

    def test_unsupported_then_known_same_device_is_conflict(self):
        """Unknown signatures within this window poison later contradictory known evidence."""
        self.start()
        self.receive(announcement(body="0001010534000400"))
        self.assertEqual(
            self.manager.snapshot()["observations"][0]["reason"],
            "unknown_pairing_payload",
        )
        self.receive()
        self.assertEqual(self.manager.state["status"], "conflict")
        self.assertFalse(self.service.devices)

    def test_pre_window_unknown_does_not_poison_new_evidence(self):
        """A previous window's unsupported evidence never leaks into a new session."""
        self.receive(announcement(body="0001010534000400"))
        self.start()
        self.receive()
        self.clock.return_value += 1
        self.manager.tick()
        self.assertEqual(self.manager.state["status"], "registering")

    def test_known_then_unknown_conflicts_and_multiple_devices_conflict(self):
        """Conflicting signatures and simultaneous eligible IDs cannot be auto-selected."""
        self.start()
        self.receive()
        self.receive(announcement(body="0001010534000400"))
        self.assertEqual(self.manager.state["error"], "conflicting_pairing_profiles")
        self.service.submit("commission", {"request_id": "second"})
        self.receive()
        self.receive(announcement(device="12345678"))
        self.assertEqual(self.manager.state["error"], "multiple_devices")
        self.assertEqual(len(self.manager.state["observations"]), 2)
        self.assertFalse(self.service.devices)

    def test_duplicate_announcements_do_not_extend_deadline(self):
        """Repeated requests with varying retransmit counters remain one candidate."""
        self.start()
        self.receive()
        self.clock.return_value += 0.5
        self.receive(announcement(body="0001010534012e00"))
        self.assertEqual(self.manager.deadline, 220)
        self.assertEqual(self.manager.candidate["collected"], 100)
        self.clock.return_value = 101
        self.manager.tick()
        self.assertEqual(len(self.service.devices), 1)

    def test_registry_full_preserves_profile_and_does_not_add(self):
        """Radio-reported capacity constrains slots before any durable registration."""
        self.service.peer_capacity = 0
        self.start()
        self.receive()
        self.clock.return_value += 1
        self.manager.tick()
        self.assertEqual(self.manager.state["error"], "registry_full")
        self.assertEqual(self.manager.state["outputs"], 1)
        self.assertFalse(self.manager.state["registered"])

    def test_registration_never_confirms_and_expected_restore_continues(self):
        """Only the deliberate peer reconciliation can resume this exact bounded attempt."""
        self.prepare()
        self.assertEqual(self.manager.state["status"], "pairing")
        self.assertTrue(self.manager.state["registered"])
        self.receive(self.state_report())
        self.assertEqual(self.manager.state["status"], "pairing")
        self.manager.radio_ended()
        self.assertEqual(self.manager.state["status"], "unknown")
        self.assertIn("66780945", self.service.devices)

    def test_strict_native_confirmation_and_no_retained_completion(self):
        """Wrong address/profile/body/tag, old timestamps and retained reports cannot pair."""
        self.prepare()
        self.manager.begin_transmit()
        self.manager.end_transmit(True)
        for changes in (
            dict(destination="ffffffff"),
            dict(destination="9bf4ea38"),
            dict(tag="b7"),
            dict(opcode=0xFA),
            dict(body="0000010100000500"),
            dict(body="0100030100000500"),
            dict(body="0100010200000500"),
            dict(suffix="0002"),
            dict(device="12345678"),
        ):
            self.receive(self.state_report(**changes))
            self.assertEqual(self.manager.state["status"], "pairing", changes)
        self.receive(self.state_report(), received=100)
        self.receive(self.state_report(), retained=True)
        self.assertEqual(self.manager.state["status"], "pairing")
        self.receive(self.state_report())
        self.assertEqual(self.manager.state["status"], "confirmed")
        self.assertIsNone(self.service.pairing)
        self.assertEqual(self.service.schedule, [])

    def test_report_during_uart_reply_requires_actual_success(self):
        """A buffered post-send report may confirm only the positively completed attempt."""
        self.prepare()
        self.manager.begin_transmit()
        self.receive(self.state_report())
        self.assertEqual(self.manager.state["status"], "pairing")
        self.manager.end_transmit(False)
        self.assertFalse(self.manager.buffered_report)
        self.clock.return_value += 0.1
        self.receive(self.state_report())
        self.manager.begin_transmit()
        self.manager.end_transmit(True)
        self.assertEqual(self.manager.state["status"], "pairing")
        self.receive(self.state_report())
        self.assertEqual(self.manager.state["status"], "confirmed")

    def test_cancel_fences_delayed_start_without_closing_other_session(self):
        """Cancellation is scoped, idempotent and blocks acceptance of a delayed POST."""
        self.start()
        result = self.service.submit(
            "commission_cancel", dict(request_id="cancel", session_id="lost")
        )
        self.assertEqual(result["status"], "confirmed")
        self.assertEqual(self.manager.state["status"], "listening")
        self.assertEqual(
            self.service.submit("commission", {"request_id": "lost"})["status"],
            "rejected",
        )
        result = self.service.submit(
            "commission_cancel", dict(request_id="cancel-start", session_id="start")
        )
        self.assertEqual(result["status"], "confirmed")
        self.assertEqual(self.start()["status"], "cancelled")

    def test_cancel_during_registration_preserves_device_and_cannot_resume(self):
        """Cancellation stops admission while retaining an already committed device."""
        self.start()
        self.receive()
        self.clock.return_value += 1
        self.manager.tick()
        self.service.submit(
            "commission_cancel", dict(request_id="cancel", session_id="start")
        )
        self.manager.radio_ended(expected=True)
        self.manager.restored()
        self.assertEqual(self.manager.state["status"], "cancelled")
        self.assertTrue(self.manager.state["registered"])
        self.assertIsNone(self.service.pairing)

    def test_restart_keeps_registration_and_marks_unknown_without_replay(self):
        """A new daemon restores outcome and identity but never restarts an interrupted window."""
        self.prepare()
        service = Service(self.config, self.store)
        self.assertEqual(service.commissioning.state["status"], "unknown")
        self.assertTrue(service.commissioning.state["registered"])
        self.assertIsNone(service.pairing)
        self.assertEqual(service.command_result("start")["status"], "unknown")
        self.assertEqual(
            service.submit("commission", {"request_id": "start"})["status"], "unknown"
        )

    def test_summary_and_result_transition_are_atomic(self):
        """An interrupted ledger write rolls back the corresponding summary update."""
        self.start()
        with patch.object(
            self.store, "finish", side_effect=sqlite3.OperationalError("disk")
        ):
            with self.assertRaises(sqlite3.OperationalError):
                self.manager.finish("cancelled")
        row = self.store.db.execute(
            "SELECT value FROM meta WHERE key='commissioning_session'"
        ).fetchone()
        self.assertEqual(json.loads(row[0])["status"], "listening")
        self.assertEqual(self.service.command_result("start")["status"], "listening")

    def test_expiry_is_total_deadline_and_unsupported_reason_is_visible(self):
        """No evidence extends the window and unsupported discoveries explain expiry."""
        self.start(seconds=10)
        self.receive(announcement(suffix="0003"))
        self.clock.return_value += 10
        self.manager.tick()
        self.assertEqual(self.manager.state["status"], "expired")
        self.assertEqual(self.manager.state["error"], "unsupported_device_seen")
        self.assertFalse(self.service.devices)

    def test_firmware_preflight_blocks_active_listener(self):
        """Firmware ownership checks include a listener with no target/pairing object."""
        self.start()
        with patch(
            "livolod.firmware_preflight.build_report", return_value={}
        ) as report:
            self.service.firmware_preflight()
        self.assertEqual(report.call_args.kwargs["blocked"], "radio_busy")
        self.assertTrue(native_snapshot(self.service)["commissioning_supported"])

    def test_existing_peer_slot_and_state_survive_registration(self):
        """Automatic admission allocates a free slot without replacing existing identities."""
        existing = Device("46715621", "Kitchen", 0, 2, "0002")
        self.service.devices = {existing.id: existing}
        self.service.config = replace(self.config, devices=(existing,))
        self.store.replace_devices([existing])
        self.store.save_state(existing.id, {"state_bits": 1})
        self.prepare()
        self.assertEqual(self.service.devices[existing.id], existing)
        self.assertEqual(self.service.devices["66780945"].slot, 1)
        self.assertEqual(self.store.states()[existing.id], {"state_bits": 1})

    def test_cca_backoff_report_cannot_confirm_later_successful_attempt(self):
        """Use actual transmit retries to reject evidence preceding the successful attempt."""
        self.prepare()
        attempts = []

        async def send(text):
            """Return one definite non-send before actual transmission success.

            @param text Generated UART command.
            """
            attempts.append(text)
            if len(attempts) == 1:
                raise CommandError("ERR TX 2")
            return "OK TX 0"

        async def backoff(seconds):
            """Deliver evidence between attempts without wall-clock waiting.

            @param seconds Requested retry delay.
            """
            self.clock.return_value += seconds
            self.receive(self.state_report())

        self.service.gateway = type("Gateway", (), {"command": staticmethod(send)})()
        with patch("livolod.service.asyncio.sleep", backoff):
            sent = asyncio.get_event_loop().run_until_complete(
                self.service._transmit(
                    bytes(39),
                    before_send=self.manager.begin_transmit,
                    after_send=self.manager.end_transmit,
                )
            )
        self.assertTrue(sent)
        self.assertEqual(len(attempts), 2)
        self.assertEqual(self.manager.state["status"], "pairing")
        self.receive(self.state_report())
        self.assertEqual(self.manager.state["status"], "confirmed")

    def test_generic_ok_cannot_confirm_admission(self):
        """Queue acceptance or malformed success strings never mean radio TX completion."""
        self.prepare()
        for reply in ("OK", "OK QUEUED", "OK TX 1"):

            async def send(text):
                """Deliver a plausible report followed by an invalid completion.

                @param text Generated UART command.
                """
                self.receive(self.state_report())
                return reply

            self.service.gateway = type(
                "Gateway", (), {"command": staticmethod(send)}
            )()
            with self.assertRaises(LinkError):
                asyncio.get_event_loop().run_until_complete(
                    self.service._transmit(
                        bytes(39),
                        before_send=self.manager.begin_transmit,
                        after_send=self.manager.end_transmit,
                    )
                )
            self.assertEqual(self.manager.state["status"], "pairing")

    def test_cancel_clears_future_pairing_transmissions(self):
        """A cancellation cannot undo sent RF but removes every remaining scheduled frame."""
        self.prepare()
        self.receive()
        self.assertEqual(len(self.service.schedule), 5)
        self.service.submit(
            "commission_cancel", {"request_id": "cancel", "session_id": "start"}
        )
        self.receive()
        self.assertEqual(self.service.schedule, [])
        self.assertIsNone(self.service.pairing)

    def test_registry_write_failure_closes_window_without_losing_existing_data(self):
        """A failed registry commit is reported and cannot leave an endless listener."""
        self.start()
        self.receive()
        self.clock.return_value += 1

        async def stop(seconds):
            """End the watcher after its first handled failure.

            @param seconds Requested watch interval.
            """
            self.service.stop.set()

        with (
            patch.object(
                self.store,
                "replace_devices",
                side_effect=sqlite3.OperationalError("disk"),
            ),
            patch("livolod.commissioning.asyncio.sleep", stop),
        ):
            asyncio.get_event_loop().run_until_complete(self.manager.watch())
        self.assertEqual(self.manager.state["status"], "failed")
        self.assertEqual(self.manager.state["error"], "registry_error")
        self.assertFalse(self.manager.state["registered"])
        self.assertFalse(self.store.devices(()))
        self.assertTrue(self.service.ready)

    def test_cancel_during_actual_send_prevents_remaining_transmissions(self):
        """An in-flight send may finish, but cancellation cannot become confirmation."""
        self.prepare()

        async def send(text):
            """Cancel the selected session while UART completion is outstanding.

            @param text Generated admission command.
            """
            self.service.submit(
                "commission_cancel", {"request_id": "close", "session_id": "start"}
            )
            self.receive(self.state_report())
            return "OK TX 0"

        self.service.gateway = type("Gateway", (), {"command": staticmethod(send)})()
        asyncio.get_event_loop().run_until_complete(
            self.service._transmit(
                bytes(39),
                before_send=self.manager.begin_transmit,
                after_send=self.manager.end_transmit,
            )
        )
        self.assertEqual(self.manager.state["status"], "cancelled")
        self.assertEqual(self.service.schedule, [])


class StrictPairingSimulator(Simulator):
    def __init__(self):
        """Enforce the real disarmed peer-table boundary and record admission frames."""
        super().__init__()
        self.admission = asyncio.Event()
        self.suffix = "0002"

    def current_state(self, device):
        """Emit the selected fixture's native state without treating ARM as pairing.

        @param device Simulated switch identity.
        """
        self.report(device, suffix=self.suffix, mask=1, bits=0)

    async def command(self, text, retries=1):
        """Reject hot peer mutation and emit confirmation during the admission reply.

        @param text UART command issued by the real Service orchestration.
        @param retries UART compatibility argument.
        """
        if text.startswith("PEER SET") and self.armed:
            raise CommandError("ERR ARMED")
        result = await super().command(text, retries)
        if text.startswith("SEND "):
            packet = decode(bytes.fromhex(text.split()[2]))
            if packet.opcode == 0xFA:
                self.admission.set()
                self.current_state(packet.subject)
                await asyncio.sleep(0)
        return result


class CommissioningIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_journal_failure_during_radio_loss_still_closes_adapter(self):
        """An unwritable outcome journal cannot bypass the radio's shutdown path."""
        with tempfile.TemporaryDirectory() as directory:
            config = replace(
                load(Path(__file__).parents[1] / "config.example.json"),
                database=directory + "/state.db",
                mqtt={"enabled": False},
            )
            store = Store(config.database, "test")
            radio = StrictPairingSimulator()
            service = Service(config, store, gateway_factory=lambda: radio)
            task = asyncio.create_task(service.run())
            try:

                async def ready():
                    """Wait for the isolated radio owner to accept commands."""
                    while not service.ready:
                        await asyncio.sleep(0.01)

                await asyncio.wait_for(ready(), 3)
                service.submit("commission", {"request_id": "disk-failure"})
                with patch.object(
                    store, "finish", side_effect=sqlite3.OperationalError("disk")
                ):
                    radio.fault.set_result(LinkError("simulated unplug"))
                    await asyncio.wait_for(task, 3)
                self.assertFalse(radio.armed)
                self.assertTrue(service.stop.is_set())
            finally:
                service.stop.set()
                await asyncio.gather(task, return_exceptions=True)
                store.close()

    async def test_watcher_failure_is_supervised_and_stops_radio_task(self):
        """Unexpected watcher failure propagates and tears down the parallel radio owner."""
        with tempfile.TemporaryDirectory() as directory:
            config = replace(
                load(Path(__file__).parents[1] / "config.example.json"),
                database=directory + "/state.db",
            )
            store = Store(config.database, "test")
            service = Service(config, store)
            stopped = asyncio.Event()

            async def radio():
                """Stand in for the long-lived radio task and prove cancellation."""
                try:
                    await asyncio.Event().wait()
                finally:
                    stopped.set()

            async def broken():
                """Model an unexpected watcher exception after both tasks start."""
                await asyncio.sleep(0)
                raise RuntimeError("watcher failure")

            try:
                with (
                    patch.object(service, "_run_radio", radio),
                    patch.object(service.commissioning, "watch", broken),
                ):
                    with self.assertRaisesRegex(RuntimeError, "watcher failure"):
                        await service.run()
                self.assertTrue(stopped.is_set())
                self.assertTrue(service.stop.is_set())
            finally:
                store.close()

    async def test_real_service_reconciles_and_confirms_without_output_actuation(self):
        """Run complete offline lifecycle through actual RX/maintenance/restore tasks."""
        await self.lifecycle("0002", "0002030534010400")

    async def test_real_service_one_gang_lifecycle(self):
        """The one-gang captured profile completes the same real service lifecycle."""
        await self.lifecycle("0001", "0001010534010400")

    async def lifecycle(self, suffix, body):
        """Exercise one exact profile from discovery through disarmed peer setup and RX.

        @param suffix Captured device profile suffix.
        @param body Captured native pairing announcement payload.
        """
        with tempfile.TemporaryDirectory() as directory:
            config = replace(
                load(Path(__file__).parents[1] / "config.example.json"),
                database=directory + "/state.db",
                devices=(),
                mqtt={"enabled": False},
            )
            store = Store(config.database, "test")
            radios = []

            def factory():
                """Provide a fresh strict adapter after the planned disconnect."""
                radio = StrictPairingSimulator()
                radio.suffix = suffix
                radios.append(radio)
                return radio

            service = Service(config, store, gateway_factory=factory, simulated=True)
            task = asyncio.create_task(service.run())

            async def until(predicate):
                """Bound test polling independently of the commissioning deadline.

                @param predicate State condition expected within the test timeout.
                """

                async def poll():
                    """Yield until the expected owner-loop transition is visible."""
                    while not predicate():
                        await asyncio.sleep(0.01)

                await asyncio.wait_for(poll(), 5)

            try:
                await until(lambda: service.ready)
                self.assertEqual(
                    service.submit("commission", {"request_id": "whole"})["status"],
                    "listening",
                )
                raw = announcement(suffix=suffix, body=body)
                radios[-1].events.put_nowait(
                    (time.monotonic(), f"RX 1 0 -42 99 {len(raw)} {raw.hex()}")
                )
                await until(lambda: len(radios) == 2 and service.ready)
                self.assertEqual(service.commissioning.state["status"], "pairing")
                # ARM emits a same-tag report, which must not confirm before FA.
                await asyncio.sleep(0.1)
                self.assertEqual(service.commissioning.state["status"], "pairing")
                radios[-1].events.put_nowait(
                    (time.monotonic(), f"RX 2 0 -42 99 {len(raw)} {raw.hex()}")
                )
                await until(
                    lambda: service.commissioning.state["status"] == "confirmed"
                )
                self.assertTrue(radios[-1].admission.is_set())
                self.assertEqual(len(store.devices(())), 1)
                for radio in radios:
                    for text in radio.commands:
                        if text.startswith("SEND "):
                            self.assertNotEqual(
                                decode(bytes.fromhex(text.split()[2])).opcode, 2
                            )
            finally:
                service.stop.set()
                await asyncio.wait_for(task, 3)
                store.close()
