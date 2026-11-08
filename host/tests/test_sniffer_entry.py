"""Passive sniffer proof and one-shot remote preparation without radio hardware."""

import asyncio
import binascii
from copy import deepcopy
from dataclasses import replace
import json
import struct
import unittest
from unittest.mock import Mock, patch

from livolod.bootstrap_controller import (
    BootstrapController,
    BootstrapError,
    ENTRY_RECOVERY,
)
from livolod.bootstrap_store import BootstrapStore
from livolod.firmware_bundle import BundleError
from livolod.sniffer_identity import (
    ENTRY_METHOD,
    HEADER,
    MARKER,
    MAX_BYTES,
    SnifferIdentity,
)
import test_sonoff_bootstrap as sonoff_fixture
import test_bootstrap_worker as worker_fixture


def record(
    sequence=1,
    timestamp=1000000,
    *,
    kind=2,
    flags=1,
    version=(0, 12),
    baud=921600,
    marker=MARKER,
    body=None,
):
    """Encode synthetic CRC-valid traffic independently of the production parser.

    @param sequence Wrapping record counter.
    @param timestamp Extended firmware microseconds.
    @param kind Status or interleaved RX record.
    @param flags Exact transport flags.
    @param version Synthetic firmware version pair.
    @param baud Reported serial rate.
    @param marker Exact status identity bytes.
    @param body Optional malformed or custom payload.
    """
    if body is None:
        body = struct.pack("<HH8I", *version, baud, *([0] * 7)) + marker
    raw = HEADER.pack(b"ESN1", len(body), kind, flags, sequence, timestamp) + body
    return raw + struct.pack("<I", binascii.crc32(raw))


class DecoderTests(unittest.TestCase):
    def test_fragmented_quiet_statuses_and_sequence_wrap(self):
        """Two quiet statuses suffice; initial fragments, forward gaps and wrap are valid."""
        decoder = SnifferIdentity()
        data = b"partial initial suffix" + record(0xFFFFFFFE) + record(2, 2000000)
        ready = False
        for offset in range(0, len(data), 7):
            ready = decoder.feed(data[offset : offset + 7])
        self.assertTrue(ready)
        self.assertEqual(decoder.statuses, 2)
        self.assertLess(len(decoder.buffer), HEADER.size)

    def test_rx_frames_do_not_substitute_for_status(self):
        """RF traffic may be interleaved, but cannot prove firmware identity alone."""
        decoder = SnifferIdentity()
        rx = record(2, 1100000, kind=1, body=b"\xd0\x80\x0b\x00a")
        self.assertFalse(decoder.feed(record() + rx))
        self.assertTrue(decoder.feed(record(3, 2000000)))

    def test_corrupt_wrong_or_replayed_evidence_rejects(self):
        """CRC, identity, version, baud, framing and monotonicity all gate entry."""
        corrupt = bytearray(record(2, 2000000))
        corrupt[-1] ^= 1
        cases = (
            bytes(corrupt),
            record(2, 2000000, version=(0, 13)),
            record(2, 2000000, baud=460800),
            record(2, 2000000, marker=b"other\0"),
            record(2, 2000000, marker=MARKER + b"\0"),
            record(2, 2000000, flags=0),
            record(2, 2000000, body=b"short"),
            record(),
            record(0, 2000000),
            record(2, 999999),
            b"garbage" * 100,
        )
        for data in cases:
            with self.subTest(data=data[:24]), self.assertRaises(BundleError):
                decoder = SnifferIdentity()
                decoder.feed(record())
                decoder.feed(data)

    def test_noise_and_byte_budgets_fail_closed(self):
        """Marker text and unlimited garbage cannot grow an unbounded observation."""
        for data in (MARKER * 40, b"x" * 516, b"x" * (MAX_BYTES + 1)):
            with self.subTest(size=len(data)), self.assertRaises(BundleError):
                SnifferIdentity().feed(data)


class EntryProviderTests(unittest.TestCase):
    def setUp(self):
        """Retain real provider ownership checks with a deferred fake serial stream."""
        sonoff_fixture.SonoffProviderTests.setUp(self)
        self.serial.pending.extend(record() + record(2, 2000000))
        self.fresh = record(10, 10000000) + record(11, 11000000)
        self.resets = []
        self.write_events = []
        self.read_once = False
        self.delayed = b""
        self.release_at = 0
        original_read = self.serial.read
        original_write = self.serial.write

        def reset():
            """Discard input bytes while retaining the descriptor and profile line policy."""
            self.resets.append(self.serial.baudrate)
            self.serial.pending.clear()

        def read(size):
            """Emit the independent fresh stream only after the initial buffer reset.

            @param size Requested read bound.
            """
            if self.serial.baudrate == 921600 and not self.read_once:
                self.read_once = True
                self.serial.pending.extend(self.fresh[: len(record())])
                self.delayed = self.fresh[len(record()) :]
                self.release_at = self.clock.now + 1
            if self.serial.baudrate == 921600 and self.clock.now >= self.release_at:
                self.serial.pending.extend(self.delayed)
                self.delayed = b""
            return original_read(size)

        def write(data):
            """Model reset without acknowledging or echoing the maintenance command.

            @param data Exact bytes submitted by the provider.
            """
            self.write_events.append((data, self.clock.now, self.serial.baudrate))
            if data == b"\nBOOTLOADER\n":
                self.serial.writes.append(data)
                return len(data)
            return original_write(data)

        self.serial.reset_input_buffer = reset
        self.serial.read = read
        self.serial.write = write
        self.serial.baudrate = 921600
        self.before = Mock()
        self.progress = Mock()

    def entry(self):
        """Invoke the reviewed conditional entry action with injected durable permission."""
        return self.provider.prepare_from_sniffer(self.before, self.progress)

    def test_exact_write_after_fresh_proof_and_ack_then_same_fd_menu(self):
        """Entry and observation use one owned descriptor with exactly the two fixed writes."""
        self.before.side_effect = lambda: self.assertEqual(self.serial.writes, [])
        result = self.entry()
        self.assertEqual(result["version"], "2.4.2")
        self.before.assert_called_once_with()
        self.assertEqual(self.serial.writes, [b"\nBOOTLOADER\n", b"\n"])
        self.assertEqual(self.resets, [921600, 115200])
        self.assertEqual(len(self.serial.events), 1)
        self.assertEqual(self.serial.events[0][1:3], (True, True))
        self.assertTrue(self.serial.closed)
        self.assertEqual(self.write_events[0][2], 921600)
        self.assertEqual(self.write_events[1][2], 115200)
        self.assertGreaterEqual(self.write_events[1][1] - self.write_events[0][1], 1.1)
        self.assertEqual(
            [call.args[0] for call in self.progress.call_args_list],
            ["identifying_application", "verifying_loader"],
        )

    def test_stale_buffer_and_bad_identity_cannot_authorize_write(self):
        """Already queued statuses are discarded; absent or wrong fresh traffic stays read-only."""
        for fresh in (
            b"",
            record(),
            record(version=(0, 13)),
            b"Gecko Bootloader v2.4.2",
            MARKER,
        ):
            self.fresh, self.read_once = fresh, False
            self.serial.pending.extend(record() + record(2, 2000000))
            with self.subTest(fresh=fresh[:16]), self.assertRaises(BundleError):
                self.entry()
            self.before.assert_not_called()
            self.assertEqual(self.serial.writes, [])
            self.assertTrue(self.serial.closed)

    def test_durable_permission_failure_or_replacement_never_writes(self):
        """A failed ACK or changed serial after ACK cannot reach the maintenance write."""
        self.before.side_effect = OSError("fixture journal unavailable")
        with self.assertRaises(BundleError):
            self.entry()
        self.assertEqual(self.serial.writes, [])
        self.read_once = False
        self.before.side_effect = lambda: setattr(
            self.inventory, "return_value", (replace(self.target, serial="other"),)
        )
        with self.assertRaises(BundleError):
            self.entry()
        self.assertEqual(self.serial.writes, [])

    def test_partial_entry_write_is_not_retried_or_followed_by_menu(self):
        """An uncertain short maintenance write stops without another command."""
        self.serial.write = Mock(return_value=3)
        with self.assertRaises(BundleError):
            self.entry()
        self.serial.write.assert_called_once_with(b"\nBOOTLOADER\n")
        self.assertTrue(self.serial.closed)

    def test_menu_mismatch_never_selects_run_or_upload(self):
        """An entered but unexpected loader cannot turn inspection into flash permission."""
        self.serial.data = self.serial.data.replace(b"2.04.02", b"2.04.03")
        with self.assertRaises(BundleError):
            self.entry()
        self.assertEqual(self.serial.writes, [b"\nBOOTLOADER\n", b"\n"])

    def test_immediate_buffered_burst_is_not_current_application_proof(self):
        """Two old USB-buffered statuses cannot qualify without later fresh traffic."""
        pending = [record() + record(2, 2000000)]

        def read(size):
            """Return one immediate burst and then a silent endpoint.

            @param size Provider byte bound, larger than this fixture burst.
            """
            self.clock.now += 0.05
            return pending.pop(0) if pending else b""

        self.serial.read = read
        with self.assertRaises(BundleError):
            self.entry()
        self.before.assert_not_called()
        self.assertEqual(self.serial.writes, [])

    def test_input_clear_and_baud_change_failures_do_not_fall_back(self):
        """Driver failures before and after entry never become reset or retry sequences."""
        original_reset = self.serial.reset_input_buffer
        self.serial.reset_input_buffer = Mock(side_effect=OSError("input reset failed"))
        with self.assertRaises(BundleError):
            self.entry()
        self.before.assert_not_called()
        self.assertEqual(self.serial.writes, [])
        self.serial.reset_input_buffer = original_reset

        def get_baud(port):
            """Return the fixed current speed of this deferred fake serial object.

            @param port Fixture serial object.
            """
            return port.__dict__["baudrate"]

        def set_baud(port, value):
            """Refuse the loader-speed transition after the one authorized entry write.

            @param port Fixture serial object.
            @param value Requested serial speed.
            """
            raise OSError("baud transition failed")

        with patch.object(
            type(self.serial), "baudrate", property(get_baud, set_baud), create=True
        ):
            with self.assertRaises(BundleError):
                self.entry()
        self.assertEqual(self.serial.writes, [b"\nBOOTLOADER\n"])
        self.assertTrue(self.serial.closed)


class EntryControllerTests(unittest.IsolatedAsyncioTestCase):
    submit = sonoff_fixture.SonoffControllerTests.submit
    prepare = sonoff_fixture.SonoffControllerTests.prepare
    asyncTearDown = sonoff_fixture.SonoffControllerTests.asyncTearDown

    async def asyncSetUp(self):
        """Use actual controller/journal transactions with an injected entry boundary."""
        await sonoff_fixture.SonoffControllerTests.asyncSetUp(self)
        self.worker.poisoned = False
        self.runtime.quiesced = True
        self.fail_after_entry = False
        self.fail_before_entry = False

    async def work(self, kind, job, candidate, binding, checkpoint):
        """Emulate the mandatory passive-before-write boundary and possible interruption.

        @param kind Admitted operation kind.
        @param job Durable operation binding.
        @param candidate Validated fixture image.
        @param binding Private previous target observation.
        @param checkpoint Real controller durable acknowledgment.
        """
        if kind == "prepare" and job.get("entry_method") == ENTRY_METHOD:
            await checkpoint("identifying_application")
            if self.fail_before_entry:
                raise BundleError("application_unrecognized", "fixture")
            await checkpoint("bootloader_entry")
            self.assertTrue(self.store.lookup(job["request_id"])[1]["entry_attempted"])
            await checkpoint("verifying_loader")
            if self.fail_after_entry:
                raise BundleError("inspection_timeout", "fixture")
        return await sonoff_fixture.SonoffControllerTests.work(
            self, kind, job, candidate, binding, checkpoint
        )

    async def remote(self, request_id="remote"):
        """Submit explicitly confirmed conditional remote preparation.

        @param request_id Durable idempotency identity.
        """
        return await self.submit(
            "prepare",
            request_id,
            target_id=self.controller.target_id,
            candidate_id=self.controller.candidate["id"],
            dedicated=True,
            board_confirmed=True,
            entry_method=ENTRY_METHOD,
            entry_confirmed=True,
        )

    async def test_remote_prepare_has_no_install_permission_and_is_idempotent(self):
        """A completed remote inspection makes a normal plan, never flashes implicitly."""
        result, payload, _ = await self.remote()
        self.assertEqual(result["stage"], "prepared")
        self.assertTrue(result["entry_attempted"])
        self.assertFalse(result["application_verified"])
        self.assertFalse(self.store.meta("installation_attempted", False))
        self.assertEqual(await self.controller.submit("prepare", payload), result)
        self.assertEqual(self.calls, ["prepare"])

    async def test_optional_fields_require_exact_method_and_separate_consent(self):
        """Legacy omission works, but partial or spoofed remote consent is refused."""
        _, payload, _ = await self.remote()
        for change in (
            {"entry_confirmed": False},
            {"entry_method": "automatic"},
            {"entry_confirmed": 1},
        ):
            with self.subTest(change=change), self.assertRaises(BootstrapError):
                await self.controller.submit(
                    "prepare", dict(payload, request_id="bad", **change)
                )
        for missing in ("entry_method", "entry_confirmed"):
            incomplete = dict(payload, request_id="missing")
            del incomplete[missing]
            with self.assertRaises(BootstrapError):
                await self.controller.submit("prepare", incomplete)
        manual = await self.prepare("manual")
        self.assertEqual(manual["status"], "confirmed")

    async def test_clean_refusal_has_no_entry_fence_or_plan(self):
        """Unsupported sniffer evidence fails before entry and leaves manual choice explicit."""
        self.fail_before_entry = True
        result, _, _ = await self.remote()
        self.assertEqual(result["status"], "rejected")
        self.assertFalse(result["entry_attempted"])
        self.assertIsNone(result["plan"])
        self.assertFalse(self.store.unresolved())

    async def test_uncertain_entry_reinspection_preserves_original_result(self):
        """Only explicit manual loader inspection reconciles entry uncertainty atomically."""
        old = await self.prepare("old")
        self.fail_after_entry = True
        result, payload, _ = await self.remote()
        original = deepcopy(result)
        self.assertEqual(result["status"], "unknown")
        self.assertEqual(result["recovery"], ENTRY_RECOVERY)
        self.assertIsNone(result["plan"])
        self.assertTrue(self.controller.info()["manual_reinspection_supported"])
        self.assertEqual(
            self.controller.targets()["targets"][0]["entry_methods"], ["manual"]
        )
        self.assertEqual(await self.controller.submit("prepare", payload), original)
        repeated, _, _ = await self.remote("repeat")
        self.assertEqual(repeated["status"], "rejected")
        fresh = await self.prepare("reinspect")
        self.assertEqual(fresh["status"], "confirmed")
        self.assertFalse(self.store.unresolved())
        self.assertEqual(self.controller.result("remote"), original)
        self.assertEqual(await self.controller.submit("prepare", payload), original)
        stale, _, _ = await self.submit(
            "install", "stale", plan_id=old["plan_id"], confirmed=True
        )
        self.assertEqual(stale["status"], "rejected")

    async def test_reinspection_cannot_clear_flash_recovery_or_poisoned_cleanup(self):
        """A possible image write or surviving worker remains stronger than entry recovery."""
        self.fail_after_entry = True
        await self.remote()
        self.worker.poisoned = True
        self.assertFalse(self.controller.info()["manual_reinspection_supported"])
        self.worker.poisoned = False
        self.store.set_meta("installation_attempted", True)
        self.assertFalse(self.controller.info()["manual_reinspection_supported"])
        rejected = await self.prepare("unsafe-reinspect")
        self.assertEqual(rejected["status"], "rejected")
        self.assertTrue(self.store.unresolved())

    async def test_failed_manual_reinspection_preserves_original_entry_fence(self):
        """A failed read-only retry has its own truthful receipt and cannot hide prior uncertainty."""
        self.fail_after_entry = True
        original, _, _ = await self.remote()
        self.worker.run.side_effect = BundleError("inspection_timeout", "fixture")
        failed = await self.prepare("failed-manual")
        self.assertEqual(failed["status"], "rejected")
        self.assertFalse(failed["entry_attempted"])
        self.assertIsNone(failed["recovery"])
        self.assertEqual(self.controller.result("remote"), original)
        self.assertTrue(self.controller.info()["manual_reinspection_supported"])
        self.assertEqual(self.controller.info()["blocked_reason"], ENTRY_RECOVERY)
        self.assertEqual(self.controller.info()["active_request_id"], "remote")

    async def test_restart_after_entry_does_not_replay(self):
        """Durable entry intent fences restart even if the last child receipt was lost."""
        result, _, _ = await self.remote()
        result.update(status="pending", stage="bootloader_entry")
        self.store.save(result)
        await self.controller.close()
        self.store.close()
        self.store = BootstrapStore(self.root / "bootstrap.db")
        self.controller = BootstrapController(
            self.store, self.settings, self.worker, self.runtime
        )
        await self.controller.resume_startup()
        interrupted = self.controller.result("remote")
        self.assertEqual(interrupted["recovery"], ENTRY_RECOVERY)
        self.assertEqual(interrupted["status"], "unknown")
        self.assertEqual(self.calls, ["prepare"])
        self.assertTrue(self.controller.info()["manual_reinspection_supported"])
        self.runtime.start_gateway.assert_not_awaited()

    async def test_restart_before_entry_does_not_invent_mutation(self):
        """An interrupted passive-only attempt is unknown but has no entry recovery fence."""
        result, _, _ = await self.remote()
        result.update(
            status="pending",
            stage="identifying_application",
            entry_attempted=False,
            plan=None,
        )
        self.store.save(result)
        await self.controller.close()
        self.store.close()
        self.store = BootstrapStore(self.root / "bootstrap.db")
        self.controller = BootstrapController(
            self.store, self.settings, self.worker, self.runtime
        )
        await self.controller.resume_startup()
        interrupted = self.controller.result("remote")
        self.assertEqual(interrupted["status"], "unknown")
        self.assertIsNone(interrupted["recovery"])
        self.assertFalse(self.store.unresolved())
        self.assertEqual(self.calls, ["prepare"])

    async def test_cancellation_before_and_after_durable_entry_is_distinguished(self):
        """Shutdown fences only attempts that reached durable entry permission."""
        for after in (False, True):
            reached = asyncio.Event()

            async def blocked(kind, job, candidate, binding, checkpoint):
                """Hold the injected worker on a chosen side of possible serial mutation.

                @param kind Admitted operation.
                @param job Durable request.
                @param candidate Synthetic artifact.
                @param binding Inspected target.
                @param checkpoint Actual journal callback.
                """
                await checkpoint(
                    "bootloader_entry" if after else "identifying_application"
                )
                reached.set()
                await asyncio.Event().wait()

            self.worker.run.side_effect = blocked
            request_id = "cancel-after" if after else "cancel-before"
            body = dict(
                request_id=request_id,
                instance_id=self.store.instance_id,
                target_id=self.controller.target_id,
                candidate_id=self.controller.candidate["id"],
                dedicated=True,
                board_confirmed=True,
                entry_method=ENTRY_METHOD,
                entry_confirmed=True,
            )
            await self.controller.submit("prepare", body)
            await reached.wait()
            task = next(iter(self.controller.tasks))
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            result = self.controller.result(request_id)
            self.assertEqual(result["entry_attempted"], after)
            self.assertEqual(result["recovery"], ENTRY_RECOVERY if after else None)
            self.assertIsNone(result["plan"])

    async def test_remote_entry_cannot_reinterpret_a_saved_gateway_baseline(self):
        """Sniffer entry is not a remote updater for an existing gateway application."""
        self.store.set_meta("baseline", {"target": {}, "version": "0.14"})
        self.assertEqual(
            self.controller.targets()["targets"][0]["entry_methods"], ["manual"]
        )
        result, _, _ = await self.remote()
        self.assertEqual(result["error"], "entry_baseline_conflict")
        self.worker.run.assert_not_awaited()


class EntryWorkerTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = worker_fixture.WorkerTests.asyncSetUp
    asyncTearDown = worker_fixture.WorkerTests.asyncTearDown
    spawn = worker_fixture.WorkerTests.spawn

    async def run_entry(self):
        """Exercise actual child staging, durable IPC and process cleanup for remote Prepare."""
        with patch(
            "livolod.bootstrap_worker.asyncio.create_subprocess_exec",
            side_effect=self.spawn,
        ):
            return await self.worker.run(
                "prepare",
                dict(request_id="job", kind="prepare", entry_method=ENTRY_METHOD),
                None,
                None,
                self.checkpoint,
            )

    async def test_entry_checkpoint_ack_and_no_implicit_flash(self):
        """The actual child must receive the matching durable entry ACK before proceeding."""
        self.script = """import json,sys
print(json.dumps(dict(job_id='job',sequence=1,stage='identifying_application',critical=False)),flush=True)
print(json.dumps(dict(job_id='job',sequence=2,stage='bootloader_entry',critical=True)),flush=True)
assert json.loads(sys.stdin.readline()) == dict(ack=2,job_id='job')
print(json.dumps(dict(job_id='job',sequence=3,stage='verifying_loader',critical=False)),flush=True)
print(json.dumps(dict(job_id='job',sequence=4,stage='completed',critical=False,result={'fixture':True})),flush=True)
"""
        self.assertEqual(await self.run_entry(), {"fixture": True})
        self.assertEqual(
            [c.args[0] for c in self.checkpoint.await_args_list],
            ["identifying_application", "bootloader_entry", "verifying_loader"],
        )

    async def test_missing_entry_ack_rejects_even_success_exit(self):
        """A worker cannot fabricate remote success without the parent's entry permission."""
        self.script = "import json; print(json.dumps(dict(job_id='job',sequence=1,stage='completed',critical=False,result={'fixture':True})),flush=True)"
        with self.assertRaises(BundleError):
            await self.run_entry()
        self.checkpoint.assert_not_awaited()

    async def test_cross_operation_mutation_events_never_receive_ack(self):
        """Only remote Prepare can request entry; it cannot request flash or run permission."""
        for kind, method, stage in (
            ("prepare", "manual", "bootloader_entry"),
            ("install", "manual", "bootloader_entry"),
            ("cancel", "manual", "bootloader_entry"),
            ("prepare", ENTRY_METHOD, "flash"),
            ("prepare", ENTRY_METHOD, "run_intact"),
        ):
            self.checkpoint.reset_mock()
            event = dict(job_id="job", sequence=1, stage=stage, critical=True)
            self.script = (
                "import json; print(" + repr(json.dumps(event)) + ",flush=True)"
            )
            with patch(
                "livolod.bootstrap_worker.asyncio.create_subprocess_exec",
                side_effect=self.spawn,
            ):
                with (
                    self.subTest(kind=kind, stage=stage),
                    self.assertRaises(BundleError),
                ):
                    await self.worker.run(
                        kind,
                        dict(request_id="job", kind=kind, entry_method=method),
                        None,
                        None,
                        self.checkpoint,
                    )
            self.checkpoint.assert_not_awaited()

    async def test_parent_cancellation_before_and_after_entry_drains_child(self):
        """Cancellation on either side of entry ACK reaps the owned process before returning."""
        for after in (False, True):
            reached = asyncio.Event()

            async def checkpoint(stage):
                """Mark the test boundary before or after durable entry permission.

                @param stage Observed child checkpoint.
                """
                if stage == (
                    "verifying_loader" if after else "identifying_application"
                ):
                    reached.set()
                    if not after:
                        await asyncio.Event().wait()

            self.checkpoint.side_effect = checkpoint
            self.script = """import json,sys,time
print(json.dumps(dict(job_id='job',sequence=1,stage='identifying_application',critical=False)),flush=True)
print(json.dumps(dict(job_id='job',sequence=2,stage='bootloader_entry',critical=True)),flush=True)
assert json.loads(sys.stdin.readline()) == dict(ack=2,job_id='job')
print(json.dumps(dict(job_id='job',sequence=3,stage='verifying_loader',critical=False)),flush=True)
time.sleep(60)
"""
            task = asyncio.create_task(self.run_entry())
            await asyncio.wait_for(reached.wait(), 2)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            self.assertIsNone(self.worker.child)
            self.assertFalse(self.worker.poisoned)
