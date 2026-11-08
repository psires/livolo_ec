"""Offline bounds and fail-stop checks for the RF-off lifecycle experiment."""

import unittest
from unittest.mock import patch

import xiao_c6_serial_probe as probe
from test_xiao_rf_bench import Clock


class Port:
    """Count explicit lifecycle calls without accessing hardware."""

    def __init__(self):
        """Initialize closed fixture counters."""
        self.opens = self.closes = 0

    def open(self):
        """Count exactly one requested open."""
        self.opens += 1

    def close(self):
        """Count cleanup without changing device state."""
        self.closes += 1


class ProbeTests(unittest.TestCase):
    """Exercise control flow, transcript retention and no automatic recovery."""

    def setUp(self):
        """Build fixed diagnostic replies and injectable failure points."""
        self.clock = Clock()
        self.port = Port()
        self.commands = []
        self.health_error = False
        self.build = "xiao-rx-1"
        self.busy = "0"
        self.evidence = {}

    def command(self, link, text, links, timeout, clock, sleep):
        """Supply read-only responses and retain bytes that precede the first reply.

        @param link Current fixture link.
        @param text Requested diagnostic command.
        @param links Owned link list.
        @param timeout Per-request bound.
        @param clock Virtual monotonic clock.
        @param sleep Virtual scheduler function.
        @return Successful response, or an injected timeout without recovery.
        """
        self.commands.append(text)
        self.assertLessEqual(timeout, 2)
        self.assertEqual(links, [link])
        if text == "PING":
            link.timeline.append(
                dict(line="ROM pre-reply fixture", host_monotonic=clock())
            )
            return "OK EC-GATEWAY 3"
        if text in ("VERSION", "CAPS"):
            return f"OK board=seeed-xiao-esp32c6 proto=3 experimental=1 update=none build={self.build} gateway=0 peers=0 ack_mask=00"
        if text == "RFSTATUS":
            return f"OK active=0 busy={self.busy} fault=0 queued=0 auto_ack=0 retries=0 channel=26 power_dbm=0 tx_started=0 tx_done=0 tx_failed=0 rx=0 dropped=0 bad_crc=unavailable ticks_hz=1000000 timestamp=callback"
        if text == "RFHEALTH":
            if self.health_error:
                link.buffer.extend(b"partial")
                raise TimeoutError("injected health timeout")
            return f"OK counter_bits=32 uptime_ms={int(clock()*1000)} reset_reason=11 task_wdt_events=0 read_error=0 write_error=0 pending=0"
        self.fail(f"Unexpected command {text}")

    def execute(self):
        """Replace only diagnostic delivery and waiting; execute real probe control."""
        with (
            patch.object(probe.rf, "command", side_effect=self.command),
            patch.object(
                probe.rf,
                "pump",
                side_effect=lambda links, until, clock, sleep: sleep(until - clock()),
            ),
        ):
            probe.run(self.port, self.evidence, self.clock.time, self.clock.sleep)

    def test_fixed_three_opens_and_closed_gaps(self):
        """A completed run has no RF writes, reopen retries or variable wait lengths."""
        self.execute()
        self.assertTrue(self.evidence["completed"])
        self.assertEqual((self.port.opens, self.port.closes), (3, 3))
        self.assertEqual(
            self.commands,
            ["PING", "VERSION", "CAPS", "RFSTATUS", "RFHEALTH", "RFSTATUS", "RFHEALTH"]
            * 3,
        )
        self.assertEqual(self.clock.time(), 31)
        self.assertEqual(len(self.evidence["cycles"]), 3)
        for cycle in self.evidence["cycles"]:
            self.assertEqual(cycle["transcript"][0]["line"], "ROM pre-reply fixture")
            self.assertEqual(
                cycle["after_hold"]["host_monotonic"]
                - cycle["before_hold"]["host_monotonic"],
                7,
            )

    def test_timeout_preserves_partial_data_and_stops(self):
        """A missing reply closes once and retains evidence without another open."""
        self.health_error = True
        with self.assertRaises(TimeoutError):
            self.execute()
        self.assertFalse(self.evidence["completed"])
        self.assertEqual((self.port.opens, self.port.closes), (1, 1))
        self.assertEqual(
            self.evidence["cycles"][0]["incomplete_line_hex"], b"partial".hex()
        )
        self.assertEqual(self.commands[-1], "RFHEALTH")

    def test_wrong_build_or_busy_stops_before_health(self):
        """Unreviewed firmware or active work cannot advance into the experiment."""
        for field, value in (("build", "other"), ("busy", "1")):
            with self.subTest(field=field):
                self.setUp()
                setattr(self, field, value)
                with self.assertRaises(ValueError):
                    self.execute()
                self.assertEqual((self.port.opens, self.port.closes), (1, 1))
                self.assertNotIn("RFHEALTH", self.commands)


if __name__ == "__main__":
    unittest.main()
