"""Capability-gated volatile MCU queue and heartbeat control; no network persistence."""

import asyncio
import time
from .protocol import decode, key_values
from .uart import LinkError


class SchedulerClient:
    def __init__(self, gateway, session, caps):
        """Bind the scheduler to one installed host-authorized radio session.

        @param gateway Exclusive UART transport.
        @param session Installed session identity.
        @param caps Advertised capabilities.
        """
        if (
            caps.get("scheduler") != "timed_v1"
            or caps.get("scheduler_hz") not in ("65536", "1000000")
            or caps.get("rtimer_hz", caps.get("scheduler_hz"))
            != caps.get("scheduler_hz")
            or caps.get("scheduler_slots") != "8"
            or caps.get("tx_sequence") != "adapter_when_hb"
        ):
            raise LinkError("Unsupported MCU scheduler contract")
        self.gateway, self.session = gateway, session
        self.hz = int(caps["scheduler_hz"])
        self.query_supported = (
            caps.get("query") == "transaction_v1" and caps.get("query_slots") == "1"
        )
        self.query_ac_supported = caps.get("query_ac") == "slot_preserved_v1"
        self.query_grid_supported = caps.get("query_grid") == "heartbeat_200ms_v1"
        self.output_supported = (
            self.query_supported and caps.get("output_grid") == "transaction_200ms_v1"
        )
        self.job_id = 0
        self.last_status = None

    async def status(self):
        """Read queue credits, heartbeat timing and the current phase anchor."""
        result = key_values(await self.gateway.command(f"SCHED {self.session}"))
        try:
            for name in ("now", "hb_due", "hb_sent", "hb_missed", "hb_late_max"):
                if not 0 <= int(result[name]) <= 0xFFFFFFFF:
                    raise ValueError
            if result["hb_active"] not in ("0", "1"):
                raise ValueError
            if sum(int(result[k]) for k in ("free", "pending", "done")) != 8:
                raise ValueError
        except (KeyError, ValueError):
            raise LinkError("Malformed MCU scheduler status") from None
        self.last_status = result
        return result

    async def start_heartbeat(self, raw):
        """Install an absolute ten-second cadence and verify its first phase anchor.

        @param raw Host-authorized heartbeat frame with initial counter/parameter.
        """
        before = await self.status()
        due = (int(before["now"]) + (self.hz + 9) // 10) & 0xFFFFFFFF
        await self.gateway.command(f"HEARTBEAT {self.session} {due} {raw.hex()}")
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            await asyncio.sleep(0.02)
            status = await self.status()
            if (
                status["hb_active"] == "1"
                and status.get("phase_valid") == "1"
                and status["hb_sent"] != before["hb_sent"]
            ):
                return status
        raise LinkError("MCU initial heartbeat did not establish a phase reference")

    async def enqueue(self, raw, due, *, phase=False, grace=None):
        """Reserve a one-shot job without claiming it has transmitted.

        @param raw Read-only query or self-addressed diagnostic frame.
        @param due Absolute MCU target timestamp in advertised scheduler ticks.
        @param phase Whether to stamp a read-only query at dispatch.
        @param grace Maximum dispatch lateness in MCU ticks.
        @return Monotonically increasing job identifier within this session.
        """
        if grace is None:
            grace = self.hz // 50
        if not 0 <= due <= 0xFFFFFFFF or not 1 <= grace <= self.hz // 10:
            raise ValueError("Invalid scheduled timestamp or grace")
        if self.job_id == 0xFFFFFFFF:
            raise LinkError("Scheduler job identities exhausted; restore session")
        self.job_id += 1
        job = self.job_id
        reply = await self.gateway.command(
            f"QUEUE {self.session} {job} {due} {grace} {int(phase)} {raw.hex()}"
        )
        if reply != "OK QUEUED":
            raise LinkError("Invalid queue acceptance")
        return job

    async def result(self, job, *, cancel=False, release=False):
        """Read or cancel a job; completed results remain retained until released.

        @param job Job identity returned by enqueue.
        @param cancel Request cancellation if it has not yet dispatched.
        @param release Free a completed slot after reading its outcome.
        @return Adapter job metadata; acceptance is distinct from TX or native confirmation.
        """
        op = "QCANCEL" if cancel else "QGET"
        result = key_values(await self.gateway.command(f"{op} {self.session} {job}"))
        if result.get("id") != str(job) or result.get("state") not in (
            "pending",
            "done",
        ):
            raise LinkError("Invalid retained job result")
        if release and result["state"] == "done":
            await self.gateway.command(f"QDROP {self.session} {job}")
        return result

    async def start_query(
        self, raw, mask, response_wait=4.0, *, ac_only=False, grid=False
    ):
        """Delegate a bounded read-only transaction using the measured TI RF spacing.

        @param raw Validated direct opcode-10 query template.
        @param mask Allowed peer output bits from the authoritative registry.
        @param response_wait Existing host response-wait policy in seconds.
        @param ac_only Leave the preparation slot silent; preserve timing and limits.
        @param grid Defer eligible AC sends to heartbeat-relative 200 ms boundaries.
        @return Unique transaction identity; acceptance is not confirmation.
        """
        if not self.query_supported:
            raise LinkError("MCU query transactions unsupported")
        if grid and (not ac_only or not self.query_grid_supported):
            raise LinkError("MCU grid queries require AC-only and grid capability")
        if ac_only:
            if not self.query_ac_supported:
                raise LinkError("MCU AC-only queries unsupported")
            if len(raw) != 41 or raw[22] != 0x10:
                raise LinkError("AC-only query requires an opcode-10 template")
            raw = bytearray(raw)
            raw[22] = 0xAC
            raw[37] = sum(raw[21:37]) & 255
            raw = bytes(raw)
        if self.job_id == 0xFFFFFFFF:
            raise LinkError("Scheduler job identities exhausted; restore session")
        # Preserve the earlier 235.5ms / 4247.5ms measured wire schedule. The
        # former UART/reply overhead is now an explicit timer interval.
        pair = round(0.2355 * self.hz)
        wait = round((response_wait + 0.012) * self.hz)
        self.job_id += 1
        job = self.job_id
        command = "QUERY_GRID" if grid else "QUERY"
        reply = await self.gateway.command(
            f"{command} {self.session} {job} {pair} {wait} {mask} {raw.hex()}"
        )
        if reply != "OK QUERY":
            raise LinkError("Invalid query acceptance")
        return job

    async def start_output(self, raw, mask, baseline):
        """Install a six-attempt, four-second output transaction on the MCU grid.

        @param raw Opcode-02 frame containing the requested mask, value and token.
        @param mask Output bits authorized by the peer registry.
        @param baseline Three bytes of prior state, counter and token for freshness.
        @return Accepted transaction identity; acceptance does not confirm output.
        """
        packet = decode(raw)
        if (
            not self.output_supported
            or len(raw) != 41
            or packet.opcode != 2
            or packet.family != 0x8C
            or len(baseline) != 3
            or not 1 <= mask <= 15
            or raw[38:] != bytes(3)
            or packet.data[:2] != b"\x01\x00"
            or not packet.data[2]
            or packet.data[2] & ~mask
            or packet.data[3] & ~packet.data[2]
            or packet.data[4:7] != bytes(3)
            or not packet.data[7]
        ):
            raise ValueError("Invalid grid output transaction")
        if self.job_id == 0xFFFFFFFF:
            raise LinkError("Scheduler job identities exhausted; restore session")
        self.job_id += 1
        job = self.job_id
        reply = await self.gateway.command(
            f"OUTPUT_GRID {self.session} {job} {mask} {baseline.hex()} {raw.hex()}"
        )
        if reply != "OK OUTPUT":
            raise LinkError("Invalid output acceptance")
        return job

    async def query_result(
        self, job, raw, mask, *, cancel=False, output=False, baseline=None
    ):
        """Validate retained progress and any exact-peer temporal confirmation.

        @param job Accepted transaction identity.
        @param raw Original query or output frame binding source, peer, tag and suffix.
        @param mask Registry-authorized output mask.
        @param cancel Stop pending execution while retaining its outcome.
        @param output Validate the four-second output contract instead of a query.
        @param baseline Prior state, counter and token bytes required for outputs.
        @return Validated transaction fields, including a retained report if confirmed.
        """
        op = "QUERY_CANCEL" if cancel else "QUERY_GET"
        result = key_values(await self.gateway.command(f"{op} {self.session} {job}"))
        try:
            if result["id"] != str(job) or result["state"] not in ("pending", "done"):
                raise ValueError
            outcome = int(result["outcome"])
            started, deadline, rx = (
                int(result[k]) for k in ("started", "deadline", "rx_ticks")
            )
            rounds, sent, attempts, retries = (
                int(result[k]) for k in ("rounds", "sent", "attempts", "retries")
            )
            duration = (4 if output else 14) * self.hz
            bounds = (
                (
                    result.get("output") == "1"
                    and result.get("grid") == "1"
                    and rounds == 0
                    and 0 <= sent <= attempts <= 6
                    and retries == 0
                )
                if output
                else (
                    result.get("output", "0") == "0"
                    and 0 <= rounds <= 3
                    and 0 <= sent <= 2 * rounds
                    and sent <= attempts <= 8 * rounds
                    and 0 <= retries <= 18
                )
            )
            if (
                not all(0 <= n <= 0xFFFFFFFF for n in (started, deadline, rx))
                or (deadline - started) & 0xFFFFFFFF != duration
                or not bounds
                or outcome not in range(5)
                or (result["state"] == "pending") != (outcome == 0)
            ):
                raise ValueError
            if outcome == 1:
                report = bytes.fromhex(result["report"])
                packet = decode(report)
                if (
                    len(report) != 39
                    or packet.family != 0x9E
                    or packet.opcode != 5
                    or packet.source != raw[3:7].hex()
                    or packet.subject != packet.source
                    or packet.suffix != raw[27:29].hex()
                    or packet.tag != raw[19:20].hex()
                    or packet.destination not in (raw[7:11].hex(), "ffffffff")
                    or packet.data[:2] != b"\x01\x00"
                    or packet.data[2] & ~mask
                    or packet.data[3] & ~mask
                    or ((rx - started) & 0xFFFFFFFF) >= duration
                    or not 0 <= int(result["rx_sequence"]) <= 0xFFFFFFFF
                    or not -128 <= int(result["rssi"]) <= 127
                    or not 0 <= int(result["lqi"]) <= 255
                ):
                    raise ValueError
                if output and (
                    not attempts
                    or baseline is None
                    or len(baseline) != 3
                    or packet.data[2] != raw[31]
                    or packet.data[3] & raw[31] != raw[32]
                    or packet.data[7] != raw[36]
                    or bytes((packet.data[3], packet.data[6], packet.data[7]))
                    == baseline
                    or ((rx - int(result["entry_ticks"])) & 0xFFFFFFFF) >= 0x80000000
                ):
                    raise ValueError
            elif result["report"] != "-":
                raise ValueError
        except (KeyError, ValueError, TypeError):
            raise LinkError("Invalid retained query result") from None
        return result

    async def drop_query(self, job):
        """Release a completed transaction only after the Pi consumes its outcome.

        @param job Completed transaction identity.
        """
        if (
            await self.gateway.command(f"QUERY_DROP {self.session} {job}")
            != "OK DROPPED"
        ):
            raise LinkError("Invalid query drain response")
