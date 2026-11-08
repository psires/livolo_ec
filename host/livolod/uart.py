"""POSIX serial transport: one owner, one outstanding request, independent RX."""

import asyncio
import secrets
import time

from .config import uart_baudrate


class LinkError(RuntimeError):
    """The adapter session must be discarded; RF outcome may be unknown."""


class CommandError(RuntimeError):
    """An explicit firmware ERR reply, whose reason remains available to policy."""


class Gateway:
    def __init__(self, serial_port, timeout=2.0):
        """Attach an open serial port to the owning asyncio event loop.

        @param serial_port Open nonblocking serial object owned by this gateway.
        @param timeout Reply timeout in seconds for each command attempt.
        """
        self.serial = serial_port
        self.timeout = timeout
        self.loop = asyncio.get_running_loop()
        self.events = asyncio.Queue(maxsize=2048)
        self.fault = self.loop.create_future()
        self.lock = asyncio.Lock()
        self.pending = None
        self.buffer = bytearray()
        self.next_id = secrets.randbelow(0xFFFFFF00) + 1
        self.closed = False
        self.loop.add_reader(self.serial.fileno(), self._read)

    @classmethod
    def open(cls, port, baudrate=115200):
        """Open an exclusive adapter connection with the UART v3 serial settings.

        @param port Serial device path.
        @param baudrate Application rate matching the installed firmware.
        @return A Gateway that owns the opened serial connection.
        """
        import serial

        uart = serial.Serial(
            port,
            uart_baudrate(baudrate),
            timeout=0,
            write_timeout=0.2,
            exclusive=True,
            rtscts=False,
            dsrdtr=False,
        )
        try:
            return cls(uart)
        except BaseException:
            uart.close()
            raise

    def _fail(self, reason):
        """Mark the adapter session unusable and notify command and service waiters.

        @param reason Error or text explaining why reconciliation is required.
        """
        error = LinkError(str(reason))
        if not self.fault.done():
            self.fault.set_result(error)
        if self.pending and not self.pending[1].done():
            self.pending[1].set_exception(error)

    def _read(self):
        """Accept available serial records and detect transport or framing failures."""
        try:
            data = self.serial.read(max(1, min(self.serial.in_waiting, 4096)))
            if not data:
                self._fail("Serial device disconnected")
                self.loop.remove_reader(self.serial.fileno())
                return
            self.buffer.extend(data)
            while b"\n" in self.buffer:
                raw, _, rest = self.buffer.partition(b"\n")
                self.buffer = bytearray(rest)
                if len(raw) > 1024:
                    raise LinkError("UART record exceeds limit")
                self.feed_line(raw.decode("ascii").rstrip("\r"))
            if len(self.buffer) > 1024:
                raise LinkError("UART record exceeds limit")
        except (OSError, ValueError, UnicodeError, LinkError) as error:
            self._fail(error)

    def feed_line(self, line):
        """Route one adapter record to its command waiter or unsolicited event queue.

        @param line Decoded UART record without a line terminator.
        """
        if line.startswith("EC-GATEWAY "):
            self._fail("Adapter boot detected; restore a fresh session")
        elif line.startswith("RSP "):
            parts = line.split(" ", 2)
            if len(parts) == 3 and self.pending and parts[1] == str(self.pending[0]):
                if not self.pending[1].done():
                    self.pending[1].set_result(parts[2])
        elif line.startswith(("RX ", "STATUS ", "ACKTX ")):
            try:
                self.events.put_nowait((time.monotonic(), line))
            except asyncio.QueueFull:
                self._fail("UART event backlog overflow; state needs reconciliation")

    async def command(self, text, retries=1):
        """Request an adapter operation with bounded retries of the same request ID.

        @param text UART operation and arguments without ID or newline.
        @param retries Additional reply attempts after the first transmission.
        @return The successful OK reply; errors distinguish rejection from uncertain outcome.
        """
        async with self.lock:
            if self.closed or (self.fault.done() and text != "DISARM"):
                raise LinkError("Adapter session is unavailable")
            request_id = self.next_id
            self.next_id = self.next_id % 0xFFFFFFFF + 1
            request = f"{request_id} {text}\n".encode("ascii")
            if len(request) > 320 or any(c < 32 or c > 126 for c in request[:-1]):
                raise ValueError("Invalid UART command")
            future = self.loop.create_future()
            self.pending = (request_id, future)
            try:
                for _ in range(retries + 1):
                    # A bounded write of at most 320 bytes; never await between
                    # write and registering the request. All state stays on this loop.
                    try:
                        if self.serial.write(request) != len(request):
                            raise OSError("Short UART write")
                    except OSError as error:
                        raise LinkError("UART write outcome unknown") from error
                    try:
                        result = await asyncio.wait_for(
                            asyncio.shield(future), self.timeout
                        )
                    except asyncio.TimeoutError:
                        continue  # Exact same bytes and ID: firmware reply cache.
                    if result.startswith("ERR "):
                        raise CommandError(result)
                    if result == "OK" or result.startswith("OK "):
                        return result
                    raise LinkError("Malformed UART response")
                raise LinkError("UART request timed out; RF outcome unknown")
            except LinkError as error:
                self._fail(error)
                raise
            finally:
                self.pending = None
                if not future.done():
                    future.cancel()
                elif not future.cancelled():
                    future.exception()  # Retrieve failures raised during write/cleanup.

    async def close(self):
        """Disarm the adapter when possible and release the serial connection."""
        if self.closed:
            return
        try:
            await self.command("DISARM", retries=0)
        except (LinkError, CommandError):
            pass  # MCU lease is the independent fallback.
        finally:
            try:
                self.loop.remove_reader(self.serial.fileno())
            finally:
                self.serial.close()
                self.closed = True
