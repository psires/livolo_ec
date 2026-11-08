# XIAO passive switch reception

`tools/xiao_rx_bench.py` listens on both new XIAOs on fixed channel 11. It sends
no radio frames and never issues RFSTART, RFTX, NETWORK, ARM, pairing, output,
bootloader or reset commands. Automatic ACK stays disabled. ZigBee Specialist
is the sole live operator; existing radios, sniffers, services and HA are excluded.

This is separate from the [synthetic RF experiments](XIAO_RF_BENCH.md), whose
default 40-frame baseline and opt-in eight-frame CCA/ACK-request plans are unchanged.
The owner-confirmed detached, nearby switches are `46715621` (two gang, suffix
`0002`) and `66780945` (one gang, suffix `0001`). Passive reception does not
establish pairing, output control, gateway readiness or updater qualification.

## Contract and operation

Both exact endpoints must report their canonical board identity, protocol 3,
`experimental=1`, `update=none`, `gateway=0`, `peers=0`, `ack_mask=00` and
**`rx_diag=passive_v1`**. There are no alternative capability spellings.
PING, VERSION, CAPS and RFSTATUS are read before starting either receiver.
Preflight requires inactive, not-busy radios; the last selected channel may be
11 or 26. A window observed active **after opening the port** is refused without
sending a stop command. This does not guarantee preserving a window that existed
before opening: serial close/reopen can span a USB reset on the tested C6 fixture.
Post-open idle status cannot establish what the radio was doing beforehand.

`RFLISTEN <nonzero-eight-lowercase-hex-nonce>` starts a fixed 60-second lease.
Its exact admission response is `OK active=1 channel=11 mode=passive`.
RFSTATUS must confirm channel 11, passive mode, automatic ACK off, retries off,
no latched fault and zero TX counters. Admission is not an MCU reset. Firmware
rejects every RFTX while passive; the host independently forbids sending it.

The CLI observation window is 30–45 seconds, default 30, beginning after both
receivers enter passive mode. Identity/admission/status requests have two-second
budgets. Cleanup sends RFSTOP once per owned window, drains for 200 ms and checks
inactive channel-11 passive status. Stop/final-status budgets are one second.
When CAPS advertises `queue_diag=ring_v1`, cleanup also reads RFQUEUE and
reconciles accepted, queued, formatted and independently delivered counts.
Legacy absence is recorded as unavailable, never inferred as zero overflow;
unknown advertised contracts or inconsistent snapshots fail the run. Measured
queue loss still fails the strict capture assessment. See the
[queue contract](XIAO_QUEUE_DIAGNOSTICS.md) for the exact accounting limits.
`adapter_diag=handoff_v1` additionally requires an independent RFADAPTER read
after confirmed stop, even when queue validation fails. The runner retains
legacy absence and rejects malformed or inconsistent advertised snapshots;
see the [adapter contract](XIAO_ADAPTER_DIAGNOSTICS.md).
The tool checks admission elapsed time before observing, reserving four seconds
for end-of-window checks and targeting STOP before 55 seconds from the first
start request. It aborts rather than silently shortening a requested window when
that headroom is unavailable. Scheduling/USB failure can still outlast the lease;
hardware expiry remains independent. If USB fails or
a partial write makes further commands unsafe, cleanup uncertainty is recorded
and firmware expiry remains necessary. There is no replay, automatic reopening,
re-enumeration recovery, channel change, explicit reset command or capture extension.

Use the exact private by-id paths and USB serials, checked against USB VID/PID.
Before opening either port, the sole operator must establish exclusive ownership
and that both radios are already idle. Post-open preflight is not a substitute
for that condition. Both distinct endpoints are explicitly selected and
exclusively opened. Settings
are 115200 baud, MG24 DTR true, C6 DTR false and RTS false. Use Python 3.9+ and
pyserial 3.5, with `decode_ec.py`, `xiao_rf_bench.py` and `xiao_c6_serial.py`
beside the tool. C6 uses the qualified combined line clear, with no stock fallback:

```sh
python3 tools/xiao_rx_bench.py \
  --mg24-port /dev/serial/by-id/EXACT_MG24_PATH --mg24-serial EXACT_MG24_SERIAL \
  --c6-port /dev/serial/by-id/EXACT_C6_PATH --c6-serial EXACT_C6_SERIAL \
  --seconds 30 --output /private/bench/new-passive-receipt.json
```

Replace placeholders from inventory. The output file must not exist and contains
private USB identity and raw traffic. Coordinate any physical button stimulus
with the owner inside the window; the harness never actuates the switches.

## Identity, CRC and incomplete evidence

Every RX record is retained, including unknown traffic. The vetted
`tools/decode_ec.py` parser validates the 39/41-byte EC layout, header,
family/length and application checksum. A target match requires native family
`9e`, the actual decoded MAC source equal to an authorized ID, application
subject equal to that source and the known suffix. IDs occurring only in a
destination or payload never qualify. Unsupported, invalid-checksum or
inconsistent-role records remain visible as unmatched observations.
The native-family/subject/suffix requirement is a conservative acceptance filter;
other frames from a target source remain recorded but unverified.

MG24 must include original FCS bytes, independently checked with IEEE 802.15.4
CRC. C6 must supply stripped MAC data and hardware CRC-valid metadata: its driver
replaces FCS with RSSI/LQI, so it cannot provide independent software radio-CRC
proof. Application checksum validation is separate and applies to both radios.
Wrong per-board FCS representation or failed MG24 CRC invalidates integrity.

Each receiver has its own raw lines, host delivery timestamps, decoded fields,
CRC evidence, callback ticks and full-payload SHA-256. Shared target hashes are
listed separately; MCU clocks are not synchronized, so this is not a timing
correlation. Identical payloads may be legitimate retransmissions. Modulo-256 MAC
sequence changes may cover other traffic and are not a reliable loss count.
The intersection of payload hashes does not prove that all packets were captured.
There is no RX event sequence. Delivery gaps can mean silence, buffering or
interruption; callback ticks are not SFD timestamps.

Final RX counters must equal delivered RX events. Drops, queued data, busy/fault
states, any TX counter or TX event invalidate integrity. `bad_crc=unavailable`
means no bad-CRC rate can be calculated. Partial final lines are retained in hex.

- `passed` / exit 0: complete window, verified passive shutdown, intact delivery
  and at least one valid native frame from **each switch on each radio**.
- `inconclusive` / exit 2: clean window but one or more required observations
  absent. No matches do not prove radio failure or successful reception.
- `failed` / exit 1: transport, identity, protocol, CRC, overflow, counter or
  cleanup-integrity failure. Partial evidence remains available.

## C6 USB investigation limits

Host open/close requested/returned events carry UTC and monotonic timestamps.
Complete serial lines have host delivery timestamps, including ROM boot noise.
These are host observations, not inferred reset times. The sole live operator
should retain a matching kernel USB timeline separately; the harness reads no
kernel logs and changes no USB power or system configuration.

The locally inspected pyserial 3.5 POSIX implementation calls `os.open` before
applying DTR/RTS and internally clears its input buffer during open. Setting
control lines in advance avoids an intentional toggle but does not prove
glitch-free electrical behavior. Missing boot text does not prove no boot.
Close is not an explicit reset command, but OS/USB hangup effects require hardware
evidence. Lack of explicit buffer-reset calls does not bypass pyserial's internal
behavior.

Before the combined-line fix, Release Engineering verified three close/reopen cycles:
C6 uptime restarted at 41 ms each time, then advanced to approximately 7044–7045
ms within the same open session. RFHEALTH reported reset reason 11, the pinned
SDK's `ESP_RST_USB`, with zero watchdog events and reported I/O errors. The close
and open edges were approximately 5 microseconds apart, so the evidence establishes
**USB reset across serial close/reopen under the tested host settings**, not a
unique cause in open, close or DTR. It does not establish the cause of the earlier
stall. The [public verification receipt](verification/xiao-rx-20261108.json)
records the evidence and review outcome; these observations do not apply
automatically to other hosts, USB connections or firmware builds.

The subsequent [stock-versus-combined comparison](XIAO_C6_SERIAL.md) preserved
uptime through three combined opens and both five-second closed gaps, while
stock opens each reset to 41 ms. This bench now uses that combined helper for
C6 only. The original stall remains separate and unresolved.

Both `in_waiting` and `read` can raise EIO during re-enumeration. A stable by-id
name does not repair an already open descriptor. The harness fails closed and
attempts to stop the other owned receiver. It never silently follows a replacement
device or adds a framing newline: a bare newline could finish an old partial
command. The earlier C6 startup anomaly remains unresolved; cable replacement
and later success do not establish its cause. Optional RFHEALTH is separate
specialist evidence and is neither required nor requested by this capture tool.

## Offline regression

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=tools \
  python3 -m unittest test_xiao_rx_bench test_xiao_rf_bench -v
```

Tests use captured frames and the vetted decoder, reject incidental ID matches
and corrupt/mismatched application data, preserve unknown traffic, distinguish
silence from failure, and exercise lost replies, USB EIO, counter mismatch and
counterpart cleanup. Synthetic tests cover returning from stopped passive mode
without reset and older channel-26 firmware lacking mode metadata. All tests
use virtual time and fake serial objects, with no hardware access or RF.
