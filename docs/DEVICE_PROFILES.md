# Experimental switch profile detection

The bounded automatic commissioning flow recognizes the pairing layouts of the
tested one-gang and two-gang EC switches. An explicit **Pair new device** action
opens a host-owned observation/admission window; putting the intended switch
into physical pairing mode supplies the evidence. The daemon selects its output
configuration from that evidence and verifies local communication after admission.
This is not a standard Zigbee endpoint interview or a universal Livolo model
decoder. The manual output-count/suffix flow remains available for unknown or
conflicting evidence. See [the pairing guide](PAIRING.md) for user instructions.

## Evidence and matching rule

The classifier accepts only checksum-validated native `9e/01` requests with
unregistered network tag `ff`, source equal to subject, a nonreserved device ID,
and destination equal to our gateway or broadcast. It requires both the
subject suffix and the following eight-byte registration payload:

| Experimental profile | Subject suffix | Registration payload | Outputs |
| --- | --- | --- | --- |
| `livolo-ec-suffix-0001` | `0001` | `00 01 01 05 34 01 CC 00` | 1 |
| `livolo-ec-suffix-0002` | `0002` | `00 02 03 05 34 01 CC 00` | 2 |
| `livolo-ec-suffix-0002` (additional observed variant) | `0002` | `00 02 03 04 2e 00 CC 00` | 2 |

`CC` is the changing byte at payload offset 6; its precise semantics are not
required for classification. It varies between retransmission groups and is
excluded from the profile signature. The other bytes must match the observed
layout exactly. A known suffix with an unfamiliar payload is unknown, not a
successful detection. Only the listed exact prefixes are accepted; other
variants remain unknown. In particular, bytes 3–5 are not treated as arbitrary
version fields, and the additional two-gang prefix does not authorize an
analogous one-gang prefix.

The raw captures inspected for this change contain:

- `ec_device_pairing_sequence.pcapng`: three native requests from physically
  two-gang switch `46715621`, suffix `0002`, payload
  `0002030534010400`; the first is frame 136.
- `gateway-pairing-20261017T182031Z-nrf52840.pcapng`: three native requests from
  that same two-gang switch, payload `0002030534013d00`; first frame 292.
- `onegang-pairing-20261101-nrf52840.pcapng`: 178 native requests from the
  user-confirmed one-gang switch `66780945`, suffix `0001`, with the pattern
  above and changing byte `04` through `2e`; first frame 1691.

The first capture is in `captured/`; the latter two are development evidence
under `captured/development/` and may not be included in a source distribution.
The profile tests embed four short, exact CRC-valid frames from these captures,
including early and later requests from each tested switch. The portable
[`commissioning_capture.json`](../host/tests/fixtures/commissioning_capture.json)
fixture additionally preserves admission ACKs, relayed requests, old-destination
reports and the first locally addressed state report. These tests require no
radio or development capture files.

The [protocol investigation](../analysis/protocol.md#one-gang-discovery-and-binding-on-sonoff-011-2026-11-01)
records the physical one-button ON/OFF verification, one-gang full-state mask
`01`, and two-gang full-state mask `03`. The separate user-confirmed one-gang
EC switch `75710834` also uses suffix `0001` in its control capture, which
corroborates the suffix association but supplies no additional pairing layout.
These repeated frames are observations of two paired test units, not hundreds
of independent device validations.

### Additional two-gang observation, November 7, 2026

After the diagnostic retention change, the owner's commissioning attempt for
the physically confirmed two-gang switch `67181844` retained suffix `0002` and
exact payload `00 02 03 04 2e 00 30 00`. This is evidence from a checksum-validated,
eligible native pairing observation in the explicit session, not a recovered
pcap frame. The session expired with `unsupported_device_seen`; the switch
remained unregistered. One distinct signature was retained, with truncation
false. Neither a full raw frame nor a repetition count was retained. The exact
source/destination envelope cannot be reconstructed from these diagnostics.

The additional allowlist entry changes only the fixed prefix from
`00 02 03 05 34 01` to the separately observed `00 02 03 04 2e 00`. The meanings
of the differing bytes remain unknown; a hardware/firmware generation difference
is a hypothesis, not a decoded field. The retained byte 6 is `30`. Ignoring
that position uses the existing retransmission rule established by the earlier
captures; this new record alone does not demonstrate its variation.

Historical original-EC traffic independently supports the two-output control
layout for this same switch. In
`captured/development/original-ec-live-20261025-nrf52840.pcapng`, command/report
pairs 1254→1256, 1260→1265 and 1457→1463 use masks `02`, `03` and `01` with the
existing `8c/02` and `9e/05` formats. Frames 1953→1955 command and report output 2
ON with mask/state `02/02`. Separate historical native reports include complete
state `03`. These observations and the owner's physical identification support
two outputs; a suffix or a partial state mask alone would not suffice.

A scan of all 64 retained pcaps found no earlier native pairing request from
this ID and no `04 2e 00` payload sequence. Thus the exact new pairing payload
comes only from the retained November 7 commissioning evidence. Regression
frames derived from it use synthetic envelopes and checksums, and counter-byte
variants in tests are synthetic too. They must not be described as captured
full frames. Matching uses the payload layout, never the example device ID.

See the [investigation record](verification/pairing-67181844-20261107.md) for
session provenance and limits. Successful local admission and control of this
variant remain unverified. All existing freshness, ambiguity and locally
addressed state-report confirmation requirements still apply; neither a matching
profile nor an offline lifecycle test establishes physical pairing success.

## Evidence that must not determine output count

State bits describe current outputs, and a state report can cover only a
subset of outputs. An `01` mask alone does not prove a one-gang device. The
tested one- and two-gang switches also share properties `24→02`, `25→04`, and
`26→00`. Those values do not distinguish their physical gang counts; in
particular `04` does not establish four outputs. The `a0` capability-like
report remains unresolved. Passive `07`/`09` announcements, ACKs, gateway
commands and relayed source/subject mismatches are not eligible pairing
evidence for this feature.

Matching is empirical, not authentication. These packets are plaintext and
their application CRC detects corruption, not forgery. The classifier must
never itself admit a device, transmit a probe, or open unrestricted pairing.

## Freshness and conflict handling

`livolod.device_profile.pairing_profile(packet, gateway=...)` is a pure function.
It returns profile evidence for every qualifying request, including unknown
suffixes/payloads. It returns `None` for unrelated traffic. The daemon owns
aggregation and validates the selected observation again when adding a device:

- Evidence belongs to the current radio session and device identity. Clear it
  on reconnect; do not restore old detection evidence from the device registry.
- Use local monotonic receive time, with a 60-second freshness bound. Repeated
  copies can refresh reception time but do not increase confidence in the
  physical model. Expired evidence cannot authorize automatic addition.
- Observe relevant frames before discovery publication throttling. Conflicting
  signatures within the active observation cohort make it ambiguous, including
  an unknown signature alongside a known one. Do not let a later known packet
  silently overwrite the conflict. A new cohort can begin after the freshness
  gap.
- In the manual reviewed-selection path, bind the selection to a server-issued
  profile token. At submission derive outputs and suffix from fresh,
  unambiguous server evidence; do not trust a client-supplied count. A stale or
  changed token requires reselection.
- In the bounded commissioning path, accept only evidence received after the
  explicit window opened. A one-second initial settling interval detects
  competing eligible IDs or conflicting signatures and stops instead of choosing
  an arbitrary first frame. Latch one eligible unregistered identity atomically;
  subsequent devices must not replace that target or become extra registrations.
  Never select a device by strongest RSSI. Known devices and repeated requests
  must not allocate duplicate slots or entities. Repetition must not extend the
  window's overall deadline, and conflicting evidence must not be silently
  overwritten by another familiar request.

The aggregation signature includes suffix, payload bytes 0–5 and byte 7. It
excludes byte 6 so routine counter changes do not create false conflicts.
The two accepted two-gang prefixes remain distinct signatures: seeing both
for one identity in the same cohort is a conflict, despite their shared output count.
Unknown and ambiguous outcomes must remain visible with a manual fallback.

## Pairing procedure and limits

Open **Pair new device**, then put only the intended new switch into physical
pairing mode, with original EC gateway pairing disabled. The window is bounded
to 120 seconds by default and at most 120 seconds, and handles one supported
device. Previously cached discovery
alone cannot start admission. The pure classifier never sends anything; the
explicit host commissioning transaction owns registration, peer configuration,
the bounded admission sequence and its final result.

Firmware 0.13 / UART protocol 3 already provides the required transport and
volatile peer table. `PEER SET` is allowed only while disarmed: adding a peer
requires a guarded brief radio reconfiguration, not a hot edit while armed.
The host must preserve existing peers, verify the table and restore ARM, ACK,
heartbeat scheduling and phase reference before admission traffic. DISARM
invalidates queued work and timing references. Normal commands and maintenance
must not race this operation. Firmware retains no authoritative registry.

Admission preserves the observed `fa`, `10`, `ac` exchange. The registration
ACK body is `01` followed by the selected request's first seven payload bytes;
the changing request byte must be echoed, not replaced with a profile constant.
No output toggle is needed to commission a switch. Sending or queueing the ACK,
registering a database row, receiving a property/query response, or observing
the expected tag alone does not establish successful local communication.

The automatic confirmation rule requires all of:

- A CRC-valid native family `9e`, opcode `05` report, with immediate source and
  subject both equal to the selected ID and suffix equal to the detected profile.
- The configured local network tag and destination equal to **our gateway**.
  Broadcast reports and reports addressed to the original EC are insufficient
  for this stronger commissioning result.
- The supported eight-byte state payload starting `01 00`, with mask and state
  bits contained in the detected output mask. Validate this before confirmation;
  an arbitrary tag-matching announcement cannot complete the transaction.
- Fresh reception during the current admission session, after the admission
  transmit-start boundary and before its fixed deadline, plus a positive radio
  TX result for the selected registration ACK. `OK TX 0` proves transmission,
  not device action; queue acceptance alone does not prove transmission. RX may
  precede the UART TX reply, so it can be held until that positive result arrives.

In the one-gang capture, our registration ACKs are frames 2123/2124. Reports
2132–2139 carry the adopted tag but still address the old EC gateway. Frame
2143 is a valid state report to our gateway and frame 2144 acknowledges it.
This distinction prevents premature success while routing still points elsewhere.
The existing manual targeted-pairing path's broader tag-adoption result must not
be mistaken for this automatic commissioning confirmation.

Timeout, cancellation or a lost session must stop new admission sends. Once an
ACK may have reached the switch, local cleanup cannot prove that the physical
switch has become unpaired. Keep that uncertainty explicit; do not claim remote
unpairing or an automatically restored previous network. A device left registered
but unconfirmed uses the advanced targeted pairing action for a retry; automatic
commissioning selects only unregistered devices. Never silently repeat admission
after a daemon restart.

Older devices exposing four logical gangs, other product types, unknown
suffixes and different registration payloads remain unqualified. This feature
must stay labelled experimental until broader independent hardware testing
establishes its limits.

These rules and capture fixtures support offline implementation and testing.
They do not establish a live end-to-end qualification of the new one-button
flow, nor authentication: there is no proven commissioning nonce or device
identity challenge in this protocol. No new firmware build is required by the
observed exchange.
