#!/usr/bin/env python3
"""Flash and verify a complete 352-KiB CC2652P image, then optionally reset.

Requires an already synchronized ROM bootloader. Never autodetects a port.
Refuses images that disable DIO15 active-low bootloader recovery or JTAG.
"""
import argparse
import binascii
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys

SIZE = 352 * 1024
BL_OFFSET = 0x57FD8


def read_image(path):
    """Read one full image without blocking on special files or following a file symlink.

    @param path Operator-selected full-image file path.
    @return Immutable complete flash bytes, bounded to the supported chip size.
    """
    fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_NOFOLLOW", 0))
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size != SIZE:
            raise ValueError("Expected a regular full-size CC2652P image")
        image = stream.read(SIZE + 1)
        if len(image) != SIZE:
            raise ValueError("Full image changed while being read")
    return image


def validate_image(image):
    """Validate a complete CC2652P image while preserving recovery access.

    @param image 352-KiB flash image bytes including the CCFG region.
    @return Image size, SHA-256, CRC32, and verified bootloader configuration.
    """
    if len(image) != SIZE:
        raise ValueError(f"Expected {SIZE} bytes including CCFG, got {len(image)}")
    sp = int.from_bytes(image[:4], "little")
    pc = int.from_bytes(image[4:8], "little")
    if not (0x20000000 < sp <= 0x20014000 and sp % 8 == 0):
        raise ValueError("Initial stack pointer is outside the 80-KiB SRAM")
    if not (pc & 1 and 8 <= (pc & ~1) < 0x57FA8):
        raise ValueError("Reset vector is not a Thumb address in application flash")
    bl = int.from_bytes(image[BL_OFFSET : BL_OFFSET + 4], "little")
    if bl != 0xC5FE0FC5:
        raise ValueError(f"DIO15 bootloader configuration changed: 0x{bl:08x}")
    # CPU DAP and chip erase must remain available.
    erase = int.from_bytes(image[0x57FDC:0x57FE0], "little")
    if image[0x57FE6] != 0xC5 or erase & 0x101 != 0x101:
        raise ValueError("JTAG CPU access or chip erase disabled")
    return {
        "bytes": len(image),
        "sha256": hashlib.sha256(image).hexdigest(),
        "crc32": f"{binascii.crc32(image) & 0xffffffff:08x}",
        "ccfg_bl_config": f"0x{bl:08x}",
    }


def main(argv=None):
    """Validate, flash and verify the selected image, optionally resetting the board.

    @param argv Optional CLI arguments for offline testing; None uses process arguments.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("image", type=Path)
    parser.add_argument("--port")
    parser.add_argument("--bsl-source", type=Path)
    parser.add_argument("--reset", action="store_true")
    parser.add_argument(
        "--check-only",
        action="store_true",
        help="Validate without opening a serial port",
    )
    parser.add_argument(
        "--sha256", help="Expected full-image SHA-256 from a trusted source"
    )
    args = parser.parse_args(argv)
    if args.check_only and args.reset:
        parser.error("--check-only cannot request --reset")
    if not args.check_only and (not args.port or not args.bsl_source):
        parser.error("Flashing requires --port and --bsl-source")
    image = read_image(args.image)
    metadata = validate_image(image)
    if args.sha256 is not None and (
        not re.fullmatch(r"[0-9a-f]{64}", args.sha256)
        or args.sha256 != metadata["sha256"]
    ):
        raise ValueError("Full-image SHA-256 does not match the expected artifact")
    print(json.dumps(metadata), flush=True)
    if args.check_only:
        return
    sys.path.insert(0, str(args.bsl_source.resolve()))
    import serial
    from cc2538_bsl.cc2538_bsl import CommandInterface

    cmd = CommandInterface()
    with serial.Serial(
        args.port, 115200, timeout=2, write_timeout=2, exclusive=True
    ) as port:
        cmd.sp = port
        if cmd.cmdPing() != 1:
            raise RuntimeError("ROM bootloader is not synchronized")
        # Read a hardware register for silicon and recovery-configuration checks.
        # @param addr Byte address of the memory-mapped 32-bit register.
        # @return Register value as an unsigned little-endian integer.
        read = lambda addr: int.from_bytes(bytes(cmd.cmdMemReadCC26xx(addr)), "little")
        if (read(0x50001318) >> 12) & 0xFFFF != 0xBB41 or read(0x4003002C) & 255 != 44:
            raise RuntimeError("Unexpected silicon or flash size")
        if read(0x50001294) != 0x3202F000:
            raise RuntimeError(
                "Factory chip configuration differs from verified CC2652P"
            )
        print("Erasing main flash", flush=True)
        if cmd.cmdBankErase() != 1:
            raise RuntimeError("Erase failed")
        # Program each nonblank block with explicit address/status checks.
        # CCFG is programmed last. Any interruption leaves ROM recovery available.
        for address in range(0, SIZE, 248):
            block = image[address : address + 248]
            if block == b"\xff" * len(block):
                continue
            if cmd.cmdDownload(address, len(block)) != 1 or cmd.cmdSendData(block) != 1:
                raise RuntimeError(
                    f"Programming failed at 0x{address:x}; leaving bootloader running"
                )
        crc = cmd.cmdCRC32CC26xx(0, SIZE)
        if crc != int(metadata["crc32"], 16):
            raise RuntimeError(f"Full-image CRC mismatch: {crc!r}; not resetting")
        if read(BL_OFFSET) != 0xC5FE0FC5:
            raise RuntimeError("Bootloader configuration readback mismatch")
        print(
            f"Verified full flash CRC32 {crc:08x} and bootloader configuration",
            flush=True,
        )
        if args.reset:
            if cmd.cmdReset() != 1:
                raise RuntimeError("Reset was not acknowledged")
            print("Reset acknowledged; application starting", flush=True)
            port.timeout = 0.2
            import time

            deadline = time.monotonic() + 8
            while time.monotonic() < deadline:
                line = port.readline()
                if line:
                    print(line.decode("ascii", errors="replace").rstrip(), flush=True)


if __name__ == "__main__":
    main()
