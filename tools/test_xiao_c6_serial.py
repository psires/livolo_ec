"""Exercise real pyserial open/close with only OS boundaries replaced offline."""

from contextlib import ExitStack
import errno
import struct
import unittest
from unittest.mock import patch

import serial
from serial import serialposix as posix

import xiao_c6_serial as candidate


class LifecycleTests(unittest.TestCase):
    """Prove the candidate's request sequence and abort behavior without a port."""

    def setUp(self):
        """Intercept syscalls, retaining actual pyserial termios/open/close logic."""
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.trace = []
        self.ioctl_error = None
        self.stack.enter_context(patch.object(candidate.sys, "platform", "linux"))
        self.verify = self.stack.enter_context(
            patch.object(candidate, "verify_endpoint", return_value="/dev/ttyACM9")
        )
        self.stack.enter_context(
            patch.object(candidate.list_ports, "comports", return_value=[])
        )
        self.open_call = self.stack.enter_context(
            patch.object(posix.os, "open", side_effect=self.os_open)
        )
        self.stack.enter_context(
            patch.object(
                posix.os,
                "close",
                side_effect=lambda fd: self.trace.append(("close", fd)),
            )
        )
        self.stack.enter_context(
            patch.object(posix.os, "pipe", return_value=(101, 102))
        )
        self.stack.enter_context(patch.object(posix.fcntl, "fcntl", return_value=0))
        self.flock_call = self.stack.enter_context(
            patch.object(
                posix.fcntl,
                "flock",
                side_effect=lambda *args: self.trace.append(("flock",)),
            )
        )
        self.stack.enter_context(
            patch.object(posix.fcntl, "ioctl", side_effect=self.ioctl)
        )
        self.stack.enter_context(
            patch.object(
                posix.termios,
                "tcgetattr",
                side_effect=lambda fd: [
                    0,
                    0,
                    posix.termios.HUPCL | posix.termios.CS8,
                    0,
                    posix.termios.B115200,
                    posix.termios.B115200,
                    [0] * posix.termios.NCCS,
                ],
            )
        )
        self.setattr_call = self.stack.enter_context(
            patch.object(
                posix.termios,
                "tcsetattr",
                side_effect=lambda *args: self.trace.append(("termios",)),
            )
        )
        self.stack.enter_context(
            patch.object(
                posix.termios,
                "tcflush",
                side_effect=lambda *args: self.trace.append(("flush",)),
            )
        )

    def os_open(self, path, flags):
        """Record the OS open; never access a filesystem device.

        @param path Requested identity-verified path.
        @param flags Original pyserial OS flags.
        @return Synthetic descriptor.
        """
        self.trace.append(("open", path, flags))
        return 100

    def ioctl(self, fd, request, value):
        """Retain line operations and optionally simulate a kernel rejection.

        @param fd Synthetic descriptor.
        @param request Modem ioctl number.
        @param value Native unsigned mask bytes.
        @return Successful ioctl result unless rejection is injected.
        """
        self.trace.append(("ioctl", request, struct.unpack("I", value)[0]))
        if self.ioctl_error:
            raise self.ioctl_error
        return 0

    def port(self, mode):
        """Construct a closed fixture using the real candidate initializer.

        @param mode Explicit comparison arm.
        @return Candidate transport with cleanup registered.
        """
        port = candidate.C6Serial("/dev/serial/by-id/test-c6", "test-serial", mode=mode)
        self.addCleanup(port.close)
        return port

    def test_stock_vs_combined_real_open_sequence(self):
        """Only the two clear requests change; termios/HUPCL and close survive."""
        for mode, masks in (
            ("stock", [posix.TIOCM_DTR, posix.TIOCM_RTS]),
            ("combined", [posix.TIOCM_DTR | posix.TIOCM_RTS]),
        ):
            with self.subTest(mode=mode):
                self.trace.clear()
                port = self.port(mode)
                self.assertEqual(self.trace, [])
                port.open()
                self.assertEqual([x[2] for x in self.trace if x[0] == "ioctl"], masks)
                self.assertTrue(
                    all(x[1] == posix.TIOCMBIC for x in self.trace if x[0] == "ioctl")
                )
                self.assertEqual(
                    [x[0] for x in self.trace],
                    ["open", "flock", "termios"] + ["ioctl"] * len(masks) + ["flush"],
                )
                self.assertEqual((port.dtr, port.rts), (False, False))
                self.assertTrue(
                    self.setattr_call.call_args.args[2][2] & posix.termios.HUPCL
                )
                before = len(self.trace)
                port.close()
                self.assertTrue(all(x[0] == "close" for x in self.trace[before:]))
                self.assertFalse(port.is_open)

    def test_reopen_revalidates_identity_and_repeats_one_clear(self):
        """Each explicit reopen validates metadata and repeats no hidden opens."""
        port = self.port("combined")
        port.open()
        port.close()
        port.open()
        self.assertEqual(self.verify.call_count, 2)
        self.assertEqual(self.open_call.call_count, 2)
        self.assertEqual(len([x for x in self.trace if x[0] == "ioctl"]), 2)

    def test_identity_mismatch_never_opens(self):
        """Reject an uncertain endpoint before any OS operation."""
        port = self.port("combined")
        self.verify.side_effect = ValueError("USB endpoint identity mismatch")
        with self.assertRaises(ValueError):
            port.open()
        self.assertEqual(self.trace, [])

    def test_flock_failure_closes_without_termios_or_line_changes(self):
        """A competing owner stops the userspace sequence immediately after open."""
        port = self.port("combined")
        self.flock_call.side_effect = OSError(errno.EAGAIN, "owned")
        with self.assertRaises(serial.SerialException):
            port.open()
        self.assertFalse(port.is_open)
        self.assertIsNone(port.fd)
        self.assertEqual([x[0] for x in self.trace], ["open", "close"])

    def test_ioctl_failure_closes_and_never_falls_back(self):
        """Even errors normally swallowed by pyserial abort this candidate."""
        for error in (errno.EINVAL, errno.ENOTTY, errno.EIO):
            with self.subTest(errno=error):
                self.trace.clear()
                port = self.port("combined")
                self.ioctl_error = OSError(error, "injected")
                with self.assertRaisesRegex(RuntimeError, "no fallback"):
                    port.open()
                self.assertFalse(port.is_open)
                self.assertIsNone(port.fd)
                self.assertEqual(
                    [x[0] for x in self.trace],
                    ["open", "flock", "termios", "ioctl", "close"],
                )

    def test_forbidden_line_change_keeps_internal_state(self):
        """Reject controls before changing pyserial's desired state."""
        port = self.port("combined")
        for name in ("dtr", "rts"):
            with self.assertRaises(ValueError):
                setattr(port, name, True)
        port.open()
        before = list(self.trace)
        for name in ("dtr", "rts"):
            for value in (True, False):
                with self.assertRaises(ValueError):
                    setattr(port, name, value)
        self.assertEqual((port.dtr, port.rts), (False, False))
        self.assertEqual(self.trace, before)

    def test_changed_policy_fails_before_open(self):
        """Identity and flow-control constraints cannot be weakened before open."""
        for name, value in (
            ("port", "/dev/ttyACM0"),
            ("rtscts", True),
            ("dsrdtr", True),
            ("exclusive", False),
            ("baudrate", 9600),
            ("xonxoff", True),
            ("bytesize", 7),
            ("parity", "E"),
            ("stopbits", 2),
            ("timeout", 1),
            ("write_timeout", 1),
            ("inter_byte_timeout", 0.1),
            ("rs485_mode", object()),
        ):
            port = self.port("combined")
            setattr(port, name, value)
            with self.assertRaises(ValueError):
                port.open()
        self.assertEqual(self.trace, [])

    def test_version_platform_and_mode_fail_closed(self):
        """Reject unreviewed backends and implicit experiment selection."""
        with patch.object(serial, "VERSION", "future"):
            with self.assertRaises(RuntimeError):
                self.port("combined")
        with patch.object(candidate.sys, "platform", "darwin"):
            with self.assertRaises(RuntimeError):
                self.port("combined")
        with self.assertRaises(ValueError):
            self.port("auto")
        self.assertEqual(self.trace, [])


if __name__ == "__main__":
    unittest.main()
