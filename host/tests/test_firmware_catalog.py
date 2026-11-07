"""Persistent catalog publication and live refresh without radio or Web access."""

import asyncio
from contextlib import redirect_stdout
import io
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

from livolod import FIRMWARE_COMPAT_VERSION
from livolod.config import load
from livolod.firmware_bundle import BundleError, select_candidate
from livolod.firmware_catalog import inventory, provision
from livolod.service import Service
from livolod.storage import Store
from test_firmware_bundle import gbl, mg21_image


def fixture(root, variant=0):
    """Create synthetic, nondeployable ZB-GW04 catalog bytes.

    @param root Temporary source directory.
    @param variant Change unused application bytes to produce another valid hash.
    @return Candidate manifest metadata.
    """
    root.mkdir(exist_ok=True)
    app = bytearray(mg21_image("zb-gw04-1v2", FIRMWARE_COMPAT_VERSION))
    app[-1] = variant
    data = gbl(bytes(app))
    entry = dict(
        id="test",
        board="zb-gw04-1v2",
        role="gateway",
        version=FIRMWARE_COMPAT_VERSION,
        protocol=3,
        backend="mg21_gecko_gbl_v1",
        path="image.gbl",
        size=len(data),
        sha256=hashlib.sha256(data).hexdigest(),
        app_baudrate=460800,
        layout="mg21-app-4000-30000",
        qualified=True,
        qualification="qualification.json",
        source_commit="a" * 40,
        notices=["NOTICE"],
        bootloader_baudrate=115200,
    )
    (root / "image.gbl").write_bytes(data)
    (root / "qualification.json").write_text('{"synthetic": true}')
    (root / "NOTICE").write_text("Synthetic fixture; never flash")
    (root / "manifest.json").write_text(
        json.dumps(
            dict(schema=1, host_version=FIRMWARE_COMPAT_VERSION, artifacts=[entry])
        )
    )
    return entry


class CatalogTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        """Build a real isolated service, store and filesystem catalog."""
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.source = self.root / "source"
        self.catalog = self.root / "catalog"
        self.entry = fixture(self.source)
        self.config = replace(
            load(Path(__file__).parents[1] / "config.example.json"),
            database=str(self.root / "state.sqlite3"),
            firmware_bundle_dir=str(self.catalog),
            serial_baudrate=460800,
            web={"enabled": False},
            mqtt={"enabled": False},
        )
        self.store = Store(
            self.config.database, self.config.gateway + ":" + self.config.tag
        )
        self.service = Service(
            self.config,
            self.store,
            gateway_factory=Mock(side_effect=AssertionError("No radio")),
        )
        self.manager = self.service.firmware_manager
        self.service.ready = True
        self.caps = dict(
            board="zb-gw04-1v2", fw_version="0.12", baud="460800", proto="3"
        )
        self.helper = patch(
            "livolod.firmware_backends.backend_status", return_value=None
        )
        self.helper.start()
        self.manager.observe(self.caps)
        self.manager.version_info = dict(self.caps)
        self.manager.update_info = {"preserve": "facts"}

    async def asyncTearDown(self):
        """Release the isolated database and temporary catalog files."""
        self.helper.stop()
        self.store.close()
        self.temp.cleanup()

    def publish(self):
        """Promote the current source using the real operator provisioning helper."""
        return provision(self.source, self.catalog, FIRMWARE_COMPAT_VERSION)

    async def test_publish_refresh_replace_remove_without_radio_or_database_writes(
        self,
    ):
        """All clients see revised eligibility while live facts and SQLite remain intact."""
        before = self.store.db.total_changes
        self.publish()
        notify = Mock()
        (
            self.service.add_listener(notify)
            if hasattr(self.service, "add_listener")
            else None
        )
        with patch.object(self.service, "notify_changed") as changed:
            await self.manager.refresh_catalog()
            self.assertTrue(self.manager.snapshot()["update_available"])
            first = self.manager.candidate["id"]
            changed.assert_called_once()
            changed.reset_mock()
            await self.manager.refresh_catalog()
            changed.assert_not_called()
            fixture(self.source, 1)
            self.publish()
            await self.manager.refresh_catalog()
            self.assertNotEqual(self.manager.candidate["id"], first)
            (self.catalog / "manifest.json").unlink()
            await self.manager.refresh_catalog()
            self.assertFalse(self.manager.snapshot()["install_supported"])
            self.assertEqual(self.manager.reason, "bundle_missing")
        self.assertEqual(self.manager.version_info, self.caps)
        self.assertEqual(self.manager.update_info, {"preserve": "facts"})
        self.assertEqual(self.store.db.total_changes, before)
        self.service.gateway_factory.assert_not_called()

    async def test_corrupt_bytes_and_helper_loss_withdraw_install(self):
        """Tampering and missing helpers never leave stale installation permission."""
        self.publish()
        await self.manager.refresh_catalog()
        manifest = json.loads((self.catalog / "manifest.json").read_text())
        path = self.catalog / manifest["artifacts"][0]["path"]
        data = path.read_bytes()
        path.write_bytes(data[:-1] + bytes([data[-1] ^ 1]))
        await self.manager.refresh_catalog()
        self.assertIsNone(self.manager.candidate)
        path.write_bytes(data)
        await self.manager.refresh_catalog()
        with patch(
            "livolod.firmware_backends.backend_status",
            return_value="backend_unavailable",
        ):
            await self.manager.refresh_catalog()
        self.assertEqual(
            self.manager.snapshot()["blocked_reason"], "backend_unavailable"
        )

    async def test_admission_rereads_before_cached_candidate_can_start(self):
        """Removal or hash replacement between refresh and install rejects before handoff."""
        self.publish()
        await self.manager.refresh_catalog()
        original = self.manager.candidate["id"]
        fixture(self.source, 1)
        self.publish()
        result = self.manager.start(dict(request_id="stale", candidate_id=original))
        self.assertEqual(result["status"], "rejected")
        self.assertFalse(self.manager.requested.is_set())
        self.assertIsNone(self.store.firmware_job())
        self.assertTrue(self.service.ready)

    async def test_refresh_during_job_keeps_target_and_durable_identity(self):
        """New catalog metadata cannot retarget an accepted or recovery-blocked job."""
        self.publish()
        await self.manager.refresh_catalog()
        original = self.manager.candidate["id"]
        result = self.manager.start(dict(request_id="job", candidate_id=original))
        self.assertEqual(result["status"], "pending")
        job = dict(self.manager.job)
        fixture(self.source, 1)
        self.publish()
        await self.manager.refresh_catalog()
        self.assertEqual(self.manager.job, job)
        self.assertNotEqual(self.manager.candidate["id"], original)
        self.assertEqual(self.manager.snapshot()["candidate_id"], original)
        self.assertFalse(self.manager.snapshot()["install_supported"])
        self.manager.failed("Synthetic uncertainty", uncertain=True)
        job = dict(self.manager.job)
        await self.manager.refresh_catalog()
        self.assertEqual(self.manager.job, job)
        self.assertEqual(self.manager.snapshot()["candidate_id"], original)

    async def test_stale_background_result_discarded_after_session_change(self):
        """An old scan cannot publish metadata into a new radio/configuration context."""
        self.publish()
        started = threading.Event()
        release = threading.Event()
        from livolod.firmware_manager import scan_catalog

        def blocked(*args):
            """Delay a real filesystem scan while the owner advances its session.

            @param args Captured scan inputs from the owner loop.
            @return Real scan result after the test permits continuation.
            """
            started.set()
            release.wait(3)
            return scan_catalog(*args)

        with patch("livolod.firmware_manager.scan_catalog", side_effect=blocked):
            task = asyncio.create_task(self.manager.refresh_catalog())
            await asyncio.to_thread(started.wait, 3)
            self.service.session = "new-session"
            release.set()
            self.assertFalse(await task)
        self.assertIsNone(self.manager.candidate)

    def test_invalid_source_does_not_replace_active_manifest(self):
        """Publication validates staged bytes before exposing any new manifest."""
        self.publish()
        before = (self.catalog / "manifest.json").read_bytes()
        (self.source / "image.gbl").write_bytes(b"bad")
        with self.assertRaises(BundleError):
            self.publish()
        self.assertEqual((self.catalog / "manifest.json").read_bytes(), before)
        self.assertEqual(
            select_candidate(
                self.catalog,
                board="zb-gw04-1v2",
                host_version=FIRMWARE_COMPAT_VERSION,
                app_baudrate=460800,
            )["version"],
            FIRMWARE_COMPAT_VERSION,
        )

    def test_recreated_service_sees_persistent_catalog(self):
        """A new daemon instance uses persistent app data, not modified package files."""
        self.publish()
        from livolod.firmware_manager import FirmwareManager

        fresh = FirmwareManager(self.service)
        fresh.observe(self.caps)
        self.assertTrue(fresh.snapshot()["update_available"])
        self.assertTrue((self.catalog / "objects").is_dir())
        self.assertFalse((self.source / "objects").exists())

    def test_catalog_links_and_digest_mismatch_rejected(self):
        """Operator mistakes cannot redirect validation or bypass explicit provenance."""
        with self.assertRaises(ValueError):
            provision(self.source, self.catalog, FIRMWARE_COMPAT_VERSION, "0" * 64)
        (self.source / "NOTICE").unlink()
        (self.source / "NOTICE").symlink_to("/etc/hosts")
        with self.assertRaises(BundleError):
            self.publish()

    def test_interrupted_promotion_keeps_old_catalog_readable(self):
        """A failure after staging new objects leaves the previous manifest authoritative."""
        self.publish()
        before = (self.catalog / "manifest.json").read_bytes()
        fixture(self.source, 2)
        from livolod.firmware_catalog import _write

        def interrupt(path, data):
            """Fail at the manifest commit point after immutable objects are present.

            @param path Intended publication target.
            @param data Immutable bytes to publish.
            """
            if path == self.catalog / "manifest.json":
                raise OSError("Synthetic interruption")
            _write(path, data)

        with patch("livolod.firmware_catalog._write", side_effect=interrupt):
            with self.assertRaises(OSError):
                self.publish()
        self.assertEqual((self.catalog / "manifest.json").read_bytes(), before)
        self.assertEqual(
            select_candidate(
                self.catalog,
                board="zb-gw04-1v2",
                host_version=FIRMWARE_COMPAT_VERSION,
                app_baudrate=460800,
            )["id"],
            "test:" + self.entry["sha256"],
        )
        self.publish()
        self.assertNotEqual((self.catalog / "manifest.json").read_bytes(), before)

    def test_cli_publication_needs_no_config_database_or_radio(self):
        """Administrative publication is independent of daemon/transport ownership."""
        from livolod.firmware_cli import main

        output = io.StringIO()
        before = self.store.db.total_changes
        with (
            patch("livolod.firmware_cli.UpdatePort") as port,
            patch("livolod.firmware_cli.Service") as service,
            redirect_stdout(output),
        ):
            self.assertEqual(
                main(
                    [
                        "catalog",
                        "--source",
                        str(self.source),
                        "--destination",
                        str(self.catalog),
                    ]
                ),
                0,
            )
        self.assertEqual(json.loads(output.getvalue())["artifacts"], 1)
        port.assert_not_called()
        service.assert_not_called()
        self.assertEqual(self.store.db.total_changes, before)

    def test_empty_catalog_is_an_explicit_atomic_withdrawal(self):
        """A validated empty catalog preserves prior manifests for operator rollback."""
        self.publish()
        (self.source / "manifest.json").write_text(
            json.dumps(
                dict(schema=1, host_version=FIRMWARE_COMPAT_VERSION, artifacts=[])
            )
        )
        self.assertEqual(self.publish()["artifacts"], 0)
        self.assertTrue(list((self.catalog / "history").glob("*.json")))
        with self.assertRaises(BundleError):
            select_candidate(
                self.catalog,
                board="zb-gw04-1v2",
                host_version=FIRMWARE_COMPAT_VERSION,
                app_baudrate=460800,
            )
