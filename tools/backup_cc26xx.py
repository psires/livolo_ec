#!/usr/bin/env python3
"""Back up the identified 352-KiB CC26x2 module through its ROM bootloader.

Read-only radio operations: ping, memory read, status, chip ID and CRC32.
Requires an already synchronized bootloader at the specified baud rate.
No reset, erase, download, memory write or configuration commands are issued.
"""

import argparse
import binascii
import hashlib
import json
from pathlib import Path
import sys


def main():
    """Back up the attached CC2652P flash to a new file and verify its identity."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", required=True)
    parser.add_argument("--baud", type=int, default=115200)
    parser.add_argument("--bsl-source", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    metadata_path = args.output.with_suffix(".json")
    if args.output.exists() or metadata_path.exists():
        parser.error("Refusing to overwrite an existing backup or metadata file")
    sys.path.insert(0, str(args.bsl_source.resolve()))
    import serial
    from cc2538_bsl.cc2538_bsl import CommandInterface, CmdException

    cmd = CommandInterface()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    try:
        with serial.Serial(
            args.port, args.baud, timeout=1, write_timeout=1, exclusive=True
        ) as port:
            cmd.sp = port
            if cmd.cmdPing() != 1:
                raise CmdException(
                    "Bootloader is not already synchronized; run the identity probe first"
                )
            chip_id = cmd.cmdGetChipId()
            registers = {}
            for name, address in {
                "ICEPICK_DEVICE_ID": 0x50001318,
                "FCFG_USER_ID": 0x50001294,
                "FLASH_SIZE": 0x4003002C,
                "MISC_CONF_1": 0x500010A0,
                "IEEE_ADDRESS_LOW": 0x500012F0,
                "IEEE_ADDRESS_HIGH": 0x500012F4,
            }.items():
                data = bytes(cmd.cmdMemReadCC26xx(address))
                if len(data) != 4:
                    raise CmdException(f"Short register read: {name}")
                registers[name] = f"0x{int.from_bytes(data, 'little'):08x}"
            wafer = (int(registers["ICEPICK_DEVICE_ID"], 16) >> 12) & 0xFFFF
            sectors = int(registers["FLASH_SIZE"], 16) & 0xFF
            if wafer != 0xBB41 or sectors != 44:
                raise CmdException(
                    "This backup tool is restricted to the identified 352-KiB CC26x2 layout"
                )
            size = sectors * 8192
            image = bytearray()
            for address in range(0, size, 252):
                words = min(252, size - address) // 4
                # TI COMMAND_MEMORY_READ: address in big endian, width=32 bits,
                # up to 63 words. Response bytes retain flash memory order.
                body = bytes([0x2A]) + address.to_bytes(4, "big") + bytes([1, words])
                cmd._write(bytes([len(body) + 2, sum(body) & 255]) + body)
                if cmd._wait_for_ack("Read backup block", 2) != 1:
                    raise CmdException(f"Read rejected at 0x{address:x}")
                block = bytes(cmd.receivePacket())
                if len(block) != words * 4 or cmd.checkLastCmd() != 1:
                    raise CmdException(f"Invalid read at 0x{address:x}")
                image.extend(block)
                if address // 65536 != (address + len(block)) // 65536:
                    print(f"Read {len(image)}/{size} bytes", flush=True)
            local_crc = binascii.crc32(image) & 0xFFFFFFFF
            device_crc = cmd.cmdCRC32CC26xx(0, size)
            if local_crc != device_crc:
                raise CmdException(
                    f"Backup CRC mismatch: host {local_crc:08x}, device {device_crc:08x}"
                )
            metadata = {
                "port": args.port,
                "baud": args.baud,
                "bootloader_chip_id": f"0x{chip_id:04x}",
                "wafer_id": f"0x{wafer:04x}",
                "registers": registers,
                "flash_bytes": size,
                "sector_bytes": 8192,
                "sha256": hashlib.sha256(image).hexdigest(),
                "host_crc32": f"{local_crc:08x}",
                "device_crc32": f"{device_crc:08x}",
                "crc_verified": True,
                "initial_sp": f"0x{int.from_bytes(image[:4], 'little'):08x}",
                "reset_vector": f"0x{int.from_bytes(image[4:8], 'little'):08x}",
                "ccfg_bl_config": f"0x{int.from_bytes(image[0x57fd8:0x57fdc], 'little'):08x}",
            }
            with args.output.open("xb") as output:
                output.write(image)
            with metadata_path.open("x") as output:
                output.write(json.dumps(metadata, indent=2) + "\n")
            print(json.dumps(metadata, indent=2))
    except (serial.SerialException, CmdException) as error:
        raise SystemExit(f"Read-only backup failed: {error}")


if __name__ == "__main__":
    main()
