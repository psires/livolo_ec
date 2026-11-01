"""Run the shared core at MG21's native 1 MHz, including timer wrap and CCA."""

import test_uart_v3 as fixture
from test_uart_v3 import EPOCH, heartbeat, phased_frame, report


class MG21ClockTests(fixture.FirmwareUART):
    scheduler = True
    baudrate = 460800
    rtimer_hz = 1000000

    def query_start(self, tick=0xFFFF0000, ac_only=False, grid=False):
        """Install a heartbeat and query, by default near the 32-bit clock wrap.

        @param tick Initial query timestamp in wrapping 1 MHz radio ticks.
        @param ac_only Omit the preparation transmission when true.
        @param grid Align AC attempts to the heartbeat 200 ms grid.
        @return Initial query timestamp.
        """
        self.configured()
        self.arm()
        self.lib.test_set_rtimer((tick - 200000) & 0xFFFFFFFF)
        due = (tick - 100000) & 0xFFFFFFFF
        self.cmd(f"HEARTBEAT {EPOCH} {due} {heartbeat().hex()}")
        self.poll(due)
        self.lib.test_set_rtimer(tick)
        raw = bytearray(phased_frame())
        raw[22] = 0xAC if ac_only else 0x10
        raw[29:37] = bytes(7) + b"\x01"
        raw[37] = sum(raw[21:37]) & 255
        command = "QUERY_GRID" if grid else "QUERY"
        self.cmd(f"{command} {EPOCH} 1 235500 4012000 3 {raw.hex()}")
        return tick

    def poll(self, tick):
        """Dispatch at a selected wrapping radio-clock tick.

        @param tick Wrapping 1 MHz radio timestamp.
        """
        self.lib.test_set_rtimer(tick & 0xFFFFFFFF)
        self.lib.ec_gateway_poll()

    def query_status(self):
        """Read retained status through the real UART parser."""
        return dict(
            x.split("=", 1)
            for x in self.cmd(f"QUERY_GET {EPOCH} 1").split()
            if "=" in x
        )

    def test_microsecond_queries_keep_wall_time_and_retry_bounds_across_wrap(self):
        """A clock change must not compress three rounds or the 14-second deadline."""
        start = self.query_start()
        self.assertIn("scheduler_hz=1000000", self.cmd("CAPS"))
        self.assertIn("rtimer_hz=1000000", self.cmd("CAPS"))
        q = self.query_status()
        self.assertEqual((int(q["deadline"]) - start) & 0xFFFFFFFF, 14000000)
        expected = start
        for index in range(6):
            self.assertEqual(int(q["due"]), expected & 0xFFFFFFFF)
            self.poll(expected)
            q = self.query_status()
            self.assertEqual(self.sent()[22], (0x10, 0xAC)[index % 2])
            expected += (235500, 4012000)[index % 2]
        self.poll(expected)
        q = self.query_status()
        self.assertEqual(
            (q["outcome"], q["rounds"], q["sent"], q["attempts"]), ("2", "3", "6", "6")
        )

    def test_microsecond_cca_backoff_and_fresh_report_cancel(self):
        """Keep 25/50/75ms backoff and suppress the next send on a fresh report."""
        tick = self.query_start()
        self.lib.test_radio(2, 0)
        for attempt in range(1, 5):
            self.poll(tick)
            q = self.query_status()
            gap = (int(q["due"]) - tick) & 0xFFFFFFFF
            self.assertEqual(gap, attempt * 25000 if attempt < 4 else 235500)
            tick = int(q["due"])
        self.assertEqual((q["attempts"], q["retries"], q["sent"]), ("4", "3", "0"))
        self.lib.test_set_rtimer((tick - 1) & 0xFFFFFFFF)
        self.receive(report())
        count = self.lib.test_sends()
        self.poll(tick)
        self.assertEqual(self.lib.test_sends(), count)
        self.assertEqual(self.query_status()["outcome"], "1")

    def test_microsecond_heartbeat_epoch_and_disarm_flush(self):
        """Ten seconds stays ten seconds and disarm removes future work."""
        start = self.query_start()
        self.cmd(f"QUERY_CANCEL {EPOCH} 1")
        status = self.cmd(f"SCHED {EPOCH}")
        due = (start - 100000 + 10000000) & 0xFFFFFFFF
        self.assertIn(f"hb_due={due}", status)
        self.poll(due)
        self.assertIn("hb_sent=2", self.cmd(f"SCHED {EPOCH}"))
        self.cmd("DISARM")
        count = self.lib.test_sends()
        self.poll(due + 10000000)
        self.assertEqual(self.lib.test_sends(), count)

    def test_ac_only_preserves_slots_deadline_and_three_rounds(self):
        """Omit preparation RF while keeping every AC slot and the terminal wait."""
        start = self.query_start(ac_only=True)
        self.assertIn("query_ac=slot_preserved_v1", self.cmd("CAPS"))
        baseline = self.lib.test_sends()
        for i in range(3):
            t = start + i * 4247500
            self.poll(t)
            self.assertEqual(self.lib.test_sends(), baseline + i)
            self.assertEqual(int(self.query_status()["due"]), (t + 235500) & 0xFFFFFFFF)
            self.poll(t + 235500)
            self.assertEqual(self.sent()[22], 0xAC)
        self.poll(start + 3 * 4247500)
        q = self.query_status()
        self.assertEqual(
            (q["outcome"], q["rounds"], q["sent"], q["attempts"]), ("2", "3", "3", "3")
        )
        self.assertEqual((int(q["deadline"]) - start) & 0xFFFFFFFF, 14000000)

    def test_ac_only_four_cca_attempts_and_fresh_stop(self):
        """Retain AC CCA limits and suppress a retry after a fresh state arrives."""
        start = self.query_start(ac_only=True)
        self.poll(start)
        self.lib.test_radio(2, 0)
        tick = start + 235500
        for attempt in range(1, 5):
            self.poll(tick)
            q = self.query_status()
            self.assertEqual(int(q["attempts"]), attempt)
            self.assertEqual(
                (int(q["due"]) - tick) & 0xFFFFFFFF,
                attempt * 25000 if attempt < 4 else 4012000,
            )
            tick = int(q["due"])
        self.assertEqual((q["retries"], q["sent"]), ("3", "0"))
        self.lib.test_set_rtimer((tick - 1) & 0xFFFFFFFF)
        self.receive(report())
        count = self.lib.test_sends()
        self.poll(tick)
        self.assertEqual(self.lib.test_sends(), count)
        self.assertEqual(self.query_status()["outcome"], "1")

    def test_grid_ac_slots_wrap_and_unchanged_round_deadline(self):
        """Schedule real AC entries on 200ms boundaries without stamping a fiction."""
        start = self.query_start(ac_only=True, grid=True)
        self.assertIn("query_grid=heartbeat_200ms_v1", self.cmd("CAPS"))
        count = self.lib.test_sends()
        for index, offset in enumerate((300000, 4700000, 9100000)):
            self.poll(int(self.query_status()["due"]))  # Silent preparation slot.
            self.poll(int(self.query_status()["due"]))  # Eligibility -> grid target.
            self.assertEqual(
                int(self.query_status()["due"]), (start + offset) & 0xFFFFFFFF
            )
            self.assertEqual(self.lib.test_sends(), count + index)
            self.poll(start + offset)
            q = self.query_status()
            self.assertEqual(self.sent()[22], 0xAC)
            self.assertEqual(int(q["age_ticks"]) % 200000, 0)
            self.assertEqual(q["grid"], "1")
        self.poll(int(self.query_status()["due"]))
        q = self.query_status()
        self.assertEqual((q["outcome"], q["rounds"], q["attempts"]), ("2", "3", "3"))
        self.assertEqual((int(q["deadline"]) - start) & 0xFFFFFFFF, 14000000)

    def test_grid_cca_attempt_budget_and_fresh_stop(self):
        """CCA waits retain their minima, align retries, and stop on fresh reports."""
        start = self.query_start(ac_only=True, grid=True)
        self.poll(start)
        self.poll(start + 235500)
        self.lib.test_radio(2, 0)
        for attempt in range(1, 5):
            self.poll(start + 300000 + (attempt - 1) * 200000)
            q = self.query_status()
            self.assertEqual(int(q["attempts"]), attempt)
            due = int(q["due"])
            if attempt < 4:
                self.assertEqual(
                    (due - int(q["entry_ticks"])) & 0xFFFFFFFF, attempt * 25000
                )
                self.poll(due)
        self.assertEqual((q["retries"], q["rounds"], q["sent"]), ("3", "1", "0"))
        self.lib.test_set_rtimer((due - 1) & 0xFFFFFFFF)
        self.receive(report())
        count = self.lib.test_sends()
        self.poll(due)
        self.assertEqual(self.lib.test_sends(), count)
        self.assertEqual(self.query_status()["outcome"], "1")

    def test_grid_skips_missed_slot_and_stops_at_deadline(self):
        """Lateness cannot force off-grid sends or extend the transaction budget."""
        start = self.query_start(ac_only=True, grid=True)
        self.poll(start)
        self.poll(start + 235500)
        count = self.lib.test_sends()
        self.poll(start + 302000)
        self.assertEqual(self.lib.test_sends(), count)
        self.assertEqual(int(self.query_status()["due"]), (start + 500000) & 0xFFFFFFFF)
        self.assertEqual(self.query_status()["grid_skips"], "1")
        self.poll(start + 14000000)
        self.assertEqual(self.query_status()["outcome"], "2")
        self.assertEqual(self.lib.test_sends(), count)

    def test_grid_rejects_non_ac_template(self):
        """Grid mode cannot silently change a paired query or allow other opcodes."""
        self.query_start()
        self.cmd(f"QUERY_CANCEL {EPOCH} 1")
        self.cmd(f"QUERY_DROP {EPOCH} 1")
        raw = bytearray(phased_frame())
        raw[22] = 0x10
        raw[29:37] = bytes(7) + b"\x01"
        raw[37] = sum(raw[21:37]) & 255
        self.cmd(
            f"QUERY_GRID {EPOCH} 2 235500 4012000 3 {raw.hex()}", "ERR FRAME_POLICY"
        )

    def test_grid_uses_new_successful_heartbeat_anchor(self):
        """A heartbeat dispatched before an eligible AC updates its grid origin."""
        start = self.query_start(ac_only=True, grid=True)
        self.poll(start)
        self.poll(start + 9890000)
        self.assertEqual(
            int(self.query_status()["due"]), (start + 9900000) & 0xFFFFFFFF
        )
        self.poll(start + 9900123)
        q = self.query_status()
        self.assertEqual(q["anchor_gen"], "2")
        self.assertEqual(q["age_ticks"], "0")
        self.assertEqual(q["sent"], "1")
        self.assertEqual(q["grid_skips"], "0")
