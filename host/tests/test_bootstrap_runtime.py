"""Real normal-service lifecycle with fake UART and no radio/deployment access."""

import asyncio
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from livolod.bootstrap_runtime import BootstrapRuntime
from livolod.native_api import NativeAPI
from livolod.preserved_gecko import GeckoTarget
from livolod.simulator import Simulator
from livolod.storage import Store


class RuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        """Use normal Service and Store with a strictly in-process UART simulator."""
        self.temp = tempfile.TemporaryDirectory()
        self.raw = json.loads(
            (Path(__file__).parents[1] / "config.example.json").read_text()
        )
        self.raw.update(
            database=self.temp.name + "/state.db",
            mqtt={"enabled": False},
            web={"enabled": False},
            output_mode="disabled",
        )
        self.raw["api"] = dict(
            enabled=True, host="127.0.0.1", port=8090, token_env="TEST_TOKEN"
        )
        self.settings = dict(serial_port="/dev/fixture", lease_directory=self.temp.name)
        self.runtime = BootstrapRuntime(
            self.raw, self.settings, gateway_factory=Simulator, ready_timeout=1
        )
        self.network = {k: self.raw["network"][k] for k in ("gateway", "tag")}

    async def asyncTearDown(self):
        """Close normal tasks and database before removing fixture storage."""
        await self.runtime.enter()
        self.temp.cleanup()

    async def test_real_service_preserves_registry_and_network_across_handoff(self):
        """An explicit roundtrip restores the same durable peers and network identity."""
        await self.runtime.handoff(self.network)
        first = self.runtime.service
        before = self.runtime.store.db.execute(
            "SELECT id,value FROM devices ORDER BY id"
        ).fetchall()
        self.assertTrue(first.ready)
        await self.runtime.enter()
        self.assertTrue(self.runtime.quiesced)
        self.assertIsNone(self.runtime.service)
        reopened = Store(
            self.raw["database"], self.network["gateway"] + ":" + self.network["tag"]
        )
        reopened.close()
        await self.runtime.handoff(self.network)
        self.assertEqual(
            self.runtime.store.db.execute(
                "SELECT id,value FROM devices ORDER BY id"
            ).fetchall(),
            before,
        )
        self.assertIsNot(first, self.runtime.service)
        self.assertTrue(self.runtime.service.ready)

    async def test_fence_rejects_commands_while_websocket_close_is_waiting(self):
        """Maintenance admission closes before the first awaited client shutdown."""
        await self.runtime.handoff(self.network)
        service = self.runtime.service
        api = NativeAPI(service, token="fixture")
        api.loop = asyncio.get_running_loop()
        self.runtime.api = api
        barrier = asyncio.Event()
        entered = asyncio.Event()

        async def blocked_drop(ws):
            """Hold the close await to expose concurrent request admission.

            @param ws Synthetic subscription identity.
            """
            entered.set()
            await barrier.wait()

        api.clients[object()] = None
        api.drop = blocked_drop
        self.runtime.fence()
        task = asyncio.create_task(self.runtime.enter())
        await entered.wait()
        self.assertIsNone(api.service)
        result = service.submit(
            "remove", dict(request_id="late", device=next(iter(service.devices)))
        )
        self.assertEqual(result["error"], "radio_setup_active")
        self.assertEqual(
            service.start_firmware_update({})["error"], "guided_setup_required"
        )
        barrier.set()
        await task
        self.runtime.api = None

    async def test_body_read_cannot_enter_old_native_service_after_detach(self):
        """A request authorized before setup is checked again after its body await."""
        await self.runtime.handoff(self.network)
        service = self.runtime.service
        api = NativeAPI(service, token="fixture")
        started = asyncio.Event()
        release = asyncio.Event()

        async def read():
            """Simulate a slow client whose body arrives after maintenance admission."""
            started.set()
            await release.wait()
            return json.dumps(
                dict(action="remove", payload=dict(device="10203040"))
            ).encode()

        task = asyncio.create_task(
            api.command(SimpleNamespace(content_type="application/json", read=read))
        )
        await started.wait()
        api.service = None
        release.set()
        response = await task
        self.assertEqual(response.status, 503)

    async def test_legacy_recovery_remains_fenced_without_creating_database(self):
        """A preexisting unresolved updater cannot be bypassed using a separate journal."""
        self.assertFalse(self.runtime.has_gateway_state())
        self.assertIsNone(self.runtime.admission_reason())
        self.assertFalse(self.runtime.has_gateway_state())
        await self.runtime.handoff(self.network)
        self.runtime.store.db.execute(
            "INSERT OR REPLACE INTO meta VALUES (?,?)",
            (
                "firmware_job",
                json.dumps(dict(status="unknown", recovery="recovery_required")),
            ),
        )
        self.runtime.store.db.commit()
        await self.runtime.enter()
        self.assertEqual(self.runtime.admission_reason(), "existing_update_unresolved")

    async def test_gateway_uses_canonical_target_and_rejects_wrong_opened_fd(self):
        """Changing a configured alias cannot move I/O outside the physical lease."""
        target = GeckoTarget(
            "/dev/ttyUSB0",
            "devices/fixture/usb1/1-2",
            "generation",
            (188, 0),
            "00",
            "1.9.2",
        )
        self.runtime.raw["serial_port"] = "/dev/serial/by-id/alias"
        self.runtime.ownership = Mock()
        self.runtime.ownership.acquire.return_value = (target, Mock())
        gateway = Mock()
        gateway.serial.fileno.return_value = 17
        metadata = SimpleNamespace(st_rdev=os.makedev(18, 1))
        with (
            patch(
                "livolod.bootstrap_runtime.Gateway.open", return_value=gateway
            ) as opened,
            patch("livolod.bootstrap_runtime.os.fstat", return_value=metadata),
        ):
            with self.assertRaises(ValueError):
                self.runtime._gateway()
        opened.assert_called_once_with("/dev/ttyUSB0", self.raw["serial_baudrate"])
        gateway.serial.close.assert_called_once()
        gateway.command.assert_not_called()

    async def test_guided_profile_does_not_advertise_legacy_install(self):
        """Clients learn the supported route before offering an actionable update."""
        await self.runtime.handoff(self.network)
        snapshot = self.runtime.service.firmware_update_snapshot()
        preflight = self.runtime.service.firmware_preflight()
        self.assertFalse(snapshot["install_supported"])
        self.assertEqual(snapshot["blocked_reason"], "guided_setup_required")
        self.assertFalse(preflight["executable"])

    async def test_pending_handoff_attachment_fences_until_durable_completion(self):
        """A changed attachment is rejected before UART until handoff completed durably."""
        from dataclasses import replace
        from livolod.bootstrap_worker import target_json

        old = GeckoTarget(
            "/dev/fixture", "devices/fixture/usb1/1-2", "old", (18, 0), "00", "1.9.2"
        )
        fresh = replace(old, attachment="after-host-reboot")
        self.runtime.ownership = Mock()
        self.runtime.ownership.acquire.return_value = (fresh, Mock())
        self.runtime.journal = Mock()
        self.runtime.journal.meta.return_value = target_json(old)
        with patch("livolod.bootstrap_runtime.Gateway.open") as opened:
            with self.assertRaises(ValueError):
                self.runtime._gateway()
            opened.assert_not_called()
        self.runtime.journal.meta.return_value = None
        gateway = Mock()
        gateway.serial.fileno.return_value = 17
        with (
            patch("livolod.bootstrap_runtime.Gateway.open", return_value=gateway),
            patch(
                "livolod.bootstrap_runtime.os.fstat",
                return_value=SimpleNamespace(st_rdev=os.makedev(18, 0)),
            ),
        ):
            self.assertIs(self.runtime._gateway(), gateway)
        self.runtime.ownership.acquire.assert_any_call(allow_reattach=True)
