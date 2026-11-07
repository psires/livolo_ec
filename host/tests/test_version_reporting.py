"""Software identity reporting is side-effect-free and never drives radio policy."""

import ast
from contextlib import ExitStack, redirect_stdout
from dataclasses import replace
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from livolod import __version__, FIRMWARE_COMPAT_VERSION
from livolod import __main__, commission_cli, firmware_cli, firmware_update
from livolod.config import load
from livolod.service import Service
from livolod.storage import Store
from livolod.version import version_info
from test_daemon import Broker


class VersionCLITests(unittest.TestCase):
    def test_all_four_version_info_commands_avoid_config_io_and_hardware(self):
        """Version actions exit before required config/subcommands or transport access."""
        for module in (__main__, commission_cli, firmware_cli, firmware_update):
            output = io.StringIO()
            with ExitStack() as stack:
                for target in (
                    "livolod.__main__.load",
                    "livolod.firmware_cli.load",
                    "livolod.firmware_update.load",
                    "livolod.storage.sqlite3.connect",
                    "livolod.uart.Gateway.open",
                    "livolod.firmware_update.UpdatePort",
                    "livolod.firmware_cli.UpdatePort",
                    "subprocess.Popen",
                    "subprocess.run",
                    "asyncio.create_subprocess_exec",
                    "urllib.request.OpenerDirector.open",
                    "pathlib.Path.read_bytes",
                    "pathlib.Path.read_text",
                ):
                    stack.enter_context(
                        patch(
                            target,
                            side_effect=AssertionError(
                                "Version reporting attempted I/O"
                            ),
                        )
                    )
                stack.enter_context(redirect_stdout(output))
                with (
                    self.subTest(module=module.__name__),
                    self.assertRaises(SystemExit) as done,
                ):
                    module.main(["--version-info"])
                self.assertEqual(done.exception.code, 0)
            self.assertEqual(json.loads(output.getvalue()), version_info())

    def test_software_version_flags_and_artifact_version_namespace_are_distinct(self):
        """Only software-reporting CLIs claim --version; maintenance keeps artifact flags."""
        for module in (__main__, commission_cli, firmware_update):
            output = io.StringIO()
            with redirect_stdout(output), self.assertRaises(SystemExit) as done:
                module.main(["--version"])
            self.assertEqual(done.exception.code, 0)
            self.assertEqual(output.getvalue().strip(), __version__)
        candidate = {"id": "fixture", "inspection": {"version": "0.13"}}
        with (
            patch(
                "livolod.firmware_artifacts.inspect_artifact", return_value=candidate
            ) as inspect,
            redirect_stdout(io.StringIO()),
        ):
            firmware_cli.main(
                [
                    "inspect",
                    "--artifact",
                    "/unused",
                    "--board",
                    "zb-gw04-1v2",
                    "--version",
                    "0.13",
                ]
            )
        self.assertEqual(inspect.call_args.kwargs["version"], FIRMWARE_COMPAT_VERSION)

    def test_compatibility_decision_modules_never_import_software_version(self):
        """Prevent an accidental reintroduction of package identity into firmware policy."""
        directory = Path(__file__).parents[1] / "livolod"
        for name in (
            "firmware_manager",
            "firmware_preflight",
            "firmware_worker",
            "firmware_cli",
            "firmware_update",
            "service",
        ):
            tree = ast.parse((directory / (name + ".py")).read_text())
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom):
                    self.assertNotIn(
                        "__version__", [alias.name for alias in node.names], name
                    )
                if isinstance(node, ast.Name):
                    self.assertNotEqual(node.id, "__version__", name)
                if isinstance(node, ast.Attribute):
                    self.assertNotEqual(node.attr, "__version__", name)


class VersionSnapshotTests(unittest.IsolatedAsyncioTestCase):
    async def test_mqtt_and_web_report_all_identities_even_without_a_radio(self):
        """Offline version reporting does not wait for CAPS or change firmware offers."""
        with tempfile.TemporaryDirectory() as directory:
            config = replace(
                load(Path(__file__).parents[1] / "config.example.json"),
                database=directory + "/state.db",
            )
            store = Store(config.database, "test")
            try:
                broker = Broker()
                service = Service(config, store, broker)
                service.snapshot()
                info = next(
                    value
                    for topic, value, _ in broker.messages
                    if topic == "bridge/info"
                )
                expected = dict(version_info(), host_version=FIRMWARE_COMPAT_VERSION)
                for key, value in expected.items():
                    self.assertEqual(info[key], value)
                    self.assertEqual(service.web_snapshot()["diagnostics"][key], value)
                self.assertIsNone(info["firmware_version"])
                self.assertFalse(service.firmware_update_snapshot()["update_available"])
                self.assertIsNone(store.firmware_job())
            finally:
                store.close()
