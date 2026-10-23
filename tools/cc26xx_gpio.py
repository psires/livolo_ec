#!/usr/bin/env python3
"""Control configurable Raspberry Pi CC2652P RESET/FLASH lines.

BCM GPIO23 (header pin 16) is RESET; GPIO24 (header pin 18) is FLASH/DIO15.
Both are active low. Use libgpiod 2.x open-drain outputs with internal pull-ups:
assert low, release high, never drive high against a physical button.
These are defaults; --config or --reset-gpio/--flash-gpio select other wiring.
GPIO numbers are BCM numbers, not physical header positions. The selected
controller label and line names are checked before driving any pins.
The UART is locked while changing lines. Stop the daemon/capture clients first.
"""

import argparse
import json
from pathlib import Path
import time

DEFAULTS = {
    "chip": "/dev/gpiochip0",
    "chip_label": "pinctrl-rp1",
    "port": "/dev/ttyAMA0",
    "reset_gpio": 23,
    "flash_gpio": 24,
}


def configuration(path=None, overrides=None):
    """Resolve and validate the board wiring before any hardware access.

    @param path Optional JSON hardware profile path.
    @param overrides Optional explicit settings; None values retain the profile defaults.
    @return Validated GPIO controller, BCM line numbers, and UART settings.
    """
    config = dict(DEFAULTS)
    if path:
        supplied = json.loads(Path(path).read_text())
        if not isinstance(supplied, dict) or supplied.keys() - DEFAULTS.keys():
            raise ValueError(
                "GPIO configuration must be an object containing only "
                + ", ".join(DEFAULTS)
            )
        config.update(supplied)
    for key, value in (overrides or {}).items():
        if key not in DEFAULTS:
            raise ValueError("Unknown GPIO configuration field")
        if value is not None:
            config[key] = value
    for key in ("reset_gpio", "flash_gpio"):
        if type(config[key]) is not int or not 0 <= config[key] <= 27:
            raise ValueError(f"{key} must be a BCM GPIO number from 0 to 27")
    if config["reset_gpio"] == config["flash_gpio"]:
        raise ValueError("RESET and FLASH must use different GPIOs")
    for key in ("chip", "chip_label", "port"):
        if (
            not isinstance(config[key], str)
            or not config[key].strip()
            or "\0" in config[key]
        ):
            raise ValueError(f"{key} must be a nonempty string")
    return config


def main(argv=None):
    """Inspect or operate the configured RESET and FLASH wiring.

    @param argv Optional argument list; None reads the process command line.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("status", "idle", "reset", "bootloader"))
    parser.add_argument(
        "--config", help="JSON hardware profile; explicit flags override it"
    )
    parser.add_argument("--chip", help="GPIO controller device path")
    parser.add_argument(
        "--chip-label", help="Expected GPIO controller label, e.g. pinctrl-rp1"
    )
    parser.add_argument("--port", help="UART device to lock before changing pins")
    parser.add_argument(
        "--reset-gpio", type=int, help="RESET BCM GPIO number (not header pin)"
    )
    parser.add_argument(
        "--flash-gpio", type=int, help="FLASH BCM GPIO number (not TI DIO number)"
    )
    parser.add_argument(
        "--show-config",
        action="store_true",
        help="Print resolved configuration without opening GPIO or UART",
    )
    args = parser.parse_args(argv)
    try:
        config = configuration(
            args.config, {key: getattr(args, key) for key in DEFAULTS}
        )
    except (ValueError, OSError) as error:
        parser.error(str(error))
    if args.show_config:
        print(json.dumps(config, indent=2))
        return
    reset_gpio, flash_gpio = config["reset_gpio"], config["flash_gpio"]

    import gpiod
    from gpiod.line import Bias, Direction, Drive, Value

    with gpiod.Chip(config["chip"]) as chip:
        info = chip.get_info()
        if info.label != config["chip_label"]:
            raise SystemExit(
                f"Expected GPIO controller {config['chip_label']!r}, got {info.label!r}"
            )
        for offset in (reset_gpio, flash_gpio):
            line = chip.get_line_info(offset)
            if line.name != f"GPIO{offset}":
                raise SystemExit(f"Unexpected line mapping: {line}")
            print(line, flush=True)
            if args.action != "status" and line.used:
                raise SystemExit(f"GPIO{offset} already claimed by {line.consumer!r}")
    if args.action == "status":
        return

    import serial

    # active_low=False: ACTIVE means raw high/released, INACTIVE means low.
    release = Value.ACTIVE
    low = Value.INACTIVE
    with serial.Serial(
        config["port"],
        115200,
        timeout=0.2,
        write_timeout=1,
        exclusive=True,
        rtscts=False,
        dsrdtr=False,
    ) as uart:
        with gpiod.request_lines(
            config["chip"],
            consumer="livolo-cc2652p-boot",
            config={
                (reset_gpio, flash_gpio): gpiod.LineSettings(
                    direction=Direction.OUTPUT,
                    drive=Drive.OPEN_DRAIN,
                    bias=Bias.PULL_UP,
                    active_low=False,
                    output_value=release,
                )
            },
        ) as lines:

            def set_and_check(reset, flash, delay=0.05):
                """Apply requested pin levels and require matching readback.

                @param reset libgpiod Value: ACTIVE releases RESET high; INACTIVE asserts low.
                @param flash libgpiod Value: ACTIVE releases FLASH high; INACTIVE asserts low.
                @param delay Settling time in seconds before readback.
                """
                lines.set_values({reset_gpio: reset, flash_gpio: flash})
                time.sleep(delay)
                actual = lines.get_values([reset_gpio, flash_gpio])
                print(
                    json.dumps({"reset": actual[0].value, "flash": actual[1].value}),
                    flush=True,
                )
                if actual != [reset, flash]:
                    raise RuntimeError("GPIO level mismatch: check wiring and buttons")

            try:
                set_and_check(release, release)
                if args.action == "bootloader":
                    set_and_check(release, low)
                    set_and_check(low, low)
                    uart.reset_input_buffer()
                    set_and_check(release, low, delay=0.3)
                    set_and_check(release, release)
                    print("Bootloader entry sequence complete; run the ROM probe next.")
                elif args.action == "reset":
                    set_and_check(low, release)
                    uart.reset_input_buffer()
                    set_and_check(release, release)
                    # Preserve startup evidence while this tool owns the UART.
                    deadline = time.monotonic() + 3
                    startup_seen = False
                    while time.monotonic() < deadline:
                        record = uart.readline()
                        if record:
                            print(
                                record.decode(
                                    "ascii", errors="backslashreplace"
                                ).rstrip(),
                                flush=True,
                            )
                            if record.startswith(
                                (
                                    b"EC-RECEIVER 1 board=codm-cc2652p ",
                                    b"EC-GATEWAY 2 board=codm-cc2652p ",
                                    b"EC-GATEWAY 3 board=codm-cc2652p ",
                                )
                            ):
                                startup_seen = True
                    if not startup_seen:
                        raise RuntimeError(
                            "Reset not verified: receiver startup banner absent"
                        )
            finally:
                # Leave reset and boot selection deasserted even on an error.
                lines.set_values({reset_gpio: release, flash_gpio: release})
                time.sleep(0.05)
    print("Released both lines with internal pull-ups; GPIO request and UART closed.")


if __name__ == "__main__":
    try:
        main()
    except RuntimeError as error:
        raise SystemExit(str(error)) from None
