#!/usr/bin/env python3
"""Package a bounded MG21 Livolo application for its existing Gecko bootloader.

Creates only uncompressed application tags, never bootloader/SE/token writes.
This is an unsigned recovery image, not the TI signed UART update format.
"""

import argparse
import binascii
import hashlib
import json
from pathlib import Path
import struct

APP_BASE = 0x4000
APP_LIMIT = 0x30000
MAGIC = bytes.fromhex("13b779fac925ddb7adf3cfe0f1b614b8")
PRODUCT = b"LIVOLO-MG21".ljust(16, b"\0")
BOARD_MARKER = b"EC-MG21-BOARD="
BOARDS = ("zb-gw04-1v2", "sonoff-zbdongle-e")


def board_name(image):
    """Read the compiled board marker without guessing from the output filename.

    @param image Application binary; older images may have no explicit marker.
    @return Supported board name, or unspecified for a legacy MG21 image.
    """
    if BOARD_MARKER not in image:
        return "mg21-unspecified"
    names = {
        bytes(part.split(b" ", 1)[0].split(b"\0", 1)[0])
        for part in image.split(BOARD_MARKER)[1:]
    }
    if len(names) != 1 or next(iter(names)) not in {b.encode() for b in BOARDS}:
        raise ValueError("Unknown or conflicting MG21 board markers")
    return next(iter(names)).decode("ascii")


def tag(identifier, data):
    """Encode a Gecko GBL TLV.

    @param identifier Silicon Labs 32-bit tag ID.
    @param data Tag payload bytes.
    @return Little-endian tag header followed by its payload.
    """
    return struct.pack("<II", identifier, len(data)) + data


def package(image):
    """Validate an MG21 application and construct an application-only GBL.

    @param image Binary linked at APP_BASE with Gecko application properties.
    @return GBL bytes; raises ValueError for an invalid vector/layout/identity.
    """
    if not 256 <= len(image) <= APP_LIMIT - APP_BASE or len(image) % 4:
        raise ValueError("Application must be aligned and fit 0x4000..0x30000")
    sp, pc = struct.unpack_from("<II", image)
    if not 0x20000000 < sp <= 0x20010000 or sp % 8:
        raise ValueError("Invalid MG21 stack pointer")
    if not pc & 1 or not APP_BASE + 8 <= pc - 1 < APP_BASE + len(image):
        raise ValueError("Invalid MG21 reset vector")
    properties = struct.unpack_from("<I", image, 13 * 4)[0] - APP_BASE
    if properties % 4 or not 0 <= properties <= len(image) - 56:
        raise ValueError("Application properties must reside inside application flash")
    if image[properties : properties + 16] != MAGIC:
        raise ValueError("Gecko application properties magic is missing")
    version, signature = struct.unpack_from("<II", image, properties + 16)
    if version & 0xFF != 1 or signature != 0:
        raise ValueError("Only unsigned version-1 application properties supported")
    app = image[properties + 28 : properties + 56]
    if struct.unpack_from("<I", app)[0] != 4 or app[12:] != PRODUCT:
        raise ValueError("Image is not the Livolo MG21 application")
    if b"EC-FW-VERSION=" not in image:
        raise ValueError("Livolo release marker is missing")
    board_name(image)
    gbl = tag(0x03A617EB, struct.pack("<II", 0x03000000, 0))
    gbl += tag(0xF40A0AF4, app)
    gbl += tag(0xFE0101FE, struct.pack("<I", APP_BASE) + image)
    gbl += struct.pack("<II", 0xFC0404FC, 4)
    gbl += struct.pack("<I", binascii.crc32(gbl))
    if binascii.crc32(gbl) != 0x2144DF1C:
        raise ValueError("GBL CRC residue mismatch")
    return gbl


def main():
    """Write a new application-only GBL and print reproducible image metadata."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("image", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    image = args.image.read_bytes()
    gbl = package(image)
    with args.output.open("xb") as stream:
        stream.write(gbl)
    print(
        json.dumps(
            {
                "board": board_name(image),
                "address": APP_BASE,
                "image_size": len(image),
                "gbl_size": len(gbl),
                "image_sha256": hashlib.sha256(image).hexdigest(),
                "gbl_sha256": hashlib.sha256(gbl).hexdigest(),
                "signed": False,
                "bootloader_included": False,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
