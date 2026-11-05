"""Shared compatibility checks and CLI inspection must not implicitly operate a radio."""

import asyncio
from contextlib import redirect_stdout
from dataclasses import replace
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from livolod.config import load
from livolod.firmware_bundle import BundleError
from livolod.firmware_cli import main, preflight, probe_identity
from livolod.firmware_preflight import build_report
from livolod.storage import Store
from test_firmware_bundle import gbl, mg21_image


class PreflightTests(unittest.TestCase):
    """Check machine-readable prerequisites without service startup or SQLite mutation."""

    def setUp(self):
        """Create isolated config and synthetic validated-candidate metadata."""
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.config = replace(
            load(Path(__file__).parents[1] / "config.example.json"),
            database=self.temp.name + "/state.db",
            serial_baudrate=460800,
        )
        self.candidate = dict(
            id="fixture:" + "a" * 64,
            board="sonoff-zbdongle-e",
            version="0.12",
            backend="mg21_gecko_gbl_v1",
            app_baudrate=460800,
            rollback_supported=False,
            path="/private/must-not-leak",
            _data=b"private bytes",
        )
        self.caps = dict(
            board="sonoff-zbdongle-e", proto="3", fw_version="0.11", baud="460800"
        )
        for name in ("load_candidate", "select_candidate"):
            patcher = patch(
                "livolod.firmware_preflight." + name, return_value=self.candidate
            )
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = patch("livolod.firmware_preflight.backend_status", return_value=None)
        patcher.start()
        self.addCleanup(patcher.stop)

    def report(self, **changes):
        """Construct a complete live report, optionally replacing one prerequisite.

        @param changes Keyword overrides for the shared builder.
        @return Sanitized public report.
        """
        values = dict(
            board=self.caps["board"],
            caps=self.caps,
            version_info=self.caps,
            live=True,
            identity_source="live_session",
        )
        values.update(changes)
        return build_report(self.config, self.candidate["id"], **values)

    def test_offline_checks_never_start_service_open_serial_or_create_database(self):
        """Default preflight reports missing live evidence without acquiring hardware."""
        with (
            patch("livolod.firmware_cli.UpdatePort") as port,
            patch("livolod.firmware_cli.Service") as service,
        ):
            report = preflight(
                self.config, self.candidate["id"], self.candidate["board"]
            )
        port.assert_not_called()
        service.assert_not_called()
        self.assertFalse(Path(self.config.database).exists())
        self.assertIsNone(report["compatible"])
        self.assertFalse(report["executable"])
        self.assertEqual(report["blocked_reason"], "live_identity_required")
        encoded = json.dumps(report)
        self.assertNotIn("private", encoded)
        self.assertNotIn("_data", encoded)

    def test_live_board_version_protocol_baud_and_lifecycle_are_checked(self):
        """Any incompatible or busy live target prevents execution."""
        self.assertTrue(self.report()["executable"])
        for changes in (
            {"board": "zb-gw04-1v2"},
            {"fw_version": "0.12"},
            {"baud": "115200"},
            {"proto": "2"},
        ):
            with self.subTest(changes=changes):
                self.assertFalse(
                    self.report(caps=dict(self.caps, **changes))["executable"]
                )
        self.assertEqual(
            self.report(blocked="radio_busy")["blocked_reason"], "radio_busy"
        )
        self.assertFalse(self.report(simulated=True)["executable"])

    def test_missing_artifact_retains_only_verified_live_version(self):
        """Missing bundles retain known firmware facts without trusting mismatched banners."""
        with patch(
            "livolod.firmware_preflight.load_candidate",
            side_effect=BundleError("artifact_missing", "Missing test artifact"),
        ):
            report = self.report()
            self.assertEqual(report["installed_version"], "0.11")
            self.assertFalse(report["executable"])
            self.assertEqual(report["blocked_reason"], "artifact_missing")
            self.assertIsNone(self.report(live=False)["installed_version"])
            self.assertIsNone(
                self.report(version_info=dict(self.caps, fw_version="0.10"))[
                    "installed_version"
                ]
            )

    def test_ti_key_trial_and_bootloader_are_checked(self):
        """A same-family TI target still requires its matching signing identity."""
        self.candidate.update(
            board="codm-cc2652p", backend="ti_uart_signed_v1", public_key="b" * 64
        )
        caps = dict(
            self.caps, board="codm-cc2652p", update="uart_signed_v1", boot_version="0.2"
        )
        status = dict(key="b" * 64, trial="0", state="ffffffff")
        report = self.report(
            board=caps["board"], caps=caps, version_info=caps, update_info=status
        )
        self.assertTrue(report["executable"])
        for changes in ({"key": "c" * 64}, {"trial": "1"}, {"state": "fffffffe"}):
            self.assertFalse(
                self.report(
                    board=caps["board"],
                    caps=caps,
                    version_info=caps,
                    update_info=dict(status, **changes),
                )["executable"]
            )

    def test_active_database_owner_blocks_probe_before_serial_access(self):
        """An explicit probe cannot bypass the daemon's SQLite lifetime lock."""
        store = Store(self.config.database, self.config.gateway + ":" + self.config.tag)
        try:
            changes = store.db.total_changes
            with patch("livolod.firmware_cli.UpdatePort") as port:
                report = preflight(
                    self.config, self.candidate["id"], self.candidate["board"], True
                )
            port.assert_not_called()
            self.assertEqual(report["blocked_reason"], "radio_busy")
            self.assertEqual(store.db.total_changes, changes)
        finally:
            store.close()

    def test_probe_uses_only_identity_queries_and_does_not_initialize_sqlite(self):
        """Opt-in probing owns both guards and never sends reset, arm or flash commands."""
        port = Mock()
        port.command.side_effect = [
            "OK EC-GATEWAY 3",
            "OK board=sonoff-zbdongle-e proto=3 fw_version=0.11",
            "OK board=sonoff-zbdongle-e proto=3 fw_version=0.11 baud=460800",
            "OK armed=0 ack=0",
        ]
        with patch("livolod.firmware_cli.UpdatePort", return_value=port) as factory:
            probe_identity(self.config)
        factory.assert_called_once_with(self.config.serial_port, 460800, passive=True)
        self.assertEqual(
            [c.args[0] for c in port.command.call_args_list],
            ["PING", "VERSION", "CAPS", "STATUS"],
        )
        port.close.assert_called_once()
        self.assertFalse(Path(self.config.database).exists())

    def test_cli_inspect_needs_no_config_and_emits_only_public_metadata(self):
        """An operator can validate actual container bytes without deployment access."""
        path = Path(self.temp.name) / "application.gbl"
        path.write_bytes(gbl(mg21_image()))
        output = io.StringIO()
        with redirect_stdout(output), patch("livolod.firmware_cli.UpdatePort") as port:
            main(
                [
                    "inspect",
                    "--artifact",
                    str(path),
                    "--board",
                    "sonoff-zbdongle-e",
                    "--version",
                    "0.12",
                ]
            )
        value = json.loads(output.getvalue())
        self.assertEqual(value["inspection"]["board"], "sonoff-zbdongle-e")
        self.assertNotIn(str(path), output.getvalue())
        self.assertNotIn("_data", output.getvalue())
        port.assert_not_called()

    def test_unreviewed_hash_is_rejected_before_service_or_transport(self):
        """An explicit local flash cannot race or bypass its operator-selected digest."""
        path = Path(self.temp.name) / "application.gbl"
        path.write_bytes(gbl(mg21_image()))
        config = Path(self.temp.name) / "config.json"
        raw = json.loads(
            (Path(__file__).parents[1] / "config.example.json").read_text()
        )
        raw.update(database=self.config.database, serial_baudrate=460800)
        config.write_text(json.dumps(raw))
        output = io.StringIO()
        with (
            redirect_stdout(output),
            patch("livolod.firmware_cli.UpdatePort") as port,
            patch("livolod.firmware_cli.Service") as service,
        ):
            with self.assertRaises(SystemExit) as error:
                main(
                    [
                        "--config",
                        str(config),
                        "flash",
                        "--operation",
                        "update",
                        "--artifact",
                        str(path),
                        "--board",
                        "sonoff-zbdongle-e",
                        "--version",
                        "0.12",
                        "--sha256",
                        "0" * 64,
                    ]
                )
        self.assertEqual(error.exception.code, 1)
        self.assertEqual(json.loads(output.getvalue())["error"], "artifact_invalid")
        port.assert_not_called()
        service.assert_not_called()

    def test_passive_transport_sets_inactive_lines_before_open(self):
        """Read-only probes must not request the updater's default modem-line state."""
        from livolod.firmware_update import UpdatePort

        serial = Mock()

        def opened():
            """Inspect line state at the moment the mocked device would open."""
            self.assertIs(serial.dtr, False)
            self.assertIs(serial.rts, False)
            self.assertEqual(serial.port, "/dev/nonexistent")

        serial.open.side_effect = opened
        with patch("serial.Serial", return_value=serial) as factory:
            port = UpdatePort("/dev/nonexistent", 460800, passive=True)
            port.close()
        self.assertIsNone(factory.call_args.args[0])
        serial.open.assert_called_once()
        serial.close.assert_called_once()


if __name__ == "__main__":
    unittest.main()
