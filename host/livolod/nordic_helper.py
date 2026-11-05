"""Pinned Nordic serial DFU wrapper with all USB discovery/triggering disabled.

Runs only in the separate Python 3.10 runtime. The parent verifies the exact
factory-DFU endpoint and acknowledges the flash boundary before invoking this.
"""

import argparse
import importlib.metadata
import sys


def main():
    """Transfer to the fixed node using the pinned vendor transport without discovery."""
    if (
        sys.version_info[:2] != (3, 10)
        or importlib.metadata.version("nrfutil") != "6.1.7"
    ):
        raise RuntimeError("Unsupported isolated Nordic runtime")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package", required=True)
    parser.add_argument("--port", required=True)
    args = parser.parse_args()
    from nordicsemi.dfu.dfu import Dfu
    from nordicsemi.dfu.dfu_transport_serial import DfuTransportSerial

    class FixedTransport(DfuTransportSerial):
        def _DfuTransportSerial__ensure_bootloader(self):
            """Disable vendor USB discovery and automatic port substitution entirely."""
            return

    transport = FixedTransport(
        com_port=args.port,
        baud_rate=115200,
        flow_control=False,
        prn=0,
        do_ping=False,
        timeout=20,
    )
    try:
        Dfu(
            zip_file_path=args.package, dfu_transport=transport, connect_delay=0
        ).dfu_send_images()
    finally:
        if transport.serial_port is not None:
            transport.close()


if __name__ == "__main__":
    main()
