"""Exercise the real TI queue/parser core with deterministic clock and radio faults."""

import test_uart_v3 as fixture
from test_uart_v3 import EPOCH, GW, heartbeat, phased_frame


class SchedulerTests(fixture.FirmwareUART):
    scheduler = True
    baudrate = 460800

    def start(self, now=10000):
        """Install a live test session at a chosen wrapping timestamp.

        @param now Initial precise timer ticks.
        """
        self.configured()
        self.arm()
        self.lib.test_set_rtimer(now)

    def queue(self, job, due, raw=None, grace=1310, mode=0, expected="OK"):
        """Submit one queue command through the real parser.

        @param job Increasing job identity.
        @param due Absolute target ticks.
        @param raw Optional frame; defaults to a self-addressed diagnostic.
        @param grace Allowed dispatch lateness in ticks.
        @param mode Zero raw diagnostic or one phase query.
        @param expected Optional expected response prefix.
        """
        raw = raw or bytes.fromhex("018842" + GW + GW) + b"EC_SCHED_TEST"
        return self.cmd(
            f"QUEUE {EPOCH} {job} {due} {grace} {mode} {raw.hex()}", expected
        )

    def poll(self, tick):
        """Dispatch due work at a chosen precise timestamp.

        @param tick Absolute clock value.
        """
        self.lib.test_set_rtimer(tick)
        self.lib.ec_gateway_poll()

    def test_queue_order_retention_and_replay(self):
        """Order by deadline, retain outcomes, and reject replays after draining."""
        self.start()
        self.queue(1, 30000)
        self.queue(2, 20000)
        self.poll(19999)
        self.assertEqual(self.lib.test_sends(), 0)
        self.poll(20000)
        self.assertEqual(self.lib.test_sends(), 1)
        self.assertIn("state=done", self.cmd(f"QGET {EPOCH} 2"))
        self.cmd(f"QDROP {EPOCH} 1", "ERR JOB_PENDING")
        self.cmd(f"QDROP {EPOCH} 2")
        self.queue(2, 40000, expected="ERR JOB_ID")
        self.poll(30000)
        self.assertEqual(self.lib.test_sends(), 2)

    def test_full_cancel_expiry_and_no_late_execution(self):
        """Reserve results under congestion and never transmit expired/cancelled jobs."""
        self.start()
        for i in range(1, 9):
            self.queue(i, 20000)
        self.queue(9, 20000, expected="ERR QUEUE_FULL")
        self.cmd(f"QCANCEL {EPOCH} 1")
        self.poll(22000)
        self.assertEqual(self.lib.test_sends(), 0)
        self.assertIn("result=-4", self.cmd(f"QGET {EPOCH} 1"))
        self.assertIn("result=-3", self.cmd(f"QGET {EPOCH} 2"))
        self.queue(9, 30000, expected="ERR QUEUE_FULL")
        self.cmd(f"QDROP {EPOCH} 1")
        self.queue(9, 30000)

    def test_session_disarm_and_lease_flush(self):
        """Flush authority on disarm, session replacement and lease expiry."""
        self.start()
        self.queue(1, 20000)
        self.cmd("DISARM")
        self.poll(20000)
        self.assertEqual(self.lib.test_sends(), 0)
        self.arm()
        self.cmd(f"QGET {EPOCH} 1", "ERR JOB_UNKNOWN")
        self.queue(1, 30000, expected="ERR JOB_ID")
        self.queue(2, 30000)
        self.cmd(f"ARM {EPOCH} 1")
        self.lib.test_advance(129)
        self.lib.ec_gateway_poll()
        self.assertEqual(self.lib.test_sends(), 0)
        self.arm()
        self.cmd(f"QGET {EPOCH} 2", "ERR JOB_UNKNOWN")
        self.queue(3, 200000)
        self.cmd(f"NETWORK {GW} a7 1122334455667788")
        self.poll(200000)
        self.assertEqual(self.lib.test_sends(), 0)

    def test_wrap_and_heartbeat_absolute_cadence(self):
        """Keep absolute cadence across wrap and skip missed slots without bursts."""
        first = 0xFFFFFF00
        self.start(first - 100)
        self.cmd(f"HEARTBEAT {EPOCH} {first} {heartbeat().hex()}")
        self.poll(first + 10)
        self.assertEqual(self.lib.test_sends(), 1)
        next_due = (first + 655360) & 0xFFFFFFFF
        self.assertIn(f"hb_due={next_due}", self.cmd(f"SCHED {EPOCH}"))
        self.poll(next_due + 100)
        self.assertEqual(self.lib.test_sends(), 2)
        self.assertEqual(int.from_bytes(self.sent()[29:33], "little"), 60)
        # A long processing stall consumes slots, it never emits a catch-up burst.
        self.poll(next_due + 2 * 655360 + 2000)
        self.assertEqual(self.lib.test_sends(), 2)
        status = self.cmd(f"SCHED {EPOCH}")
        self.assertIn("hb_missed=2", status)
        self.assertIn(f"hb_due={next_due+3*655360}", status)
        self.cmd(f"SEND {EPOCH} {heartbeat().hex()}", "ERR HEARTBEAT_OWNED")

    def test_busy_heartbeat_preserves_four_attempts_and_absolute_epoch(self):
        """Use existing 25/50/75ms CCA backoff, then skip without moving the epoch."""
        self.start()
        self.cmd(f"HEARTBEAT {EPOCH} 20000 {heartbeat().hex()}")
        self.lib.test_radio(2, 0)
        self.poll(20000)
        self.assertEqual(self.lib.test_sends(), 1)
        tick = 20000
        for attempt in range(1, 4):
            tick += (attempt * 65536 + 39) // 40
            self.poll(tick - 1)
            self.assertEqual(self.lib.test_sends(), attempt)
            self.poll(tick)
            self.assertEqual(self.lib.test_sends(), attempt + 1)
        status = self.cmd(f"SCHED {EPOCH}")
        self.assertIn("hb_missed=1", status)
        self.assertIn("hb_retries=3", status)
        self.assertIn("hb_due=675360", status)
        self.poll(tick + 1000)
        self.assertEqual(self.lib.test_sends(), 4)

    def test_frame_policy_and_phase_query(self):
        """Reject queued outputs and stamp allowed read-only queries at execution."""
        self.start()
        self.queue(1, 20000, raw=phased_frame(), mode=1, expected="ERR FRAME_POLICY")
        raw = bytearray(phased_frame())
        raw[22] = 0x10
        raw[29:37] = bytes(7) + b"\x01"
        raw[37] = sum(raw[21:37]) & 255
        self.cmd(f"SEND {EPOCH} {heartbeat().hex()}")
        self.queue(1, 20000, raw=bytes(raw), mode=1)
        self.poll(20000)
        self.assertIn("result=0", self.cmd(f"QGET {EPOCH} 1"))
        self.assertNotEqual(self.sent()[-3:], bytes(3))
        self.queue(2, 20000, expected="ERR SCHEDULE")
        self.queue(2, 20000 + 31 * 65536, expected="ERR SCHEDULE")

    def query_start(self, job=1, tick=12000, grid=False):
        """Install heartbeat ownership and submit one bounded query without dispatch.

        @param job Monotonic transaction identity.
        @param tick Initial query tick, including wrap tests.
        @param grid Use AC-only queries aligned to the heartbeat 200 ms grid.
        @return Validated query template bytes.
        """
        self.start((tick - 2000) & 0xFFFFFFFF)
        due = (tick - 1000) & 0xFFFFFFFF
        self.cmd(f"HEARTBEAT {EPOCH} {due} {heartbeat().hex()}")
        self.poll(due)
        self.lib.test_set_rtimer(tick)
        raw = bytearray(phased_frame())
        raw[22] = 0xAC if grid else 0x10
        raw[29:37] = bytes(7) + b"\x01"
        raw[37] = sum(raw[21:37]) & 255
        command = "QUERY_GRID" if grid else "QUERY"
        self.cmd(f"{command} {EPOCH} {job} 15434 262930 3 {raw.hex()}")
        return bytes(raw)

    def query_status(self):
        """Parse retained transaction fields without consuming them."""
        return dict(
            item.split("=", 1)
            for item in self.cmd(f"QUERY_GET {EPOCH} 1").split()
            if "=" in item
        )

    def test_query_silence_six_frames_three_rounds_and_retention(self):
        """Silence exhausts exactly six sends, retaining a bounded terminal result."""
        self.query_start()
        opcodes = []
        for _ in range(6):
            self.poll(int(self.query_status()["due"]))
            opcodes.append(self.sent()[22])
        self.assertEqual(opcodes, [0x10, 0xAC] * 3)
        self.poll(int(self.query_status()["due"]))
        q = self.query_status()
        self.assertEqual(
            (q["state"], q["outcome"], q["rounds"], q["sent"]), ("done", "2", "3", "6")
        )
        self.assertEqual(q["attempts"], "6")
        self.cmd(f"QUERY_DROP {EPOCH} 1")
        self.cmd(f"QUERY_GET {EPOCH} 1", "ERR JOB_UNKNOWN")

    def test_query_fresh_report_stops_between_pair_and_survives_drain(self):
        """A matching report stops AC immediately and remains recoverable without RX logs."""
        from test_uart_v3 import report

        self.query_start()
        self.poll(12000)
        self.lib.test_set_rtimer(13000)
        self.receive(report())
        before = self.lib.test_sends()
        self.poll(30000)
        q = self.query_status()
        self.assertEqual(self.lib.test_sends(), before)
        self.assertEqual(
            (q["outcome"], q["sent"], q["report"]), ("1", "1", report().hex())
        )
        self.cmd(f"QUERY_CANCEL {EPOCH} 1")
        self.assertEqual(self.query_status()["outcome"], "1")

    def test_query_rejects_wrong_reports_but_accepts_repeated_counter(self):
        """CRC, exact identity, suffix, tag and output mask gate temporal freshness."""
        import binascii
        from test_uart_v3 import report

        raw = self.query_start()
        for offset, value in (
            (7, 0),
            (23, 0),
            (27, 1),
            (19, 1),
            (29, 0),
            (31, 4),
            (32, 4),
            (22, 0xFA),
        ):
            bad = bytearray(report())
            bad[offset] = value
            bad[37:] = binascii.crc_hqx(bad[21:37], 0xFFFF).to_bytes(2, "big")
            self.receive(bytes(bad))
            self.assertEqual(self.query_status()["state"], "pending")
        bad = bytearray(report())
        bad[37] ^= 1
        self.receive(bytes(bad))
        self.assertEqual(self.query_status()["state"], "pending")
        # A callback predating acceptance cannot confirm, even with a valid CRC.
        self.lib.test_set_rtimer(11999)
        self.receive(report())
        self.assertEqual(self.query_status()["state"], "pending")
        self.lib.test_set_rtimer(12001)
        self.receive(report(broadcast=True))
        self.assertEqual(self.query_status()["outcome"], "1")
        self.cmd(f"QUERY_DROP {EPOCH} 1")
        self.cmd(f"QUERY {EPOCH} 1 15434 262930 3 {raw.hex()}", "ERR JOB_ID")
        self.cmd(f"QUERY {EPOCH} 2 15434 262930 3 {raw.hex()}")
        self.receive(report(broadcast=True))  # Same counter is a new callback.
        self.assertIn("outcome=1", self.cmd(f"QUERY_GET {EPOCH} 2"))

    def test_query_cca_four_attempts_then_advance_and_fresh_stop(self):
        """CCA retains four attempts with 25/50/75ms backoff and checks reports before retry."""
        from test_uart_v3 import report

        self.query_start()
        self.lib.test_radio(2, 0)
        tick = 12000
        for attempt in range(1, 5):
            self.poll(tick)
            q = self.query_status()
            self.assertEqual(int(q["attempts"]), attempt)
            if attempt < 4:
                self.assertEqual(int(q["due"]) - tick, (attempt * 65536 + 39) // 40)
            tick = int(q["due"])
        self.assertEqual((q["retries"], q["sent"]), ("3", "0"))
        self.assertEqual(tick - int(q["entry_ticks"]), 15434)
        self.lib.test_radio(0, 0)
        self.lib.test_set_rtimer(tick - 1)
        self.receive(report())
        before = self.lib.test_sends()
        self.poll(tick)
        self.assertEqual(self.lib.test_sends(), before)
        self.assertEqual(self.query_status()["outcome"], "1")

    def test_query_deadline_wrap_cancellation_and_authority_flush(self):
        """Deadline comparisons wrap safely; cancellation and lost authority prevent replay."""
        self.query_start(tick=0xFFFFFF00)
        deadline = int(self.query_status()["deadline"])
        self.poll(deadline)
        self.assertEqual(self.query_status()["outcome"], "2")
        self.setUp()
        raw = self.query_start()
        self.cmd(f"QUERY_DROP {EPOCH} 1", "ERR JOB_PENDING")
        self.cmd(f"QUERY {EPOCH} 2 15434 262930 3 {raw.hex()}", "ERR QUERY_BUSY")
        self.cmd(f"QUERY_CANCEL {EPOCH} 1")
        before = self.lib.test_sends()
        self.poll(13000)
        self.assertEqual(self.lib.test_sends(), before)
        self.assertEqual(self.query_status()["outcome"], "3")
        self.cmd("DISARM")
        self.arm()
        self.cmd(f"QUERY_GET {EPOCH} 1", "ERR JOB_UNKNOWN")
        self.cmd(
            f"QUERY {EPOCH} 1 15434 262930 3 {raw.hex()}", "ERR HEARTBEAT_REQUIRED"
        )

    def test_grid_at_ti_clock_uses_rational_fifth_seconds(self):
        """65536 Hz needs rational grid rounding rather than a truncated period."""
        self.query_start(grid=True)
        self.poll(12000)
        self.poll(27434)
        due = 11000 + (2 * 65536 + 4) // 5
        self.assertEqual(int(self.query_status()["due"]), due)
        self.poll(due)
        q = self.query_status()
        self.assertEqual(q["sent"], "1")
        self.assertEqual(int(q["age_ticks"]), (2 * 65536 + 4) // 5)
