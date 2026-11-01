"""Exercise the production DMA consumer with adversarial producer snapshots."""

import ctypes as C
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SIZE = 8192


class Cursor(C.Structure):
    _fields_ = [
        (n, C.c_uint32)
        for n in (
            "before_us",
            "max_gap_us",
            "resyncs",
            "head",
            "tail",
            "pending",
            "highwater",
        )
    ]


class ReceiveTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        """Compile the production DMA cursor into a host shared library."""
        cls.tmp = tempfile.TemporaryDirectory()
        lib = Path(cls.tmp.name) / "rx.so"
        subprocess.run(
            [
                "cc",
                "-shared",
                "-fPIC",
                "-Wall",
                "-Wextra",
                "-Werror",
                "-std=c11",
                str(ROOT / "ec-uart-rx.c"),
                "-o",
                str(lib),
            ],
            check=True,
        )
        cls.lib = C.CDLL(str(lib))
        cls.lib.ec_uart_rx_init.argtypes = [C.POINTER(Cursor), C.c_uint32]
        cls.lib.ec_uart_rx_observe.argtypes = [
            C.POINTER(Cursor),
            C.c_uint,
            C.c_uint32,
            C.c_uint32,
            C.c_uint,
            C.c_int,
        ]
        cls.lib.ec_uart_rx_copy.argtypes = [
            C.POINTER(Cursor),
            C.POINTER(C.c_uint8),
            C.POINTER(C.c_uint8),
        ]
        cls.lib.ec_uart_rx_consume.argtypes = [C.POINTER(Cursor), C.c_uint]

    @classmethod
    def tearDownClass(cls):
        """Remove the temporary compiled test library."""
        cls.tmp.cleanup()

    def setUp(self):
        """Create a fresh cursor, producer ring and bounded copy buffer."""
        self.rx = Cursor()
        self.lib.ec_uart_rx_init(C.byref(self.rx), 0)
        self.ring = (C.c_uint8 * SIZE)(*[i % 251 for i in range(SIZE)])
        self.copy = (C.c_uint8 * 256)()

    def observe(self, head, before, after=None, baud=460800, fault=0):
        """Validate one producer snapshot against the production loss detector.

        @param head Producer offset within the DMA ring, in bytes.
        @param before Wrapping microsecond timestamp before reading the DMA pointer.
        @param after Timestamp after the read; None uses before.
        @param baud Configured UART bit rate, in bits per second.
        @param fault Nonzero to inject a sticky hardware error.
        @return One for a valid snapshot, zero when reception must resynchronize.
        """
        return self.lib.ec_uart_rx_observe(
            C.byref(self.rx),
            head,
            before,
            before if after is None else after,
            baud,
            fault,
        )

    def consume(self, n):
        """Commit a copied chunk after its producer snapshot was revalidated.

        @param n Number of validated bytes to consume.
        """
        self.lib.ec_uart_rx_consume(C.byref(self.rx), n)

    def test_partial_command_is_available_without_waiting_for_dma_block(self):
        """Partial DMA blocks are immediately available to the line parser."""
        self.assertEqual(self.observe(17, 500), 1)
        self.assertEqual(
            self.lib.ec_uart_rx_copy(C.byref(self.rx), self.ring, self.copy), 17
        )
        self.assertEqual(bytes(self.copy[:17]), bytes(range(17)))
        self.assertEqual(self.observe(21, 600), 1)
        self.consume(17)
        self.assertEqual(self.rx.pending, 4)

    def test_wrap_preserves_order_and_bounded_copy(self):
        """Ring wrap preserves byte order and the 256-byte copy bound."""
        self.observe(SIZE - 10, 100000)
        self.consume(SIZE - 10)
        self.assertEqual(self.observe(300, 110000), 1)
        self.assertEqual(
            self.lib.ec_uart_rx_copy(C.byref(self.rx), self.ring, self.copy), 256
        )
        expected = bytes(self.ring[SIZE - 10 :]) + bytes(self.ring[:246])
        self.assertEqual(bytes(self.copy), expected)
        self.consume(256)
        self.assertEqual((self.rx.tail, self.rx.pending), (246, 54))

    def test_dma_overtaking_unconsumed_data_invalidates_staged_copy(self):
        """Producer overwrite discards staged data and resynchronizes the cursor."""
        self.observe(8100, 100000)
        self.lib.ec_uart_rx_copy(C.byref(self.rx), self.ring, self.copy)
        self.assertEqual(self.observe(20, 105000), 0)
        self.assertEqual((self.rx.tail, self.rx.pending, self.rx.resyncs), (20, 0, 1))
        self.assertEqual(self.observe(25, 105200), 1)
        self.assertEqual(self.rx.pending, 5)

    def test_full_invisible_lap_and_long_copy_fail_closed(self):
        """A possible unseen DMA lap invalidates copied bytes at every baud rate."""
        for baud in (115200, 230400, 460800, 921600):
            with self.subTest(baud=baud):
                self.setUp()
                self.observe(10, 100)
                self.lib.ec_uart_rx_copy(C.byref(self.rx), self.ring, self.copy)
                limit = (SIZE - 16) * 9500000 // baud
                self.assertEqual(self.observe(10, 100 + limit, baud=baud), 0)
                self.assertEqual(self.rx.pending, 0)

    def test_clock_bracket_includes_preemption_during_pointer_read(self):
        """Preemption inside the pointer-read bracket contributes to the loss bound."""
        self.observe(10, 100, 1000)
        limit = (SIZE - 16) * 9500000 // 460800
        self.assertEqual(self.observe(20, 2000, 100 + limit), 0)

    def test_hardware_error_flushes_even_with_no_new_byte(self):
        """A hardware error flushes pending data even without producer movement."""
        self.observe(50, 1100)
        self.assertEqual(self.observe(50, 1200, fault=1), 0)
        self.assertEqual((self.rx.tail, self.rx.pending), (50, 0))

    def test_timer_wrap_is_not_loss(self):
        """Microsecond timer wrap alone does not signal reception loss."""
        self.lib.ec_uart_rx_init(C.byref(self.rx), 0xFFFFFFF0)
        self.assertEqual(self.observe(1, 30), 1)
        self.assertEqual(self.rx.max_gap_us, 46)

    def test_many_laps_with_radio_sized_pauses_preserve_every_byte(self):
        """Repeated radio-sized pauses preserve every byte across many ring laps."""
        produced = consumed = now = 0
        while produced < 100000:
            # 20 ms of RX at 460800, longer than the usual radio call.
            count = min(921, 100000 - produced)
            for i in range(count):
                self.ring[(produced + i) % SIZE] = (produced + i) % 251
            produced += count
            now += 20000
            self.assertEqual(self.observe(produced % SIZE, now), 1)
            while self.rx.pending:
                n = self.lib.ec_uart_rx_copy(C.byref(self.rx), self.ring, self.copy)
                self.assertEqual(self.observe(produced % SIZE, now + 10), 1)
                self.assertEqual(
                    bytes(self.copy[:n]), bytes((consumed + i) % 251 for i in range(n))
                )
                self.consume(n)
                consumed += n
                now += 10
        self.assertEqual(consumed, produced)
        self.assertEqual(self.rx.resyncs, 0)

    def test_exact_capacity_is_overflow_not_empty(self):
        """A full ring is detected as overflow rather than mistaken for empty."""
        self.observe(8191, 100000)
        self.assertEqual(self.observe(0, 100100), 0)


if __name__ == "__main__":
    unittest.main()
