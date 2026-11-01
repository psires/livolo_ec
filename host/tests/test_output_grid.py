"""Reject stale/mismatched retained confirmations and preserve the output bounds."""

import unittest
from livolod.protocol import frame
from livolod.scheduler import SchedulerClient
from livolod.uart import LinkError
from test_daemon import report
from test_scheduler import CAPABILITY


class OutputGridHostTests(unittest.IsolatedAsyncioTestCase):
    async def test_capability_gate_and_output_confirmation_validation(self):
        """Reject unsupported output jobs and stale, mismatched or unbounded results."""
        raw = frame(
            "2a992b12", "a7", "46715621", "0002", 1, 2, bytes((1, 0, 1, 1, 0, 0, 0, 7))
        )
        fields = dict(
            id=1,
            state="done",
            outcome=1,
            started=0xFFFFFF00,
            deadline=(0xFFFFFF00 + 4000000) & 0xFFFFFFFF,
            rx_ticks=100000,
            entry_ticks=50000,
            rounds=0,
            sent=1,
            attempts=1,
            retries=0,
            rx_sequence=1,
            rssi=-40,
            lqi=60,
            report=report(bits=1, mask=1, counter=2, token=7).split()[-1],
            output=1,
            grid=1,
        )
        history = []

        class Stub:
            async def command(self, text):
                """Record submission and return the mutable retained result fixture.

                @param text UART command submitted by the scheduler client.
                """
                history.append(text)
                if text.startswith("OUTPUT_GRID"):
                    return "OK OUTPUT"
                return "OK QUERY " + " ".join(f"{k}={v}" for k, v in fields.items())

        caps = dict(
            CAPABILITY, scheduler_hz="1000000", query="transaction_v1", query_slots="1"
        )
        old = SchedulerClient(Stub(), "session", caps)
        with self.assertRaises(ValueError):
            await old.start_output(raw, 3, b"\xff" * 3)
        self.assertFalse(history)
        client = SchedulerClient(
            Stub(), "session", dict(caps, output_grid="transaction_200ms_v1")
        )
        self.assertEqual(await client.start_output(raw, 3, b"\xff" * 3), 1)
        self.assertTrue(history[-1].startswith("OUTPUT_GRID session 1 3 ffffff "))
        baseline = bytes((0, 1, 6))
        self.assertEqual(
            (await client.query_result(1, raw, 3, output=True, baseline=baseline))[
                "outcome"
            ],
            "1",
        )
        for key, bad in (
            ("attempts", 7),
            ("rounds", 1),
            ("output", 0),
            ("rx_ticks", fields["deadline"]),
            ("entry_ticks", 100001),
        ):
            saved = fields[key]
            fields[key] = bad
            with self.assertRaises(LinkError):
                await client.query_result(1, raw, 3, output=True, baseline=baseline)
            fields[key] = saved
        with self.assertRaises(LinkError):
            await client.query_result(1, raw, 3, output=True, baseline=bytes((1, 2, 7)))
        for kwargs in ({"token": 8}, {"mask": 2}, {"bits": 0}):
            values = dict(bits=1, mask=1, counter=2, token=7)
            values.update(kwargs)
            fields["report"] = report(**values).split()[-1]
            with self.assertRaises(LinkError):
                await client.query_result(1, raw, 3, output=True, baseline=baseline)
