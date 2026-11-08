"""Exercise optional adapter diagnostics through both actual public bench runners."""

import unittest

import xiao_rf_bench as rf
import xiao_rx_bench as rx
from test_xiao_queue_bench import PassivePort, SyntheticPort, queue_reply
from test_xiao_rf_bench import Clock


def adapter_reply(board, total):
    """Build consistent handoff evidence without claiming SFD packet equivalence.

    @param board Exact fixture endpoint key.
    @param total Receive callback/core calls before portable queue admission.
    @return Complete successful response body.
    """
    counts = dict.fromkeys(rf.ADAPTER_COUNTERS[board], 0)
    counts["core_calls"] = total
    if board == "mg24":
        for key in ("rx_event", "hold_ok", "copy_calls", "release_ok"):
            counts[key] = total
    else:
        counts.update(sfd_event=total + 7, rx_done=total, rx_release_ok=total)
    return (
        "OK "
        + " ".join(f"{k}={v}" for k, v in counts.items())
        + (" counter_bits=32 counter_mode=wrapping scope=last_start_attempt")
    )


class AdapterWire:
    """Add adapter evidence while retaining the underlying fragmented wire."""

    capability = "handoff_v1"
    bad_adapter = None

    def emit(self, line):
        """Advertise the selected contract only in capability responses.

        @param line Complete fixture response or event without newline.
        """
        if " proto=3 " in line and self.capability is not None:
            line += " adapter_diag=" + self.capability
        super().emit(line)

    def write(self, data):
        """Intercept RFADAPTER without changing partial-write behavior.

        @param data Offered request suffix.
        @return Exact accepted prefix length.
        """
        if self.zero_writes:
            return super().write(data)
        count = min(len(data), self.write_limit)
        combined = bytes(self.output) + data[:count]
        if b"\n" in combined:
            line, _, rest = combined.partition(b"\n")
            ident, command = line.decode().split(" ", 1)
            if command == "RFADAPTER":
                self.output = bytearray(rest)
                self.commands.append(command)
                body = self.bad_adapter or adapter_reply(
                    self.name, self.rx + (self.full if self.rx else 0)
                )
                self.emit(f"RSP {ident} {body}")
                return count
        return super().write(data)


class AdapterSyntheticPort(AdapterWire, SyntheticPort):
    """Compose the synthetic fixture with optional handoff diagnostics."""


class AdapterPassivePort(AdapterWire, PassivePort):
    """Compose the receive-only fixture with optional handoff diagnostics."""


class AdapterBenchTests(unittest.TestCase):
    """Missing or inconsistent advertised evidence must fail after safe cleanup."""

    def run_wire(
        self, passive, capability="handoff_v1", bad_adapter=None, bad_queue=None
    ):
        """Run actual protocol logic with independent counters and fragmented I/O.

        @param passive Choose passive capture instead of synthetic transmission.
        @param capability Advertised contract, or None for a legacy endpoint.
        @param bad_adapter Optional replacement MG24 response body.
        @param bad_queue Optional replacement MG24 queue response body.
        @return Completed evidence and both fixture ports.
        """
        clock = Clock()
        kind = AdapterPassivePort if passive else AdapterSyntheticPort
        ports = [kind(name) for name in rf.BOARDS]
        ports[0].peer, ports[1].peer = ports[1], ports[0]
        for port in ports:
            port.capability = capability
        ports[0].bad_adapter = bad_adapter
        ports[0].bad_queue = bad_queue
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

    def test_actual_runners_query_only_after_stop_and_retain_counters(self):
        """Both runners reconcile advertised handoffs with independently received data."""
        for passive in (False, True):
            evidence, ports = self.run_wire(passive)
            self.assertFalse(evidence["cleanup_errors"])
            for port in ports:
                self.assertLess(
                    port.commands.index("RFSTOP"), port.commands.index("RFADAPTER")
                )
                self.assertEqual(
                    int(evidence["adapter"][port.name]["counters"]["core_calls"]),
                    port.rx,
                )
            self.assertTrue(
                evidence["assessment"] == "passed" if passive else rf.passed(evidence)
            )

    def test_legacy_absence_remains_explicit(self):
        """Unadvertised diagnostics are unavailable rather than fabricated as zero."""
        for passive in (False, True):
            evidence, ports = self.run_wire(passive, capability=None)
            for port in ports:
                self.assertNotIn("RFADAPTER", port.commands)
                self.assertEqual(
                    evidence["adapter"][port.name],
                    dict(available=False, reason="not_advertised"),
                )

    def test_queue_failure_preserves_independent_adapter_evidence(self):
        """A failed queue check cannot suppress the other stopped diagnostic read."""
        for passive in (False, True):
            evidence, ports = self.run_wire(passive, bad_queue=queue_reply(0))
            self.assertTrue(evidence["cleanup_errors"])
            self.assertIn("RFADAPTER", ports[0].commands)
            self.assertEqual(
                int(evidence["adapter"]["mg24"]["counters"]["core_calls"]),
                ports[0].rx,
            )

    def test_unknown_capability_rejects_before_start(self):
        """Future adapter semantics cannot silently fall back to legacy behavior."""
        for passive in (False, True):
            with self.assertRaisesRegex(ValueError, "Unknown adapter"):
                self.run_wire(passive, capability="future_v2")

    def test_missing_reset_or_release_failure_stays_failed(self):
        """Bad snapshots preserve failed assessments and owned-window cleanup."""
        for bad in (
            "ERR RF_ADAPTER_NOT_IDLE",
            "OK core_calls=0",
            adapter_reply("mg24", 0),
            adapter_reply("mg24", 20).replace("release_fail=0", "release_fail=1"),
        ):
            for passive in (False, True):
                evidence, ports = self.run_wire(passive, bad_adapter=bad)
                self.assertTrue(evidence["cleanup_errors"])
                self.assertTrue(all("RFSTOP" in p.commands for p in ports))
                self.assertFalse(
                    evidence["assessment"] == "passed"
                    if passive
                    else rf.passed(evidence)
                )

    def test_exact_schema_bounds_and_epoch(self):
        """Ambiguous counters and changed snapshot scopes are rejected."""
        valid = adapter_reply("c6", 2)
        for bad in (
            valid + " core_calls=2",
            valid.replace("core_calls=2", "core_calls=-1"),
            valid.replace("core_calls=2", "core_calls=4294967296"),
            valid.replace("wrapping", "saturating"),
            valid.replace("last_start_attempt", "last_successful_start"),
        ):
            with self.assertRaises(ValueError):
                rf.adapter_status(bad, "c6")

    def test_c6_handoffs_and_wrapping_without_sfd_equality(self):
        """Callback/release equations wrap while SFD counts remain independent."""
        state = dict(active="0", busy="0", queued="0", fault="0", rx="2")
        snapshot = rf.adapter_status(adapter_reply("c6", 2), "c6")
        rf.reconcile_adapter(snapshot, state, "c6")
        for key in (
            "rx_done",
            "rx_release_ok",
            "core_calls",
            "rx_release_fail",
            "ack_release_fail",
        ):
            changed = dict(snapshot, **{key: "1"})
            with self.assertRaises(ValueError):
                rf.reconcile_adapter(changed, state, "c6")
        snapshot.update(
            core_calls="4294967295", rx_done="0", rx_invalid="1", rx_release_ok="0"
        )
        state["rx"] = "4294967295"
        rf.reconcile_adapter(snapshot, state, "c6")


if __name__ == "__main__":
    unittest.main()
