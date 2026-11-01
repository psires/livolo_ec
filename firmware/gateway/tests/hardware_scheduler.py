#!/usr/bin/env python3
"""Verify MCU queue execution without a host reader, then lease cancellation.

Requires exclusive UART ownership and disarms on exit. Emits only self-addressed
probes and gateway heartbeats; never sends a device output command.
"""
import argparse
import json
from pathlib import Path
import secrets
import time
from hardware_v3 import Client, fields


def probe(gateway, number):
    """Construct a uniquely identifiable self-addressed diagnostic frame.

    @param gateway Four-byte gateway identity in wire hex.
    @param number Probe/job identifier.
    """
    return (
        bytes.fromhex("018800" + gateway * 2)
        + b"EC_SCHED_05"
        + number.to_bytes(4, "little")
    )


def heartbeat(gateway):
    """Build the approved heartbeat envelope for the selected test identity.

    @param gateway Four-byte gateway identity in wire hex.
    """
    raw = bytearray.fromhex(
        "018830ffffffff" + gateway + "0000000000000000a70e8c08000000000000"
    )
    raw += (1000).to_bytes(4, "little") + (1).to_bytes(4, "little")
    raw += bytes((sum(raw[21:37]) & 255, 0, 0, 0))
    assert len(raw) == 41
    return bytes(raw)


def main():
    """Exercise queue credits, cancellation, host silence and lease expiry on air."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", required=True)
    parser.add_argument("--baud", type=int, default=460800)
    parser.add_argument("--gateway", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--log", type=Path, required=True)
    args = parser.parse_args()
    assert len(bytes.fromhex(args.gateway)) == 4
    assert not args.output.exists()
    c = Client(args.port, args.log, args.baud)
    epoch = secrets.token_hex(8)
    result = {
        "started_at": time.time(),
        "gateway": args.gateway,
        "session": epoch,
        "jobs": [],
        "passed": False,
    }
    try:
        c.command("DISARM")
        caps = fields(c.command("CAPS"))
        hz = int(caps["scheduler_hz"])
        assert hz in (65536, 1000000) and hz == int(caps["rtimer_hz"])
        grace = hz // 50
        result["caps"] = caps
        c.command(f"NETWORK {args.gateway} a7 {epoch}")
        c.command(f"ARM {epoch} 30")
        status = fields(c.command(f"SCHED {epoch}"))
        now = int(status["now"])
        first = (now + hz) & 0xFFFFFFFF
        result["before"] = status
        c.command(f"HEARTBEAT {epoch} {first} {heartbeat(args.gateway).hex()}")
        for job in range(1, 9):
            due = (first + hz + round((job - 1) * 0.2 * hz)) & 0xFFFFFFFF
            raw = probe(args.gateway, job)
            c.command(f"QUEUE {epoch} {job} {due} {grace} 0 {raw.hex()}")
            result["jobs"].append({"id": job, "due": due, "raw": raw.hex()})
        c.command(
            f"QUEUE {epoch} 9 {(first+5*hz)&0xffffffff} {grace} 0 {probe(args.gateway,9).hex()}",
            "ERR QUEUE_FULL",
        )
        c.command(f"QCANCEL {epoch} 4")
        c.command(f"QDROP {epoch} 1", "ERR JOB_PENDING")
        # Leave the UART entirely untouched while two heartbeats and seven jobs run.
        result["host_silence_started_at"] = time.time()
        time.sleep(12)
        result["host_silence_finished_at"] = time.time()
        # Drain buffered records before new commands; retained results survive this.
        c.listen(0.2)
        for job in result["jobs"]:
            metadata = fields(c.command(f'QGET {epoch} {job["id"]}'))
            job["result"] = metadata
            assert metadata["state"] == "done"
            assert int(metadata["result"]) == (-4 if job["id"] == 4 else 0)
            c.command(f'QDROP {epoch} {job["id"]}')
        result["after_silence"] = fields(c.command(f"SCHED {epoch}"))
        assert int(result["after_silence"]["hb_sent"]) - int(status["hb_sent"]) == 2
        assert result["after_silence"]["free"] == "8"
        c.command(f"ARM {epoch} 30")
        result["uart_load_started_at"] = time.time()
        load_until = time.monotonic() + 12
        result["uart_load_requests"] = 0
        reference = c.command("CAPS")
        while time.monotonic() < load_until:
            assert c.command("CAPS") == reference
            result["uart_load_requests"] += 1
        result["uart_load_finished_at"] = time.time()
        result["after_load"] = fields(c.command(f"SCHED {epoch}"))
        assert int(result["after_load"]["hb_sent"]) - int(status["hb_sent"]) >= 3
        assert result["after_load"]["hb_missed"] == status["hb_missed"]
        c.command(f"ARM {epoch} 1")
        now = int(fields(c.command(f"SCHED {epoch}"))["now"])
        c.command(
            f"QUEUE {epoch} 9 {(now+2*hz)&0xffffffff} {grace} 0 {probe(args.gateway,9).hex()}"
        )
        result["before_expiry"] = fields(c.command("STATUS"))
        c.listen(2.5)
        result["after_expiry"] = fields(c.command("STATUS"))
        assert result["after_expiry"]["armed"] == "0"
        assert result["after_expiry"]["tx"] == result["before_expiry"]["tx"]
        c.command(f"SCHED {epoch}", "ERR DISARMED")
        c.command(f"ARM {epoch} 1")
        c.command(f"QGET {epoch} 9", "ERR JOB_UNKNOWN")
        result["passed"] = True
    finally:
        c.command("DISARM")
        c.uart.close()
        c.log.close()
        result["finished_at"] = time.time()
        args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({"passed": result["passed"], "jobs": len(result["jobs"])}))


if __name__ == "__main__":
    main()
