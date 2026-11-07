"""Experimental UART transport identity must never imply gateway readiness."""

from contextlib import closing
from dataclasses import replace
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock

from livolod.config import load
from livolod.service import Service
from livolod.storage import Store
from livolod.uart import LinkError


class XiaoTransportTests(unittest.IsolatedAsyncioTestCase):
    """Exercise the real restore gate with the private transport-only wire contract."""

    async def test_transport_only_caps_never_configure_arm_or_transmit(self):
        """Both opaque XIAO identities remain not-ready with zero RF capabilities."""
        for board in ("seeed-xiao-mg24", "seeed-xiao-esp32c6"):
            for mode in ("disabled", "experimental_phase"):
                with self.subTest(board=board, mode=mode):
                    with tempfile.TemporaryDirectory() as directory:
                        config = replace(
                            load(Path(__file__).parents[1] / "config.example.json"),
                            database=directory + "/state.db",
                            serial_baudrate=115200,
                            output_mode=mode,
                        )
                        with closing(Store(config.database, "network")) as store:
                            service = Service(config, store)
                            replies = {
                                "PING": "OK EC-GATEWAY 3",
                                "VERSION": (
                                    f"OK fw_version=0.14 board={board} proto=3 "
                                    "update=none experimental=1 build=xiao-bringup-1"
                                ),
                                "CAPS": (
                                    "OK proto=3 peers=0 ack_mask=00 max_raw=0 "
                                    "max_line=319 command_slots=0 request_cache=0 "
                                    "lease_max=0 session_bytes=0 baud=115200 "
                                    f"board={board} fw_version=0.14 update=none "
                                    "experimental=1 gateway=0 radio_tx=0"
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
