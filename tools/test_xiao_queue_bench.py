"""Queue capability negotiation and wire-accounting regressions without hardware."""

from copy import deepcopy
import unittest

import xiao_rf_bench as rf
import xiao_rx_bench as rx
from test_xiao_rf_bench import Clock, Port as RFPort
from test_xiao_rx_bench import Port as RXPort


def queue_reply(enqueued=0, full=0):
    """Produce a complete frozen queue contract for independent wire fixtures.

    @param enqueued Delivered fixture records.
    @param full Explicit accepted records rejected by the portable queue.
    @return Numbered-response body without its wire prefix.
    """
    return (
        f"OK rx_queue_full={full} rx_enqueued={enqueued} rx_dequeued={enqueued} "
        f"rx_high_water={7 if full else min(enqueued, 7)} rx_max_age_us={123 if enqueued else 0} "
        "queue_capacity=7 counter_bits=32 counter_mode=wrapping "
        "scope=last_successful_start age_basis=adapter_timestamp age_clock=mod32"
    )


class QueueWire:
    """Add optional queue telemetry to existing fragmented, real-parser fixtures."""

    def emit(self, line):
        """Advertise queue capability and expose separately counted overflow.

        @param line Fixture response or observation.
        """
        if " proto=3 " in line and self.queue_cap is not None:
            line += " queue_diag=" + self.queue_cap
        if (
            " tx_started=" in line
            and self.rx
            and (not self.active or getattr(self, "start_reads", 1000) > 100)
        ):
            line = line.replace(f"rx={self.rx} ", f"rx={self.rx + self.full} ")
            line = line.replace(
                f"dropped={self.dropped} ", f"dropped={self.dropped + self.full} "
            )
        super().emit(line)

    def write(self, data):
        """Intercept only RFQUEUE while retaining original partial-write semantics.

        @param data Next offered command suffix.
        @return Exact accepted prefix length.
        """
        if self.zero_writes:
            return super().write(data)
        count = min(len(data), self.write_limit)
        combined = bytes(self.output) + data[:count]
        if b"\n" in combined:
            line, _, rest = combined.partition(b"\n")
            ident, command = line.decode().split(" ", 1)
            if command == "RFQUEUE":
                self.output = bytearray(rest)
                self.commands.append(command)
                body = self.bad_queue or queue_reply(
                    self.rx, self.full if self.rx else 0
                )
                self.emit(f"RSP {ident} {body}")
                return count
        return super().write(data)


class SyntheticPort(QueueWire, RFPort):
    """Synthetic wire with explicit queue instrumentation."""

    def __init__(self, name):
        """Initialize independent diagnostics and baseline radio fixture.

        @param name Exact fixture board name.
        """
        super().__init__(name)
        self.queue_cap, self.full, self.bad_queue = "ring_v1", 0, None


class PassivePort(QueueWire, RXPort):
    """Passive-only wire with explicit queue instrumentation."""

    def __init__(self, name):
        """Initialize independent diagnostics and baseline passive fixture.

        @param name Exact fixture board name.
        """
        super().__init__(name)
        self.zero_writes = 0
        self.queue_cap, self.full, self.bad_queue = "ring_v1", 0, None


class QueueTests(unittest.TestCase):
    """Legacy absence and measured overflow must never hide missing diagnostics."""

    def run_wire(self, passive=False, full=0, capability="ring_v1", bad_queue=None):
        """Execute actual public runner and return its independent wire evidence.

        @param passive Select receive-only capture instead of synthetic TX.
        @param full Explicit overflow injected on MG24.
        @param capability Advertised queue contract, or None for legacy firmware.
        @param bad_queue Optional malformed RFQUEUE reply body.
        @return Receipt and both fixture ports.
        """
        clock = Clock()
        ports = [(PassivePort if passive else SyntheticPort)(n) for n in rf.BOARDS]
        ports[0].peer, ports[1].peer = ports[1], ports[0]
        for port in ports:
            port.queue_cap = capability
        ports[0].full, ports[0].bad_queue = full, bad_queue
        links = [
            (rx.Link(p, p.name, clock.time) if passive else rf.Link(p, p.name))
            for p in ports
        ]
        evidence = {}
        if passive:
            rx.capture(links, "12345678", 1, evidence, clock.time, clock.sleep)
        else:
            rf.run(links, "12345678", evidence, clock.time, clock.sleep)
        return evidence, ports

    def test_both_runners_collect_negotiated_snapshot_after_stop(self):
        """Actual synthetic and passive paths retain complete queue evidence."""
        for passive in (False, True):
            with self.subTest(passive=passive):
                evidence, ports = self.run_wire(passive)
                self.assertFalse(evidence["cleanup_errors"])
                for port in ports:
                    self.assertLess(
                        port.commands.index("RFSTOP"), port.commands.index("RFQUEUE")
                    )
                    counters = evidence["queue"][port.name]["counters"]
                    self.assertEqual(int(counters["rx_dequeued"]), port.rx)
                (
                    self.assertEqual(evidence["assessment"], "passed")
                    if passive
                    else self.assertTrue(rf.passed(evidence))
                )

    def test_legacy_absence_is_explicit_and_never_queried(self):
        """Old firmware remains usable without inventing a zero queue snapshot."""
        for passive in (False, True):
            evidence, ports = self.run_wire(passive, capability=None)
            for port in ports:
                self.assertNotIn("RFQUEUE", port.commands)
                self.assertEqual(
                    evidence["queue"][port.name],
                    dict(available=False, reason="not_advertised"),
                )

    def test_measured_overflow_reconciles_but_stays_strict_failure(self):
        """Known queue loss is evidence, not a relaxation of capture integrity."""
        for passive in (False, True):
            evidence, _ = self.run_wire(passive, full=3)
            self.assertFalse(evidence["cleanup_errors"])
            self.assertEqual(
                evidence["queue"]["mg24"]["counters"]["rx_queue_full"], "3"
            )
            (
                self.assertEqual(evidence["assessment"], "failed")
                if passive
                else self.assertFalse(rf.passed(evidence))
            )

    def test_advertised_but_missing_or_reset_snapshot_fails(self):
        """Missing diagnostics and erased record counts fail after safe STOP."""
        for bad in ("ERR RF_QUEUE_NOT_IDLE", "OK rx_queue_full=0", queue_reply(0)):
            for passive in (False, True):
                evidence, ports = self.run_wire(passive, bad_queue=bad)
                self.assertTrue(evidence["cleanup_errors"])
                self.assertTrue(all("RFSTOP" in p.commands for p in ports))
                (
                    self.assertEqual(evidence["assessment"], "failed")
                    if passive
                    else self.assertFalse(rf.passed(evidence))
                )

    def test_unknown_contract_rejected_before_rf_start(self):
        """Unsupported advertised semantics cannot silently become legacy absence."""
        for passive in (False, True):
            with self.assertRaisesRegex(ValueError, "Unknown queue"):
                self.run_wire(passive, capability="future_v9")

    def test_snapshot_bounds_duplicates_scope_and_modulo_accounting(self):
        """Validate every semantic token and independently reconcile wrapping counts."""
        valid = queue_reply(7, 3)
        for bad in (
            valid + " rx_queue_full=3",
            valid.replace("queue_capacity=7", "queue_capacity=8"),
            valid.replace("rx_high_water=7", "rx_high_water=8"),
            valid.replace("counter_mode=wrapping", "counter_mode=saturating"),
            valid.replace("scope=last_successful_start", "scope=last_query"),
            valid.replace("rx_max_age_us=123", "rx_max_age_us=-1"),
            valid.replace("rx_high_water=7", "rx_high_water=6"),
            queue_reply(1).replace("rx_high_water=1", "rx_high_water=0"),
            queue_reply(0).replace("rx_max_age_us=0", "rx_max_age_us=1"),
        ):
            with self.assertRaises(ValueError):
                rf.queue_status(bad)
        state = dict(active="0", busy="0", queued="0", fault="0", rx="2")
        snapshot = rf.queue_status(queue_reply(0xFFFFFFFF, 3))
        rf.reconcile_queue(snapshot, state, 0xFFFFFFFF)
        for field in ("rx", "queued"):
            changed = deepcopy(state)
            changed[field] = "1"
            with self.assertRaises(ValueError):
                rf.reconcile_queue(snapshot, changed, 0xFFFFFFFF)


if __name__ == "__main__":
    unittest.main()
