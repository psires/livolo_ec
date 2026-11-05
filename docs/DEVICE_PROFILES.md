# Experimental switch profile detection

The optional automatic add flow recognizes the pairing layouts of the tested
one-gang and two-gang EC switches. It proposes an output configuration from
observed traffic; it does not perform a standard Zigbee endpoint interview or
identify every Livolo model. The original manual output-count/suffix flow
remains available and is required for unknown or conflicting evidence.

## Evidence and matching rule

The classifier accepts only checksum-validated native `9e/01` requests with
unregistered network tag `ff`, source equal to subject, a nonreserved device ID,
and destination equal to our gateway or broadcast. It requires both the
subject suffix and the following eight-byte registration payload:

| Experimental profile | Subject suffix | Registration payload | Outputs |
| --- | --- | --- | --- |
| `livolo-ec-suffix-0001` | `0001` | `00 01 01 05 34 01 CC 00` | 1 |
| `livolo-ec-suffix-0002` | `0002` | `00 02 03 05 34 01 CC 00` | 2 |

`CC` is the changing byte at payload offset 6; its precise semantics are not
required for classification. It varies between retransmission groups and is
excluded from the profile signature. The other bytes must match the observed
layout exactly. A known suffix with an unfamiliar payload is unknown, not a
successful detection. This intentionally leaves untested firmware variants to
manual configuration.

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
The unit tests embed four short, exact CRC-valid frames from these captures,
including early and later requests from each tested switch. They require no
radio or development capture files.

The [protocol investigation](../analysis/protocol.md#one-gang-discovery-and-binding-on-sonoff-011-2026-11-01)
records the physical one-button ON/OFF verification, one-gang full-state mask
`01`, and two-gang full-state mask `03`. The separate user-confirmed one-gang
EC switch `75710834` also uses suffix `0001` in its control capture, which
corroborates the suffix association but supplies no additional pairing layout.
These repeated frames are observations of two paired test units, not hundreds
of independent device validations.

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
- Bind the user's selection to a server-issued profile token. At submission,
  derive outputs and suffix from fresh, unambiguous server evidence; do not
  trust a client-supplied count. A stale or changed token requires reselection.

The aggregation signature includes suffix, payload bytes 0–5 and byte 7. It
excludes byte 6 so routine counter changes do not create false conflicts.
Unknown and ambiguous outcomes must remain visible with a manual fallback.

## Pairing procedure and limits

Put only the intended new switch into its physical pairing mode, with original
EC gateway pairing disabled. In the add dialog, choose **Auto-detect · Experimental**
first, then select the switch's observed ID. Changing modes clears the current
selection so the profile is reviewed again. If no fresh
known profile is available, use the existing manual process rather than
assuming two outputs or using a stale advertisement.

After registration, use the existing targeted pairing window and admission
sequence. Automatic profile selection changes no radio timing, retry limits,
ACK rules or handshake packets. Verify the actual button count and state
reporting after joining. The original one-gang capture showed reports initially
routed toward the original EC gateway even after tag adoption, so temporary
routing/control problems are not by themselves evidence of a gang mismatch.

Older devices exposing four logical gangs, other product types, unknown
suffixes and different registration payloads remain unqualified. This feature
must stay labelled experimental until broader independent hardware testing
establishes its limits.
