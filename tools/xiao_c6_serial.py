"""Qualified C6 bench open policy and opt-in comparison; never used by the daemon."""

import struct
import sys

import serial
from serial import serialposix
from serial.tools import list_ports

from xiao_rf_bench import verify_endpoint


class C6Serial(serialposix.Serial):
    """Compare stock open with a combined clear on one explicitly selected C6.

    Ownership and known RF-off state must be established before opening: even
    the kernel open can change CDC line state, before pyserial obtains its lock.
    This is an experimental transport, not a guarantee against hardware reset.
    """

    def __init__(self, path, usb_serial, *, mode):
        """Configure a closed port; do not enumerate, open or write hardware.

        @param path Explicit /dev/serial/by-id path for the new bench C6 only.
        @param usb_serial Exact serial from the operator's private inventory.
        @param mode Required stock or combined comparison, without fallback.
        """
        if sys.platform != "linux" or serial.VERSION != "3.5":
            raise RuntimeError("Experiment requires Linux and pyserial 3.5")
        if mode not in ("stock", "combined"):
            raise ValueError("Explicit stock or combined mode required")
        self._experiment_mode = mode
        self._expected_path = path
        self._expected_serial = usb_serial
        self._opening = False
        self._combined_done = False
        super().__init__(
            port=None,
            baudrate=115200,
            timeout=0,
            write_timeout=0.25,
            exclusive=True,
            rtscts=False,
            dsrdtr=False,
            xonxoff=False,
        )
        self.dtr = False
        self.rts = False
        self.port = path

    @property
    def dtr(self):
        """Expose the false state also used by pyserial's open path."""
        return self._dtr_state

    @dtr.setter
    def dtr(self, value):
        """Reject line changes before internal state or hardware can change.

        @param value Only literal False while closed is permitted.
        """
        if value is not False or self.is_open:
            raise ValueError("Experiment forbids DTR changes")
        self._dtr_state = False

    @property
    def rts(self):
        """Expose the false state also used by pyserial's open path."""
        return self._rts_state

    @rts.setter
    def rts(self, value):
        """Reject line changes before internal state or hardware can change.

        @param value Only literal False while closed is permitted.
        """
        if value is not False or self.is_open:
            raise ValueError("Experiment forbids RTS changes")
        self._rts_state = False

    def open(self):
        """Verify identity each time, then execute one unmodified parent lifecycle."""
        if self.is_open or self._opening:
            raise RuntimeError("Port already open or opening")
        if (
            self.port != self._expected_path
            or self.dtr
            or self.rts
            or self.rtscts
            or self.dsrdtr
            or self.xonxoff
            or self.exclusive is not True
            or self.baudrate != 115200
            or self.bytesize != 8
            or self.parity != "N"
            or self.stopbits != 1
            or self.timeout != 0
            or self.write_timeout != 0.25
            or self.inter_byte_timeout is not None
            or self.rs485_mode is not None
        ):
            raise ValueError("Experiment serial policy changed")
        verify_endpoint(
            self._expected_path,
            self._expected_serial,
            "c6",
            list(list_ports.comports()),
        )
        self._opening = True
        self._combined_done = False
        try:
            super().open()
        finally:
            self._opening = False

    def _update_dtr_state(self):
        """Change only the open-time pair of line-state operations in combined mode."""
        if not self._opening:
            raise RuntimeError("Line-state operation outside experimental open")
        if self._experiment_mode == "stock":
            return super()._update_dtr_state()
        mask = serialposix.TIOCM_DTR | serialposix.TIOCM_RTS
        try:
            serialposix.fcntl.ioctl(
                self.fd, serialposix.TIOCMBIC, struct.pack("I", mask)
            )
        except OSError as error:
            # Parent open ignores EINVAL/ENOTTY. The candidate must fail closed
            # instead of silently continuing without the requested operation.
            raise RuntimeError("Combined line clear failed; no fallback") from error
        self._combined_done = True

    def _update_rts_state(self):
        """Keep stock behavior or verify the preceding combined clear succeeded."""
        if not self._opening:
            raise RuntimeError("Line-state operation outside experimental open")
        if self._experiment_mode == "stock":
            return super()._update_rts_state()
        if not self._combined_done:
            raise RuntimeError("Combined clear was not completed")
