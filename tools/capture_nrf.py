#!/usr/bin/env python3
"""Run a bounded, receive-only capture using Nordic's official sniffer script.

Requires pySerial for the upstream script and editcap for pcapng conversion.
Keeps the raw pcap and sniffer log alongside the requested pcapng file.
"""

import argparse
from pathlib import Path
import shutil
import signal
import subprocess
import sys


def main():
    """Run a bounded Nordic radio capture with the requested output path."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--device", required=True, help="Sniffer serial port or /dev/serial/by-id path"
    )
    parser.add_argument(
        "--sniffer", required=True, type=Path, help="Official nrf802154_sniffer.py path"
    )
    parser.add_argument("--channel", type=int, choices=range(11, 27), default=11)
    parser.add_argument(
        "--duration", type=float, default=30, help="Capture duration in seconds"
    )
    parser.add_argument(
        "--output", type=Path, required=True, help="New .pcapng output path"
    )
    args = parser.parse_args()
    if not 0 < args.duration <= 3600:
        parser.error("Duration must be greater than zero and at most 3600 seconds")
    if not args.sniffer.is_file() or not Path(args.device).exists():
        parser.error("Sniffer script and serial device must exist")
    if args.output.suffix != ".pcapng":
        parser.error("Output must have a .pcapng suffix")
    if not shutil.which("editcap"):
        parser.error("editcap is required (Debian package wireshark-common)")
    raw = args.output.with_suffix(".pcap")
    log = args.output.with_suffix(".sniffer.log")
    if any(p.exists() for p in (args.output, raw, log)):
        parser.error("Output files already exist; choose a new name")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    command = [
        sys.executable,
        str(args.sniffer.resolve()),
        "--capture",
        "--extcap-interface",
        args.device,
        "--channel",
        str(args.channel),
        "--metadata",
        "ieee802154-tap",
        "--fifo",
        str(raw.resolve()),
    ]
    with log.open("x") as output:
        process = subprocess.Popen(command, stdout=output, stderr=subprocess.STDOUT)
        stopped = False
        try:
            process.wait(timeout=args.duration)
        except (subprocess.TimeoutExpired, KeyboardInterrupt):
            stopped = True
        finally:
            if process.poll() is None:
                # Nordic's SIGTERM handler stops its reader and puts the radio to sleep.
                process.send_signal(signal.SIGTERM)
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
                    raise SystemExit(f"Sniffer did not stop cleanly; inspect {log}")
        if process.returncode != 0:
            raise SystemExit(f"Sniffer exited with {process.returncode}; inspect {log}")
        if not stopped:
            raise SystemExit(
                f"Sniffer exited before capture duration elapsed; inspect {log}"
            )
    subprocess.run(["editcap", "-F", "pcapng", str(raw), str(args.output)], check=True)
    print(f"Saved {args.output}; raw data: {raw}; diagnostics: {log}")


if __name__ == "__main__":
    main()
