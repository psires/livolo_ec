import unittest
import io
import json
import os
from pathlib import Path
import tempfile
from unittest.mock import patch
from flash_cc26xx import SIZE, BL_OFFSET, main, read_image, validate_image


class FirmwareGuardTests(unittest.TestCase):
    def image(self):
        """Create a full-size flash image with valid vectors and recovery configuration."""
        data = bytearray(b"\xff" * SIZE)
        data[:4] = (0x20014000).to_bytes(4, "little")
        data[4:8] = (0x101).to_bytes(4, "little")
        data[BL_OFFSET : BL_OFFSET + 4] = (0xC5FE0FC5).to_bytes(4, "little")
        data[0x57FE6] = 0xC5
        return data

    def test_valid_layout(self):
        """Verify valid layout."""
        self.assertEqual(validate_image(self.image())["bytes"], SIZE)

    def test_rejects_p7_layout(self):
        """Verify rejects p7 layout."""
        with self.assertRaises(ValueError):
            validate_image(self.image() + self.image())

    def test_rejects_bad_vectors(self):
        """Verify rejects bad vectors."""
        for offset, value in [(0, 0x20020000), (4, 0x100), (4, 0x60001)]:
            image = self.image()
            image[offset : offset + 4] = value.to_bytes(4, "little")
            with (
                self.subTest(offset=offset, value=value),
                self.assertRaises(ValueError),
            ):
                validate_image(image)

    def test_rejects_locked_recovery(self):
        """Verify rejects locked recovery."""
        for offset in [
            BL_OFFSET,
            BL_OFFSET + 1,
            BL_OFFSET + 3,
            0x57FE6,
            0x57FDC,
            0x57FDD,
        ]:
            image = self.image()
            image[offset] = 0
            with self.subTest(offset=offset), self.assertRaises(ValueError):
                validate_image(image)

    def test_check_only_needs_no_port_or_bootloader_dependency(self):
        """Offline bootstrap checks stop before importing any flashing dependency."""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "factory.bin"
            path.write_bytes(self.image())
            output = io.StringIO()
            with patch("sys.stdout", output):
                main([str(path), "--check-only"])
            self.assertEqual(json.loads(output.getvalue())["bytes"], SIZE)
            with self.assertRaises(ValueError):
                main([str(path), "--check-only", "--sha256", "0" * 64])

    def test_full_image_reader_refuses_fifo_without_waiting_for_writer(self):
        """Image validation cannot hang on an accidentally selected named pipe."""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "pipe"
            os.mkfifo(path)
            with self.assertRaises(ValueError):
                read_image(path)


if __name__ == "__main__":
    unittest.main()
