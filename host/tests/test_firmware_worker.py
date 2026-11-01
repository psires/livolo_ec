"""Verify durable IPC and orphan-safe lock inheritance with local pipes/processes."""

import fcntl
import io
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from livolod.firmware_bundle import BundleError
from livolod.firmware_worker import Channel, main, validate_guard


class WorkerTests(unittest.TestCase):
    def channel(self, response, timeout=0.05):
        """Create an IPC channel with a preloaded parent response and EOF.

        @param response Exact parent acknowledgement bytes to feed.
        @param timeout Bounded acknowledgement timeout in seconds.
        @return Channel and its captured JSON event stream.
        """
        read, write = os.pipe()
        self.addCleanup(os.close, read)
        if response:
            os.write(write, response)
        os.close(write)
        output = io.StringIO()
        return Channel("job-1", read, output, timeout), output

    def test_matching_ack_follows_global_sequence_even_after_progress(self):
        """Progress events consume sequence numbers before a later critical event."""
        channel, output = self.channel(b'{"ack":3,"job_id":"job-1"}\n')
        channel.progress(dict(stage="transfer", percent=25))
        channel.progress(dict(stage="transfer", percent=100))
        channel.before("commit")
        events = [json.loads(line) for line in output.getvalue().splitlines()]
        self.assertEqual([e["sequence"] for e in events], [1, 2, 3])
        self.assertEqual([e["critical"] for e in events], [False, False, True])
        self.assertTrue(channel.mutation_possible)
        self.assertNotIn("progress_percent", events[0])

    def test_eof_stale_wrong_job_oversized_and_batched_ack_stop_mutation(self):
        """Malformed or absent parent acknowledgement never permits a new phase."""
        for response in (
            b"",
            b"{}\n",
            b'{"ack":0,"job_id":"job-1"}\n',
            b'{"ack":1,"job_id":"wrong"}\n',
            b'{"ack":true,"job_id":"job-1"}\n',
            b'{"ack":1,"job_id":"job-1"}\n{}\n',
            b"x" * 1025,
        ):
            channel, _ = self.channel(response)
            with self.subTest(response=response[:40]), self.assertRaises(BundleError):
                channel.before("activate")
            self.assertFalse(channel.mutation_possible)

    def test_silent_parent_ack_wait_is_bounded(self):
        """A live but unresponsive parent cannot leave a worker waiting forever."""
        read, write = os.pipe()
        try:
            channel = Channel("job", read, io.StringIO(), timeout=0.01)
            with self.assertRaises(BundleError):
                channel.before("flash")
            self.assertFalse(channel.mutation_possible)
        finally:
            os.close(read)
            os.close(write)

    def test_guard_rejects_stdin_and_preserves_existing_lock(self):
        """Only an inherited regular-file guard can authorize serial access."""
        with self.assertRaises(BundleError):
            validate_guard(0)
        with tempfile.TemporaryFile() as guard:
            fcntl.flock(guard, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.assertEqual(validate_guard(guard.fileno()), guard.fileno())

    def test_orphaned_helper_holds_guard_after_worker_exit(self):
        """A restarted daemon cannot acquire the radio while an orphan helper lives."""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "radio.lock"
            fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            read, write = os.pipe()
            # The intermediate worker exits immediately; its helper retains the
            # same open-file description and signals readiness over stdout.
            helper = (
                "import os,sys; print('ready',flush=True); os.read(int(sys.argv[1]),1)"
            )
            worker = (
                "import os,subprocess,sys; "
                "p=subprocess.Popen([sys.executable,'-c',sys.argv[1],sys.argv[3]],"
                "pass_fds=(int(sys.argv[2]),int(sys.argv[3]))); "
                "print(p.pid,flush=True); os._exit(0)"
            )
            process = subprocess.Popen(
                [sys.executable, "-c", worker, helper, str(fd), str(read)],
                pass_fds=(fd, read),
                stdout=subprocess.PIPE,
                text=True,
            )
            helper_pid = None
            try:
                lines = {
                    process.stdout.readline().strip(),
                    process.stdout.readline().strip(),
                }
                self.assertIn("ready", lines)
                helper_pid = int(next(x for x in lines if x != "ready"))
                self.assertEqual(process.wait(timeout=3), 0)
                os.close(fd)
                fd = None
                with path.open("rb") as restarted:
                    with self.assertRaises(BlockingIOError):
                        fcntl.flock(restarted, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    os.write(write, b"x")
                    deadline = time.monotonic() + 3
                    while True:
                        try:
                            fcntl.flock(restarted, fcntl.LOCK_EX | fcntl.LOCK_NB)
                            break
                        except BlockingIOError:
                            if time.monotonic() >= deadline:
                                self.fail("Helper did not release the radio guard")
                            time.sleep(0.01)
            finally:
                if fd is not None:
                    os.close(fd)
                os.close(read)
                os.close(write)
                process.stdout.close()
                if process.poll() is None:
                    process.kill()
                    process.wait()
                if helper_pid is not None:
                    try:
                        os.kill(helper_pid, signal.SIGTERM)
                    except ProcessLookupError:
                        pass

    def test_failure_reports_sanitized_outcome_without_recovery_mutation(self):
        """Worker failures never expose raw exception details or launch recovery."""
        output = io.StringIO()
        args = [
            "--bundle-dir",
            "/test",
            "--candidate-id",
            "test:hash",
            "--port",
            "/dev/test-only",
            "--baud",
            "460800",
            "--job-id",
            "job-1",
            "--lock-fd",
            "42",
        ]
        with (
            patch("livolod.firmware_worker.validate_guard", return_value=42),
            patch("livolod.firmware_worker.load_candidate", return_value={}),
            patch(
                "livolod.firmware_worker.install",
                side_effect=OSError("private device path and detail"),
            ),
            patch("sys.stdout", output),
        ):
            self.assertEqual(main(args), 1)
        event = json.loads(output.getvalue())
        self.assertEqual(event["stage"], "failed")
        self.assertEqual(event["code"], "worker_failed")
        self.assertFalse(event["recovery"])
        self.assertNotIn("private", event["error"])

    def test_transfer_exit_is_never_firmware_confirmation(self):
        """Successful worker exit reports transferred rather than confirmed."""
        output = io.StringIO()
        args = [
            "--bundle-dir",
            "/test",
            "--candidate-id",
            "test:hash",
            "--port",
            "/dev/test-only",
            "--baud",
            "460800",
            "--job-id",
            "job-1",
            "--lock-fd",
            "42",
        ]
        with (
            patch("livolod.firmware_worker.validate_guard", return_value=42),
            patch("livolod.firmware_worker.load_candidate", return_value={}),
            patch("livolod.firmware_worker.install"),
            patch("sys.stdout", output),
        ):
            self.assertEqual(main(args), 0)
        self.assertEqual(json.loads(output.getvalue())["stage"], "transferred")

    def test_invalid_job_identity_never_opens_the_guard(self):
        """Worker CLI uses the same bounded identity alphabet as the manager."""
        args = [
            "--bundle-dir",
            "/test",
            "--candidate-id",
            "test:hash",
            "--port",
            "/dev/test-only",
            "--baud",
            "460800",
            "--job-id",
            "job with spaces",
            "--lock-fd",
            "42",
        ]
        with (
            patch("livolod.firmware_worker.validate_guard") as guard,
            patch("sys.stderr", io.StringIO()),
            self.assertRaises(SystemExit) as caught,
        ):
            main(args)
        self.assertEqual(caught.exception.code, 2)
        guard.assert_not_called()
