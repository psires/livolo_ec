#!/usr/bin/env python3
"""Check scheduler clock, cancellation and authority without transmitting RF.

Requires exclusive UART ownership of an idle adapter. All queued targets are
beyond the two-second lease, and cleanup disarms even if an assertion fails.
"""
import argparse
import json
from pathlib import Path
import secrets
import time

from hardware_v3 import Client, fields
from hardware_scheduler import heartbeat, probe
from hardware_query import query


def main():
    """Exercise the real scheduler parser with future work that cannot transmit."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", required=True)
    parser.add_argument("--baud", type=int, default=460800)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--log", type=Path, required=True)
    args = parser.parse_args()
    assert not args.output.exists()
    gateway, peer, epoch = "0badf00d", "0badf00e", secrets.token_hex(8)
    c = Client(args.port, args.log, args.baud)
    result = dict(started_at=time.time(), passed=False, air=False)
    try:
        c.command("DISARM")
        caps = fields(c.command("CAPS"))
        result["caps"] = caps
        hz = int(caps["scheduler_hz"])
        assert hz in (65536, 1000000) and hz == int(caps["rtimer_hz"])
        assert caps["query"] == "transaction_v1"
        result["before"] = fields(c.command("STATUS"))
        c.command(f"NETWORK {gateway} a7 {epoch}")
        c.command(f"PEER SET 0 {peer} 1f")
        c.command(f"ARM {epoch} 2")
        c.command(
            f"QUERY {epoch} 1 {round(.2355 * hz)} {round(4.012 * hz)} 3 "
            + query(gateway, peer, "0002").hex(),
            "ERR HEARTBEAT_REQUIRED",
        )
        t0 = time.monotonic()
        first = fields(c.command(f"SCHED {epoch}"))
        c.listen(0.25)
        second = fields(c.command(f"SCHED {epoch}"))
        wall = time.monotonic() - t0
        elapsed = ((int(second["now"]) - int(first["now"])) & 0xFFFFFFFF) / hz
        assert abs(elapsed - wall) < 0.1
        result["clock_check"] = dict(wall_seconds=wall, mcu_seconds=elapsed)
        due = (int(second["now"]) + 20 * hz) & 0xFFFFFFFF
        c.command(f"QUEUE {epoch} 1 {due} {hz // 50} 0 {probe(gateway, 1).hex()}")
        assert fields(c.command(f"QGET {epoch} 1"))["state"] == "pending"
        c.command(f"QDROP {epoch} 1", "ERR JOB_PENDING")
        cancelled = fields(c.command(f"QCANCEL {epoch} 1"))
        assert cancelled["state"] == "done" and cancelled["result"] == "-4"
        c.command(f"QDROP {epoch} 1")
        c.command(f"HEARTBEAT {epoch} {due} {heartbeat(gateway).hex()}")
        result["scheduled"] = fields(c.command(f"SCHED {epoch}"))
        assert result["scheduled"]["hb_active"] == "1"
        c.listen(2.2)
        c.command(f"SCHED {epoch}", "ERR DISARMED")
        c.command(f"ARM {epoch} 2")
        result["after_expiry"] = fields(c.command(f"SCHED {epoch}"))
        assert result["after_expiry"]["hb_active"] == "0"
        assert result["after_expiry"]["free"] == "8"
        result["passed"] = True
    finally:
        try:
            c.command("DISARM")
            c.command("PEER CLEAR")
            result["after"] = fields(c.command("STATUS"))
            if "before" in result:
                for field in ("tx", "tx_ok", "ack_tx", "ack_ok"):
                    assert result["after"][field] == result["before"][field]
        except BaseException:
            result["passed"] = False
            raise
        finally:
            c.uart.close()
            c.log.close()
            result["finished_at"] = time.time()
            args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(dict(passed=result["passed"], clock=result["clock_check"])))


if __name__ == "__main__":
    main()
