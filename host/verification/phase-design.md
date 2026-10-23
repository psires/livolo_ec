# Measured phase trailer: host/MCU design proposal

Status: host helper and explicit opt-in runtime integration are implemented locally;
all 67 tests pass. The integration is deployed as release 05 in explicit experimental
phase mode; output acceptance remains a separate captured trial. See the
[runtime review record](phase-integration.md) for configuration and rollback details.
The exact probe layout and reference diagnostics now
match the finalized local [UART contract](../../firmware/gateway/UART_PROTOCOL.md).
The six-attempt retry candidate remains local and
is not part of the proposed first phase-validation trial.

The concrete candidate advertises `phase=tx_entry_v1 phase_scale=65500
phase_max_ms=12000 phase_probe=1`. It stamps immediately before radio send, before
downstream CCA/scheduler work, and explicitly does not claim SFD accuracy. A
`PHASE_PROBE <session> <seq2hex> <nonce8hex>` generates a self-addressed 38-byte
non-EC diagnostic for timing measurement. Initial helper use defaults to probes;
device sends require separate explicit opt-in. Raw heartbeat success establishes
the reference; explicit busy retains the prior reference, uncertain heartbeat
failure invalidates it, and disarm/lease expiry/reset clears it. Age at or above
12 seconds rejects with `ERR PHASE_REFERENCE`. Enriched TX replies retain their
`OK/ERR TX n` prefix and describe actual raw bytes, entry/return/anchor ticks,
reference generation and age. These are proposed candidate semantics, not a
validated hardware capability.

The host validates all probe bytes before the trailer, including both gateway
addresses, sequence, marker, nonce and reserved bytes. It verifies scaled trailer
arithmetic, unsigned clock wrap, clock plausibility and reference-generation
consistency. `phase_valid=0` ignores historical anchor fields. Existing raw SEND
heartbeat replies intentionally carry no timestamp metadata; reference recovery
must inspect STATUS. Helper and runtime tests pass, but hardware output-phase
enablement awaits joint review and a separately coordinated captured trial.

The first non-actuating hardware measurement is now complete: all 14 probes
appeared once on air, no local output frames were observed, and encoded-trailer
residuals stayed within 18.245–815.544 microseconds. See the
[probe validation record](phase-probe-plan.md). This supports reviewing an explicit
device experiment; it does not enable runtime phase controls or establish switch
acceptance. No CCA-failure timing samples were obtained.

## Independently checked evidence

The original EC capture contains 21 EC-origin 8c02 attempts for one-gang switch
75710834 (suffix 0001): 17 direct attempts and four relay-addressed attempts.
For all 21, `trailer[2] + little_endian_u16(trailer[:2]) / 65500` differs from
elapsed RF time since the preceding EC heartbeat by only 0.437–0.820 ms. The
non-grid trailer c2cf05 represents 5.812 seconds. This check excludes packets
transmitted by relays, whose trailers differ. See the per-frame
[verification record](phase-reference-2026-10-17.json).

The user reports every app operation worked. OFF token 2d used three direct
attempts at 0, 0.211997 and 0.600053 seconds, with its first state report at
0.611394 seconds. Its late success is not confounded by relay forwarding.
Token 04's 13 EC-origin attempts over 3.600305 seconds include four attempts via
relay addresses; repeated state reports carried counter zero. This does not
establish why the stock gateway continued retrying. Do not infer a required
13-attempt policy, counter-wrap bug or relay implementation from that transaction.

## Timestamp ownership

Prefer MCU ownership. UART v3 reports `OK TX 0` after synchronous radio send and
provides no TX timestamp. Host command construction, UART serialization, MCU
command queue, CCA and response/event buffering separate host monotonic time from
RF time. A roughly 100-byte UART request alone takes roughly 8.7 ms at 115200
baud with 8N1 framing. Measuring from the previous UART completion cannot account
for these delays to the sub-millisecond residual seen in the capture.

The current board reports a 65536 Hz, 32-bit high-resolution timer. That clock
frequency is distinct from the measured trailer denominator 65500; conversion
must use explicit integer scaling. The firmware owner must establish whether a
TX-entry or radio SFD timestamp is available and quantify CCA-related error. MCU
ownership improves the timing boundary; it does not by itself prove RF accuracy.

Firmware source review confirms that `ieee-mode.c` performs channel-clear checking
before scheduling, and `sched.c` uses `RF_scheduleCmd` with `RF_AllowDelayAny`
before blocking for completion. `RADIO_PARAM_LAST_PACKET_TIMESTAMP` is RX-only.
Therefore a timestamp before `NETSTACK_RADIO.send()` is a submission/entry
timestamp, not an RF SFD timestamp, and includes variable CCA/scheduler delay.
Prefer a driver hook after CCA and immediately before scheduling, with explicit
timestamp metadata. Even that hook precedes potentially delayed RF execution;
Nordic validation must measure the remaining error rather than assume it away.

## Proposed opt-in UART contract (subject to firmware review)

- Advertise a distinct phase-stamping capability in CAPS. Use an explicit command
  such as `SEND_PHASE <session> <rawhex>`, never silently mutate raw SEND behavior.
- Restrict stamping initially to valid, direct, configured-peer 41-byte 8c02
  commands. Preserve peer/network/session/lease checks. Do not expand routing,
  pairing, query or automatic-ACK behavior.
- Track the high-resolution timestamp associated with the last successfully
  transmitted own heartbeat. An explicitly busy heartbeat must not become the
  reference. Reset/session replacement invalidates it; expired/disarmed state
  must not permit a stale reference to be used on re-arm.
- Compute the three-byte trailer immediately before each new radio attempt.
  Preserve the application body/token and checksum, which excludes the trailer.
  Repeated UART requests with the same ID/bytes still return the cached response
  without retransmission or restamping. An explicit busy result may be retried
  with a new request ID and a newly computed trailer.
- Reject missing/expired reference and unsupported frame types explicitly. Agree
  the reference-age bound and timer-wrap handling in the firmware contract; do
  not modulo-wrap an arbitrarily old phase into apparently fresh bytes.
- Return or expose the actual stamped trailer and timestamp/reference metadata
  for Nordic comparison. Document whether timestamps are TX entry, completion or
  SFD. Keep native state reports as the only output-confirmation evidence.

## Host behavior after negotiation

Once that capability is verified, remove the forced per-set heartbeat prelude
and up-to-ten-second wait from the phase-enabled path. Maintain ordinary periodic
heartbeats. Use short RF-lock sections to order any due heartbeat before the next
phase-stamped command; release the lock during retry/report waits so a scheduled
heartbeat can establish a new reference between retries. Firmware then stamps
each retry against the actual current reference. Keep lease maintenance runnable.

Require a successful reference heartbeat after session restoration before accepting
phase-enabled output commands. Treat reference errors as explicit non-sends and
recover the reference before any bounded retry; treat UART ambiguity as an unknown
outcome and rebuild the session without replaying output intent. Unsupported
firmware must report the missing capability, not silently use estimated phase.
Any legacy zero-trailer behavior must remain explicitly experimental and opt-in.

For the first captured comparison, keep release 04's three application attempts,
token allocation and approximately 200 ms waits. Preserve the local candidate's
absolute four-second deadline and early-stop checks. Park its six-attempt expansion
so phase stamping and retry duration are not changed together. No relaxation of
device/tag/source/subject/mask/value/token/freshness matching is proposed.

## Required validation before deployment

Jointly review the final UART contract. Firmware tests should cover known trailer
vectors, fraction/second rollover, timer wrap, successful versus busy heartbeat
reference, stale/reset/session handling and request-cache replay. Host tests should
cover capability gating, readiness after reference creation, ordering across a
heartbeat boundary, same-token retries with refreshed phase, busy versus uncertain
outcomes, deadline/early stop and reconnect without replay. A bounded Nordic trial
must first measure actual RF timing errors without actuating outputs, using a
jointly agreed diagnostic method that respects the existing frame policy. Do not
label a same-state output command non-actuating. Only after that timing check
should a bounded output trial measure phase errors at several phases of the
ten-second cycle.
Successful software tests do not establish switch acceptance or RF timestamp accuracy.
