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
        for board, mode, registered, rf_bench in product(
            ("seeed-xiao-mg24", "seeed-xiao-esp32c6"),
            ("disabled", "experimental_phase"),
            (False, True),
            (False, True),
        ):
            with self.subTest(
                board=board, mode=mode, registered=registered, rf_bench=rf_bench
            ):
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
                        build = "xiao-rf-1" if rf_bench else "xiao-bringup-1"
                        replies = {
                            "PING": "OK EC-GATEWAY 3",
                            "VERSION": (
                                f"OK fw_version=0.14 board={board} proto=3 "
                                f"update=none experimental=1 build={build}"
                            ),
                            "CAPS": (
                                "OK proto=3 peers=0 ack_mask=00 max_raw=0 "
                                "max_line=319 command_slots=0 request_cache=0 "
                                "lease_max=0 session_bytes=0 channel=11 baud=115200 "
                                f"board={board} fw_version=0.14 update=none "
                                "experimental=1 gateway=0 "
                                + (
                                    "radio_tx=synthetic_only rf_diag=synthetic_v1"
                                    if rf_bench
                                    else "radio_tx=0"
                                )
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
