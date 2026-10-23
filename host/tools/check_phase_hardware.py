#!/usr/bin/env python3
"""Finite heartbeat/self-probe measurement. Requires stopped livolod; never actuates.

Run only after the coordinated firmware/UART handoff and Nordic capture start.
This tool does not flash firmware, stop/start system services or edit the host DB.
"""
import argparse
import asyncio
from dataclasses import replace
import json
from pathlib import Path
import secrets
import sqlite3
import subprocess
import time

from livolod.config import Device, load
from livolod.phase import PhaseCapabilities, PhaseClient
from livolod.protocol import decode, heartbeat, key_values
from livolod.uart import CommandError, Gateway, LinkError

POINTS = (0.2, 0.9, 1.1, 3.25, 5.812, 8.5, 9.8)
CYCLES = 2


def check_command(text, gateway_id, tag):
    """Reject radio commands outside the heartbeat and self-probe trial policy.

    @param text UART command to validate before sending.
    @param gateway_id Expected gateway wire identity as hexadecimal.
    @param tag Expected network tag as hexadecimal.
    """
    words = text.split()
    if words[0] == "SEND":
        raw = bytes.fromhex(words[-1])
        packet = decode(raw)
        expected = heartbeat(
            gateway_id,
            tag,
            raw[2],
            int.from_bytes(packet.data[:4], "little"),
            int.from_bytes(packet.data[4:], "little"),
        )
        if raw != expected:
            raise ValueError("Probe tool permits only own raw heartbeat SENDs")
    elif words[0] not in (
        "PING",
        "CAPS",
        "STATUS",
        "NETWORK",
        "PEER",
        "ARM",
        "ACK",
        "DISARM",
        "PHASE_PROBE",
    ):
        raise ValueError("Probe tool refuses output, pairing and query commands")


class AuditedGateway(Gateway):
    async def command(self, text, retries=1):
        """Audit a permitted diagnostic command and its outcome.

        @param text UART command subject to the diagnostic policy.
        @param retries Additional attempts with the same request identifier.
        @return The successful adapter response, also recorded in the transcript.
        """
        check_command(text, self.gateway_id, self.tag)
        record = dict(
            time=time.time(),
            monotonic=time.monotonic(),
            request_id=self.next_id,
            command=text,
        )
        self.transcript.append(record)
        try:
            result = await super().command(text, retries)
            record["result"] = result
            return result
        except Exception as error:
            record["error"] = str(error)
            raise
        finally:
            record["finished"] = time.monotonic()

    async def replay_last_probe(self):
        # Dedicated single-task diagnostic only. Never expose arbitrary ID rewind
        # on the production transport or insert another command before replay.
        """Verify that an immediate duplicate probe returns its cached reply.
        @return The cached response, required to match the original probe reply.
        """
        previous = self.transcript[-1]
        if (
            not previous["command"].startswith("PHASE_PROBE ")
            or "result" not in previous
        ):
            raise ValueError("Only a successful immediate probe may be replayed")
        expected_next = previous["request_id"] % 0xFFFFFFFF + 1
        if self.next_id != expected_next or self.pending:
            raise ValueError("UART request changed before replay")
        self.next_id = previous["request_id"]
        reply = await self.command(previous["command"], retries=0)
        if reply != previous["result"]:
            raise LinkError("Cached replay result differs from original probe")
        return reply


async def measurements(
    gateway, client, config, results, *, now=time.monotonic, sleep=asyncio.sleep
):
    """Measure phase references and replay behavior over a bounded schedule.

    @param gateway Audited UART gateway supporting immediate probe replay.
    @param client PhaseClient bound to the active radio session.
    @param config Gateway configuration supplying identity and heartbeat settings.
    @param results List to receive measurement evidence.
    @param now Callable returning monotonic seconds; replaceable for offline tests.
    @param sleep Async delay callable accepting seconds; replaceable for offline tests.
    """
    sequence = 0
    started = now()

    async def until(target):
        """Wait for a measurement boundary, surfacing a failed radio link.

        @param target Scheduled boundary in seconds on the injected monotonic clock.
        """
        await sleep(max(0, target - now()))
        if gateway.fault.done():
            raise gateway.fault.result()

    async def no_reference(label):
        """Require a probe without a phase reference to be rejected without RF.

        @param label Evidence label for the missing or expired reference check.
        """
        try:
            await client.probe(0, secrets.token_bytes(4))
        except CommandError as error:
            if str(error) != "ERR PHASE_REFERENCE":
                raise
            results.append(dict(check=label, rejected_without_send=True))
        else:
            raise RuntimeError(f"{label}: probe unexpectedly had a valid reference")

    await no_reference("missing_reference")
    replayed = False
    for cycle in range(CYCLES):
        if cycle:
            await until(heartbeat_completed + 10)
        for attempt in range(3):
            boundary = now()
            raw = heartbeat(
                config.gateway,
                config.tag,
                sequence,
                int(boundary - started),
                config.heartbeat_parameter,
            )
            sequence = (sequence + 1) & 255
            try:
                await gateway.command(f"SEND {client.session} {raw.hex()}")
                break
            except CommandError as error:
                # Only explicit CCA busy permits a new RF request. Everything
                # uncertain aborts; UART recovery itself replays identical IDs.
                if str(error) != "ERR TX 2" or attempt == 2:
                    raise
                await sleep(0.05 * (attempt + 1))
        heartbeat_completed = now()
        status = key_values(await gateway.command("STATUS"))
        reference = client.reference_status(status)
        if reference is None:
            raise LinkError("Successful heartbeat did not establish a phase reference")
        results.append(
            dict(
                check="heartbeat",
                cycle=cycle,
                host_started=boundary,
                reference=reference,
            )
        )
        for phase in POINTS:
            await until(boundary + phase)
            nonce = secrets.token_bytes(4)
            sequence = (sequence + 1) & 255
            record = dict(
                check="probe",
                cycle=cycle,
                requested_phase=phase,
                nonce=nonce.hex(),
                host_started=now(),
            )
            results.append(record)
            try:
                record["metadata"] = dict(await client.probe(sequence, nonce))
            except CommandError as error:
                record["error"] = str(error)
                if not str(error).startswith("ERR TX 2 "):
                    raise
                record["metadata"] = dict(client.last_tx)
                continue
            if not replayed:
                await gateway.replay_last_probe()
                results.append(
                    dict(check="cached_replay", nonce=nonce.hex(), identical_reply=True)
                )
                replayed = True
    if not replayed:
        raise RuntimeError("No successful probe available for cached replay validation")
    # Completion-relative wait guarantees >=12 seconds since anchor entry even
    # when UART transmission/response delayed the final heartbeat.
    await until(heartbeat_completed + 12.3)
    status = key_values(await gateway.command("STATUS"))
    if client.reference_status(status) is not None:
        raise RuntimeError("Phase reference did not expire")
    await no_reference("expired_reference")


def read_registry(config):
    # Never use Store here: its startup recovery writes the live command ledger.
    """Load the live device registry without changing its command ledger.

    @param config Configuration identifying the database and expected network.
    @return A configuration copy containing the database device registry.
    """
    with sqlite3.connect(Path(config.database).as_uri() + "?mode=ro", uri=True) as db:
        network = db.execute("SELECT value FROM meta WHERE key='network'").fetchone()
        if network != (config.gateway + ":" + config.tag,):
            raise ValueError("Live database identity does not match configuration")
        devices = tuple(
            Device(**json.loads(row[0]))
            for row in db.execute("SELECT value FROM devices ORDER BY id")
        )
    return replace(config, devices=devices)


async def check(args):
    """Run a finite phase trial and save evidence, including cleanup failures.

    @param args Parsed options naming the daemon configuration and new evidence path.
    """
    state = subprocess.run(
        ["systemctl", "show", "livolod.service", "--property=ActiveState", "--value"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    if state != "inactive":
        raise RuntimeError(
            "livolod must be stopped by the coordinated hardware owner before this tool runs"
        )
    config = read_registry(load(args.config))
    output = Path(args.output)
    with output.open("x"):
        pass
    record = dict(
        started_at=time.time(),
        output_commands_sent=0,
        transcript=[],
        events=[],
        measurements=[],
    )
    gateway = None
    consumer = None
    try:
        gateway = AuditedGateway.open(config.serial_port)
        gateway.gateway_id, gateway.tag, gateway.transcript = (
            config.gateway,
            config.tag,
            record["transcript"],
        )

        async def consume():
            """Collect unsolicited UART events for the phase-trial evidence."""
            while True:
                received, line = await gateway.events.get()
                record["events"].append(dict(monotonic=received, line=line))

        consumer = asyncio.create_task(consume())
        if await gateway.command("PING") != "OK EC-GATEWAY 3":
            raise LinkError("UART v3 required")
        caps = key_values(await gateway.command("CAPS"))
        PhaseCapabilities.parse(caps)
        record["caps"] = caps
        session = secrets.token_hex(8)
        while int(session, 16) == 0:
            session = secrets.token_hex(8)
        await gateway.command("DISARM")
        await gateway.command(f"NETWORK {config.gateway} {config.tag} {session}")
        for device in config.devices:
            await gateway.command(f"PEER SET {device.slot} {device.id} 1f")
            peer = key_values(await gateway.command(f"PEER GET {device.slot}"))
            if any(
                peer.get(k) != v
                for k, v in dict(
                    slot=str(device.slot), used="1", device=device.id, ack_mask="1f"
                ).items()
            ):
                raise LinkError("Peer restore verification failed")
        await gateway.command(f"ARM {session} 60")
        await gateway.command(f"ACK {session} 1")
        client = PhaseClient(gateway, session, caps, gateway_id=config.gateway)
        await asyncio.wait_for(
            measurements(gateway, client, config, record["measurements"]), 45
        )
        record["completed"] = True
    except BaseException as error:
        record["completed"] = False
        record["error"] = str(error) or type(error).__name__
        raise
    finally:
        cleanup_error = None
        if gateway:
            try:
                await gateway.command("DISARM", retries=0)
                record["final_status"] = key_values(
                    await gateway.command("STATUS", retries=0)
                )
                if (
                    record["final_status"].get("armed") != "0"
                    or record["final_status"].get("ack") != "0"
                ):
                    raise LinkError("Final DISARM verification failed")
            except BaseException as error:
                cleanup_error = error
                record["cleanup_error"] = str(error) or type(error).__name__
            finally:
                try:
                    await gateway.close()
                except BaseException as error:
                    cleanup_error = error
                    record["close_error"] = str(error) or type(error).__name__
        if consumer:
            consumer.cancel()
            await asyncio.gather(consumer, return_exceptions=True)
        record["finished_at"] = time.time()
        if cleanup_error:
            record["completed"] = False
        output.write_text(json.dumps(record, indent=2) + "\n")
        if cleanup_error and "error" not in record:
            raise cleanup_error


def main():
    """Parse phase-trial options and run the bounded measurement session."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True)
    parser.add_argument("--output", required=True, help="New JSON evidence path")
    args = parser.parse_args()
    asyncio.run(check(args))


if __name__ == "__main__":
    main()
