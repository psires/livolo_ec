#!/usr/bin/env python3
"""Run bounded read-only MCU queries through host silence and sustained UART load.

Requires exclusive UART ownership. The caller must restore the normal daemon
session afterward. No pairing or output-setting commands are transmitted.
"""
import argparse
import json
from pathlib import Path
import secrets
import time
from hardware_v3 import Client, fields
from hardware_scheduler import heartbeat


def query(gateway, device, suffix):
    """Build a direct read-only opcode-10 template with a zero phase trailer.

    @param gateway Source wire identity.
    @param device Registered destination wire identity.
    @param suffix Exact registered peer suffix.
    @return Validated 41-byte template.
    """
    raw = bytearray.fromhex(
        "018800" + device + gateway + "00000000" + device + "a71e8c10" + device + suffix
    )
    raw += bytes(7) + b"\x01"
    raw += bytes((sum(raw[21:37]) & 255, 0, 0, 0))
    assert len(raw) == 41
    return bytes(raw)


def main():
    """Verify retained autonomous query completion without extending retry limits."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", required=True)
    parser.add_argument("--baud", type=int, default=460800)
    parser.add_argument("--gateway", required=True)
    parser.add_argument("--device", required=True)
    parser.add_argument("--suffix", default="0002")
    parser.add_argument("--mask", type=int, default=3)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--log", type=Path, required=True)
    args = parser.parse_args()
    assert not args.output.exists()
    raw = query(args.gateway, args.device, args.suffix)
    c = Client(args.port, args.log, args.baud)
    epoch = secrets.token_hex(8)
    result = dict(started_at=time.time(), session=epoch, trials=[], passed=False)
    try:
        c.command("DISARM")
        caps = fields(c.command("CAPS"))
        hz = int(caps["scheduler_hz"])
        assert hz in (65536, 1000000) and hz == int(caps["rtimer_hz"])
        result["caps"] = caps
        pair, wait = round(0.2355 * hz), round(4.012 * hz)
        c.command(f"NETWORK {args.gateway} a7 {epoch}")
        c.command(f"PEER SET 0 {args.device} 1f")
        c.command(f"ARM {epoch} 30")
        c.command(f"ACK {epoch} 1")
        now = int(fields(c.command(f"SCHED {epoch}"))["now"])
        c.command(
            f"HEARTBEAT {epoch} {(now+(hz+9)//10)&0xffffffff} {heartbeat(args.gateway).hex()}"
        )
        c.listen(0.3)
        for job, mode in ((1, "silence"), (2, "uart_load")):
            c.command(f"ARM {epoch} 30")
            trial = dict(job=job, mode=mode, started_at=time.time())
            result["trials"].append(trial)
            c.command(f"QUERY {epoch} {job} {pair} {wait} {args.mask} {raw.hex()}")
            trial["accepted_at"] = time.time()
            if mode == "silence":
                time.sleep(14.5)
            else:
                until = time.monotonic() + 14.5
                reference = c.command("CAPS")
                trial["caps_requests"] = 0
                while time.monotonic() < until:
                    assert c.command("CAPS") == reference
                    trial["caps_requests"] += 1
            trial["window_finished_at"] = time.time()
            c.listen(0.1)
            state = fields(c.command(f"QUERY_GET {epoch} {job}"))
            trial["result"] = state
            assert state["state"] == "done" and state["outcome"] in ("1", "2")
            assert int(state["rounds"]) <= 3 and int(state["sent"]) <= 6
            assert int(state["attempts"]) <= 24 and int(state["retries"]) <= 18
            if state["outcome"] == "1":
                assert len(bytes.fromhex(state["report"])) == 39
            c.command(f"QUERY_DROP {epoch} {job}")
        result["scheduler"] = fields(c.command(f"SCHED {epoch}"))
        result["passed"] = True
    finally:
        c.command("DISARM")
        c.uart.close()
        c.log.close()
        result["finished_at"] = time.time()
        args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
