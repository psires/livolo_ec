"""Independently exercise TI confirmation after a software-only restart."""

import unittest
from unittest.mock import patch

from livolod import __version__, FIRMWARE_COMPAT_VERSION
from livolod.firmware_manager import FirmwareManager
from livolod.uart import LinkError
import test_version


class FirmwareVersionBoundaryTests(unittest.IsolatedAsyncioTestCase):
    async def test_restarted_ti_trial_requires_compatibility_and_recorded_identity(
        self,
    ):
        """A newer daemon cannot confirm a wrong target, board or same-version digest."""
        current = FIRMWARE_COMPAT_VERSION
        other = "0.1" if current != "0.1" else "0.2"
        self.assertNotEqual(current, other)
        for running, selected, board, digest, allowed in (
            (current, current, "codm-cc2652p", "c" * 128, True),
            (current, current, "codm-cc2652p", "d" * 128, False),
            (current, current, "sonoff-zbdongle-e", "c" * 128, False),
            (other, current, "codm-cc2652p", "c" * 128, False),
            (other, other, "codm-cc2652p", "c" * 128, False),
            (__version__, current, "codm-cc2652p", "c" * 128, False),
        ):
            with (
                self.subTest(
                    running=running, selected=selected, board=board, digest=digest[:1]
                ),
                patch("livolod.version.__version__", "9.8.7"),
            ):
                service, store, history = await test_version.TrialTests.exercise(
                    self, running
                )
                job = dict(
                    request_id="restart-trial",
                    candidate_id="ti:" + "b" * 64,
                    status="pending",
                    stage="activate",
                    backend="ti_uart_signed_v1",
                    board="codm-cc2652p",
                    version=selected,
                    installed_version="0.12",
                    baudrate=service.config.serial_baudrate,
                    public_key="e" * 64,
                    image_sha512="c" * 128,
                )
                store.save_firmware_job(job)
                service.firmware_manager = FirmwareManager(service)
                manager = service.firmware_manager
                self.assertEqual(manager.job["status"], "unknown")
                self.assertFalse(manager.requested.is_set())
                for key in ("candidate_id", "version", "public_key", "image_sha512"):
                    self.assertEqual(manager.job[key], job[key])
                command = service.gateway.command

                async def reply(line, retries=1):
                    """Supply fresh simulated identity and digest across trial confirmation.

                    @param line UART command requested by the real restoration path.
                    @param retries Existing caller retry allowance, left unchanged.
                    @return Simulated response with this scenario's board or image digest.
                    """
                    result = await command(line, retries)
                    if line in ("CAPS", "VERSION"):
                        result = result.replace("board=codm-cc2652p", "board=" + board)
                    if line == "CAPS":
                        result += " baud=" + str(service.config.serial_baudrate)
                    if line == "UPDATE_STATUS":
                        result += " current_sha512=" + digest
                    return result

                service.gateway.command = reply
                if allowed:
                    await service.restore()
                    self.assertTrue(service.ready)
                    self.assertEqual(manager.job["status"], "confirmed")
                    self.assertEqual(history.count("UPDATE_CONFIRM"), 1)
                else:
                    with self.assertRaises(LinkError):
                        await service.restore()
                    self.assertFalse(service.ready)
                    self.assertNotIn("UPDATE_CONFIRM", history)
                    self.assertNotEqual(manager.job["status"], "confirmed")
                self.assertEqual(service.diagnostics["daemon_version"], "9.8.7")
                self.assertEqual(service.diagnostics["host_version"], current)
                self.assertFalse(manager.requested.is_set())
                self.assertFalse(
                    any(line.startswith("UPDATE_BEGIN") for line in history)
                )
