"""Exercise actual child IPC/termination and inherited guards without any physical I/O."""

import asyncio
import fcntl
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from livolod.bootstrap_worker import BootstrapWorker, target_json
from livolod.firmware_bundle import BundleError
from livolod.preserved_gecko import GeckoTarget


class WorkerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        """Use real local file guards but replace only the child physical implementation."""
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.fd = os.open(self.root / "physical.lock", os.O_RDWR | os.O_CREAT, 0o600)
        fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        self.target = GeckoTarget(
            "/dev/test-only",
            "devices/fixture/usb1/1-2",
            "generation",
            (188, 0),
            "00",
            "1.9.2",
        )
        self.settings = dict(
            database=str(self.root / "state.db"),
            lease_directory=str(self.root),
            loader_policy=dict(target=target_json(self.target), loader={}),
        )
        self.ownership = Mock()
        self.ownership.acquire.return_value = (
            self.target,
            SimpleNamespace(fileno=lambda: self.fd),
        )
        self.runtime = SimpleNamespace(quiesced=True)
        with patch("livolod.bootstrap_worker.backend_status", return_value=None):
            self.worker = BootstrapWorker(
                self.settings, self.ownership, self.runtime, timeout=0.5
            )
        self.real_spawn = asyncio.create_subprocess_exec
        self.script = ""
        self.pids = []
        self.checkpoint = AsyncMock()

    async def asyncTearDown(self):
        """Prove production supervision finished before releasing local guard files."""
        self.assertIsNone(self.worker.child)
        os.close(self.fd)
        self.temp.cleanup()

    async def spawn(self, *args, **kwargs):
        """Substitute a fixed test child while preserving production pipe/group/fd options.

        @param args Production Python worker argv, inspected to locate its private spec.
        @param kwargs Production subprocess group, pipes, limits and inherited guards.
        """
        spec = json.loads(Path(args[-1]).read_text())
        self.assertEqual(set(kwargs["pass_fds"]), {self.fd, *spec["guards"]})
        self.assertTrue(kwargs["start_new_session"])
        process = await self.real_spawn(
            sys.executable, "-c", self.script, args[-1], **kwargs
        )
        self.pids.append(process.pid)
        return process

    async def run_worker(self, kind="prepare"):
        """Run production staging, guards, IPC, timeout and cleanup against the fake child.

        @param kind Parent operation kind used to validate critical event boundaries.
        """
        with patch(
            "livolod.bootstrap_worker.asyncio.create_subprocess_exec",
            side_effect=self.spawn,
        ):
            return await self.worker.run(
                kind, dict(request_id="job", kind=kind), None, None, self.checkpoint
            )

    async def test_child_waits_for_durable_phase_ack_and_inherits_all_guards(self):
        """The child cannot cross the simulated mutation boundary without matching ACK."""
        self.script = """import json,sys,os
spec=json.load(open(sys.argv[1]))
for fd in [spec['lease_fd'],*spec['guards']]: os.fstat(fd)
print(json.dumps(dict(job_id='job',sequence=1,stage='flash',critical=True)),flush=True)
ack=json.loads(sys.stdin.readline())
assert ack==dict(ack=1,job_id='job')
print(json.dumps(dict(job_id='job',sequence=2,stage='completed',critical=False,result={'fixture':True})),flush=True)
"""
        self.assertEqual(await self.run_worker("install"), dict(fixture=True))
        self.checkpoint.assert_awaited_once_with("flash")
        for pid in self.pids:
            with self.assertRaises(ProcessLookupError):
                os.kill(pid, 0)

    async def test_wrong_job_cannot_receive_mutation_ack(self):
        """Cross-job events fail before granting any mutation permission."""
        self.script = "import json; print(json.dumps(dict(job_id='other',sequence=1,stage='flash',critical=True)),flush=True)"
        with self.assertRaises(BundleError):
            await self.run_worker("install")
        self.checkpoint.assert_not_awaited()

    async def test_timeout_reaps_child_and_releases_deployment_guards(self):
        """A wedged child is terminated and cannot race the later normal daemon."""
        self.script = "import time; time.sleep(60)"
        with self.assertRaises(asyncio.TimeoutError):
            await self.run_worker()
        fd = os.open(self.settings["database"] + ".lock", os.O_RDWR)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        finally:
            os.close(fd)
        for pid in self.pids:
            with self.assertRaises(ProcessLookupError):
                os.kill(pid, 0)

    async def test_exit_zero_without_receipt_is_failure(self):
        """Process exit alone never means firmware application or network verification."""
        self.script = "pass"
        with self.assertRaises(BundleError):
            await self.run_worker()

    async def test_existing_owner_blocks_before_child_launch(self):
        """A real competing deployment lock prevents even a fixture worker from starting."""
        fd = os.open(self.settings["database"] + ".lock", os.O_RDWR | os.O_CREAT, 0o600)
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            with self.assertRaises(BlockingIOError):
                await self.run_worker()
        finally:
            os.close(fd)
        self.assertEqual(self.pids, [])

    async def test_checkpoint_failure_never_acknowledges_mutation(self):
        """A journal failure closes and reaps the child while it waits for permission."""
        self.script = """import json,sys
print(json.dumps(dict(job_id='job',sequence=1,stage='flash',critical=True)),flush=True)
assert sys.stdin.readline() == '', 'mutation must not be acknowledged'
"""
        self.checkpoint.side_effect = OSError("fixture journal failure")
        with self.assertRaises(OSError):
            await self.run_worker("install")
        self.checkpoint.assert_awaited_once_with("flash")

    async def test_cancellation_reaps_blocked_child(self):
        """Cancelling the parent operation still supervises its blocked subprocess."""
        self.script = "import time; time.sleep(60)"
        task = asyncio.create_task(self.run_worker())
        for _ in range(100):
            if self.pids:
                break
            await asyncio.sleep(0.01)
        self.assertTrue(self.pids)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        for pid in self.pids:
            with self.assertRaises(ProcessLookupError):
                os.kill(pid, 0)

    async def test_surviving_helper_group_is_reaped_before_result(self):
        """A worker exit cannot hide a helper that still holds inherited device guards."""
        self.script = """import json,sys,subprocess
spec=json.load(open(sys.argv[1]))
child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)'],
  stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
  pass_fds=(spec['lease_fd'],*spec['guards']))
print(json.dumps(dict(job_id='job',sequence=1,stage='completed',critical=False,result={'helper':child.pid})),flush=True)
"""
        result = await self.run_worker()
        with self.assertRaises(ProcessLookupError):
            os.kill(result["helper"], 0)

    async def test_repeated_critical_phase_is_not_acknowledged_twice(self):
        """Only one mutation boundary exists in a fixed install operation."""
        self.script = """import json,sys
for sequence in (1,2):
 print(json.dumps(dict(job_id='job',sequence=sequence,stage='flash',critical=True)),flush=True)
 sys.stdin.readline()
"""
        with self.assertRaises(BundleError):
            await self.run_worker("install")
        self.checkpoint.assert_awaited_once_with("flash")

    async def test_out_of_order_and_oversized_events_fail_without_ack(self):
        """Malformed IPC cannot become durable permission or a success receipt."""
        for script in (
            "import json; print(json.dumps(dict(job_id='job',sequence=2,stage='flash',critical=True)),flush=True)",
            "print('x'*5000,flush=True)",
        ):
            self.script = script
            with self.assertRaises((BundleError, ValueError)):
                await self.run_worker("install")
        self.checkpoint.assert_not_awaited()

    async def test_receipt_without_required_boundary_cannot_claim_success(self):
        """An apparently valid success receipt must follow durable mutation permission."""
        self.script = "import json; print(json.dumps(dict(job_id='job',sequence=1,stage='completed',critical=False,result={'application_verified':True})),flush=True)"
        for kind in ("install", "cancel"):
            with self.assertRaises(BundleError):
                await self.run_worker(kind)
        self.checkpoint.assert_not_awaited()

    async def test_signal_permission_error_requires_proven_group_disappearance(self):
        """A failed signal is not success; only reap plus absent group permits cleanup."""
        child = SimpleNamespace(pid=987654, wait=AsyncMock(return_value=0))
        self.worker.child = child
        with patch(
            "livolod.bootstrap_worker.os.killpg",
            side_effect=[PermissionError(), ProcessLookupError()],
        ):
            await self.worker._reap()
        child.wait.assert_awaited_once()
        self.assertIsNone(self.worker.child)

    async def test_probe_permission_error_retries_until_proven_absence(self):
        """Transient zero-signal denial never counts as group disappearance."""
        child = SimpleNamespace(pid=987654, wait=AsyncMock(return_value=0))
        self.worker.child = child
        with (
            patch(
                "livolod.bootstrap_worker.os.killpg",
                side_effect=[None, PermissionError(), ProcessLookupError()],
            ),
            patch("livolod.bootstrap_worker.asyncio.sleep", new=AsyncMock()) as delay,
        ):
            await self.worker._reap()
        delay.assert_awaited_once_with(0.1)
        self.assertIsNone(self.worker.child)

    async def test_persistent_probe_denial_retains_cleanup_fence(self):
        """Persistent denial exhausts a bounded wait and cannot authorize handoff."""
        child = SimpleNamespace(pid=987654, wait=AsyncMock(return_value=0))
        self.worker.child = child
        try:
            with (
                patch(
                    "livolod.bootstrap_worker.os.killpg", side_effect=PermissionError()
                ),
                patch(
                    "livolod.bootstrap_worker.asyncio.sleep", new=AsyncMock()
                ) as delay,
            ):
                with self.assertRaises(BundleError) as error:
                    await self.worker._reap()
            self.assertEqual(error.exception.code, "worker_cleanup_unconfirmed")
            self.assertEqual(delay.await_count, 30)
            self.assertIs(self.worker.child, child)
        finally:
            # This is a fake PID; discard only the test object after asserting the fence.
            self.worker.child = None


class OwnershipTests(unittest.TestCase):
    def test_normal_reconnect_renews_attachment_but_setup_remains_pinned(self):
        """A completed setup permits ordinary reattachment; in-flight work stays exact."""
        from dataclasses import replace
        from livolod.bootstrap_worker import RadioOwnership

        with tempfile.TemporaryDirectory() as directory:
            target = GeckoTarget(
                "/dev/fixture",
                "devices/fixture/usb1/1-2",
                "first",
                (18, 0),
                "00",
                "1.9.2",
            )
            current = [target]
            owner = RadioOwnership(
                dict(serial_port=target.port, lease_directory=directory),
                inventory=lambda: tuple(current),
            )
            try:
                owner.acquire()
                current[0] = replace(target, attachment="second")
                owner.pinned = True
                with self.assertRaises(BundleError):
                    owner.acquire(allow_reattach=True)
                owner.pinned = False
                with self.assertRaises(BundleError):
                    owner.acquire()
                renewed, lease = owner.acquire(allow_reattach=True)
                self.assertEqual(renewed.attachment, "second")
                lease.check()
                current[0] = replace(current[0], usb_path="devices/fixture/usb1/1-3")
                with self.assertRaises(BundleError):
                    owner.acquire(allow_reattach=True)
            finally:
                owner.close()


class AdoptedGroupTests(unittest.TestCase):
    def test_reaping_is_nonblocking_and_limited_to_owned_group(self):
        """Only the completed worker's process group is eligible for adopted-child reap."""
        from livolod.bootstrap_worker import reap_adopted_group

        with patch(
            "livolod.bootstrap_worker.os.waitpid",
            side_effect=[(101, 0), (102, 0), (0, 0)],
        ) as wait:
            reap_adopted_group(100)
        self.assertEqual(wait.call_count, 3)
        for call in wait.call_args_list:
            self.assertEqual(call.args, (-100, os.WNOHANG))

    def test_reaping_is_bounded_and_rejects_global_wait_selectors(self):
        """Neither endless descendants nor a malformed group creates an unbounded/global wait."""
        from livolod.bootstrap_worker import reap_adopted_group

        with patch(
            "livolod.bootstrap_worker.os.waitpid", return_value=(101, 0)
        ) as wait:
            reap_adopted_group(100)
        self.assertEqual(wait.call_count, 64)
        for group in (-100, -1, 0, 1, True):
            with self.subTest(group=group), self.assertRaises(ValueError):
                reap_adopted_group(group)

    def test_no_children_is_not_group_disappearance_evidence(self):
        """ECHILD ends this bounded reap pass; the caller still must probe the group."""
        from livolod.bootstrap_worker import reap_adopted_group

        with patch(
            "livolod.bootstrap_worker.os.waitpid", side_effect=ChildProcessError()
        ) as wait:
            reap_adopted_group(100)
        wait.assert_called_once_with(-100, os.WNOHANG)
