#!/usr/bin/env python3
"""Read CC13xx/CC26xx ROM bootloader identity; never erase, write or reset.

Requires pySerial and a checkout of JelmerT/cc2538-bsl. Enter the module's
bootloader manually first if it does not start automatically with blank flash.
Raw register values are retained because upstream chip marketing names can be
inaccurate for newer CC26x2 variants.
"""

import argparse
import json
from pathlib import Path
import sys


def main():
    """Read ROM bootloader identity and hardware registers from the selected module."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", required=True)
    parser.add_argument("--baud", type=int, default=115200)
    parser.add_argument("--bsl-source", required=True, type=Path)
    parser.add_argument(
        "--already-synced",
        action="store_true",
        help="Use ping instead of autobaud after a successful probe in the same boot session",
    )
    args = parser.parse_args()
    sys.path.insert(0, str(args.bsl_source.resolve()))
    import serial
    from cc2538_bsl.cc2538_bsl import CommandInterface, CmdException

    interface = CommandInterface()
    try:
        with serial.Serial(
            args.port,
            args.baud,
            timeout=1,
            write_timeout=1,
            exclusive=True,
            rtscts=False,
            dsrdtr=False,
        ) as port:
            interface.sp = port
            response = (
                interface.cmdPing() if args.already_synced else interface.sendSynch()
            )
            if response != 1:
                raise CmdException("No bootloader acknowledgment")
            chip_id = interface.cmdGetChipId()
            registers = {}
            for name, address in {
                "ICEPICK_DEVICE_ID": 0x50001318,
                "FCFG_USER_ID": 0x50001294,
                "FLASH_SIZE": 0x4003002C,
                "MISC_CONF_1": 0x500010A0,
                "IEEE_ADDRESS_LOW": 0x500012F0,
                "IEEE_ADDRESS_HIGH": 0x500012F4,
            }.items():
                raw = bytes(interface.cmdMemReadCC26xx(address))
                if len(raw) != 4:
                    raise CmdException(f"Unexpected register length for {name}")
                registers[name] = {
                    "address": f"0x{address:08x}",
                    "bytes": raw.hex(),
                    "value": f"0x{int.from_bytes(raw, 'little'):08x}",
                }
            device_id = int(registers["ICEPICK_DEVICE_ID"]["value"], 16)
            print(
                json.dumps(
                    {
                        "port": args.port,
                        "baud": args.baud,
                        "bootloader_chip_id": f"0x{chip_id:04x}",
                        "wafer_id": f"0x{(device_id >> 12) & 0xffff:04x}",
                        "registers": registers,
                    },
                    indent=2,
                )
            )
    except (serial.SerialException, CmdException) as error:
        raise SystemExit(f"Read-only probe failed: {error}")


if __name__ == "__main__":
    main()
