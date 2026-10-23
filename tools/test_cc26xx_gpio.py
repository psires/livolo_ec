"""Hardware-free checks of pin selection and recovery sequencing."""

import contextlib
from enum import Enum
import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from cc26xx_gpio import configuration, main


class GPIOTests(unittest.TestCase):
    def test_profile_and_explicit_override(self):
        """Verify profile and explicit override."""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "hardware.json"
            path.write_text(
                json.dumps(dict(reset_gpio=17, flash_gpio=18, chip="/dev/gpiochip4"))
            )
            result = configuration(path, dict(flash_gpio=22, port=None))
        self.assertEqual((result["reset_gpio"], result["flash_gpio"]), (17, 22))
        self.assertEqual(result["chip"], "/dev/gpiochip4")
        self.assertEqual(result["port"], "/dev/ttyAMA0")

    def test_invalid_pins_fail_before_hardware(self):
        """Verify invalid pins fail before hardware."""
        for values in (
            dict(reset_gpio=True),
            dict(flash_gpio=-1),
            dict(reset_gpio=28),
            dict(reset_gpio=24),
            dict(reset_gpio="17"),
            dict(chip_label=""),
        ):
            with self.subTest(values=values), self.assertRaises(ValueError):
                configuration(overrides=values)

    def test_rejects_profile_typos(self):
        """Verify rejects profile typos."""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "hardware.json"
            for value in ([], dict(reset_pin=17)):
                path.write_text(json.dumps(value))
                with self.assertRaises(ValueError):
                    configuration(path)

    def test_show_config_needs_no_hardware_dependencies(self):
        """Verify show config needs no hardware dependencies."""
        with (
            patch.dict("sys.modules", {"gpiod": None, "serial": None}),
            contextlib.redirect_stdout(io.StringIO()) as output,
        ):
            main(
                ["status", "--reset-gpio", "17", "--flash-gpio", "18", "--show-config"]
            )
        self.assertEqual(json.loads(output.getvalue())["reset_gpio"], 17)

    def hardware(self, *, label="pinctrl-rp1", used=False, wrong_name=False):
        """Provide fake GPIO and serial devices with controllable wiring validation.

        @param label GPIO controller label reported by the fake chip.
        @param used Whether the selected GPIO line is already claimed.
        @param wrong_name Whether to report an unexpected GPIO line name.
        """

        class Value(Enum):
            INACTIVE = 0
            ACTIVE = 1

        values = {}
        writes = []
        lines = Mock()
        lines.__enter__ = Mock(return_value=lines)
        lines.__exit__ = Mock(return_value=False)

        def write(update):
            """Record a serial write and trigger the configured fake response.

            @param update Mapping of GPIO line offsets to requested levels.
            """
            writes.append(dict(update))
            values.update(update)

        lines.set_values.side_effect = write
        lines.get_values.side_effect = lambda offsets: [values[n] for n in offsets]
        chip = Mock()
        chip.__enter__ = Mock(return_value=chip)
        chip.__exit__ = Mock(return_value=False)
        chip.get_info.return_value = SimpleNamespace(label=label)
        chip.get_line_info.side_effect = lambda n: SimpleNamespace(
            name="WRONG" if wrong_name else f"GPIO{n}", used=used, consumer="owner"
        )
        gpio = SimpleNamespace(
            Chip=Mock(return_value=chip),
            request_lines=Mock(return_value=lines),
            LineSettings=Mock(side_effect=lambda **kw: kw),
        )
        enums = SimpleNamespace(
            Value=Value,
            Bias=SimpleNamespace(PULL_UP="pull-up"),
            Direction=SimpleNamespace(OUTPUT="out"),
            Drive=SimpleNamespace(OPEN_DRAIN="open-drain"),
        )
        uart = Mock()
        uart.__enter__ = Mock(return_value=uart)
        uart.__exit__ = Mock(return_value=False)
        serial = SimpleNamespace(Serial=Mock(return_value=uart))
        return gpio, enums, serial, lines, writes

    def test_boot_sequence_uses_selected_lines_and_releases_them(self):
        """Verify boot sequence uses selected lines and releases them."""
        gpio, enums, serial, lines, writes = self.hardware()
        with (
            patch.dict(
                "sys.modules", {"gpiod": gpio, "gpiod.line": enums, "serial": serial}
            ),
            patch("cc26xx_gpio.time.sleep"),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            main(
                [
                    "bootloader",
                    "--reset-gpio",
                    "17",
                    "--flash-gpio",
                    "18",
                    "--chip",
                    "/dev/gpiochip4",
                    "--port",
                    "/dev/ttyUSB0",
                ]
            )
        self.assertEqual(
            [{k: v.value for k, v in w.items()} for w in writes],
            [
                {17: 1, 18: 1},
                {17: 1, 18: 0},
                {17: 0, 18: 0},
                {17: 1, 18: 0},
                {17: 1, 18: 1},
                {17: 1, 18: 1},
            ],
        )
        self.assertEqual(gpio.request_lines.call_args.args[0], "/dev/gpiochip4")
        settings = gpio.request_lines.call_args.kwargs["config"][(17, 18)]
        self.assertEqual(settings["drive"], "open-drain")
        self.assertEqual(serial.Serial.call_args.args[0], "/dev/ttyUSB0")
        self.assertTrue(serial.Serial.call_args.kwargs["exclusive"])

    def test_wrong_controller_mapping_or_claimed_line_never_drives_gpio(self):
        """Verify wrong controller mapping or claimed line never drives gpio."""
        for options in (
            dict(label="wrong-chip"),
            dict(wrong_name=True),
            dict(used=True),
        ):
            gpio, enums, serial, lines, writes = self.hardware(**options)
            with (
                self.subTest(options=options),
                patch.dict(
                    "sys.modules",
                    {"gpiod": gpio, "gpiod.line": enums, "serial": serial},
                ),
                contextlib.redirect_stdout(io.StringIO()),
                self.assertRaises(SystemExit),
            ):
                main(["bootloader"])
            gpio.request_lines.assert_not_called()
            serial.Serial.assert_not_called()

    def test_readback_failure_releases_both_selected_lines(self):
        """Verify readback failure releases both selected lines."""
        gpio, enums, serial, lines, writes = self.hardware()
        lines.get_values.side_effect = lambda offsets: [enums.Value.INACTIVE] * 2
        with (
            patch.dict(
                "sys.modules", {"gpiod": gpio, "gpiod.line": enums, "serial": serial}
            ),
            patch("cc26xx_gpio.time.sleep"),
            contextlib.redirect_stdout(io.StringIO()),
            self.assertRaisesRegex(RuntimeError, "GPIO level mismatch"),
        ):
            main(["bootloader", "--reset-gpio", "17", "--flash-gpio", "18"])
        self.assertEqual(writes[-1], {17: enums.Value.ACTIVE, 18: enums.Value.ACTIVE})


if __name__ == "__main__":
    unittest.main()
