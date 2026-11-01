"""Reject wrong-target recovery images before opening a serial port."""

import binascii
import struct
import unittest

from mg21_gbl import APP_BASE, APP_LIMIT, MAGIC, PRODUCT, board_name, package


class GblTests(unittest.TestCase):
    def image(self):
        """Return a minimal flash-resident MG21 application fixture."""
        image = bytearray(512)
        struct.pack_into("<II", image, 0, 0x20010000, APP_BASE + 257)
        struct.pack_into("<I", image, 52, APP_BASE + 128)
        image[128:144] = MAGIC
        struct.pack_into("<III", image, 144, 0x201, 0, 0)
        struct.pack_into("<III", image, 156, 4, 2, 0)
        image[168:184] = PRODUCT
        marker = b"EC-FW-VERSION=0.2\0"
        image[300 : 300 + len(marker)] = marker
        return image

    def test_application_only(self):
        """Emit only the four permitted tags and a valid complete-file CRC."""
        image = self.image()
        gbl = package(image)
        tags = []
        offset = 0
        while offset < len(gbl):
            identifier, size = struct.unpack_from("<II", gbl, offset)
            tags.append(identifier)
            offset += 8 + size
        self.assertEqual(tags, [0x03A617EB, 0xF40A0AF4, 0xFE0101FE, 0xFC0404FC])
        self.assertEqual(binascii.crc32(gbl), 0x2144DF1C)
        self.assertEqual(offset, len(gbl))

    def test_invalid_targets(self):
        """Reject TI vectors, RAM properties, wrong products and oversized images."""
        for offset, value in [
            (0, 0x20014000),
            (4, 0x8001),
            (52, 0x20000100),
            (144, 2),
            (148, 1),
            (156, 1),
            (168, 0),
        ]:
            with self.subTest(offset=offset):
                image = self.image()
                struct.pack_into("<I", image, offset, value)
                with self.assertRaises(ValueError):
                    package(image)
        with self.assertRaises(ValueError):
            package(bytes(APP_LIMIT - APP_BASE + 4))

    def test_board_identity(self):
        """Identify both compiled boards and reject ambiguous or unknown markers."""
        self.assertEqual(board_name(self.image()), "mg21-unspecified")
        for name in ("zb-gw04-1v2", "sonoff-zbdongle-e"):
            image = self.image()
            marker = ("EC-MG21-BOARD=" + name + " part=%08lx\0").encode()
            image[350 : 350 + len(marker)] = marker
            self.assertEqual(board_name(image), name)
            package(image)
        for marker in (
            b"EC-MG21-BOARD=unknown\0",
            b"EC-MG21-BOARD=zb-gw04-1v2\0EC-MG21-BOARD=sonoff-zbdongle-e\0",
        ):
            image = self.image()
            image[350 : 350 + len(marker)] = marker
            with self.assertRaises(ValueError):
                package(image)
