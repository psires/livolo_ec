"""Emergency recovery must never erase a committed or unidentified TI image."""

import unittest

from livolod.firmware_cli import recover_ti


class Port:
    def __init__(
        self,
        state="ffffffff",
        receiving="0",
        trial="0",
        current=None,
        candidate=None,
        version="0.11",
    ):
        """Prepare read-only updater identity and record any requested mutation.

        @param state Persistent bootloader state word.
        @param receiving Whether a transfer is currently receiving application bytes.
        @param trial Whether a trial application is running.
        @param current Current application digest, defaulting to the previous image.
        @param candidate Committed candidate digest, defaulting to the selected image.
        @param version Running application version.
        """
        self.status = dict(
            state=state,
            receiving=receiving,
            trial=trial,
            current_sha512=current or "a" * 128,
            candidate_sha512=candidate or "b" * 128,
            key="c" * 64,
        )
        self.version = version
        self.commands = []

    def command(self, text, **kwargs):
        """Return fixture status and record bounded recovery commands.

        @param text Requested UART operation.
        @param kwargs Transport timeout and retry arguments accepted for compatibility.
        """
        self.commands.append(text)
        if text == "VERSION":
            return f"OK board=codm-cc2652p fw_version={self.version}"
        if text == "UPDATE_STATUS":
            return "OK " + " ".join(
                f"{key}={value}" for key, value in self.status.items()
            )
        return "OK"


class FirmwareRecoveryTests(unittest.TestCase):
    def setUp(self):
        """Define selected and previous images from an interrupted update journal."""
        self.job = dict(
            backend="ti_uart_signed_v1",
            board="codm-cc2652p",
            version="0.12",
            installed_version="0.11",
            original_sha512="a" * 128,
            image_sha512="b" * 128,
            public_key="c" * 64,
        )

    def test_receiving_aborts_only_verified_previous_image(self):
        """An incomplete transfer can be aborted and rebooted under journal ownership."""
        port = Port(receiving="1")
        self.assertEqual(recover_ti(port, self.job), "incomplete_transfer_aborted")
        self.assertEqual(port.commands[-2:], ["UPDATE_ABORT", "UPDATE_REBOOT"])

    def test_matching_commit_activates_without_abort(self):
        """A durable selected candidate is activated once and never erased."""
        port = Port(state="fffffffe")
        self.assertEqual(recover_ti(port, self.job), "activation_requested")
        self.assertEqual(port.commands, ["VERSION", "UPDATE_STATUS", "UPDATE_ACTIVATE"])

    def test_different_commit_never_mutates(self):
        """A different committed digest cannot be erased or activated by this job."""
        port = Port(state="fffffffe", candidate="d" * 128)
        with self.assertRaises(ValueError):
            recover_ti(port, self.job)
        self.assertEqual(port.commands, ["VERSION", "UPDATE_STATUS"])

    def test_matching_trial_resumes_daemon_confirmation_only(self):
        """Recovery leaves selected trial confirmation to normal session restoration."""
        port = Port(state="fffffff8", trial="1", current="b" * 128, version="0.12")
        self.assertEqual(recover_ti(port, self.job), "resume_trial_confirmation")
        self.assertEqual(port.commands, ["VERSION", "UPDATE_STATUS"])

    def test_unknown_original_and_wrong_key_never_mutate(self):
        """Identity, current digest and transfer state are checked before abort/reset."""
        for changes in (
            dict(current_sha512="d" * 128),
            dict(key="e" * 64),
            dict(receiving="?"),
            dict(state="fffffffc"),
        ):
            port = Port(receiving="1")
            port.status.update(changes)
            with self.assertRaises(ValueError):
                recover_ti(port, self.job)
            self.assertEqual(port.commands, ["VERSION", "UPDATE_STATUS"])

    def test_verified_rollback_and_confirmed_target_need_no_mutation(self):
        """Stable previous or selected images resume ordinary readiness checks."""
        previous = Port(state="ffffffc8")
        target = Port(state="fffffff0", current="b" * 128, version="0.12")
        self.assertEqual(recover_ti(previous, self.job), "previous_image_ready")
        self.assertEqual(recover_ti(target, self.job), "selected_image_ready")
        self.assertEqual(previous.commands, target.commands)
        self.assertEqual(previous.commands, ["VERSION", "UPDATE_STATUS"])
