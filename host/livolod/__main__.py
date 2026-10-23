import argparse
import asyncio
from dataclasses import replace
import logging
import signal

from .config import integer, load
from . import __version__
from .mqtt import Broker
from .service import Service
from .simulator import Simulator
from .storage import Store


async def run(config, simulate=False):
    """Run the daemon until shutdown and release its database and connections.

    @param config Validated daemon configuration.
    @param simulate Whether to use a simulated radio and isolated state and topics.
    """
    if simulate:
        config = replace(config, database=config.database + ".simulated")
        logging.warning(
            "SIMULATION: isolated topics/database, no serial port will be opened"
        )
    store = Store(config.database, config.gateway + ":" + config.tag)
    service = None
    web = None
    try:
        service = Service(
            config,
            store,
            gateway_factory=Simulator if simulate else None,
            simulated=simulate,
        )
        service.simulated = simulate
        service.mqtt_factory = None if simulate else Broker
        service.broker = Broker(service.config.mqtt, service.receive, service.snapshot)
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, service.stop.set)
        if config.web["enabled"]:
            from .web import start_web

            web = await start_web(service, config)
        service.broker.start()
        await service.run()
    finally:
        if web:
            await web.close()
        if service and service.broker:
            await service.broker.close()
        store.close()


def main():
    """Validate command-line options and launch the daemon or configuration check."""
    parser = argparse.ArgumentParser(description="Livolo EC MQTT daemon (UART v3)")
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument("--config", required=True)
    parser.add_argument(
        "--check-config",
        action="store_true",
        help="Validate and exit without opening UART/MQTT/database",
    )
    parser.add_argument(
        "--simulate",
        action="store_true",
        help="Use a simulated radio and isolated MQTT prefix/database",
    )
    parser.add_argument(
        "--web-host",
        help="Enable web UI on this host (non-loopback requires configured token_env)",
    )
    parser.add_argument("--web-port", type=int, help="Enable web UI on this port")
    parser.add_argument(
        "--log-level", choices=("DEBUG", "INFO", "WARNING", "ERROR"), default="INFO"
    )
    args = parser.parse_args()
    logging.basicConfig(
        level=args.log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    try:
        config = load(args.config)
        if args.web_host is not None or args.web_port is not None:
            web = dict(config.web, enabled=True)
            if args.web_host is not None:
                web["host"] = args.web_host
            if args.web_port is not None:
                web["port"] = integer(args.web_port, 1, 65535, "web port")
            if web["host"] not in ("127.0.0.1", "::1", "localhost") and not web.get(
                "token_env"
            ):
                raise ValueError(
                    "A non-loopback web host requires configured web.token_env"
                )
            config = replace(config, web=web)
        if args.check_config:
            print(
                f"Configuration valid: gateway={config.gateway}, peers={len(config.devices)}"
            )
            return
        asyncio.run(run(config, args.simulate))
    except (ValueError, KeyError, OSError, RuntimeError) as error:
        parser.exit(1, f"livolod: {error}\n")


if __name__ == "__main__":
    main()
