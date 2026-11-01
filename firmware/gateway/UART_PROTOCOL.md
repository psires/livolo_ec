# EC radio UART protocol v3

Host integration contract. Application UART: 8N1, no flow control; default 115200.
The firmware build selects `UART_BAUD=115200|230400|460800|921600`; the host
`serial_baudrate` must match. `CAPS baud=` and the startup banner report the build
rate. This is not live baud negotiation and does not persist a setting to flash.
MG21 Gecko recovery and TI ROM recovery remain at 115200, independently of the
application rate. A baud-changing release needs coordinated firmware/config
migration; normal updates should preserve the installed rate.
One exclusive owner, one outstanding request. No settings are written to flash.
This is a guarded EC radio adapter, not a Zigbee coordinator or device database.

MG21 additionally reports `radio_drops`, `uart_rx_errors`, `uart_rx_overruns`,
`uart_framing_errors` and `uart_parity_errors` in STATUS. The three specific UART
counters classify observed hardware flags; a single observation can contain more
than one flag, and counts are not a byte-loss total. `board=zb-gw04-1v2` and
`board=sonoff-zbdongle-e` identify the supported MG21 wiring profiles.

## Framing and lifecycle

Requests: `<nonzero uint32 decimal id> COMMAND arguments\n` (maximum 319 ASCII
characters excluding newline). Replies: `RSP <id> OK ...` or `RSP <id> ERR ...`.
RX, ACK and periodic STATUS records may appear between request and reply.
Use exact request-ID matching. Unknown commands/arity normally return `ERR SYNTAX`;
configuration/edit gates may reject earlier with NOT_CONFIGURED or ARMED.
Numbers are decimal except fields explicitly described as hex.

Opening a live UART may start mid-line or yield a corrupt first line. Discard
malformed records and resynchronize at newline; do not search inside garbage for
a successful reply. Retry an unanswered request using the same ID and bytes.
The hardware test observed this condition on its initial PING and recovered
using the reply cache on the subsequent run.

Boot prints `EC-GATEWAY 3 ... tx=disabled`. Configuration, all peers, lease,
ACK enable, session and request cache are volatile. Boot is unconfigured/disarmed.
The host restores its own authoritative configuration after every module reset.

The last complete request and reply are cached. Same ID and identical bytes
returns the old reply without repeating effects; same ID/different bytes gives
`ERR ID_REUSE`. This is one cached request, not a durable transaction log.
Retry a missing reply using the same ID and bytes. If unresolved, DISARM and
reconnect/reconcile state; do not blindly issue a new command token.

## Commands

| Command | Result/effect |
| --- | --- |
| `PING` | `OK EC-GATEWAY 3` |
| `CAPS` | Machine-readable `key=value` capabilities; ignore unknown keys |
| `STATUS` | Network, session, revision, peer count, lease and diagnostics |
| `NETWORK <gateway8hex> <tag2hex> <session16hex>` | Disarm, clear all peers, install network; nonzero session required |
| `PEER SET <slot> <device8hex> <ackmask2hex>` | Set/replace a slot, only while disarmed; duplicate device in another slot rejected |
| `PEER DEL <slot>` | Remove slot (idempotent), only while disarmed |
| `PEER GET <slot>` | `OK PEER slot=N used=0` or `... used=1 device=... ack_mask=...` |
| `PEER CLEAR` | Clear all slots, only while disarmed |
| `ARM <session16hex> <seconds>` | Enable TX for 1–60 seconds; renew before expiry |
| `ACK <session16hex> <0\|1>` | Global fast-ACK gate; enabling requires a live lease |
| `SEND <session16hex> <rawhex>` | Guarded frame transmission; requires matching session and live lease |
| `CHECK <rawhex>` | Generate configured peer's ACK without TX: `OK ACKHEX ...` or `OK NO_ACK` |
| `META <0\|1>` | Enable/disable optional callback-time metadata; default off |
| `DISARM` | Immediately disable TX and ACKs; does not delete configuration |

Slots are 0–255. A slot's device identity must be nonzero, nonbroadcast and distinct
from the gateway. Tag must be 01–fe. Identity hex is **wire byte order**, as in
the captures; do not reverse it to match IEEE/PAN notation. An ACK mask of zero
keeps the peer eligible for host TX but disables its automatic ACKs.

ACK mask bits: `01` state (`9e/05`), `02` property (`9e/15`), `04` capability
(`9e/a0`), `08` direct keepalive (`9e/08`), `10` additionally allow broadcast state
reports. Mask `1f` is the currently verified profile. Bit `10` has no effect
without bit `01`. Unknown bits are rejected. No broadcast keepalive, admission,
announcement or ACK-of-ACK response is implemented. A valid checksum, configured
tag, and matching immediate source AND subject are required for automatic ACKs.
Existing ACK body/sequence construction is unchanged. Profiles advertising
`auto_ack_phase` stamp the timing trailer as described below; other profiles
retain the zero trailer.

Use a newly generated nonzero session per NETWORK installation. It is an opaque
host-provided incarnation identifier, not a secret or authentication mechanism.
Modern ARM/ACK/SEND require it. After reset old SEND requests fail, even if a new
network has been installed under a different session. This prevents accidental
retransmission across radio resets; it does not provide exactly-once delivery.
Never reuse a session when rebuilding configuration after a reset.

NETWORK is an explicit replacement operation and disarms even if previously
armed. PEER edits return `ERR ARMED` during a live lease. Restore a complete table
while disarmed, verify via GET/STATUS, then ARM/ACK. Expiry disables ACKs; a later
ARM does not re-enable them. Renewing a live lease preserves the ACK gate.

SEND allows a valid 41-byte family-8c EC frame with configured gateway source/tag
and destination equal to a configured peer, broadcast, or gateway itself. FCF
must be `01 88`; application sum8 must pass. Short diagnostic frames (11–38 bytes)
are allowed only to the gateway itself. FCS is generated by the radio. Max raw
length is 125. Unknown peers, IEEE ACK-request frames and arbitrary other packet
formats are rejected. The host builds EC packet bodies and schedules retries.
Manual sends use CCA; immediate MCU ACKs bypass CCA.

SEND result `OK TX 0` means radio transmission succeeded, **not** that a device
acted on it. `ERR TX 2` is an explicit channel-busy/collision result that the host
may retry with a new request ID. `ERR TX 1` is a radio error with uncertain outcome.
`ERR TX 3` is no IEEE ACK (not expected with the supported frame policy). Never
interpret a UART timeout as a known RF failure.

Errors also include `NOT_CONFIGURED`, `SESSION`, `LEASE`, `DISARMED`, `CONFIG`,
`PEER`, `DUPLICATE_PEER`, `ARMED`, `HEX`, `FRAME_POLICY`, `ID_REUSE`, `SYNTAX`.
Malformed/overflow input may be discarded without a reply; input_errors increments.

## Events and diagnostics

- `RX <sequence> <clock_ticks> <rssi> <lqi> <length> <rawhex>`: existing v2 layout.
  Sequence counts all received frames, including frames whose log was dropped.
  Ticks are sampled at MAC callback entry; they are not an RF SFD timestamp.
- Optional `RXMETA <sequence> callback_rtimer=<ticks>`: same frame, higher resolution
  callback timestamp. Discover clock frequencies and widths via CAPS. Both wrap.
- `ACKTX <rxsequence> result=<radio-result> work_ticks=<rtimer ticks> <rawhex>`:
  automatic reply. Work ticks include radio work and are not RF turnaround time.
- `STATUS <key=value ...>` every five seconds, plus STATUS command reply.

STATUS includes configured, gateway, tag, session, revision, peers, armed, ack,
lease (configured duration, not remaining), rx/tx/tx_ok/ack_tx/ack_ok, rx_drops,
uart_drops (bytes), meta_drops (records), input_errors and ticks. Counters wrap
at 32 bits and reset at boot. Revision advances on successful network/table edits.
Keep RX processing separate from outstanding command response handling. UART
serialization (especially at the default 115200) limits throughput: all raw frames are exposed; this is not a lossless
capture guarantee. Detect gaps and counter increases; a missing log does not mean
the radio failed to ACK. Use Nordic timestamps for real over-air measurements.

The compatibility `device` field in STATUS shows slot 0 only; it is not a selected
peer and does not describe the whole table. Use PEER GET for each slot. `legacy`
indicates CONFIG compatibility mode; `meta` reports metadata enable state.

## Compatibility and example

Legacy `CONFIG gw tag device` clears the table and installs slot 0 with mask 1f,
disarmed and session zero. Only in this legacy mode are `ARM seconds`, `ACK 0|1`
and `TX rawhex` accepted. Modern NETWORK disables those mutating legacy forms;
new hosts must use the session-protected forms. PING reports v3 in both modes.
RX/ACKTX formats remain compatible with existing capture tools.

```text
1 CAPS
2 NETWORK 2a992b12 a7 0123456789abcdef
3 PEER SET 0 46715621 1f
4 PEER GET 0
5 ARM 0123456789abcdef 30
6 ACK 0123456789abcdef 1
7 STATUS
8 DISARM
```

The example session is illustrative; generate a fresh one. No host heartbeats,
pairing policy, route selection, device names, output state, transaction-token
allocation, MQTT, or authoritative persistence belong in this firmware.
# Experimental phase extension

The source now advertises the optional capability `phase=tx_entry_v1`, with
`phase_scale=65500 phase_max_ms=12000 phase_probe=1`. Release 0.3 additionally
advertises `phase_queries=10_ac_v1` for the observed read-only query forms.
Do not infer these capabilities
from `proto=3`: deployed older v3 firmware does not implement it. Runtime use
must remain opt-in. The first independent Nordic diagnostic run passed; device
output acceptance and timing under CCA contention remain unverified. See
`analysis/development/gateway-phase-01.md` for the installed image and evidence.

The timestamp is sampled immediately before the radio call, after selecting its
CCA mode. It is **before the driver's actual CCA check and RF scheduling**, not
an SFD timestamp. The driver permits scheduling delay. `return_ticks` measures
when that synchronous radio call returned; it is not a TX-completion hardware
timestamp. No fitted constant offset is applied.

## Commands

`ID SEND_PHASE SESSION RAWHEX`

Requires the normal configured modern session and live lease. Only 41-byte,
checksum-valid `8c/02` frames, or `8c/10` and `8c/ac` with the exact data
`0000000000000001` when `phase_queries=10_ac_v1` is advertised, are
accepted. Frames must be addressed directly to a configured peer:
source/tag match the network, route A is zero, route B and subject
equal the destination, flags are `1e`, and the supplied trailer is zero. Other
forms return `ERR FRAME_POLICY`; there is no fallback to raw transmission.
The three trailer bytes are replaced at radio entry; application bytes and
checksum remain unchanged.

The extension does not schedule transmissions or add retries. The host retains
the existing query rounds, response waits, heartbeat schedule and cancellation
on fresh state. Missing/expired phase references are definite non-sends; once
phase queries are selected, errors must not silently fall back to raw queries.

`ID PHASE_PROBE SESSION SEQ2HEX NONCE8HEX`

Generates a 38-byte non-EC diagnostic under the existing self-addressed frame
policy. This does not issue a query or output command to any device. Layout:

| Offsets | Bytes |
| --- | --- |
| 0–2 | `01 88`, supplied sequence byte |
| 3–6 | Configured gateway destination |
| 7–10 | Configured gateway source |
| 11–27 | ASCII `EC_PHASE_PROBE_V1` (17 bytes) |
| 28–31 | Supplied four nonce bytes, in their written order |
| 32–34 | Zero |
| 35–37 | Computed timing trailer |

Both commands return the existing TX result prefix followed by diagnostics:

```text
RSP ID OK TX 0 timestamp=tx_entry entry_ticks=N return_ticks=N anchor_ticks=N anchor_gen=N age_ticks=N raw=HEX
RSP ID ERR TX 2 timestamp=tx_entry entry_ticks=N return_ticks=N anchor_ticks=N anchor_gen=N age_ticks=N raw=HEX
```

`raw` is the exact buffer supplied to the radio call. For a CCA failure it is
the attempted buffer, not proof of a transmission. Tick fields use the CAPS
`rtimer_hz` and `rtimer_bits`; age is unsigned modular subtraction. A missing
or expired reference returns `ERR PHASE_REFERENCE` without calling the radio.
Session/configuration/lease failures retain their existing errors.

## Reference and conversion

A successful raw `SEND` of the gateway's own valid heartbeat creates a reference:
41-byte `8c/08`, broadcast destination, zero route fields and subject/suffix,
flags `0e`, zero trailer, configured source/tag and valid checksum. Its reference
tick is that heartbeat's radio-entry sample, recorded only after `RADIO_TX_OK`.
The heartbeat data remains host-owned. Legacy raw reply formatting stays
unchanged (`OK TX 0` or `ERR TX n`); query `STATUS` for `phase_valid`,
`phase_anchor_ticks` and `phase_anchor_gen`.

A definitely busy heartbeat retains the previous reference. An ambiguous radio
error on a heartbeat invalidates it. DISARM, lease expiry, NETWORK/CONFIG changes
and MCU reset invalidate it. ACKs, queries, probes and output commands cannot
create a reference. A reference expires at 12 seconds; both high-resolution and
coarse-clock age checks prevent an old reference becoming valid after timer wrap.
Status anchor fields may retain historical values when `phase_valid=0`.

For age ticks `A` and timer frequency `H`:

```text
seconds = A // H
fraction = ((A % H) * 65500) // H
trailer = fraction_low_byte, fraction_high_byte, seconds
```

Multiplication uses a 64-bit intermediate. Age at or beyond 12 seconds is rejected,
not wrapped modulo a heartbeat cycle. Conversion precision is distinct from RF
accuracy. At this board's 65536 Hz, a timer tick is about 15.26 microseconds.

The existing one-request UART cache is unchanged: exact ID/body replay returns
the original result and stamped bytes without another RF attempt or restamp.
Use a new request ID only for a deliberately authorized new attempt; uncertain
UART results must use the established same-ID recovery rule. `SEND`, legacy `TX`,
automatic ACK framing, and persistent host ownership otherwise remain unchanged.

## First hardware validation, pending review

Coordinate exclusive UART ownership and preserve the host database before any
firmware change. Restore the existing session/peers from the host afterward.
Start Nordic capture before heartbeat/probe traffic. Use only ordinary own
heartbeats and self-addressed probes with unique nonces at several phases
(including near a second boundary and near the end of the heartbeat interval).
Compare captured SFD intervals with entry-age metadata and encoded trailers;
retain CCA failures and radio-call durations. Repeat exact UART IDs to verify
one over-air probe per accepted new request. Test missing/expired references
without issuing output commands. Evaluate timing error and its tail before
enabling device `SEND_PHASE`; do not label a same-state output request a probe.

# Signed UART updates — release 0.2

Release numbers are `major.minor` (numeric components, 0..65535). They are
independent of UART protocol `3` and application schema numbers. `PING` remains
`OK EC-GATEWAY 3`. `VERSION` returns `fw_version`, `board`, `proto`, `update` and
`boot_version`; `CAPS` also advertises release/update support. `STATUS` and the
startup banner include `fw_version`. Old firmware with no version fields is
**unknown**, not implicitly 0.1. An ordinary non-update build says `update=none`.

An update-enabled build advertises `update=uart_signed_v1`. Its host owns UART
exclusively throughout transfer. Except STATUS/CONFIRM, update operations require
DISARM. Radio transmission and reconfiguration are blocked during transfer and
once a candidate is committed. No update command operates RESET/FLASH GPIOs.

| Command after request ID | Result / purpose |
| --- | --- |
| `UPDATE_STATUS` | Persistent `state`, volatile `received`/`receiving`, `trial`, verification `key`, candidate/current SHA-512 |
| `UPDATE_HEADER TOKEN OFFSET HEX` | Two 96-byte manifest parts at offsets 0 and 96; TOKEN is 16 hex characters |
| `UPDATE_BEGIN TOKEN` | Verify signed manifest, board/layout/version and current image, then erase staging slot |
| `UPDATE_DATA TOKEN OFFSET HEX` | Sequential 4-byte-aligned chunks of 4..96 bytes; return `received=N` |
| `UPDATE_COMMIT TOKEN` | Verify staged image, preserve verified current image and manifest, then durably mark READY |
| `UPDATE_ACTIVATE` | Acknowledge, drain UART output, software reset into a committed image |
| `UPDATE_CONFIRM` | Make a successfully restored trial permanent |
| `UPDATE_ABORT` | Discard an incomplete transfer; a committed candidate returns `ERR ALREADY_COMMITTED` |
| `UPDATE_REBOOT` | Drain reply and software reset; an unconfirmed trial rolls back |

Existing request-ID replay rules still apply. Repeated already-written data must
match flash exactly. Duplicate data is not programmed again. A reset during
transfer discards its RAM offset; restart the transfer, preserving the current
application. After an aborted transfer, reboot to restore normal receiver state.
An interrupted host process can rerun the updater with the same signed package;
a matching committed candidate resumes at activation rather than being erased.
Do not treat a missing COMMIT/ACTIVATE response as proof that it did not happen.
Inspect the persistent state and exact candidate digest.

The package is a 192-byte manifest followed by the application bytes. The first
128 manifest bytes are signed with Ed25519; the final 64 are the signature.
The header is little-endian `<8s8I64s24s>`: magic `ECUPD01\0`, format `0x10000`,
board `0x26520001`, major, minor, UART protocol 3, execution address `0x8000`,
image length, reserved zero, SHA-512 of the image, and 24 reserved zero bytes.
The signature covers every header field. Length must be word-aligned, 256..98304
bytes. Stack/reset vectors and strictly increasing major.minor are checked.
The compiler's version marker must match the packaging version.

| Flash range | Purpose |
| --- | --- |
| `0x00000..0x07fff` | Resident bootloader and public verification key |
| `0x08000..0x1ffff` | Fixed application execution slot |
| `0x20000..0x37fff` | Candidate staging slot |
| `0x38000..0x4ffff` | Previous application recovery slot |
| `0x50000..0x51fff` | Transaction state and candidate manifest at +256 |
| `0x52000..0x53fff` | Current application manifest |
| `0x54000..0x55fff` | Recovery application manifest |
| `0x56000..0x57fff` | Reserved factory configuration sector; never updated |

State words are `ffffffff` (initial install), `fffffffe` (READY), `fffffffc`
(INSTALLING), `fffffff8` (TRIAL), `fffffff0` (CONFIRMED), `ffffffe8` (ROLLBACK),
and `ffffffc8` (RESTORED). Each transition only clears bits. Backup and candidate
are verified before READY. INSTALLING precedes the first execution-slot erase;
reset there repeats installation from staging. TRIAL is recorded before the
first jump. Any reset before confirmation restores the previous signed image;
interrupted rollback repeats from that preserved copy. A 60-second application
trial deadline and hardware watchdog provide recovery without host GPIO access.
Unknown/corrupt metadata or unverifiable images fail closed; ROM recovery remains
available. Hardware fault injection beyond the documented validation remains a
release-hardening task, not a claim of exhaustive silicon failure coverage.

The host confirms only its matching release, after capability, peer-table,
ARM/ACK and (when enabled) heartbeat-reference checks. It then persists a fresh
firmware status in SQLite for update orchestration. Firmware update metadata is
not authoritative network state; the host still reconstructs all peers/sessions.

## TI timed queues and heartbeat ownership — release 0.6

This additive UART v3 capability was introduced by the TI scheduler build:
` scheduler=timed_v1 scheduler_slots=8 scheduler_hz=65536
scheduler_horizon_ms=30000 scheduler_late_ms=20 tx_sequence=adapter_when_hb`.
MG21 also implements it in release 0.7, with `scheduler_hz=1000000`.
All state below is volatile;
NETWORK, reset, DISARM and lease expiry remove scheduled work and heartbeat
ownership. Authoritative registry, admission/pairing, routing and persistence
remain host responsibilities. The existing ARM/ACK session and lease rules apply.

Times are wrapping unsigned 32-bit MCU ticks at the advertised `scheduler_hz`
(**65536 ticks/s on TI, 1000000 on MG21**), matching `rtimer_hz`, not wall clock
or RF SFD time. Targets must be strictly in the future and at most 30 seconds
away, so signed-difference ordering is unambiguous across wrap. MG21's existing
continuously runnable main loop polls RAIL time before and between UART commands;
it does not sleep or need a wake timer. Adding sleep requires a timer wake adapter.
The TI ClockP
hardware timer wakes the Contiki process at the next deadline. The existing
radio driver then sends in process context; this stage does **not** reserve an
RF-core absolute SFD slot. CCA and radio-driver latency follow dispatch. Use
reported timestamps and Nordic measurements to distinguish those intervals.

Every command retains the ordinary decimal request-ID prefix and `RSP` envelope:

| Command body | Meaning / response |
| --- | --- |
| `SCHED session` | `OK now=... free=... pending=... done=... full=... late=... cancelled=... hb_active=... hb_due=... hb_sent=... hb_missed=... hb_retries=... hb_late_max=... hb_last_due=... hb_last_entry=... hb_result=... phase_valid=... phase_anchor_ticks=... phase_anchor_gen=...` |
| `QUEUE session job due grace mode hex` | Copy one validated frame to a free RAM slot; `OK QUEUED` means accepted, not transmitted or confirmed. |
| `QGET session job` | Read a retained pending/done job; never consumes it. |
| `QCANCEL session job` | Cancel only if pending, retaining the outcome; already completed jobs remain completed. |
| `QDROP session job` | Free a completed slot; pending jobs return `ERR JOB_PENDING`. |
| `HEARTBEAT session first_due hex` | Install a validated 41-byte heartbeat template; fixed ten-second period, `OK HEARTBEAT`. A second installation while active is rejected. |

Queue jobs use strictly increasing nonzero 32-bit identities within a session.
Reusing an identity, including after QDROP or disarm/rearm, returns `ERR JOB_ID`.
Installing a fresh NETWORK session resets that high water mark. Normal UART
request-ID replay still returns the cached acceptance without adding a second
job. Queue-full rejection does not consume a job identity. The eight slots hold
both pending jobs and terminal results; a slow host cannot lose a completion
because a UART event queue overflowed. It must explicitly drain results to regain
credits. Heartbeats have a separate slot and are not blocked by queue exhaustion.

`grace` is 1–6553 ticks (at most approximately 100 ms) and bounds process-dispatch
lateness, not radio completion. Expired jobs are completed without transmission.
`mode=0` permits only already-approved self-addressed diagnostics; `mode=1`
permits only the captured direct `10/ac` read-only query form with a zero input
phase trailer. Output commands and extra heartbeat injections cannot be queued.
Normal SEND/SEND_PHASE remain available. Their existing limits do not change.

QGET/QCANCEL return:

```
OK JOB id=... state=pending|done due=... result=... entry_ticks=... return_ticks=... anchor_ticks=... anchor_gen=... age_ticks=... raw=...
```

Only `state=done` makes the result meaningful. Codes 0–3 preserve radio-driver
semantics (0 sent, 2 definite CCA non-send, other failures have uncertain radio
outcomes). Negative results are -1 disarmed, -2 missing phase reference, -3 late
without sending, and -4 cancelled before dispatch. Phase jobs stamp at actual
radio-call entry, with the same guarded metadata as SEND_PHASE. A successful TX
is not a native state confirmation. In release 0.6 the daemon's query algorithm
owned freshness checks, retry rounds and early stopping; release 0.7 adds the
complete query transactions described below. Do not prequeue query retries without a suitable
cancellation/freshness policy.

A heartbeat's successive nominal deadlines are `first_due + n * 10 * scheduler_hz`, even
after host stalls or radio failures. Its captured payload seconds counter advances
by ten per slot, including skipped slots; no network records are written to
flash. A dispatch more than 20 ms late skips the slot. Definite CCA busy retains
the existing **four total attempts** and **25/50/75 ms** backoff (rounded upward
to MCU ticks). Retry deadlines use the MCU timer and never shift the periodic
epoch. Exhausted or other failed slots are reported as missed; there is no
catch-up burst. ACKs remain handled before UART logging.

While heartbeat ownership is active, firmware allocates MAC sequence bytes for
all non-automatic-ACK transmissions so independent host/heartbeat counters do not
collide. The host validates returned frames with only MAC byte 2 and the phase
trailer permitted to change, gated by `tx_sequence=adapter_when_hb`. Automatic
ACK sequence semantics remain unchanged. A host SEND of another heartbeat is
rejected with `ERR HEARTBEAT_OWNED`.

The daemon negotiates this capability only on its phase-capable path, installs
one heartbeat schedule during session restoration, verifies the first live phase
anchor before readiness/update confirmation, then monitors ownership and renews
the existing lease. It polls status for diagnostics; those polls do not drive the
ten-second cadence. Missing ownership triggers session recovery. Adapters lacking
the capability use the existing host-driven heartbeat path. Query limits remain
three rounds, a 14-second scheduling deadline and four CCA attempts per send;
the production query waits remain 224 ms at 460800 and 4.000 seconds per round.

## Complete TI state-query transactions — release 0.7

TI and MG21 additionally advertise `query=transaction_v1 query_slots=1`. This is one
separate retained RAM transaction slot, alongside the eight one-shot slots and
heartbeat slot. The daemon's command worker already serializes operations. The
Pi supplies the authorized peer/suffix, output mask and timing profile; registry,
pairing, routing, desired state and persistence remain on the Pi. Older images
without this capability retain their existing host-driven query fallback.

| Command body | Meaning |
| --- | --- |
| `QUERY session id pair_ticks wait_ticks mask hex` | Accept one direct opcode-10 template; reply `OK QUERY` is acceptance only. Firmware generates opcode ac and all bounded retries. |
| `QUERY_GET session id` | Read retained progress, final outcome, latest TX metadata and confirming native frame. |
| `QUERY_CANCEL session id` | Stop pending execution and retain cancellation; a completed outcome is unchanged. |
| `QUERY_DROP session id` | Release a terminal result; pending transactions return `ERR JOB_PENDING`. |

Job IDs share the one-shot queue's strictly increasing session high-water mark.
A retained slot returns `ERR QUERY_BUSY` to another transaction. Dropping a
result does not permit ID reuse. NETWORK, DISARM, reset and lease expiry clear
the entire transaction. The normal session/lease/frame-policy checks apply, and
MCU heartbeat ownership is required. Neither output commands nor arbitrary
query payloads can enter this engine.

The fixed bounds are **three rounds**, **10 then ac**, **four total CCA attempts
per opcode**, **25/50/75 ms** CCA backoff and a **14-second deadline from
acceptance**. Successful logical sends are at most six; dispatch attempts at
most 24. Every send checks the transaction state and deadline. Definite CCA
exhaustion consumes that logical opcode; uncertain radio failures terminate with
an error. A missing phase reference suppresses that opcode, as on the legacy
host path. A radio call already in progress may finish after its deadline, but
no subsequent call starts. A valid state report suppresses all remaining work,
including a scheduled CCA retry or the second half of a pair.

`pair_ticks` is a successful 10 TX-entry to ac TX-entry interval (approximately
200–300 ms permitted). `wait_ticks` is ac TX-entry to the next round, or to final
timeout (4.0–4.1 seconds permitted). Units follow `scheduler_hz`. CCA failures consume
their bounded backoff before the next logical operation is scheduled. For a
definite non-send/missing phase, the following interval starts at completion of
that operation. Delayed dispatch is measured by `late_max`; it never extends the
14-second limit. Heartbeats retain priority and their absolute epoch.

On TI the host selects **15434 ticks** (235.504 ms) and **262930 ticks**
(4.011993 s), matching the preceding TI capture's approximately 235.5 ms pair
and 4.248 s round-start intervals. This replaces the former 224 ms host sleep
plus UART round-trip overhead with explicit MCU timing. The experiment's extra
23 ms response wait selects 264438 ticks instead. Retry limits are unchanged;
`diagnostics.query_execution` distinguishes the MCU wire profile from the
legacy host sleeps in `diagnostics.query_timing`.

On MG21 the same time profile uses **235500** and **4012000** ticks, or
**4035000** ticks for the experimental extra wait. The clock wraps after about
71.6 minutes, handled by the same bounded modular comparisons. Release 0.7's
Sonoff port has passed build, deterministic core/host tests, dry UART checks and
a 24-request captured comparison (21 confirmed). An earlier interrupted run
recorded one UART RX overrun, so UART robustness remains an open issue; see the
[Sonoff evidence](../../analysis/development/sonoff-query-mcu-comparison-20261024.json).
The MG21
bootloader/update mechanism is unchanged: Gecko application upload at 115200,
not TI's signed update or rollback protocol.

The response format is:

```
OK QUERY id=... state=pending|done outcome=... started=... deadline=... due=... rounds=... sent=... attempts=... retries=... late_max=... tx_result=... entry_ticks=... return_ticks=... anchor_ticks=... anchor_gen=... age_ticks=... raw=... rx_ticks=... rx_sequence=... rssi=... lqi=... report=...
```

Outcomes are 0 pending, 1 confirmed, 2 timeout, 3 cancelled and 4 uncertain radio
error. `tx_result` retains the underlying radio/phase result. `raw` is the latest
attempt, or `-` before an attempt; `report` is the confirming 39-byte native
frame, or `-`. Results survive UART logging drops until explicitly released.
The engine runs from MCU timer wakes, never from host status polls. Timestamps
still describe process/radio-call entry and receive callback, **not RF SFD**.

Freshness preserves the existing read-query rule: a valid native 9e/05 callback
after acceptance and before the deadline, matching the exact peer, subject,
suffix, network tag, gateway/broadcast destination, CRC and allowed output bits.
The body must start 01/00; mask zero is valid. A repeated or wrapped report
counter is accepted because this is a fresh observation, not proof of an output
transition. A query ACK (`fa/10` or `fa/ac`) cannot confirm. There is no wire nonce
that proves the state report was caused by this query, and receive callback time
does not prove RF arrival time for a frame already buffered by the driver.

The host validates the retained frame independently before accepting/persisting
state. It uses RX sequence ordering to avoid applying a retained frame twice or
letting a delayed older RX log replace newer state. It drains completed results
without creating additional radio retries. Loss of session/transport produces
an unknown outcome and normal session reconciliation; it does not replay an
uncertain transaction. Physical output control remains on the existing path.

### Optional AC-only comparison — release 0.8

`query_ac=slot_preserved_v1` permits an opcode-`ac` template in the existing
`QUERY` command. All frame/session/peer/mask validation still applies. The
normal opcode-`10` template retains release 0.7 behavior.

An `ac` template leaves each round's preparation slot silent: at the round's
start the MCU increments `rounds`, waits `pair_ticks`, then transmits `ac` with
normal phase stamping. After that it waits `wait_ticks` before starting the
next round. Thus removing `10` does not advance the nominal AC slots or shorten
the round period. No transmission, attempt or CCA retry is counted for a silent
slot. Radio contention can still delay actual transmission; an omitted `10`
necessarily cannot experience CCA backoff.

Limits remain three rounds, four CCA attempts per transmitted frame with
25/50/75 ms backoff, and a 14-second transaction deadline. AC-only therefore has
at most three successful query transmissions and twelve CCA attempts (nine
retries), rather than reallocating the unused preparation budget to more ACs.
Fresh native reports stop either sequence using the same validation. Identity,
leases and all authoritative persistent state remain on the Pi.

The host `SchedulerClient.start_query(..., ac_only=True)` gates this mode on the
capability; default calls remain paired queries. No persistent configuration or
automatic migration to AC-only is enabled by this release.

### Heartbeat-relative AC grid — release 0.9

`query_grid=heartbeat_200ms_v1` advertises the optional
`QUERY_GRID session id pair_ticks wait_ticks mask hex` command. It accepts only
a validated AC-only template, returns `OK QUERY`, and uses the same retained
slot and `QUERY_GET/CANCEL/DROP` commands as `QUERY`. Ordinary paired and AC-only
`QUERY` behavior is unchanged.

The preparation slot and response-wait targets remain eligibility minima.
Every eligible AC attempt, including a CCA retry, is deferred to the next
200 ms boundary measured from the last successfully transmitted heartbeat's
radio-entry timestamp. The current phase reference is checked again before
sending; a new heartbeat updates the grid origin. A slot missed by more than
1 ms is skipped, and a missing live reference ends the transaction with outcome
4. No busy-wait or rounded/fabricated phase field is used: the existing send
path stamps its actual radio-entry age. CCA-to-air delay remains observable
rather than compensated speculatively.

Three rounds, four CCA attempts per frame, 25/50/75 ms backoff minima and the
14-second deadline remain unchanged. Actual CCA delays can grow to the next
grid slot; normal AC-to-AC intervals grow from about 4.2475 to 4.4 seconds. No
extra attempts or deadline extension compensate for alignment or missed slots.
The retained result adds `grid`, accumulated `grid_wait_ticks` and `grid_skips`.
These are volatile diagnostics, not persistent network state.

The host opt-in is `start_query(..., ac_only=True, grid=True)`, gated on the
capability. The normal daemon still defaults to paired queries. This release
does not enable grid alignment persistently or claim that it reproduces every
part of the original EC recovery schedule.


### MG21 DMA receive diagnostics — release 0.10

MG21 UART RX now uses a continuously linked 8 KiB LDMA ring, consumed in the
main loop. STATUS adds `uart_rx_dma_errors`, `uart_rx_resyncs`,
`uart_rx_highwater` (bytes) and `uart_rx_poll_gap_us` (microseconds). Hardware
error counters count observations of sticky flags, not exact lost-byte totals.
A DMA fault, UART RX error, observed overwrite, or polling gap long enough to
hide a complete ring lap discards uncertain data and the current partial line
through newline. The sender may see a timeout; it must use the existing exact
ID/body retry and reconciliation rules, never assume an RF outcome.

There is no query protocol, timing, retry-budget, UART baud or bootloader change.
The Pi remains authoritative. This receive implementation is MG21-specific;
TI keeps its existing UART driver.

### Sonoff automatic ACK phase, release 0.11

`CAPS auto_ack_phase=tx_entry_or_zero_v1` means automatic peer replies carry
heartbeat age encoded at radio-call entry when the heartbeat reference is live.
This capability is enabled for the Sonoff MG21 board and, from release 0.12,
the ZB-GW04 v1.2 MG21 profile and cod.m CC2652P TI build. Automatic replies still
bypass CCA and run before RX logging; no queue, wait or retry is added. With no
live reference, the existing zero trailer is retained and the ACK is sent.
`ACKTX` includes the transmitted trailer; `CHECK` still returns the unstamped
ACK template. Query timing, retry bounds, reply filters and lease rules are
unchanged. Firmware and host report release `0.11` through the existing version
interfaces; the preserved Gecko bootloader continues to use 115200 baud.


### Heartbeat-aligned output transactions — release 0.12

`output_grid=transaction_200ms_v1` advertises:

```
OUTPUT_GRID session id full_mask baseline_hex raw_hex
```

`baseline_hex` contains the last state bits, report counter and command token
(three bytes), or `ffffff` when unknown. `raw_hex` is the 41-byte opcode-02
output template with a zero phase trailer; the Pi chooses its token, peer,
selected mask and value. The normal session, route, peer and phase checks apply.
Acceptance returns `OK OUTPUT`. This transaction shares the retained query slot
and monotonically increasing job IDs; `QUERY_GET`, `QUERY_CANCEL` and
`QUERY_DROP` retrieve, cancel and drain it. A retained result adds `output=1`.
Query results carry `output=0`; their existing behavior is unchanged.

The MCU schedules the first attempt and every retry on the next live
heartbeat-relative 200 ms edge. A slot missed by more than 1 ms is skipped;
heartbeat reference changes recompute the target. Actual radio-entry time is
phase-stamped. There are at most six attempts, including busy-channel results,
with no extra CCA retry budget. The four-second deadline starts at admission and
is never extended. After attempt six the MCU only waits for confirmation.

A valid native opcode-05 report must arrive after a transmission attempt and
before the deadline, match the exact peer/tag/suffix, selected mask, requested
bits and token, and differ from the supplied baseline. Reports addressed to
this gateway or broadcast are eligible. Successful confirmation cancels further
sends before UART logging. The retained result preserves the report and RX
metadata for independent host validation. Cancellation and session reset stop
execution; no transaction or device state is persisted on the MCU.

The daemon uses this capability when available and falls back to its previous
host-managed output path on older firmware. Its additional two-second result
retrieval allowance cannot cause more RF attempts. Automatic ACKs remain
immediate; query schedules, retry limits, UART and bootloader baud are unchanged.

### Nordic native USB transport — release 0.12

The official PCA10059 nRF52840 port uses the same protocol-v3 commands, volatile
peer/session rules, scheduler capabilities and retry limits over native USB CDC.
`VERSION` reports `board=nordic-pca10059 fw_version=0.12 update=none`;
`CAPS baud=460800` preserves host configuration compatibility but does not
represent a physical UART clock. USB line-coding requests do not change RF timing.
`BOOTLOADER` disarms and enters the preserved factory USB DFU bootloader for an
application-only update. This is separate from the TI signed update/rollback
protocol and must not be treated as `update=signed_v1` support.

`STATUS transport=usb_cdc` identifies this transport. `uart_drops` counts rejected
software output bytes; `rx_drops` and `meta_drops` retain their shared-core meaning.
`radio_drops` counts raw receive-queue exhaustion/invalid buffer lengths, and
`radio_tx_timeouts` counts driver transmissions exceeding the 20 ms local bound.
`radio_rx_errors` excludes intentional RX aborts; `radio_crc_errors`,
`radio_filter_errors` and `radio_runtime_errors` distinguish CRC failures, frame
filter/length rejection and driver runtime failures. Hardware UART error fields
are zero because no UART is used. Air CRC failures are not USB transport errors.
The adapter has no qualified autonomous stall watchdog; a nonresponsive image
may require manual factory-DFU entry. See the board-specific build/recovery guide.
