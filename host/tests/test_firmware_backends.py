"""Exercise board handoff and durable boundaries without opening a real radio."""

import hashlib
import subprocess
import unittest
from unittest.mock import Mock, patch

from livolod import firmware_backends as backends
from livolod.firmware_bundle import BundleError
from test_firmware_bundle import gbl, mg21_image, ti_package


class Radio:
    """A disarmed protocol-v3 adapter with a durable TI commit model."""

    def __init__(self, board="sonoff-zbdongle-e", public=None):
        """Initialize a simulated previous-release radio.

        @param board Exact physical-board identity reported by VERSION/CAPS.
        @param public Optional raw TI verification-key bytes.
        """
        self.board, self.public = board, public
        self.commands, self.closed = [], False
        self.state, self.version, self.armed = "fffffff0", "0.11", "0"
        self.digest = ""

    def command(self, text, **kwargs):
        """Model replies while retaining every command for mutation assertions.

        @param text Command text without request-ID framing.
        @param kwargs Timeout/retry controls accepted without real waiting.
        @return A successful reply or simulated version/update state.
        """
        self.commands.append(text)
        if text in ("VERSION", "CAPS"):
            return f"OK board={self.board} proto=3 fw_version={self.version} baud=460800 update=uart_signed_v1 boot_version=0.2"
        if text == "STATUS":
            return f"OK armed={self.armed} ack={self.armed}"
        if text == "UPDATE_STATUS":
            return f"OK state={self.state} trial=0 key={self.public.hex()} candidate_sha512={self.digest}"
        if text.startswith("UPDATE_COMMIT "):
            self.state = "fffffffe"
        if text.startswith("UPDATE_DATA "):
            _, _, offset, data = text.split()
            return f"OK received={int(offset) + len(data)//2}"
        return "OK"

    def close(self):
        """Record exclusive serial ownership being released."""
        self.closed = True


def candidate(ti=False):
    """Build private worker metadata around synthetic test-only bytes.

    @param ti Select signed TI transport rather than the default MG21 backend.
    @return Candidate and matching simulated radio.
    """
    if ti:
        data, public = ti_package()
        board, backend = "codm-cc2652p", "ti_uart_signed_v1"
    else:
        data, public = gbl(mg21_image()), None
        board, backend = "sonoff-zbdongle-e", "mg21_gecko_gbl_v1"
    radio = Radio(board, public)
    radio.digest = hashlib.sha512(data[192:]).hexdigest() if ti else ""
    return (
        dict(
            board=board,
            backend=backend,
            version="0.12",
            app_baudrate=460800,
            _data=data,
            public_key=public.hex() if public else None,
        ),
        radio,
    )


class BackendTests(unittest.TestCase):
    def run_install(self, artifact, radio, before=None):
        """Invoke the real installer with only transport/helper side effects mocked.

        @param artifact Selected private candidate.
        @param radio Simulated UpdatePort implementation.
        @param before Optional durability callback used to inject parent loss.
        @return Phase list and mocked external flasher.
        """
        phases = []
        with (
            patch.object(backends, "backend_status", return_value=None),
            patch.object(backends, "UpdatePort", return_value=radio),
            patch.object(backends, "_run_flasher") as helper,
        ):
            backends.install(
                artifact, "/dev/test-only", 42, before or phases.append, Mock()
            )
        return phases, helper

    def test_gecko_handoff_closes_uart_before_fixed_helper_and_does_not_confirm(self):
        """MG21 releases the application connection before bootloader flashing."""
        artifact, radio = candidate()

        def helper(path, port, fd):
            """Verify helper inputs at the actual handoff boundary.

            @param path Private staged artifact path.
            @param port Exact configured serial path.
            @param fd Inherited radio guard descriptor.
            """
            self.assertTrue(radio.closed)
            self.assertEqual(path.read_bytes(), artifact["_data"])
            self.assertEqual((port, fd), ("/dev/test-only", 42))

        phases = []
        with (
            patch.object(backends, "backend_status", return_value=None),
            patch.object(backends, "UpdatePort", return_value=radio),
            patch.object(backends, "_run_flasher", side_effect=helper),
        ):
            backends.install(artifact, "/dev/test-only", 42, phases.append, Mock())
        self.assertEqual(phases, ["disarm", "bootloader_entry", "flash"])
        self.assertEqual(radio.commands[-2:], ["DISARM", "BOOTLOADER"])
        self.assertNotIn("UPDATE_CONFIRM", radio.commands)

    def test_identity_and_armed_state_fail_before_mutation(self):
        """Wrong board, version or active radio cannot enter a bootloader."""
        for field, value in (
            ("board", "zb-gw04-1v2"),
            ("version", "0.12"),
            ("armed", "1"),
        ):
            artifact, radio = candidate()
            setattr(radio, field, value)
            with self.subTest(field=field), self.assertRaises(BundleError):
                self.run_install(artifact, radio)
            self.assertTrue(radio.closed)
            self.assertNotIn("DISARM", radio.commands)
            self.assertNotIn("BOOTLOADER", radio.commands)

    def test_gecko_parent_loss_at_each_barrier_never_crosses_it(self):
        """Parent loss before bootloader/flash leaves the next mutation unexecuted."""
        for boundary in ("disarm", "bootloader_entry", "flash"):
            artifact, radio = candidate()

            def before(stage):
                """Simulate failure to durably acknowledge one critical boundary.

                @param stage Installation phase requested by the backend.
                """
                if stage == boundary:
                    raise BundleError("parent_unavailable", "Parent disconnected")

            with (
                patch.object(backends, "backend_status", return_value=None),
                patch.object(backends, "UpdatePort", return_value=radio),
                patch.object(backends, "_run_flasher") as helper,
            ):
                with self.subTest(boundary=boundary), self.assertRaises(BundleError):
                    backends.install(artifact, "/dev/test-only", 42, before, Mock())
                helper.assert_not_called()
            self.assertTrue(radio.closed)
            if boundary != "flash":
                self.assertNotIn("BOOTLOADER", radio.commands)

    def test_ti_parent_loss_at_commit_and_activation_never_replays_mutations(self):
        """A durable TI candidate is left intact when activation lacks acknowledgement."""
        for boundary in ("disarm", "begin", "commit", "activate"):
            artifact, radio = candidate(ti=True)

            def before(stage):
                """Stop exactly at the selected durable phase boundary.

                @param stage Critical TI stage about to execute.
                """
                if stage == boundary:
                    raise BundleError("parent_unavailable", "Parent disconnected")

            with self.subTest(boundary=boundary), self.assertRaises(BundleError):
                self.run_install(artifact, radio, before)
            self.assertTrue(radio.closed)
            self.assertNotIn("UPDATE_ACTIVATE", radio.commands)
            self.assertNotIn("UPDATE_REBOOT", radio.commands)
            if boundary != "activate":
                self.assertFalse(
                    any(x.startswith("UPDATE_COMMIT ") for x in radio.commands)
                )
            else:
                self.assertEqual(radio.state, "fffffffe")
                self.assertTrue(radio.commands[-1] == "UPDATE_STATUS")

    def test_ti_success_leaves_trial_confirmation_to_manager(self):
        """The worker activates once but never sends UPDATE_CONFIRM."""
        artifact, radio = candidate(ti=True)
        phases, helper = self.run_install(artifact, radio)
        self.assertEqual(phases, ["disarm", "begin", "commit", "activate"])
        self.assertEqual(radio.commands[-1], "UPDATE_ACTIVATE")
        self.assertNotIn("UPDATE_CONFIRM", radio.commands)
        helper.assert_not_called()

    def test_ti_existing_commit_requires_explicit_reconciliation(self):
        """The integrated worker never resumes a prior committed operation blindly."""
        artifact, radio = candidate(ti=True)
        radio.state = "fffffffe"
        with self.assertRaises(BundleError) as caught:
            self.run_install(artifact, radio)
        self.assertEqual(caught.exception.code, "recovery_required")
        self.assertNotIn("UPDATE_ACTIVATE", radio.commands)

    def test_restored_ti_image_can_receive_a_newer_update(self):
        """A completed rollback is a stable starting state for a later update."""
        artifact, radio = candidate(ti=True)
        radio.state = "ffffffc8"
        self.run_install(artifact, radio)
        self.assertIn("UPDATE_ACTIVATE", radio.commands)

    def test_helper_uses_pinned_bootloader_cli_and_inherits_guard(self):
        """No reset profile, force option or detached process can escape ownership."""
        child = Mock()
        child.wait.return_value = 0
        with patch.object(backends.subprocess, "Popen", return_value=child) as spawn:
            backends._run_flasher("/private/application.gbl", "/dev/test-only", 42)
        argv = spawn.call_args.args[0]
        self.assertEqual(
            argv[1:],
            [
                "-m",
                "universal_silabs_flasher",
                "--device",
                "/dev/test-only",
                "--bootloader-baudrate",
                "115200",
                "--probe-method",
                "bootloader",
                "flash",
                "--firmware",
                "/private/application.gbl",
            ],
        )
        self.assertEqual(spawn.call_args.kwargs["pass_fds"], (42,))
        self.assertNotIn("start_new_session", spawn.call_args.kwargs)

    def test_helper_timeout_kills_and_reaps_before_returning(self):
        """A wedged external uploader cannot outlive normal worker cleanup."""
        child = Mock()
        child.wait.side_effect = [
            subprocess.TimeoutExpired("helper", 120),
            subprocess.TimeoutExpired("helper", 3),
            -9,
        ]
        with (
            patch.object(backends.subprocess, "Popen", return_value=child),
            self.assertRaises(subprocess.TimeoutExpired),
        ):
            backends._run_flasher("/private/application.gbl", "/dev/test-only", 42)
        child.terminate.assert_called_once()
        child.kill.assert_called_once()
        self.assertEqual(child.wait.call_count, 3)

    def test_unqualified_helper_version_and_nordic_are_unavailable(self):
        """Only the explicitly pinned Gecko CLI is eligible for integrated use."""
        artifact, _ = candidate()
        with patch.object(
            backends.importlib.metadata, "version", return_value="0.0.30"
        ):
            self.assertEqual(backends.backend_status(artifact), "backend_unavailable")
        with patch.object(
            backends.importlib.metadata, "version", return_value="0.0.31"
        ):
            self.assertIsNone(backends.backend_status(artifact))
        artifact["backend"] = "nrf52840_usb_dfu_v1"
        self.assertEqual(backends.backend_status(artifact), "backend_unavailable")
