"""Disposable web UI demo: no serial connection, MQTT broker, or saved live state."""

import argparse
import asyncio
import logging
import signal
import tempfile

from .config import parse
from .service import Service
from .simulator import Simulator
from .storage import Store
from .web import WebServer


class DemoRadio(Simulator):
    def __init__(self):
        """Seed demo switches with a mix of on and off outputs."""
        super().__init__()
        self.states.update({"46715621": 1, "46715623": 1})

    def report(self, device, suffix="0002", mask=3, bits=0, token=0):
        """Provide demo state reports while keeping the garden switch offline.

        @param device Reporting device identity.
        @param suffix Device suffix in wire-order hexadecimal.
        @param mask Outputs described by this report.
        @param bits Complete simulated output state.
        @param token Transaction token echoed by the report.
        """
        if device != "46715625":
            super().report(device, suffix, mask, bits, token)


async def demo(host, port, directory):
    """Serve a disposable interactive gateway demo until interrupted.

    @param host Loopback address on which to serve the UI.
    @param port HTTP listening port.
    @param directory Temporary directory for isolated demo state.
    """
    config = parse(
        {
            "network": {"gateway": "2a992b12", "tag": "a7"},
            "serial_port": "/dev/unused-demo-radio",
            "database": directory + "/demo.sqlite3",
            "mqtt": {"host": "127.0.0.1"},
            "devices": [
                {
                    "id": "46715621",
                    "name": "Living room lights",
                    "slot": 0,
                    "outputs": 2,
                },
                {"id": "46715622", "name": "Kitchen ceiling", "slot": 1, "outputs": 2},
                {"id": "46715623", "name": "Bedroom lamp", "slot": 2, "outputs": 1},
                {"id": "46715624", "name": "Hallway lights", "slot": 3, "outputs": 2},
                {"id": "46715625", "name": "Garden entrance", "slot": 4, "outputs": 1},
            ],
        }
    )
    store = Store(config.database, config.gateway + ":" + config.tag)
    service = Service(config, store, gateway_factory=DemoRadio)
    service.simulated = True
    server = WebServer(service, host, port)
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, service.stop.set)

    async def reports():
        """Keep demo availability fresh and expose a nearby pairing candidate."""
        while not service.stop.is_set():
            if service.ready:
                for device in list(service.devices.values()):
                    # One offline example, plus any device added by the demo user.
                    if device.id == "46715625":
                        continue
                    bits = service.gateway.states.get(
                        device.id, 1 if device.slot in (0, 2) else 0
                    )
                    service.gateway.report(device.id, device.suffix, device.mask, bits)
                service.discoveries["46715626"] = {
                    "device": "46715626",
                    "suffix": "0002",
                    "configured": "46715626" in service.devices,
                    "rssi": -53,
                    "lqi": 96,
                }
            await asyncio.sleep(1)

    task = report_task = None
    try:
        await server.start()
        task = asyncio.create_task(service.run())
        report_task = asyncio.create_task(reports())
        print(
            f"SIMULATED Livolo UI: http://{host}:{server.port} — Ctrl+C to stop",
            flush=True,
        )
        await task
    finally:
        service.stop.set()
        if report_task:
            report_task.cancel()
            await asyncio.gather(report_task, return_exceptions=True)
        if task and not task.done():
            await task
        await server.close()
        store.close()


def main():
    """Launch the local UI demo with temporary state and command-line settings."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--host", choices=("127.0.0.1", "localhost", "::1"), default="127.0.0.1"
    )
    parser.add_argument("--port", type=int, default=8080)
    args = parser.parse_args()
    logging.basicConfig(level=logging.WARNING)
    with tempfile.TemporaryDirectory(prefix="livolo-ui-demo-") as directory:
        asyncio.run(demo(args.host, args.port, directory))


if __name__ == "__main__":
    main()
