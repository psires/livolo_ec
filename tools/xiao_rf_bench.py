#!/usr/bin/env python3
"""Bounded experimental XIAO cross-capture; never a gateway or flash tool."""

import argparse
from contextlib import ExitStack
import json
from pathlib import Path
import re
import secrets
import time

BOARDS = {
    "mg24": ("seeed-xiao-mg24", 0x2886, 0x0062, True),
    "c6": ("seeed-xiao-esp32c6", 0x303A, 0x1001, False),
}
MAX_LINES = 4096
MAX_LINE = 1024


def samples(mode):
    """Select one fixed experiment without automatically expanding its RF scope.

    @param mode Explicit baseline or cca-ack mode.
    @return Immutable length, CCA, ACK-request tuples per transmitting endpoint.
    """
    if mode == "baseline":
        return ((39, 0, 0),) * 10 + ((41, 0, 0),) * 10
    if mode == "cca-ack":
        return ((39, 1, 0), (41, 1, 0), (39, 0, 1), (41, 0, 1))
    raise ValueError("Unknown RF experiment mode")


def fields(text):
    """Parse unambiguous diagnostic key/value tokens.

    @param text Space-separated tokens with no duplicate keys.
    @return Parsed string dictionary, rejecting malformed fields.
    """
    result = {}
    for token in text.split():
        key, separator, value = token.partition("=")
        if not separator or not key or not value or key in result:
            raise ValueError("Malformed or duplicate diagnostic field")
        result[key] = value
    return result


def integer(value, low, high):
    """Read a bounded decimal diagnostic value without coercing other types.

    @param value Decimal text.
    @param low Smallest allowed value.
    @param high Largest allowed value.
    @return Validated integer.
    """
    if not isinstance(value, str) or not re.fullmatch(r"-?[0-9]+", value):
        raise ValueError("Invalid diagnostic integer")
    number = int(value)
    if not low <= number <= high:
        raise ValueError("Diagnostic integer out of range")
    return number


def frame(nonce, sequence, length, cca=0, ackreq=0):
    """Construct only the frozen non-Livolo synthetic MAC payload, without FCS.

    @param nonce Nonzero eight-character lowercase hexadecimal run identity.
    @param sequence Unique uint8 sequence within this run.
    @param length MAC length, 39 or 41, excluding FCS.
    @param cca Explicit diagnostic CCA bit.
    @param ackreq Explicit diagnostic ACK-request bit, never a Livolo action.
    @return Exact expected bytes for independent receiver comparison.
    """
    if not re.fullmatch(r"[0-9a-f]{8}", nonce) or int(nonce, 16) == 0:
        raise ValueError("Nonzero lowercase eight-hex nonce required")
    if not 0 <= sequence <= 255 or length not in (39, 41):
        raise ValueError("Invalid synthetic frame bounds")
    if cca not in (0, 1) or ackreq not in (0, 1):
        raise ValueError("Invalid diagnostic flag")
    data = bytearray.fromhex("018800efbe2222efbe1111")
    data[0] = 0x21 if ackreq else 0x01
    data[2] = sequence
    data += b"XRF1" + bytes.fromhex(nonce)
    data += bytes((sequence, length, cca, ackreq))
    data += bytes((offset * 17 + sequence) & 255 for offset in range(23, length))
    return bytes(data)


def fcs(data):
    """Calculate the reflected IEEE 802.15.4 CRC with initial value zero.

    @param data MAC bytes excluding the two transmitted CRC bytes.
    @return Two-byte little-endian FCS.
    """
    crc = 0
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ (0x8408 if crc & 1 else 0)
    return crc.to_bytes(2, "little")


def event(line):
    """Validate RF events without interpreting TX completion as reception.

    @param line Complete ASCII line from the diagnostic firmware.
    @return Structured RX/TX event, or None for another line type.
    """
    if line.startswith("RF TX "):
        data = fields(line[6:])
        sequence = integer(data.get("seq"), 0, 255)
        if data.get("status") not in ("done", "failed", "timeout"):
            raise ValueError("Invalid TX outcome")
        return dict(kind="tx", seq=sequence, status=data["status"])
    if not line.startswith("RF RX "):
        return None
    data = fields(line[6:])
    length = integer(data.get("len"), 1, 125)
    mode = data.get("fcs")
    if mode not in ("included", "stripped") or data.get("crc") != "1":
        raise ValueError("Invalid CRC metadata")
    raw = data.get("data", "")
    if not re.fullmatch(r"[0-9a-fA-F]+", raw):
        raise ValueError("Invalid received hex")
    packet = bytes.fromhex(raw)
    if len(packet) != length + (2 if mode == "included" else 0):
        raise ValueError("Received length/FCS mismatch")
    return dict(
        kind="rx",
        length=length,
        payload=packet[:length].hex(),
        fcs=mode,
        hardware_crc_valid=True,
        software_crc_valid=(
            (fcs(packet[:length]) == packet[-2:]) if mode == "included" else None
        ),
        rssi=integer(data.get("rssi"), -128, 127),
        lqi=integer(data.get("lqi"), 0, 255),
        ticks=integer(data.get("ticks"), 0, 0xFFFFFFFF),
    )


class Link:
    """Bound line framing and retain events while correlating numbered responses."""

    def __init__(self, port, name):
        """Bind an already exclusively opened endpoint.

        @param port Nonblocking serial-like object, real or offline fixture.
        @param name Exact board key used in evidence.
        """
        self.port = port
        self.name = name
        self.buffer = bytearray()
        self.records = []
        self.events = []
        self.responses = {}
        self.next_id = secrets.randbelow(0xFFFFFFFF - 128) + 1
        self.awaited = None
        self.unmatched = 0
        self.partial_write = False

    def poll(self):
        """Consume bounded fragments, preserving unsolicited RF observations."""
        raw = self.port.read(min(256, self.port.in_waiting))
        self.buffer.extend(raw)
        while b"\n" in self.buffer:
            raw, _, rest = self.buffer.partition(b"\n")
            self.buffer = bytearray(rest)
            if len(raw) > MAX_LINE or len(self.records) >= MAX_LINES:
                raise ValueError("Diagnostic stream limit exceeded")
            line = raw.rstrip(b"\r").decode("ascii", "backslashreplace")
            self.records.append(line)
            parsed = event(line)
            if parsed is not None:
                self.events.append(parsed)
            elif line.startswith("RSP "):
                match = re.fullmatch(
                    r"RSP ([1-9][0-9]*) (OK(?: .*)?|ERR(?: .*)?)", line
                )
                if match is None:
                    raise ValueError("Malformed numbered response")
                ident = int(match[1])
                if ident != self.awaited:
                    self.unmatched += 1
                elif ident in self.responses:
                    raise ValueError("Duplicate command response")
                else:
                    self.responses[ident] = match[2]
        if len(self.buffer) > MAX_LINE:
            raise ValueError("Unterminated diagnostic line exceeds limit")


def pump(links, until, clock=time.monotonic, sleep=time.sleep):
    """Service both endpoints until a finite monotonic deadline.

    @param links Open diagnostic links.
    @param until Absolute monotonic deadline.
    @param clock Injectable monotonic clock for offline tests.
    @param sleep Injectable scheduler yield.
    """
    while clock() < until:
        for link in links:
            link.poll()
        sleep(0.002)


def command(link, text, links, timeout=2, clock=time.monotonic, sleep=time.sleep):
    """Submit once; finish partial writes but never replay a timed-out request.

    @param link Destination endpoint.
    @param text Frozen diagnostic command text.
    @param links Both endpoints to drain during the request.
    @param timeout Finite request budget including writes.
    @param clock Injectable monotonic clock.
    @param sleep Injectable scheduler yield.
    @return Matching OK body; ERR and uncertainty raise an exception.
    """
    if not 0 < timeout <= 5 or "\n" in text or len(text) > 280:
        raise ValueError("Invalid request bound")
    if link.partial_write:
        raise RuntimeError("Previous write was incomplete; no further commands")
    ident = link.next_id
    link.next_id += 1
    link.awaited = ident
    wire = f"{ident} {text}\n".encode("ascii")
    sent = 0
    uncertain_write = False
    deadline = clock() + timeout
    try:
        while clock() < deadline:
            if sent < len(wire):
                uncertain_write = True
                count = link.port.write(wire[sent:])
                if not isinstance(count, int) or not 0 <= count <= len(wire) - sent:
                    raise ValueError("Invalid serial write count")
                sent += count
                uncertain_write = False
            for endpoint in links:
                endpoint.poll()
            if sent == len(wire) and ident in link.responses:
                reply = link.responses.pop(ident)
                if not reply.startswith("OK"):
                    raise RuntimeError(f"{link.name}: {reply}")
                return reply
            sleep(0.002)
        raise TimeoutError(
            f"{link.name}: request {ident} outcome unknown; not replayed"
        )
    finally:
        link.partial_write = uncertain_write or 0 < sent < len(wire)
        link.awaited = None


def status(reply, active):
    """Require diagnostic RF settings and bounded monotonic counter values.

    @param reply Successful RFSTATUS response.
    @param active Required RF window state.
    @return Parsed status evidence.
    """
    data = fields(reply.removeprefix("OK "))
    for key, expected in (
        ("active", str(active)),
        ("channel", "26"),
        ("auto_ack", "0"),
        ("retries", "0"),
    ):
        if data.get(key) != expected:
            raise ValueError(f"Unexpected RF setting: {key}")
    integer(data.get("power_dbm"), -128, 0)
    for key in ("tx_started", "tx_done", "tx_failed", "rx", "dropped", "queued"):
        integer(data.get(key), 0, 0xFFFFFFFF)
    integer(data.get("busy"), 0, 1)
    if data.get("fault") != "0" or data.get("bad_crc") != "unavailable":
        raise ValueError("RF fault or unknown bad-CRC reporting contract")
    if data.get("ticks_hz") != "1000000" or data.get("timestamp") != "callback":
        raise ValueError("Unknown RF timestamp semantics")
    return data


def analyze(expected, received, transmitted):
    """Match entire independent captures and report loss without assigning its cause.

    @param expected Sequence-to-payload mapping for one transmitting endpoint.
    @param received Events recorded on the other endpoint.
    @param transmitted Events recorded on the transmitter, never reception proof.
    @return Separate capture, sequence, duplicate and completion metrics.
    """
    payloads = {payload.hex(): sequence for sequence, payload in expected.items()}
    counts = dict.fromkeys(expected, 0)
    tx = {sequence: [] for sequence in expected}
    unrelated = invalid_crc = software_checked = hardware_only = out_of_order = 0
    last = -1
    for item in received:
        if item["kind"] != "rx":
            continue
        if item["software_crc_valid"] is False:
            invalid_crc += 1
            continue
        sequence = payloads.get(item["payload"])
        if sequence is None:
            unrelated += 1
            continue
        counts[sequence] += 1
        software_checked += item["software_crc_valid"] is True
        hardware_only += item["software_crc_valid"] is None
        if sequence < last and counts[sequence] == 1:
            out_of_order += 1
        last = max(last, sequence)
    for item in transmitted:
        if item["kind"] == "tx" and item["seq"] in tx:
            tx[item["seq"]].append(item["status"])
    missing = [sequence for sequence, count in counts.items() if not count]
    return dict(
        expected=len(expected),
        independently_received=len(expected) - len(missing),
        missing_sequences=missing,
        missing_fraction=len(missing) / len(expected) if expected else None,
        duplicate_sequences={
            str(seq): count - 1 for seq, count in counts.items() if count > 1
        },
        out_of_order=out_of_order,
        unrelated_frames=unrelated,
        invalid_software_crc=invalid_crc,
        independently_crc_checked=software_checked,
        hardware_crc_only=hardware_only,
        tx_outcomes={str(seq): values for seq, values in tx.items()},
        tx_completion_complete=all(values == ["done"] for values in tx.values()),
        capture_complete=bool(expected) and not missing and not invalid_crc,
        loss_attribution="unresolved: radio, receiver queue and USB delivery are separate",
    )


def ack_observations(events, requested):
    """Count classic IEEE ACK captures without asserting causal or timing correlation.

    @param events RX observations from one endpoint.
    @param requested ACK-request sequences transmitted by that endpoint.
    @return Counts and sequence hints; no claim of ACK success or measured timing.
    """
    counts = {}
    for item in events:
        if item["kind"] != "rx" or item["software_crc_valid"] is False:
            continue
        raw = bytes.fromhex(item["payload"])
        # Classic immediate ACK: only Frame Pending may vary in the low FCF byte.
        if len(raw) == 3 and raw[0] in (0x02, 0x12) and raw[1] == 0:
            seq = str(raw[2])
            counts[seq] = counts.get(seq, 0) + 1
    return dict(
        observed=sum(counts.values()),
        sequence_counts=counts,
        matching_sequence_observed=sum(
            count for seq, count in counts.items() if int(seq) in requested
        ),
        unmatched_sequence_observed=sum(
            count for seq, count in counts.items() if int(seq) not in requested
        ),
        matching_requested_sequences=sorted(
            int(seq) for seq in counts if int(seq) in requested
        ),
        attribution="unproven: sequence coincidence alone is not a causal ACK or timing measurement",
        scope="classic three-byte IEEE ACK only; absence means not observed",
    )


def verify_endpoint(path, serial_number, name, ports):
    """Match an explicit persistent path against enumerated USB identity before opening.

    @param path Operator-selected absolute Linux by-id path; no automatic selection.
    @param serial_number Exact expected serial from the private bench inventory.
    @param name Board key with a fixed USB VID/PID and DTR policy.
    @param ports Enumerated serial metadata, without opening any port.
    @return Resolved device path for duplicate-endpoint detection.
    """
    location = Path(path)
    if location.parent != Path("/dev/serial/by-id") or not serial_number:
        raise ValueError("Explicit /dev/serial/by-id path and USB serial required")
    resolved = location.resolve(strict=True)
    _, vid, pid, _ = BOARDS[name]
    matches = [p for p in ports if Path(p.device).resolve() == resolved]
    if len(matches) != 1 or (
        matches[0].vid,
        matches[0].pid,
        matches[0].serial_number,
    ) != (vid, pid, serial_number):
        raise ValueError("USB endpoint identity mismatch")
    return str(resolved)


def run(
    links, nonce, evidence, clock=time.monotonic, sleep=time.sleep, *, mode="baseline"
):
    """Run one explicit fixed experiment, retaining evidence and stopping owned windows.

    @param links Exactly MG24 then C6 links, already exclusively opened.
    @param nonce Fresh run nonce shared by both diagnostic windows.
    @param evidence Mutable output receipt, populated even on failure.
    @param clock Injectable monotonic clock for offline runner tests.
    @param sleep Injectable scheduler yield.
    @param mode Fixed baseline or separately selected four-frame cca-ack experiment.
    """
    started = []
    plan = samples(mode)
    expected = {link.name: {} for link in links}
    evidence.update(
        mode=mode, nonce=nonce, baseline={}, final={}, cleanup_errors=[], requests=[]
    )
    deadline = clock() + 30

    def request(link, text):
        """Keep every ordinary request inside the total baseline deadline.

        @param link Destination endpoint.
        @param text Diagnostic command.
        @return Matching success reply.
        """
        remaining = deadline - clock()
        if remaining <= 0:
            raise TimeoutError("Total baseline deadline exceeded")
        return command(link, text, links, min(2, remaining), clock, sleep)

    try:
        for link in links:
            if request(link, "PING") != "OK EC-GATEWAY 3":
                raise ValueError("Wrong UART protocol")
            version = fields(request(link, "VERSION")[3:])
            caps = fields(request(link, "CAPS")[3:])
            for data in (version, caps):
                if (
                    data.get("board") != BOARDS[link.name][0]
                    or data.get("experimental") != "1"
                    or data.get("proto") != "3"
                    or data.get("update") != "none"
                ):
                    raise ValueError("Live board identity mismatch")
            for key, value in (
                ("proto", "3"),
                ("gateway", "0"),
                ("ack_mask", "00"),
                ("peers", "0"),
                ("rf_diag", "synthetic_v1"),
                ("radio_tx", "synthetic_only"),
            ):
                if caps.get(key) != value:
                    raise ValueError(f"Unexpected diagnostic capability: {key}")
            current = status(request(link, "RFSTATUS"), 0)
            evidence["baseline"][link.name] = current
        evidence["preflight_event_counts"] = {
            link.name: len(link.events) for link in links
        }
        for link in links:
            link.events.clear()
        for link in links:
            # Even a lost START reply may have opened a window: cleanup owns it.
            started.append(link)
            request(link, f"RFSTART {nonce}")
            fresh = status(request(link, "RFSTATUS"), 1)
            if any(
                fresh[key] != "0"
                for key in ("tx_started", "tx_done", "tx_failed", "dropped", "busy")
            ):
                raise ValueError("RF window did not start with clean counters")
        for index, link in enumerate(links):
            for sample, (length, cca, ackreq) in enumerate(plan):
                seq = index * 32 + sample
                expected[link.name][seq] = frame(nonce, seq, length, cca, ackreq)
                evidence["requests"].append(
                    dict(
                        board=link.name, seq=seq, length=length, cca=cca, ackreq=ackreq
                    )
                )
                start = clock()
                response = fields(
                    request(link, f"RFTX {nonce} {seq} {length} {cca} {ackreq}")[3:]
                )
                if response.get("queued") != "1" or response.get("seq") != str(seq):
                    raise ValueError("TX admission mismatch")
                pump(links, min(deadline, start + 0.2), clock, sleep)
        pump(links, min(deadline, clock() + 1), clock, sleep)
        for link in links:
            status(request(link, "RFSTATUS"), 1)
    finally:
        for link in reversed(started):
            try:
                command(link, "RFSTOP", [link], 1, clock, sleep)
            except Exception as error:
                evidence["cleanup_errors"].append(f"{link.name}: {error}")
        if started:
            try:
                pump(links, clock() + 0.2, clock, sleep)
            except Exception as error:
                evidence["cleanup_errors"].append(f"drain: {error}")
        for link in started:
            try:
                evidence["final"][link.name] = status(
                    command(link, "RFSTATUS", [link], 1, clock, sleep), 0
                )
            except Exception as error:
                evidence["cleanup_errors"].append(f"{link.name}: {error}")
        evidence["directions"] = {
            link.name: analyze(
                expected[link.name], links[1 - index].events, link.events
            )
            for index, link in enumerate(links)
        }
        if mode == "cca-ack":
            evidence["case_outcomes"] = {
                link.name: {
                    case: analyze(
                        {
                            seq: payload
                            for seq, payload in expected[link.name].items()
                            if payload[offset] == 1
                        },
                        links[1 - index].events,
                        link.events,
                    )
                    for case, offset in (("cca", 21), ("ackreq", 22))
                }
                for index, link in enumerate(links)
            }
        evidence["ack_observations"] = {
            link.name: ack_observations(
                link.events,
                {
                    seq
                    for seq, payload in expected[link.name].items()
                    if payload[22] == 1
                },
            )
            for link in links
        }
        if mode == "cca-ack":
            observed_ack = any(
                item["observed"] for item in evidence["ack_observations"].values()
            )
            evidence["ack_check"] = dict(
                status="inconclusive" if observed_ack else "no_classic_ack_observed",
                explanation=(
                    "Observed ACKs require review; ambient traffic and sequence coincidences do not prove either board generated an ACK"
                    if observed_ack
                    else "No classic ACK captured; hidden ACKs and addressed-unicast behavior remain unqualified"
                ),
            )
        evidence["transcripts"] = {link.name: link.records for link in links}
        evidence["unmatched_responses"] = {link.name: link.unmatched for link in links}
        evidence["received_event_counts"] = {
            link.name: sum(item["kind"] == "rx" for item in link.events)
            for link in links
        }
        evidence["fcs_modes_valid"] = all(
            item["fcs"] == ("included" if link.name == "mg24" else "stripped")
            for link in links
            for item in link.events
            if item["kind"] == "rx"
        )


def passed(evidence):
    """Accept only a complete selected experiment with confirmed idle radios.

    @param evidence Completed or partial structured receipt.
    @return True only when all independent capture and counter checks pass.
    """
    if (
        evidence.get("error")
        or evidence.get("cleanup_errors")
        or not evidence.get("fcs_modes_valid")
    ):
        return False
    if set(evidence.get("final", {})) != set(BOARDS) or set(
        evidence.get("directions", {})
    ) != set(BOARDS):
        return False
    try:
        count = len(samples(evidence.get("mode", "baseline")))
    except ValueError:
        return False
    if evidence.get("mode") == "cca-ack" and (
        set(evidence.get("ack_observations", {})) != set(BOARDS)
        or any(item["observed"] for item in evidence["ack_observations"].values())
    ):
        return False
    for name, result in evidence["directions"].items():
        if not (
            result["expected"] == count
            and result["capture_complete"]
            and result["tx_completion_complete"]
            and not result["duplicate_sequences"]
        ):
            return False
        final = evidence["final"][name]
        if any(
            final.get(key) != value
            for key, value in (
                ("active", "0"),
                ("fault", "0"),
                ("dropped", "0"),
                ("queued", "0"),
                ("busy", "0"),
                ("tx_started", str(count)),
                ("tx_done", str(count)),
                ("tx_failed", "0"),
            )
        ):
            return False
        if int(final["rx"]) != evidence["received_event_counts"][name]:
            return False
    return True


def main():
    """Require explicit inventory and save a bounded private experiment receipt."""
    parser = argparse.ArgumentParser(description=__doc__)
    for name in BOARDS:
        parser.add_argument(f"--{name}-port", required=True)
        parser.add_argument(f"--{name}-serial", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--mode", choices=("baseline", "cca-ack"), default="baseline")
    args = parser.parse_args()
    # Refuse overwriting evidence and verify output before opening either radio.
    with args.output.open("x") as output:
        evidence = dict(
            schema=1, passed=False, mode=args.mode, scope="synthetic RF experiment only"
        )
        try:
            import serial
            from serial.tools import list_ports

            inventory = list(list_ports.comports())
            paths = [
                verify_endpoint(
                    getattr(args, name + "_port"),
                    getattr(args, name + "_serial"),
                    name,
                    inventory,
                )
                for name in BOARDS
            ]
            if len(set(paths)) != 2:
                raise ValueError("Two distinct endpoints required")
            evidence["endpoints"] = {
                name: dict(
                    path=getattr(args, name + "_port"),
                    usb_serial=getattr(args, name + "_serial"),
                    dtr=BOARDS[name][3],
                    rts=False,
                    baud=115200,
                )
                for name in BOARDS
            }
            with ExitStack() as stack:
                links = []
                for name in BOARDS:
                    port = serial.Serial(
                        port=None,
                        baudrate=115200,
                        timeout=0,
                        write_timeout=0.25,
                        exclusive=True,
                    )
                    stack.callback(port.close)
                    port.dtr = BOARDS[name][3]
                    port.rts = False
                    port.port = getattr(args, name + "_port")
                    port.open()
                    links.append(Link(port, name))
                nonce = f"{secrets.randbelow(0xFFFFFFFF) + 1:08x}"
                run(links, nonce, evidence, mode=args.mode)
            evidence["passed"] = passed(evidence)
        except Exception as error:
            evidence["error"] = str(error)
        finally:
            json.dump(evidence, output, indent=2)
            output.write("\n")
    print(json.dumps({"passed": evidence["passed"], "output": str(args.output)}))
    return 0 if evidence["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
