#!/usr/bin/env python3
"""Bounded v3 daemon restore/lease/RX test. Never pairs or changes outputs.

Run with PYTHONPATH=host. The database is temporary; no system service is installed.
Optional --query-device sends only the previously observed 10/ac read-only queries.
The test always stops the service and verifies DISARM afterward.
"""
import argparse
import asyncio
from dataclasses import replace
import json
import logging
from pathlib import Path
import tempfile
import time

from livolod.config import load
from livolod.protocol import decode, frame, key_values
from livolod.service import Service
from livolod.storage import Store
from livolod.uart import Gateway


async def check(args):
    """Verify daemon restoration, reception and disarm using a temporary database.

    @param args Parsed configuration, output, duration and optional query-device options.
    """
    transcript = []
    events = []

    class AuditedGateway(Gateway):
        async def command(self, text, retries=1):
            """Record diagnostic traffic and restrict raw SENDs to heartbeat or query opcodes.

            @param text UART command to validate and send.
            @param retries Additional attempts using the same UART request ID.
            @return The successful adapter response, also recorded in the transcript.
            """
            if text.startswith("SEND "):
                packet = decode(bytes.fromhex(text.split()[-1]))
                if packet.opcode not in (8, 0x10, 0xAC):
                    raise RuntimeError(
                        "Hardware check refused a non-heartbeat/query frame"
                    )
            record = {"time": time.time(), "command": text}
            transcript.append(record)
            try:
                result = await super().command(text, retries)
                record["result"] = result
                return result
            except Exception as error:
                record["error"] = str(error)
                raise

    class Recorder:
        def publish(self, topic, value, retain=False):
            """Record daemon publications as hardware acceptance evidence.

            @param topic Published topic suffix.
            @param value Published payload.
            @param retain Broker retention flag, accepted for interface compatibility.
            """
            events.append({"time": time.time(), "topic": topic, "value": value})

    with tempfile.TemporaryDirectory(prefix="livolod-hardware-") as directory:
        config = replace(load(args.config), database=directory + "/state.db")
        store = Store(config.database, config.gateway + ":" + config.tag)
        service = Service(
            config, store, Recorder(), lambda: AuditedGateway.open(config.serial_port)
        )
        task = asyncio.create_task(service.run())
        try:

            async def ready():
                """Wait for radio restoration or propagate an early service failure."""
                while not service.ready:
                    if task.done():
                        task.result()
                    await asyncio.sleep(0.05)

            await asyncio.wait_for(ready(), 15)
            if args.query_device:
                device = service.devices[args.query_device]
                for opcode in (0x10, 0xAC):
                    await service.transmit(
                        frame(
                            config.gateway,
                            config.tag,
                            device.id,
                            device.suffix,
                            service.next_sequence(),
                            opcode,
                            bytes(7) + b"\x01",
                        )
                    )
                    await asyncio.sleep(0.25)
            await asyncio.sleep(args.seconds)
            if task.done():
                task.result()
            status = key_values(await service.gateway.command("STATUS"))
            service.check_status(status)
            result = {"adapter_before_stop": status, "snapshot": service.api_snapshot()}
        finally:
            service.stop.set()
            await asyncio.wait_for(task, 8)
            store.close()
        gateway = AuditedGateway.open(config.serial_port)
        try:
            final = key_values(await gateway.command("STATUS"))
            if final["armed"] != "0" or final["ack"] != "0":
                raise RuntimeError("Adapter did not disarm on shutdown")
            result["adapter_after_stop"] = final
        finally:
            await gateway.close()
        result.update(transcript=transcript, events=events, output_commands_sent=0)
        Path(args.output).write_text(json.dumps(result, indent=2) + "\n")
        print(
            json.dumps(
                {
                    "ready": True,
                    "peers": status["peers"],
                    "states": service.states,
                    "disarmed_after_stop": True,
                    "output_commands_sent": 0,
                    "output": args.output,
                }
            )
        )


def main():
    """Parse hardware acceptance options and run the daemon restoration trial."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--seconds", type=int, default=25)
    parser.add_argument("--query-device")
    args = parser.parse_args()
    if not 10 <= args.seconds <= 120 or Path(args.output).exists():
        parser.error("Use 10..120 seconds and a new output path")
    logging.basicConfig(level=logging.INFO)
    asyncio.run(check(args))


if __name__ == "__main__":
    main()
