"""Divergent software/firmware identities across admission, workers and restarts."""

from contextlib import redirect_stdout, redirect_stderr
from dataclasses import replace
import fcntl
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from livolod import __version__, FIRMWARE_COMPAT_VERSION
from livolod.config import load
from livolod.firmware_artifacts import inspect_artifact
from livolod.firmware_cli import main as maintenance_main
from livolod.firmware_update import main as signed_main
from livolod.firmware_worker import main as worker_main
from livolod.service import Service
from livolod.storage import Store
from test_firmware_bundle import gbl, mg21_image, ti_package
from test_firmware_catalog import fixture

# Always a valid firmware version distinct from the release's compatibility target.
OTHER_FIRMWARE_VERSION = "0.1" if FIRMWARE_COMPAT_VERSION != "0.1" else "0.2"


class FirmwareVersioningTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        """Prepare a real schema-1 catalog and registry with nondeployable fixtures."""
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.catalog = self.root / "catalog"
        self.entry = fixture(self.catalog)
        self.config = replace(
            load(Path(__file__).parents[1] / "config.example.json"),
            database=str(self.root / "state.db"),
            firmware_bundle_dir=str(self.catalog),
            serial_baudrate=460800,
            mqtt={"enabled": False},
        )
        self.store = Store(self.config.database, "network")
        for target in (
            "livolod.firmware_backends.backend_status",
            "livolod.firmware_preflight.backend_status",
        ):
            patcher = patch(target, return_value=None)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.service = Service(self.config, self.store)
        self.caps = dict(
            board="zb-gw04-1v2", fw_version="0.12", proto="3", baud="460800"
        )
        self.observe(self.service)
        self.assertNotEqual(__version__, FIRMWARE_COMPAT_VERSION)
        self.assertNotEqual(OTHER_FIRMWARE_VERSION, FIRMWARE_COMPAT_VERSION)

    async def asyncTearDown(self):
        """Release only isolated state; tests never open a radio."""
        self.store.close()
        self.temp.cleanup()

    def observe(self, service):
        """Supply coherent current-session facts to normal manager selection.

        @param service Isolated service instance whose catalog must be resolved.
        """
        service.ready = True
        service.firmware_manager.observe(self.caps)
        service.firmware_manager.version_info = dict(self.caps)

    async def test_catalog_and_durable_job_survive_software_upgrade_and_rollback(self):
        """A new package preserves target/hash/state and never replays admitted jobs."""
        original = (self.catalog / "manifest.json").read_bytes()
        manager = self.service.firmware_manager
        report = self.service.firmware_preflight()
        self.assertTrue(report["executable"])
        self.assertEqual(report["target_version"], FIRMWARE_COMPAT_VERSION)
        payload = {"request_id": "upgrade-job", "candidate_id": manager.candidate["id"]}
        self.assertEqual(manager.start(payload)["status"], "pending")
        selected = dict(manager.job)
        with patch("livolod.version.__version__", "9.8.7"):
            upgraded = Service(self.config, self.store)
            self.assertEqual(upgraded.diagnostics["daemon_version"], "9.8.7")
            restored = upgraded.firmware_manager
            self.assertFalse(restored.requested.is_set())
            self.assertEqual(restored.job["status"], "unknown")
            for key in ("version", "candidate_id", "board", "request_id"):
                self.assertEqual(restored.job[key], selected[key])
            self.assertEqual(restored.start(payload)["status"], "unknown")
            restored.restored(dict(self.caps, fw_version=OTHER_FIRMWARE_VERSION))
            self.assertEqual(restored.job["status"], "unknown")
            restored.restored(dict(self.caps, fw_version=FIRMWARE_COMPAT_VERSION))
            self.assertEqual(restored.job["status"], "confirmed")
        terminal = dict(self.store.firmware_job())
        rolled_back = Service(self.config, self.store)
        self.assertEqual(rolled_back.diagnostics["daemon_version"], __version__)
        self.assertEqual(rolled_back.firmware_manager.job, terminal)
        self.assertFalse(rolled_back.firmware_manager.requested.is_set())
        self.assertEqual(
            rolled_back.firmware_manager.start(payload)["status"], "confirmed"
        )
        self.assertEqual((self.catalog / "manifest.json").read_bytes(), original)

    async def test_wrong_radio_target_and_schema_extensions_never_become_eligible(self):
        """Software identity cannot admit another radio target or alter schema-1 interpretation."""
        path = self.catalog / "manifest.json"
        original = json.loads(path.read_text())
        for manifest in (
            dict(original, host_version=OTHER_FIRMWARE_VERSION),
            dict(original, host_version=__version__),
            dict(original, daemon_version=__version__),
        ):
            path.write_text(json.dumps(manifest))
            self.observe(self.service)
            self.assertFalse(
                self.service.firmware_update_snapshot()["update_available"]
            )
            self.assertFalse(self.service.firmware_preflight()["executable"])
            self.assertIsNone(self.store.firmware_job())

    async def test_worker_revalidates_compatibility_for_catalog_and_local_artifacts(
        self,
    ):
        """Actual worker entry admits only the current target before any backend mutation."""
        with tempfile.TemporaryFile() as guard:
            fcntl.flock(guard, fcntl.LOCK_EX | fcntl.LOCK_NB)
            common = [
                "--port",
                "/dev/never-opened",
                "--baud",
                "460800",
                "--job-id",
                "worker-test",
                "--lock-fd",
                str(guard.fileno()),
            ]
            candidate = self.service.firmware_manager.candidate
            with (
                redirect_stdout(io.StringIO()),
                patch("livolod.firmware_worker.install") as install,
            ):
                self.assertEqual(
                    worker_main(
                        [
                            "--bundle-dir",
                            str(self.catalog),
                            "--candidate-id",
                            candidate["id"],
                            *common,
                        ]
                    ),
                    0,
                )
            self.assertEqual(
                install.call_args.args[0]["version"], FIRMWARE_COMPAT_VERSION
            )
            for version, expected in (
                (FIRMWARE_COMPAT_VERSION, 0),
                (OTHER_FIRMWARE_VERSION, 1),
            ):
                artifact = self.root / (version + ".gbl")
                artifact.write_bytes(gbl(mg21_image("zb-gw04-1v2", version)))
                local = inspect_artifact(
                    str(artifact), board="zb-gw04-1v2", version=version
                )
                with (
                    redirect_stdout(io.StringIO()),
                    patch("livolod.firmware_worker.install") as install,
                ):
                    self.assertEqual(
                        worker_main(
                            [
                                "--artifact",
                                str(artifact),
                                "--board",
                                "zb-gw04-1v2",
                                "--version",
                                version,
                                "--candidate-id",
                                local["id"],
                                *common,
                            ]
                        ),
                        expected,
                    )
                self.assertEqual(install.call_count, int(expected == 0))

    async def test_real_signed_catalog_retains_key_digest_board_baud_and_protocol_checks(
        self,
    ):
        """The independent package number never relaxes exact TI image qualification."""
        data, key = ti_package(FIRMWARE_COMPAT_VERSION)
        (self.catalog / "image.gbl").write_bytes(data)
        (self.catalog / "public.key").write_bytes(key)
        entry = dict(
            self.entry,
            board="codm-cc2652p",
            backend="ti_uart_signed_v1",
            layout="ti-app-8000-20000",
            size=len(data),
            sha256=hashlib.sha256(data).hexdigest(),
            public_key="public.key",
            public_key_sha256=hashlib.sha256(key).hexdigest(),
            bootloader_min="0.2",
        )
        (self.catalog / "manifest.json").write_text(
            json.dumps(
                dict(schema=1, host_version=FIRMWARE_COMPAT_VERSION, artifacts=[entry])
            )
        )
        self.caps.update(
            board="codm-cc2652p", boot_version="0.2", update="uart_signed_v1"
        )
        self.observe(self.service)
        manager = self.service.firmware_manager
        manager.update_info = dict(key=key.hex(), trial="0", state="ffffffff")
        self.assertTrue(self.service.firmware_preflight()["executable"])
        for changes in (dict(key="f" * 64), dict(trial="1"), dict(state="fffffffe")):
            with patch.object(
                manager, "update_info", dict(manager.update_info, **changes)
            ):
                self.assertFalse(self.service.firmware_preflight()["executable"])
        for changes in (
            dict(board="zb-gw04-1v2"),
            dict(proto="4"),
            dict(baud="115200"),
        ):
            with (
                patch.object(
                    manager, "version_info", dict(manager.version_info, **changes)
                ),
                patch.object(manager, "caps", dict(manager.caps, **changes)),
            ):
                self.assertFalse(self.service.firmware_preflight()["executable"])
        altered = bytearray(data)
        altered[-1] ^= 1
        (self.catalog / "image.gbl").write_bytes(altered)
        self.assertFalse(self.service.firmware_preflight()["executable"])

    async def test_legacy_signed_cli_uses_firmware_target_not_package_number(self):
        """Signed maintenance accepts exactly the compatibility target before transport."""
        for version in (FIRMWARE_COMPAT_VERSION, OTHER_FIRMWARE_VERSION):
            data, key = ti_package(version)
            path = self.root / (version + ".ecupd")
            path.write_bytes(data)
            keypath = self.root / "public.key"
            keypath.write_bytes(key)
            with (
                patch("livolod.firmware_update.load", return_value=self.config),
                patch(
                    "livolod.firmware_update.UpdatePort",
                    side_effect=RuntimeError("transport boundary"),
                ) as port,
                redirect_stderr(io.StringIO()),
            ):
                if version == FIRMWARE_COMPAT_VERSION:
                    with self.assertRaisesRegex(RuntimeError, "transport boundary"):
                        signed_main(
                            [
                                "--config",
                                "/unused",
                                "--package",
                                str(path),
                                "--public-key",
                                str(keypath),
                            ]
                        )
                    port.assert_called_once()
                else:
                    with self.assertRaises(SystemExit):
                        signed_main(
                            [
                                "--config",
                                "/unused",
                                "--package",
                                str(path),
                                "--public-key",
                                str(keypath),
                            ]
                        )
                    port.assert_not_called()

    async def test_local_maintenance_check_uses_artifact_version_without_radio_probe(
        self,
    ):
        """The artifact --version flag remains firmware-specific after adding software info."""
        for version in (FIRMWARE_COMPAT_VERSION, OTHER_FIRMWARE_VERSION):
            data = gbl(mg21_image("zb-gw04-1v2", version))
            path = self.root / (version + ".gbl")
            path.write_bytes(data)
            output = io.StringIO()
            with (
                patch("livolod.firmware_cli.load", return_value=self.config),
                patch("livolod.firmware_cli.probe_identity") as probe,
                redirect_stdout(output),
                redirect_stderr(io.StringIO()),
            ):
                with self.assertRaises(SystemExit):
                    maintenance_main(
                        [
                            "--config",
                            "/unused",
                            "flash",
                            "--operation",
                            "update",
                            "--artifact",
                            str(path),
                            "--board",
                            "zb-gw04-1v2",
                            "--version",
                            version,
                            "--sha256",
                            hashlib.sha256(data).hexdigest(),
                            "--check-only",
                        ]
                    )
                probe.assert_not_called()
            result = json.loads(output.getvalue())
            if version == FIRMWARE_COMPAT_VERSION:
                self.assertEqual(result["target_version"], FIRMWARE_COMPAT_VERSION)
                self.assertEqual(result["blocked_reason"], "live_identity_required")
            else:
                self.assertEqual(result["error"], "incompatible_host")
