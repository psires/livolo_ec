"""Experimental UART transport identity must never imply gateway readiness."""

from contextlib import closing
from dataclasses import replace
from itertools import product
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock

from livolod.config import load
from livolod.service import Service
from livolod.storage import Store
from livolod.uart import LinkError


class XiaoTransportTests(unittest.IsolatedAsyncioTestCase):
    """Exercise the real restore gate with diagnostic-only wire contracts."""

    async def test_diagnostic_caps_never_configure_arm_or_transmit(self):
        """Diagnostic identities cannot arm a gateway, even with no registered peers."""
        profiles = list(
            product(
                ("seeed-xiao-mg24", "seeed-xiao-esp32c6"),
                ("xiao-bringup-1", "xiao-rf-1", "xiao-rx-1"),
            )
        ) + [
            ("seeed-xiao-mg24", "xiao-reject-1"),
            ("seeed-xiao-esp32c6", "xiao-usb-1"),
            ("seeed-xiao-mg24", "xiao-queue-1"),
            ("seeed-xiao-esp32c6", "xiao-usb-2"),
            ("seeed-xiao-mg24", "xiao-uart-1"),
            ("seeed-xiao-esp32c6", "xiao-rxdiag-1"),
        ]
        for (board, build), mode, registered in product(
            profiles, ("disabled", "experimental_phase"), (False, True)
        ):
            with self.subTest(
                board=board, mode=mode, registered=registered, build=build
            ):
                wire_build = (
                    "xiao-rx-2"
                    if board == "seeed-xiao-mg24" and build == "xiao-rx-1"
                    else build
                )
                with tempfile.TemporaryDirectory() as directory:
                    config = replace(
                        load(Path(__file__).parents[1] / "config.example.json"),
                        database=directory + "/state.db",
                        serial_baudrate=115200,
                        output_mode=mode,
                    )
                    if not registered:
                        config = replace(config, devices=())
                    with closing(Store(config.database, "network")) as store:
                        service = Service(config, store)
                        capabilities = "radio_tx=0"
                        if build != "xiao-bringup-1":
                            capabilities = (
                                "radio_tx=synthetic_only rf_diag=synthetic_v1"
                            )
                        if build in (
                            "xiao-rx-1",
                            "xiao-usb-1",
                            "xiao-queue-1",
                            "xiao-usb-2",
                            "xiao-uart-1",
                            "xiao-rxdiag-1",
                        ):
                            capabilities += " rx_diag=passive_v1"
                        if build in (
                            "xiao-queue-1",
                            "xiao-usb-2",
                            "xiao-uart-1",
                            "xiao-rxdiag-1",
                        ):
                            capabilities += " queue_diag=ring_v1"
                        if build in ("xiao-uart-1", "xiao-rxdiag-1"):
                            capabilities += " adapter_diag=handoff_v1"
                        if build == "xiao-reject-1":
                            capabilities = (
                                "radio_tx=0 rf_diag=none rx_diag=passive_v1 "
                                "reject_diag=untrusted_v1"
                            )
                        replies = {
                            "PING": "OK EC-GATEWAY 3",
                            "VERSION": (
                                f"OK fw_version=0.14 board={board} proto=3 "
                                f"update=none experimental=1 build={wire_build}"
                            ),
                            "CAPS": (
                                "OK proto=3 peers=0 ack_mask=00 max_raw=0 "
                                "max_line=319 command_slots=0 request_cache=0 "
                                "lease_max=0 session_bytes=0 channel=11 baud=115200 "
                                f"board={board} fw_version=0.14 update=none "
                                "experimental=1 gateway=0 " + capabilities
                            ),
                        }
                        service.gateway = AsyncMock()
                        service.gateway.command.side_effect = replies.__getitem__
                        # Also prove a previously ready service loses readiness.
                        service.ready = True
                        with self.assertRaisesRegex(LinkError, "capabilities"):
                            await service.restore()
                        self.assertFalse(service.ready)
                        self.assertIsNone(service.phase_client)
                        self.assertIsNone(service.scheduler)
                        self.assertEqual(
                            [
                                call.args[0]
                                for call in service.gateway.command.await_args_list
                            ],
                            ["PING", "CAPS", "VERSION"],
                        )
