import asyncio
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
from livolod import __version__
from livolod.config import load
from livolod.service import Service
from livolod.simulator import Simulator
from livolod.storage import Store
from livolod.uart import LinkError
from livolod.version import parse_version, firmware_info


class VersionTests(unittest.TestCase):
    def test_numeric_major_minor_and_unknown_legacy(self):
        """Verify numeric major minor and unknown legacy."""
        self.assertGreater(parse_version("0.10"), parse_version("0.9"))
        for value in ("0.1.0", "01.2", "1", "1.-1", "1.2-dev", "65536.0", True):
            with self.assertRaises(ValueError):
                parse_version(value)
        self.assertIsNone(firmware_info({})["running_version"])
        self.assertIsNone(firmware_info({})["update_available"])
        self.assertTrue(firmware_info({"fw_version": "0.1"})["update_available"])
        self.assertFalse(firmware_info({"fw_version": __version__})["update_available"])


class TrialTests(unittest.IsolatedAsyncioTestCase):
    async def exercise(self, firmware="0.2", bad_confirm=False):
        """Run startup confirmation with the requested firmware-version behavior.

        @param firmware Firmware release reported during startup.
        @param bad_confirm Whether durable firmware confirmation should fail.
        """
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        config = replace(
            load(Path(__file__).parents[1] / "config.example.json"),
            database=temp.name + "/db",
        )
        store = Store(config.database, "network")
        self.addCleanup(store.close)
        service = Service(config, store)
        gateway = Simulator()
        service.gateway = gateway
        original = gateway.command
        history = []
        trial = True

        async def command(text, retries=1):
            """Provide the scenario-specific UART response or injected failure.

            @param text Command or encoded protocol text for this exchange.
            @param retries Additional UART attempts allowed by the caller.
            """
            nonlocal trial
            history.append(text)
            if text == "CAPS":
                return (
                    await original(text, retries)
                    + f" fw_version={firmware} boot_version=0.2 update=uart_signed_v1"
                )
            if text == "VERSION":
                return f"OK fw_version={firmware}"
            if text == "UPDATE_STATUS":
                return (
                    "OK trial=1 state=fffffff8"
                    if trial
                    else "OK trial=0 state=fffffff0"
                )
            if text == "UPDATE_CONFIRM":
                if not bad_confirm:
                    trial = False
                return "OK CONFIRMED"
            return await original(text, retries)

        gateway.command = command
        return service, store, history

    async def test_confirms_after_session_restoration_and_persists_version(self):
        """Verify confirms after session restoration and persists version."""
        service, store, history = await self.exercise()
        await service.restore()
        self.assertTrue(service.ready)
        self.assertLess(
            next(i for i, x in enumerate(history) if x.startswith("ARM ")),
            history.index("UPDATE_CONFIRM"),
        )
        status = json.loads(
            store.db.execute(
                "SELECT value FROM meta WHERE key='firmware_status'"
            ).fetchone()[0]
        )
        self.assertEqual(status["running_version"], __version__)
        self.assertFalse(status["trial"])

    async def test_wrong_release_or_failed_durable_confirmation_is_not_ready(self):
        """Verify wrong release or failed durable confirmation is not ready."""
        for firmware, bad in [("0.3", False), ("0.2", True)]:
            service, store, history = await self.exercise(firmware, bad)
            with self.assertRaises(LinkError):
                await service.restore()
            self.assertFalse(service.ready)
            if firmware == "0.3":
                self.assertNotIn("UPDATE_CONFIRM", history)
