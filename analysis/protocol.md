# Livolo EC protocol: capture-based reconstruction

Some development evidence referenced below remains local and is not part of the
source distribution. Dated sections record observations from specific builds and
boards; they do not imply general radio or device-family qualification.

Analysis of all five supplied captures: **1,141 packets**. Frame numbers below are the original, one-based packet numbers in each pcapng file. Offsets are zero-based from the first MAC byte, **after removing the 28-byte capture TAP header**. Hexadecimal IDs are shown in transmitted byte order unless explicitly called MAC fields.

**Follow-up evidence, 2026-10-17:** a [live home capture](development/bringup.md) now confirms transmissions from all six IDs referenced in the original gateway polling traffic. The original captures were made away from home, according to the user. Retained home-device records are therefore the leading explanation for that polling; the original-capture findings below preserve what those captures alone established.

## Principal findings

1. **This is a proprietary protocol carried in IEEE 802.15.4 data frames, not an ordinary Zigbee NWK/APS/ZCL exchange.** There is no Zigbee On/Off cluster or endpoint enumeration to decode here. An existing Livolo Zigbee converter should not be assumed compatible with EC.
2. **The switch participates in forwarding.** There are 121 matched retransmissions of gateway messages by the switch, preserving the sequence number and complete application body. Some are broadcast repetitions; others turn a broadcast into a directed transmission. This demonstrates a relay mechanism, but not arbitrary multihop routing, route recovery, or the advertised range.
3. **Output control and reporting are substantially decoded:** family `8c`, opcode `02` sets outputs; family `9e`, opcode `05` reports state; opcode `fa` carries acknowledgments/responses. The state occupies a bitfield, with bits 0 and 1 exercised in these captures.
4. **Both application checksums are recovered:** all 1,095 family-`8c` frames pass an 8-bit additive checksum; all 46 family-`9e` frames pass CRC-16/CCITT-FALSE. The MAC FCS is absent from the capture and is a separate matter.
5. **Pairing uses a proprietary exchange.** The switch's tag changes from `ff` to the gateway's `b1`, with registration-like messages and acknowledgments. Exact admission rules and the meaning of all capability fields remain unresolved.

These findings permit reliable offline decoding. They do not yet constitute a tested replacement gateway or complete pairing implementation.

**Additional controlled pairing observation, 2026-10-17:** with only the switch
put into pairing mode and the EC gateway unchanged, both receivers captured the
same 269 native registration broadcasts. No registration acknowledgement or
native `ff` → `b1` transition was observed. Five installed switch identities
forwarded registration traffic toward the gateway. Device-specific `9e/08` →
`8c/08` exchanges also revealed a separate possible keepalive operation. See the
[experiment report and packet references](development/pairing-observation-20261017T181307Z.md).

The subsequent [gateway-enabled pairing and app-control experiment](development/gateway-pairing-20261017T182031Z.md)
succeeded and was confirmed in the EC app. Both radios captured the same 65
packets involving the test switch: three registrations, two registration ACKs,
native tag adoption, follow-up queries/properties, and later independent control
of both output bits. No `0b` exchange was captured in this repeat registration;
fresh factory-reset commissioning remains unverified. The registration counter
continued from `3c` to `3d` across attempts, revising the earlier timer hypothesis.

## Capture inventory and limitations

| Capture | Packets | Duration, seconds | `8c` / `9e` frames | Matched relays |
|---|---:|---:|---:|---:|
| `ec_no_connection_registered.pcapng` | 220 | 144.410 | 220 / 0 | 0 |
| `ec_gateway ready to pair.pcapng` | 494 | 136.412 | 494 / 0 | 0 |
| `ec_device_pairing_sequence.pcapng` | 322 | 49.211 | 286 / 36 | 90 |
| `ec_set_on_off_on_off.pcapng` | 85 | 12.401 | 79 / 6 | 28 |
| `ec_1gang_on_off.pcapng` | 20 | 12.618 | 16 / 4 | 3 |

The captures are dated **2025-04-09**, approximately 15:11–15:24 UTC, rather than the date of this analysis. All packets use TAP channel 11, page 0. This is a single-channel observation; it does not establish behavior on other channels or during uncaptured intervals. No cloud/Wi-Fi traffic is included.

The sniffer interface identifies itself as `nRF Sniffer for 802.15.4`. The capture contains no FCS-type TLV; TAP specifies that this can be omitted when no FCS is present. Consequently, do not strip two trailing bytes as a presumed MAC CRC. Doing so would remove application data/checksum bytes. See the [TAP specification](https://exegin.com/wp-content/uploads/ieee802154_tap.pdf).

Two filename assumptions need qualification:

- **“No registered devices” does not mean no stored device-like records.** Starting at frame 10, the gateway cycles through six other IDs. Its heartbeat parameter also changes from 0 to 4. These could be stale records, configured targets, or another proprietary table; the captures cannot establish their provenance. No frames transmitted by those six IDs are captured.
- **The “physical button” capture includes gateway-originated output commands.** Frames 8, 13–14 and 17–18 precede the corresponding switch state reports and carry matching transaction tokens. It cannot be treated as a clean example of unsolicited physical-button reporting without clarification of the experiment. Physical interaction might coexist with gateway activity, but the observed exchanges themselves are command/response transactions.

## Radio framing and identities

Every captured frame starts `01 88`, little-endian frame control `0x8801`:

- IEEE 802.15.4 data frame, version 0;
- short source and destination addresses;
- PAN ID compression disabled, so both PAN IDs are present;
- MAC security and ACK-request bits clear.

The conventional MAC header is therefore **11 bytes**, not 19 bytes. EC appears to use each PAN/address pair as a four-byte identity. That is an interpretation of EC behavior, not an IEEE-defined 32-bit address.

| Device | EC identity, wire order | MAC PAN ID | MAC short address |
|---|---|---|---|
| Gateway | `9b f4 ea 38` | `0xf49b` | `0x38ea` |
| Switch | `46 71 56 21` | `0x7146` | `0x2156` |
| Broadcast | `ff ff ff ff` | `0xffff` | `0xffff` |

For example, a gateway command has destination PAN `7146` and short address `2156`, while its source PAN is `f49b`. This unusual addressing is another reason a conventional Zigbee coordinator integration is insufficient.

The six additional IDs appearing in the poll-like messages are `47778828`, `81422527`, `67181844`, `72715996`, `56830549`, and `75710834`. They are **referenced subjects/destinations, not six proven additional transmitters**. The last has suffix `0001`; the others have `0002`.

Standard IEEE framing was cross-checked against [Wireshark's IEEE 802.15.4 definitions](https://github.com/wireshark/wireshark/blob/master/epan/dissectors/packet-ieee802154.h). Interpreting the payload at offset 11 as a standard Zigbee NWK header produces inconsistent/reserved header interpretations across these packets, whereas the EC layout and checksums fit every packet. Compare [Wireshark's Zigbee NWK implementation](https://github.com/wireshark/wireshark/blob/master/epan/dissectors/packet-zbee-nwk.c).

## Recovered frame layout

| MAC offset | Bytes | Meaning | Confidence |
|---:|---:|---|---|
| 0 | 2 | MAC frame control, always `01 88` | Confirmed |
| 2 | 1 | MAC sequence; sometimes copied into a fast application ACK or relayed frame | Confirmed |
| 3 | 4 | Destination PAN + short address / EC destination identity | Confirmed bytes; EC identity interpretation strong |
| 7 | 4 | Source PAN + short address / immediate transmitter identity | Strong |
| 11 | 4 | Routing slot A: zeros, broadcast, or a referenced device ID | Contents confirmed; full semantics unknown |
| 15 | 4 | Routing slot B: zeros or a referenced device ID | Contents confirmed; full semantics unknown |
| 19 | 1 | Domain/network tag candidate: gateway `b1`; native switch traffic initially `ff`, subsequently `b1` | Strong association correlation |
| 20 | 1 | Routing/delivery flags; observed `00`, `0e`, `10`, `1e`, `30`, `3e` | Bytes confirmed; bit meanings unresolved |
| 21 | 1 | Application family `8c` or `9e` | Confirmed discriminator |
| 22 | 1 | Opcode | Confirmed field; semantics vary in confidence |
| 23 | 4 | Subject device ID; zero or broadcast for some global messages | Strong |
| 27 | 2 | Subject suffix/class field; switch `00 02` | Meaning unresolved; do not call it a gang count |
| 29 | 8 | Opcode-specific data | Partially decoded below |
| 37 | 1 or 2 | Application checksum, selected by family | Validated over entire corpus |

Family `8c` frames are 41 bytes: checksum at 37, then a three-byte trailer at 38–40. Family `9e` frames are 39 bytes: a two-byte CRC at 37–38, with no corresponding trailer.

The family is not simply a transmitter identifier: the switch transmits native `9e` messages **and** forwards `8c` messages.

### Checksums

For `8c`:

```python
frame[37] == sum(frame[21:37]) & 0xff
```

For `9e`:

```python
int.from_bytes(frame[37:39], "big") == binascii.crc_hqx(frame[21:37], 0xffff)
```

CRC parameters are polynomial `0x1021`, initial value `0xffff`, no reflection, xor-out 0, stored high byte first. This is CRC-16/CCITT-FALSE (also called CRC-16/IBM-3740). Both cover the 16-byte application body, excluding routing information and the domain tag.

These are exact working algorithms for all observed packets. Since each family has a constant leading byte and fixed length, mathematically equivalent CRC parameterizations over a shorter region exist; identifying the original firmware routine would require firmware or additional formats.

### Timing and routing fields

Opcode `08` is a gateway heartbeat emitted about every 10.0007 seconds. Data bytes 29–32 form a little-endian counter advancing by 10; bytes 33–36 form a parameter observed as 0, 4, 6, or 1. This looks like a seconds counter and a network/table parameter, but **neither uptime origin nor device-count semantics is established**.

In gateway `8c` traffic, trailer byte 40 tracks seconds within the approximately ten-second heartbeat cycle. Bytes 38–39 resemble a little-endian subsecond phase: for example `2c 33` near 0.2 s, `58 66` near 0.4 s, `84 99` near 0.6 s. Heartbeats have a zero trailer. Relayed copies replace the trailer, and its precise time base and relay meaning remain unknown. Do not treat it as an extra checksum or a verified replay counter.

Common gateway routing patterns are directed traffic with flag `1e`, broadcasts with `0e`, and a fallback-like form with `3e`. The switch normally changes this flag to `00` while forwarding. There are other combinations, so a complete route-selection state machine cannot be inferred.

The tag `b1` happens to equal the sum of the gateway's four identity bytes modulo 256. This is a **testable hypothesis**, not a recovered derivation rule: there is only one gateway identity in the dataset.

## Output control and state reports

### Opcode `02`, family `8c`: set outputs

Observed data layout:

```text
Offset: 29 30 31 32 33 34 35 36
Data:   01 00 MM VV 00 00 00 TT
```

- `MM`: selected-output mask candidate (`01` for first output, `03` for both).
- `VV`: desired state bits (`00` off, `01` bit 0 on, `03` both on).
- `TT`: transaction token; copied by the switch into its resulting report.

The mapping of bit 0 to the left/right physical button is unknown. The original
capture set did not establish whether unselected bits in `VV` are ignored or
include a gateway `MM=02` example. The 2026-10-25 live app capture now supplies
that evidence: switch 72715996 reports state `01`, receives `MM=02/VV=02`, and
reports `03`; then `MM=02/VV=00` produces `01` (frames 1568, 1586/1588,
1602/1606). This supports selected-bit updates preserving the other output.

### Opcode `05`, family `9e`: state report

```text
Offset: 29 30 31 32 33 34 35 36
Data:   01 00 MM SS 00 00 CC TT
```

`SS` is strongly supported as the **complete two-output state bitfield**, rather than a boolean. `MM` identifies selected/affected outputs; `CC` is a report/event counter; `TT` echoes a command token for the seven observed command transactions and is zero in the other captured state reports.

**Live-test correction:** the subsequent
[physical-button experiment](development/physical-buttons-local-01.md) observed
manual changes retaining token `b3` from the preceding local command. A nonzero
token is therefore not evidence that a new command caused the report. Correlate
reports with actual outstanding commands and state/counter changes; publish
manual changes even when the token has not changed.

The 2026-10-25 app-only capture also shows stale tokens during overlapping
gateway commands: seven command bursts reach the final requested state while
retaining the preceding command's token, and EC acknowledges those reports.
Token equality is therefore not a universal condition for successful app-driven
control either. Preserve temporal/state correlation; simply ignoring tokens
would risk matching a different outstanding action.

Evidence for a full state bitfield appears in pairing frames 76 → 98 → 144 → 173:

| Frame | `MM` | `SS` | Interpretation |
|---:|---:|---:|---|
| 76 | `02` | `02` | Only bit 1 is on |
| 98 | `01` | `03` | Bit 0 is now also on |
| 144 | `02` | `01` | Bit 1 is off; bit 0 remains on |
| 173 | `02` | `03` | Bit 1 is on again |

Repeated reports can carry the same counter despite a new MAC sequence, e.g. pairing frames 191/195 and 238/240. A receiver should not equate every MAC frame with a new user action. Some repeated/current-state reports also make `MM` unsuitable as a guaranteed XOR of consecutive states.

### Gateway-control capture: all four operations

Times are relative to the start of `ec_set_on_off_on_off.pcapng`.

| Action | Command frame(s) | First command time | Mask/state | Token | Report | Gateway ACK |
|---|---|---:|---|---|---|---|
| Both on | 15 | 1.600 s | `03 / 03` | `a4` | 16, counter 2 | 17 |
| Both off | 25, 26 | 2.800 s | `03 / 00` | `57` | 27, counter 3 | 28 |
| Both on | 47, 48 | 6.400 s | `03 / 03` | `5e` | 49, counter 4 | 50 |
| Both off | 55, 56 | 7.600 s | `03 / 00` | `fb` | 57, counter 5 | 58 |

Retries are separated by about 200 ms. They retain the application body/token while changing MAC sequence and trailer. Initial command-to-report delay is about 20 ms for the first operation and 214–216 ms for the three operations containing retries. This suggests scheduled receive/transmit behavior or retry timing; it does not prove a specific sleep schedule.

First command, frame 15, grouped by fields:

```text
01 88 73                         MAC control and sequence
46 71 56 21  9b f4 ea 38          destination / source
00 00 00 00  46 71 56 21          routing slots
b1 1e                            tag / flags
8c 02 46 71 56 21 00 02          family / opcode / subject
01 00 03 03 00 00 00 a4          data: both on, token a4
69                               additive checksum
84 99 09                         trailer
```

The “physical button” file has three corresponding transactions: command 8 → report 9 → ACK 10 (`MM=01`, state 0, token `a8`); commands 13/14 → report 15 → ACK 16 (state 1, token `a6`); commands 17/18 → report 19 → ACK 20 (state 0, token `93`). This is why the experiment label needs clarification.

### Opcode `fa`: application ACK / response envelope

A gateway acknowledgment copies the subject ID/suffix and sets its eight data bytes to:

```text
ack.data = original.opcode || original.data[0:7]
```

The last byte of the original eight-byte data field is omitted. Thus, when acknowledging a state report, the original token is not echoed a second time, while the report counter is retained. This relationship holds for **all 37 gateway `fa` packets**.

Fast acknowledgments typically arrive about 2.1 ms later and copy the report's MAC sequence. Delayed registration/capability acknowledgments use the gateway sequence stream instead. Sequence equality alone is not a sufficient matching rule. Switch-originated `fa` messages also carry responses to queries, and their data need not be a plain echo.

## Pairing reconstruction

Times below are relative to `ec_device_pairing_sequence.pcapng`.

| Frames | Time | Observation and interpretation |
|---|---:|---|
| 1–37 | 0–10.402 s | Existing gateway poll/heartbeat traffic; no switch-originated frames yet |
| 38 | 10.463 s | Switch broadcasts `9e/07`, tag `ff`, subject `467156210002`, data `f0 00 08 2c aa 00 00 00` |
| 39 | 10.465 s | Gateway responds with `8c/fa`, acknowledging opcode `07` |
| 40 | 10.499 s | Switch sends `9e/fa` whose acknowledged opcode field is `0b`; no matching `0b` request is captured |
| 41–42 | 10.801–10.808 s | First captured gateway message forwarded by the switch; forwarded tag already `b1` |
| 43–77 | 10.823–14.024 s | Native switch reports, capabilities-like `a0`, properties `15`; native tag still `ff` |
| 136, 139, 141 | 22.815–23.415 s | Repeated switch broadcasts `9e/01`, tag `ff`, data `00 02 03 05 34 01 04 00` |
| 140, 143 | 23.402, 23.802 s | Gateway acknowledges opcode `01` |
| 144 | 23.813 s | First native switch packet with tag `b1`; subsequent native traffic retains it |
| 175–177 | 29.002–29.026 s | Gateway `10` query; switch `fa` response `10 45 19 7b 38 00 00 00`, then `07` announcement |
| 188–189 | 31.203–31.413 s | Gateway `ac` query; switch `fa` response `ac 00 02 03 00 00 06 00` |
| 314–318 | 48.603–48.819 s | Gateway broadcast `0c`, relayed by switch, followed by switch `fa` response |

This supports `01` as registration/association-related and the tag as network/domain-related. It does **not** show the complete handshake: the `0b` response has no captured initiating request, and tags on forwarded traffic already differ from tags on native traffic before registration completes. A replacement gateway must not assume sending just one `fa` packet is sufficient.

The captures contain no IEEE MAC association command frames, no demonstrated Zigbee key transport, and no standard Zigbee endpoint/descriptor interview. The visible control data are plaintext and the MAC security bit is clear. The recovered checksums provide error detection, not cryptographic authentication. Whether other admission, freshness, or replay checks exist is untested.

## Other operations and the four-gang question

| Opcode | Observed role | Confidence / limitation |
|---|---|---|
| `01` | Switch registration-like announcement | Strong temporal association with tag adoption |
| `07`, `09` | Switch announcements/status associated with discovery and later operation | Content only partly understood |
| `08` | Broadcast gateway heartbeat; also a device-specific request/reply exchange in the later live capture | Heartbeat fields apply to the zero-subject form; the device exchange is a keepalive/status candidate |
| `0a` | Repeated gateway traffic cycling through six device-like records | Poll/maintenance hypothesis; not proof those devices are present |
| `0c` | Gateway global discovery/maintenance request | Appears twice in ready-to-pair; switch later relays and responds |
| `10`, `ac` | Gateway queries with switch `fa` responses | Query codes confirmed; returned fields unresolved |
| `11` | Gateway maintenance-like traffic to existing record IDs | Meaning unresolved; cannot label it “permit join” |
| `15` | Switch property reports | Keys `24`, `25`, `26` observed with values 2, 4, 0 |
| `a0` | Switch capability-like report | `00 02 03 00 00 00 03 00`; semantics unresolved |

The `15` report format is `01 KEY VALUE 00 00 00 CC 00` in these samples. Pairing frames 59/62/68 report `24→02`, `25→04`, `26→00`; frames 197/201/205 repeat the values with the counter increasing from 1 to 2.

**The value 4 is not evidence of four exposed gangs.** It could be a capability, feature mask, mode, hardware code, or something else. The 2026-11-01 live one-gang trial below strengthens the association of suffix `0001` with one output and `0002` with two outputs, but does not establish a universal capability encoding. The `02` in a capability report remains unresolved. There are no standard Zigbee endpoints advertised here, and no state bits above bit 1 are exercised. Older four-output software compatibility remains unproven.

## What the relay evidence proves

An especially clear pair is pairing frames **137 → 138**:

- Gateway sends a broadcast, with routing slot A `67181844`, sequence `98`, body `8c 0a …`.
- About **7.47 ms** later, the switch transmits the same sequence and body to destination `67181844` and clears the route slots.
- The immediate source changes from the gateway to the switch; flags and timing trailer also change.

This is more than the sniffer capturing the same transmission twice. There is a distinct source and a meaningful destination rewrite. Across all captures, 121 relay matches occur approximately **6.69–14.96 ms** after the corresponding gateway packet. The switch forwards most ordinary examples after about 7 ms.

This supports proprietary relaying/routing over 802.15.4. With only two observed transmitters, it cannot establish the maximum hop count, loop prevention, topology discovery, delivery to the six referenced IDs, or resilience when direct gateway contact is lost.

## Reproduction and next experiments

The repository includes an offline decoder and machine-readable packet index:

```sh
python3 tools/decode_ec.py --output-dir analysis
python3 -m unittest discover -s tools -p 'test_*.py' -v
```

`decoded_packets.csv` contains every raw frame, decoded field, checksum calculation, and matched relay/ACK reference. `summary.json` records capture hashes, counts, timestamps, and opcode distributions. The parser explicitly checks the pcapng/TAP framing and rejects unrecognized EC layouts instead of silently applying these offsets to unrelated packets. It is intentionally scoped to the supplied frame formats; it is not a general Zigbee analyzer.

The most useful follow-up captures, in priority order:

1. **Precisely annotated two-gang controls:** start both off; first on/off; second on/off; both on/off. Log action timestamps and whether each action is app or physical. Capture both possible unchanged-bit states during a single-gang command to test mask semantics.
2. **Physical controls with app activity excluded:** record a quiet baseline and several physical presses, then repeat with gateway powered off. Compare `05` masks, tokens, counters, and any unsolicited `07` traffic. This separates local reporting from gateway command transactions.
3. **A complete fresh pairing:** start capture before factory reset and before enabling pairing, continue through success and several idle minutes. Repeat on the same channel; if handshake gaps recur, investigate other channels. In particular, recover the missing `0b` exchange and whether `b1` is learned earlier than opcode `01`.
4. **A real one-gang EC device:** compare suffix, `a0`, and property keys `24/25/26` with this two-gang device. This is the cleanest way to test the gang-count hypothesis.
5. **Another gateway and a controlled relay topology:** test tag derivation with a second identity, then place an additional switch where it requires forwarding. This can distinguish broadcast flooding from actual route construction and verify delivery beyond one relay.

The original capture analysis above did not inject traffic or modify devices.
The subsequent [local gateway experiment](development/local-gateway-trial.md)
implemented raw 802.15.4 firmware on the CC2652P and successfully paired and
controlled the spare switch with the stock EC unplugged. It also established
that native state reports can use a broadcast destination and flags `13`;
configured-peer ACK handling must cover that form. This validates the raw-radio
approach for direct single-switch control. Route recovery, a persistent service,
multiple devices and MQTT/Home Assistant integration remain separate work.

## MG21 state-query timeout investigation — 2026-10-24

The two failed refresh requests in the [six-request MG21 trial](development/mg21-query-retries-20261024.json)
failed before any query response was observed. Their 12 gateway queries are
present in the independent Nordic recording, with valid application checksums;
neither the daemon nor Nordic saw a corresponding `9e/fa` or `9e/05` response.
This narrows the failure to reception/acceptance of the query or transmission/
reception of its reply. It is not evidence that a longer state-response wait is
needed. The strongest current hypothesis is timing/synchronization, but the
captures do not prove the switch's receive-window rules or exclude interference
at the switch.

Reproduce the comparison with `python3 tools/analyze_query_timeouts.py`.
The [machine-readable comparison](development/mg21-query-timeout-comparison-20261024.json)
includes source hashes, original packet numbers, query bytes, response
candidates, heartbeat measurements and explicit limitations. It covers the
five original supplied captures, the subsequent EC pairing and one-gang control
captures, the earlier MG21 toggle capture and the six-request retry capture.
This investigation is offline; it makes no deployment or firmware changes.

### What happened in the failed requests

All packet references in this table refer to
`captured/development/mg21-query-retries-20261024-nrf52840.pcapng`.

| Request | Query pairs (`10`, `ac`) | What the switch sent during the request |
| --- | --- | --- |
| 4 | 124/126, 150/151, 170/175 | Announcement `07`, packet 152; no query reply or state report |
| 5 | 221/225, 261/263, 310/311 | Announcement 249 and keepalive `08` 251; no query reply or state report |

The gateway answered keepalive 251 with packet 252, approximately 1.8 ms later.
Thus the switch was still transmitting, and the MG21 receive/automatic-response
path was working during the failure interval. No RX/UART/radio-drop or ACK-failure
counters increased over the trial. That does not prove the switch heard the
queries: the sniffer and gateway are different receivers from the switch.

In contrast, each successful round produced the same recognizable sequence:

1. Query `10` → `fa/10` in **2.521–2.633 ms**.
2. Query `ac` → `fa/ac` in **196.004–201.197 ms**.
3. First native state `05` **396.035–400.863 ms** after `ac`.
4. Another copy of the state about 200 ms later, followed by property `15`
   reports. EC's original 2025 pairing capture also contains two state copies
   despite intervening gateway ACKs, so duplication alone does not prove a bad ACK.

Native state counters advance contiguously through 118, 119, 120 and 121 across
the four successful snapshots. The last successful request needs no new pairing,
reset or session restoration. The four-second response waits substantially
exceed all observed successful first-state delays in this trial.

### Differences from the original EC gateway

The original query references are frames **175/188** in
`ec_device_pairing_sequence.pcapng` and **305/308/309** in the Nordic
`gateway-pairing-20261017T182031Z-nrf52840.pcapng`. Both concern the same test
switch `46715621`, suffix `0002`. The original one-gang on/off reference contains
no `10/ac` queries and is useful for cadence comparison only.

| Detail | Original EC | Deployed MG21/host |
| --- | --- | --- |
| Query payload and routing | `0000000000000001`, direct `1e`, same subject/suffix | Exact byte match after allowing MAC sequence, gateway identity, tag and trailer differences |
| Timing trailer | Encodes age since preceding heartbeat; all five queries are within 0.187–0.857 ms of measured RF age | All 22 queries use `000000`, including queries several seconds after a heartbeat |
| Heartbeat spacing | 10.000655–10.000725 s in the 2026 pairing reference | 10.072886–10.140911 s in the retry trial |
| Query scheduling | 2026 pairing queries at heartbeat +0.600187, +0.800248 and +1.000195 s | Unaligned to heartbeat; nominal 200 ms pair spacing becomes 217–219 ms, with one 260 ms gap |
| Retry pattern | Two consecutive `ac` sends 199.947 ms apart in the 2026 pairing capture | Whole `10/ac` pair repeated after approximately 4.236 s in both failed requests |

There is **no demonstrated payload/checksum/gang-count mismatch**. The original
`10` replies (`1045197b38000000`) also match the successful local replies.
Original `ac` traffic precedes the same state/property burst we receive locally.
The two close original `ac` sends have overlapping response candidates, so this
capture cannot assign every reply uniquely or prove why EC repeated the command.
The 2025 sample separates `10` and `ac` by 2.201261 seconds; therefore a strict
200 ms inter-query requirement is not established either.

The heartbeat timing difference accumulates to **400.596 ms across four local
intervals**, while the payload counter advances by exactly 40 seconds. This is
large compared with the observed 200 ms message cadence. In `service.py`,
`_emit_heartbeat()` schedules the next deadline from completion of UART work
(including `STATUS` in phase mode); the maintenance loop checks it every 50 ms.
This explains how delay can accumulate, although the capture cannot attribute
every millisecond to a particular host/UART/CCA operation.

The zero query trailer is directly explained by the implementation:
`refresh_state()` calls `_transmit()` with `frame()`, which initializes those
bytes to zero. `PhaseClient.send()` and firmware `phase_output_allowed()` only
accept opcode `02`. Consequently, enabling `experimental_phase` improves the
output-command path but **does not phase-stamp queries**. Automatic ACKs also
retain zero trailers, whereas EC ACKs carry phase. ACK timing/trailers remain a
separate compatibility concern; they do not directly explain the absence of the
first `fa/10` reply in these failed rounds.

### Receive-window hypothesis and its limits

As an exploratory measurement, fold each `10` transmit time onto a 200 ms cycle
anchored to the nearest native `07` announcement. In this retry capture all four
answered `10` packets fall at phases **168.592–176.544 ms**; all seven unanswered
ones fall outside that interval. The first failed request's rounds land at
79.379, 115.754 and 152.131 ms; the second's at 46.599, 83.831 and 119.423 ms.
The approximately 4.236-second round period advances the sampled phase by only
about 36 ms, so three retries do not cover the whole putative cycle.

This is suggestive of limited receive opportunities, **not a validated receive
window**. In the earlier MG21 toggle capture, successful `10` packet 86 falls at
1.191 ms under the same model; its reference announcement is 6.601 seconds away.
Announcement transmit delay, resynchronization and a changing clock phase can
all invalidate that extrapolation. The observed correlation must not become a
hard-coded 169–177 ms transmit window.

The next discriminating experiment should keep queries read-only and vary one
factor at a time: first phase-stamp `10/ac` at the adapter while retaining the
current retry schedule; then compare bounded, controlled phase offsets with
stable heartbeat deadlines. Record `fa/10`, `fa/ac` and `05` separately, and
verify actual timing with Nordic because the present firmware phase timestamp
is radio-call entry, not RF SFD. A bounded retry policy can subsequently use
query replies to distinguish an unanswered first query from an accepted snapshot
request. Simply lengthening the four-second wait or adding many identical
rounds would not isolate the cause established by this comparison.

**Follow-up:** [release 0.3's controlled phase-query trial](../host/DEPLOYMENT.md#phase-stamped-state-queries-03--2026-10-24)
implemented adapter stamping while retaining the host retry and heartbeat
algorithms. Eleven of twelve requests confirmed; one still timed out despite
accurate trailers and a captured `fa/10` response. The larger UART replies also
increased actual query spacing, so the success-rate change cannot be attributed
solely to the trailer. The trial establishes that stamping works and that it
does not completely resolve query timeouts.

## UART baud experiment, 0.4 (2026-10-24)

The MG21 460800 experiment retained the exact 0.3 query and heartbeat scheduling
code. Nordic measured median query-pair spacing of 213.041 ms versus 236.987 ms
at 115200, while the eight initial query phase fields remained within 0.399 ms
of heartbeat-relative RF age. UART serialization affects the real spacing even
when the configured 200 ms delay is unchanged; phase stamping still occurs in
the MCU at transmit entry.

The fast-rate run aborted on UART session loss. With Nordic actively recording,
84/200 standalone CAPS replies lost characters, versus 0/200 with Nordic idle.
Missing characters also appear in MG21 USB bulk-IN data, before host parsing.
The precise USB/bridge failure mechanism is unresolved. Do not classify these
unknown transactions as evidence of EC query rejection or RF timeout.

Restoring application 115200 gave 0/200 malformed CAPS replies with Nordic
active. A full twelve-request run confirmed ten and exhausted retries on two,
with 48 captured queries, unchanged session and zero added transport/drop/ACK
error counters. That initial faster-UART trial did not earn deployment qualification; it ended
at 115200. See the [full comparison](development/mg21-uart-baud-comparison-20261024.json)
and [deployment record](../host/verification/deployment-uart-baud-0.4.json).


### Follow-up with Nordic on a separate Pi

After the gateway reboot, Nordic was moved to `10.1.1.120`. At 460800 the same
200-reply CAPS check passed without corruption or timeout while Nordic recorded.
The complete twelve-request query trial retained its scheduling code and stayed
in one UART session, but confirmed only five requests. Seven exhausted retries.
This separates a passing serial-transport smoke test from unresolved RF query
reliability; it does not establish the exact cause of the earlier USB byte loss.

The capture contains 61 of 62 reported query transmissions, with all captured
phase stamps within 0.430 ms of RF heartbeat age. Median complete-pair spacing
was 213.286 ms versus the earlier 237.019 ms at 115200. Faster serialization
changes the actual radio spacing despite an unchanged configured 200 ms delay.
Failed requests 1, 5 and 9 had native `fa/10` but no `fa/ac`/`05`; four other
failed requests had no captured query replies. Thus accurate phase stamps and
faster UART alone do not ensure the second query is accepted. The smaller
success count cannot be attributed solely to baud without a matched timing
experiment; radio send failures and changed test conditions also remain relevant.

One query was missed by Nordic despite its `fa/10` being captured. Cross-host
report timestamps differed by approximately 120–123 ms, so exact native counters
and TX bytes supplement bounded timestamp windows; within-capture timing avoids
this alignment limitation. See [per-request evidence](development/mg21-query-separate-sniffer460800-20261024.json)
and [current deployment](../host/DEPLOYMENT.md) for counters and limitations.


### Sonoff follow-up and UART receive-overrun correction

The original Sonoff ZBDongle-E also reproduced incoming long-command `ERR HEX`
failures. Diagnostic flags identified MCU UART FIFO overruns, with zero framing
or parity errors. Removing the long global interrupt mask from the MG21 TX
queue copy eliminated the fault in 6000 requests; a ten-minute live run then
retained one session with zero UART errors. This receive-side firmware defect
is distinct from the earlier ZB-GW04 USB bulk-IN truncation.

The unchanged query schedule confirmed only two of twelve refreshes. Nordic
captured 63/64 queries, median pair spacing 213.397 ms and phase error at most
0.404 ms. Six failed requests had `fa/10` without `fa/ac`/`05`; four had no captured
query replies. UART stability therefore does not resolve RF query acceptance.
The interrupted spacing-match experiment remains incomplete. See the
[full Sonoff report](development/sonoff-migration-20261024.md) for counter,
capture-coverage and comparison limitations.

### Sonoff matched query-pair spacing

A subsequent 460800 trial increased only the host query delay from 200 to 224 ms,
retaining three rounds, a four-second response wait and a 14-second deadline.
Nordic measured median `10` → `ac` spacing of 237.302 ms, versus 237.019 ms in
the earlier 115200 reference and 213.397 ms in the preceding Sonoff run.
Confirmations increased from 2/12 to 11/12 on the same Sonoff firmware. All 54
queries were captured, phase error stayed within 0.402 ms, and the UART session
and error counters remained clean.

One request still exhausted retries with `fa/10` but no `fa/ac` or state report.
This supports spacing as a contributor, without establishing a complete fix or
exact acceptance threshold. Median round-start spacing remained approximately
23 ms shorter than the 115200 reference because the response wait was unchanged;
the entire timing sequence was not reproduced. The comparison is sequential,
and the earlier 115200 reference used different hardware. The temporary override
was removed after the experiment. See the [full timing comparison](development/sonoff-query-matched-spacing-20261024.md).

### Permanent pair correction and matched round investigation

The host now selects a 224 ms query-pair delay at 460800, retaining 200 ms at
other baud rates. Two interleaved 24-request trials then compared 4.000 s and
4.023 s response waits without changing the three-round or 14-second limits.
Nordic measured matched round-start medians of 4274.143 and 4274.217 ms, versus
the earlier 115200 reference of 4273.905 ms. Pair spacing stayed near 237.4 ms.

The extra wait confirmed 10/12 versus 6/12 initially, but 9/12 versus 10/12 when
the order was reversed. Combined 19/24 versus 16/24 is not a consistent effect:
first-round confirmations also differed, 9/24 versus 4/24, before the changed
round wait could act. The permanent response wait therefore remains 4.000 s.

Thirteen refreshes exhausted retries; ten had `fa/10` without `fa/ac`/state, and
three had neither query reply. All failed-request queries were captured, with
valid EC checksums and accurate phase trailers. No late state report appeared
before the next query, at least seven seconds after the final attempt. UART
sessions remained stable. Receive opportunities, announcement-relative timing,
heartbeat cadence and packet loss remain hypotheses; the captures do not reveal
the switch's rejection reason or establish a fixed receive window. See the
[full comparison](development/sonoff-query-round-spacing-20261024.md) for coverage,
counter deltas and limitations.


### TI cross-check and execution boundary — 2026-10-24

The shared 0.4 query implementation was built for the actual CC2652P and tested
at 460800 using the same host policy and independent Nordic sniffer. All 6000
UART integrity requests passed. Queries confirmed **18/24**, with **9/12** for
both the 4.000 s and 4.023 s response waits. All 36 queries in the six timeouts
were captured: four requests received `fa/10` without `fa/ac`/state and two
received no query reply. No late state followed before the next request.

Typical RF pair spacing was 235.540 ms, but four pairs exceeded 250 ms and the
largest was 571.144 ms. The extra-wait round median was 4270.200 ms, still 3.705 ms
shorter than the older 115200 reference: using the same host delays does not
match radio timing exactly across targets. Phase error stayed below 0.796 ms;
heartbeat cadence was about 10.037 s and accumulated 1.004 s of excess over 23
intervals. One session was retained, with no new reported RX/UART drops or input
errors; separate TI hardware UART RX counters are unavailable.

These results support moving bounded timing execution onto the MCU, but do not
prove an exact switch receive window or eliminate RF/protocol uncertainty. Keep
authoritative persistent state on the Pi; give the MCU volatile job queues,
absolute heartbeat scheduling, fresh-report cancellation and explicit timing
results. No scheduler or retry-policy change was deployed in this comparison.
See the [full TI evidence](development/ti-query-comparison-20261024.md) and
[proposed architecture](development/radio-transaction-scheduler.md).

### TI MCU scheduler: cadence verified, query failures remain — 2026-10-24

Release 0.6 adds eight volatile deadline jobs and a separate MCU-owned heartbeat
epoch, with retained results and session/lease cancellation. The Pi retains all
authoritative state. Nordic captured all seven queued diagnostic probes during
twelve seconds of host silence, with ordinary 200 ms intervals measured at
199.991–200.083 ms. Cancelled and lease-expired probes were absent. A further
768 UART requests did not disrupt the heartbeat cadence.

During 24 state refreshes, 25 heartbeat intervals measured **10.000772–10.000854 s**
(median 10.000805 s), versus **10.017983–10.144293 s** on TI 0.4. No heartbeat
slots were missed. These are Nordic-relative measurements, not calibrated clock
accuracy or RF-core SFD deadline guarantees. TI ClockP schedules process-context
dispatch through the existing driver.

Queries still use the host loop, with unchanged retry limits. **17/24 confirmed**,
versus 18/24 before. All 42 queries from seven failures were captured; each failed
request received `fa/10` but no `fa/ac` or state. The phase error remained below
0.797 ms. Three long pair intervals (352.704–703.000 ms) occurred in successful
requests, so neither heartbeat drift nor pair outliers alone explains failure.
The extra 23 ms response wait gave 10/12 versus 7/12 in this small sample, without
establishing a stable benefit across the previous trials. Normal 4.000 s wait,
three rounds and 14-second deadline remain deployed.

The first stage therefore verifies tighter heartbeat cadence and host-independent
queued execution, while leaving the proprietary response behavior unresolved.
See the [full comparison and limits](development/ti-scheduler-comparison-20261024.md).

### Complete MCU query transactions — 2026-10-24

Release 0.7 moves both query opcodes, CCA backoff, round timing and fresh-report
stopping onto TI. It retains three rounds, four CCA attempts per opcode and the
14-second deadline. The MCU stores one volatile transaction and its confirming
native frame; the Pi independently validates/persists that frame and retains all
authoritative network state. Freshness is a matching valid report callback after
acceptance, not a requirement for an advancing report counter or proof of a
nonce-correlated response.

Using explicit MCU targets matched to earlier TI wire timing, Nordic measured
**235.602–235.686 ms** between 10/ac, versus the prior 234.963–703.000 ms range.
Normal round-start median was **4248.093 ms**, within 0.479 ms of the preceding
run. No pair exceeded 250 ms. All **21 retained confirmations** matched native
reports, and no captured query followed a confirming report.

Refreshes confirmed **21/24**, versus 17/24 on 0.6. The normal/extra-wait variants
gave 11/12 and 10/12 respectively; the normal profile remains deployed. Nordic
captured 93/94 query frames, including all 18 from three timeouts. The missed
final ac from successful request 22 had a captured `fa/ac` and state response.
The three timeouts again received `fa/10` without `fa/ac` or state, with no late
state before the next request after at least 7.107 seconds. Phase error remained
below 0.817 ms. One heartbeat CCA retry delayed a slot by about 25 ms and the
next returned to the fixed epoch; no heartbeat slot was missed.

This verifies removal of the measured host timing outliers and MCU-native early
stopping. The small sequential sample does not establish a complete reliability
fix or the proprietary receive-window rule. See the
[0.7 comparison and evidence](development/ti-query-mcu-comparison-20261024.md).

### Sonoff 0.7 MCU query comparison with EC running — 2026-10-24

The Sonoff now runs the same bounded transaction core as TI, using its native
1 MHz clock. The host clock support is deployed. The original EC gateway and
six original-network switches remained audible on channel 11: 116 decoded
`b1` frames, including 19 gateway heartbeats, during the complete query window.
These are coexistence results, not isolated-network results.

| Measurement | TI 0.7 | Sonoff 0.7 completed run |
| --- | --- | --- |
| Confirmed refreshes | 21/24 | 21/24 |
| Normal / extra-wait confirmations | 11/12 / 10/12 | 11/12 / 10/12 |
| Captured / reported query sends | 93/94 | 92/92 |
| Median 10 → ac spacing | 235.653 ms | 235.541 ms |
| Median normal round spacing | 4248.093 ms | 4247.734 ms |
| Median extra-wait round spacing | 4271.111 ms | 4270.735 ms |
| Maximum MCU dispatch lateness | 0.519 ms | 0.103 ms |
| Maximum absolute phase-field error | 0.817 ms | 0.415 ms |

Sonoff's ordinary pairs span 235.522–235.569 ms. Request 23 has one 261.031 ms
pair and subsequently confirms; the UART trace records its first ac attempt
returning CCA busy, followed by the unchanged 25 ms backoff. Request 5 also has
one CCA retry, on its first 10. Across the complete trial there were 94 attempts,
92 successful sends and two CCA retries. The three-round, four-attempt-per-opcode
and 14-second bounds were unchanged. The extra-wait round remains approximately
3.170 ms shorter than the historical 115200 reference; this run did not retune it.

Requests 3, 5 and 6 timed out after all six queries were captured. Each received
`fa/10`, but no `fa/ac` or native state; no late report appeared before the next
query, at least 7.111 seconds after the final send. All 21 retained confirmations
matched Nordic (counters 129–149, OFF/OFF), with no query after confirmation.
Both separate autonomous checks confirmed: two sends during host silence, four
sends during 920 CAPS requests, with one bounded query CCA retry in the latter.

Heartbeat median was 10.000394 seconds. Four reported heartbeat CCA retries and
zero missed slots were observed across the complete trial's diagnostic snapshots;
the measured query-window interval range was 9.974916–10.025867 seconds. No new
UART, RX-drop, radio-drop or ACK failure counters appeared in this completed run.
The unsupported-layout count does not identify collisions or interference.

An initial attempt is retained separately: one confirmed request, then one
`unknown` request after `ERR SYNTAX` ended the host session. The subsequent
adapter snapshot records **one UART RX overrun**. Nordic captured four queries
for that request and a subsequent state report (counter 128), so this is not a
demonstrated RF timeout. The complete repeat used actual UART tracing and had no
error replies, but it does not establish that the initial UART problem is fixed.
The exact rejected command was not traced in the first run.

The initial activation also needed correction of configuration group ownership;
that deployment error is distinct from the later UART failure. Final verification
finds Sonoff active, TI disarmed, MQTT connected, unchanged registry/broker records
and the normal timing profile restored. No output command was sent. The tests are
small, sequential and use different radio hardware; equal counts do not establish
equal long-term reliability or rule out interference.

Evidence: [full comparison](development/sonoff-query-mcu-comparison-20261024.json),
[retained transactions](development/sonoff-query-mcu-details-20261024.json),
[initial interruption](development/sonoff-query-mcu-initial-interruption-20261024.json),
[deployment](../host/verification/deployment-sonoff-query-mcu-20261024.json) and
[Nordic capture](../captured/development/sonoff-query-mcu-comparison-20261024-nrf52840.pcapng).

### Sonoff 0.7 comparison with original EC off — 2026-10-25

The user shut down the original EC network. The same 24-request ABBA test was
repeated on Sonoff at 460800 with unchanged firmware, host source, UART tracing,
235.5 ms pair target, 4.012/4.035 s response targets, three rounds, four CCA
attempts per opcode and a 14-second deadline. Nordic remained on the separate Pi.
No output command or firmware update was performed.

| Measurement | EC on, October 24 | EC off, October 25 |
| --- | --- | --- |
| Confirmed refreshes | 21/24 | 19/24 |
| Normal / extra-wait confirmations | 11/12 / 10/12 | 10/12 / 9/12 |
| Original-network frames in query window | 116 | 0 |
| Original gateway heartbeats | 19 | 0 |
| Captured / reported query sends | 92/92 | 101/102 |
| Query CCA retries | 2 | 5 |
| Median 10 → ac spacing | 235.541 ms | 235.541 ms |
| Median normal round spacing | 4247.734 ms | 4247.733 ms |
| Median extra-wait round spacing | 4270.735 ms | 4270.739 ms |
| Maximum MCU dispatch lateness | 0.103 ms | 0.814 ms |
| New UART overruns / radio drops during trial | 0 / 0 | 0 / 0 |

Requests 6, 11, 18, 20 and 21 timed out. All 30 query sends from these requests
were captured; each received `fa/10` without `fa/ac` or native state. There was
no late state before the next query, at least 7.112 seconds after the last send.
All 19 retained confirmations matched Nordic (counters 154–172, OFF/OFF), and
no captured query followed its confirming report. The one missing query is
request 22's second-round `10`; its `ac`, `fa/ac` and confirming state were
captured. The MCU reports four successful sends for that request; Nordic alone
cannot verify the missing transmission or its reception by the switch.

Five bounded query CCA retries occurred across requests 1, 6, 21 and 22. Three
pair gaps were approximately 261 ms, consistent with the 25 ms CCA backoff;
request 21 also had a 236.364 ms pair and 0.814 ms maximum dispatch lateness.
The remaining failed requests had no query CCA retry. Maximum absolute phase
error was 0.424 ms. Heartbeat median was 10.000398 seconds, with two reported
heartbeat CCA retries and no missed slots. The radio session remained unchanged
through the full test, and its UART trace contained no error replies.

No original gateway or `b1` switch frame appeared in the query window. Other
traffic remained audible: 1398 unsupported-layout frames out of 1749 captured
frames. These include other protocols/short MAC ACKs and do not measure
collisions. Removing EC therefore did **not eliminate** the timeout pattern;
original-network coexistence is not required for the failure. These small,
sequential samples do not prove that EC-off is worse, identify the proprietary
receive rule, or exclude other interference. The extra wait still has no
demonstrated advantage, and the normal profile remains deployed.

A separate transport concern persisted between trials: cumulative UART RX
overruns rose from 1 to 8 before today's test. The journal records four
`ERR SYNTAX` and two `ERR SESSION` reconnects overnight, each followed by radio
recovery. There is no overnight UART trace to map those events to particular
lost bytes. None occurred during this completed EC-off test; they must not be
counted as its five query timeouts or treated as fixed by a clean short run.

Final verification found Sonoff active, TI disarmed, MQTT connected, switch
online OFF/OFF, unchanged configuration/registry/broker records and no timing
override. Evidence: [comparison](development/sonoff-ec-off-comparison-20261025.json),
[retained transactions](development/sonoff-ec-off-details-20261025.json),
[between-trial transport events](development/sonoff-between-trials-transport-20261025.json),
[restored deployment](../host/verification/deployment-sonoff-ec-off-20261025.json),
and [Nordic capture](../captured/development/sonoff-ec-off-comparison-20261025-nrf52840.pcapng).

### Five-minute original EC app session — 2026-10-25

Nordic recorded channel 11 for 300 seconds while the user operated all switches
through the EC app. No commands were injected and no radio/daemon settings were
changed. The existing local Sonoff network remained active. The capture contains
2239 frames, 398 recognized EC frames with valid checksums, and 331 frames on
the original `b1` network. Six original switches were controlled.

The user reports output 1 on **67181844 seemed stuck ON**, recovering after
output 2 was toggled ON/OFF. It was not established whether the physical light
or the app display was stuck. That observation must not be erased by a count of
matching radio reports.

| Captured observation | Result |
| --- | --- |
| Direct gateway output-command frames | 76 |
| Distinct command groups, preserving repeated body/token | 48 |
| Groups with repeated transmissions | 23; up to 5 copies |
| Repetition interval | median 200.038 ms; range 199.911–400.108 ms |
| Same-device command bursts, separated by more than 1 s | 34 |
| Bursts with final requested state reported and EC acknowledgement captured | 34/34 |
| Bursts reaching final state with only an earlier token in reports | 7 |
| Final-command first-copy to matching state | median 213.625 ms; maximum 617.411 ms |
| Original gateway heartbeats | 30; interval median 10.000686 s |
| Original `10` / `ac` queries | **0 / 0** |

A burst can contain several app actions; 34/34 is **not an app-action success
rate**. Of the 48 command groups, 32 have an exact-token state match, seven have
a requested-state match with an earlier token, and nine are superseded by the
next command before their response window completes. Six of those nine also
have later exact-token reports. None is classified as an observed five-second
unanswered final request. Grouping at 1/2/5 seconds gives the same 48 groups;
burst separation at 0.5/1 second gives 34 converged bursts, and at 2 seconds gives
33. These checks reduce sensitivity to an arbitrary grouping threshold but do
not reveal cloud requests that never reached the radio.

For **67181844**, all 24 captured native state reports show protocol output 1
OFF. The gateway sends 19 captured commands selecting that output OFF and none
selecting it ON; EC acknowledges multiple OFF reports. During the later output-2
sequence, frames 1953/1954 request `mask=02, value=02`, report 1955 gives state
`02`; frame 1975 requests output 1 OFF and report 1977 still gives `02`; frame
1996 requests output 2 OFF and report 2001 gives `00`. Thus the stuck-ON symptom
does not correspond to missing OFF requests or captured reports claiming ON.
The trace cannot distinguish stale app/cloud/gateway state handling from a
physical output inconsistent with the switch's report.

The same device provides concrete **relay recovery evidence**. A both-OFF body
with token `8d` is sent five times (1684, 1690, 1694, 1697, 1699). Native reports
1695/1696 already show both OFF but retain token `59`; no matching gateway ACK
is captured for those direct reports. Then:

| Frame | Path | Evidence |
| --- | --- | --- |
| 1700 | 67181844 → 75710834 | State report, counter 12, token `59`, sequence 190 |
| 1701 | 75710834 → EC | Same application body/sequence, original device in route field A; 2.399 ms later |
| 1702 | EC → 75710834 | ACK for that report, preserving original-device route field; 2.081 ms later |

The relay-to-origin ACK is not captured. This verifies one forwarding path and
gateway acknowledgement, not a complete routing algorithm or the cause of the
missing direct acknowledgements. The observation supports recovery behavior,
not a conclusion that every retry is a failed user action.

There are two concrete differences from our implementation. The host currently
looks up state reports by immediate source and requires that source's device ID
to equal the subject; MCU ACK generation and query matching also require a
direct peer source. The observed forwarded report would fail these checks.
Separately, host set-command completion requires the exact pending token, while
EC acknowledges reports that retain an earlier token. Supporting
these cases will require explicit routing and transaction-correlation rules,
not merely weakening validation. No implementation changes were made here.

Most original command phase fields (70/76) lie within 0.1 ms of a 200 ms grid;
six are off-grid by up to 12.992 ms. This is useful timing evidence, but there
are **no original state queries in this capture**, so it cannot establish the
original `10/ac` failure rate or explain our single-device query timeouts.
The session documents user-observed failure, retries, state/token quirks and
relay recovery; it does not establish inherent network instability or complete
end-to-end reliability.

Evidence: [analysis and packet-indexed failure timeline](development/original-ec-live-20261025.json)
and [five-minute Nordic capture](../captured/development/original-ec-live-20261025-nrf52840.pcapng).
Original pcap and capture metadata remain on 10.1.1.120 under
`/home/sfires/livolo-sniffer/original-ec-live-20261025`.

### Gateway and switch outage recovery — 2026-10-25

Two passive channel-11 Nordic recordings cover the original `b1` network's
recovery from power loss. The local Sonoff `a7` network remained running; the
analyst injected no commands and changed no firmware or configuration. Event
annotations record user-message receipt times, not exact power-switching times.
All six original switches retained their identities and returned without manual
pairing. The user confirmed both previously disconnected indicators cleared
following EC restart, and all switches looked connected following their restart.

| Experiment | Captured interval | Recovery evidence |
| --- | --- | --- |
| EC off, switches powered | 399.874 s between EC heartbeats, frames 370–7462 | EC heartbeat counter resets from 1150 to 0; state reports from all six peers follow |
| Switches off, EC powered | 115.538 s between original-peer frames 304–1124 | All six peers return within 0.937 s of the first; all send native state within 10.465 s |

These intervals bracket observed radio silence, not exact power-off durations.
The second recording's first returning peer is 100.443 s after the user's OFF
confirmation, satisfying the requested wait of at least one minute. Neither
recording contains a decoded original-network `01`/`ff` pairing exchange or an
`8c/02` output command.

**EC absent: peer discovery and repeated forwarding.** Between EC heartbeats,
4649 original-network frames were captured: 2567 `9e/07`, 1198 `9e/08`, 771
peer-origin `8c/08` responses and 113 `9e/05` reports. Switches 81422527 and
67181844 visibly entered disconnected mode despite peer traffic. Thus answers
from other switches alone do not establish gateway availability.

The user interacted with 81422527 during the outage. Its report body
`010002010000119d` (state `01`, counter 17, token `9d`) appears 113 times in
15.397 s: 50 native copies and 63 copies from the other five switches. These are
copies of one report body, not 113 independent state changes, and the event
must not be described as spontaneous switching caused by the outage. This
extends the earlier concrete relay observation to multi-peer recovery traffic.

**EC restart: captured queries use `ac` alone.** Starting 17.197 s after the
first resumed heartbeat, EC sends 30 direct `ac` requests to the six peers
(frames 7846–8119), with the usual `0000000000000001` body. There are **zero
captured `10` requests in the entire recording**. Counts by peer are:
81422527: 1, 67181844: 2, 56830549: 2, 75710834: 2, 47778828: 10, 72715996: 13.
Queries use the approximately 200 ms phase grid; repeated requests to the same
peer range from 200 ms to 4 s, with many later repetitions 600 ms apart.

For a descriptive response window ending at the next query to the same peer or
four seconds, whichever comes first, five requests have a captured `fa/ac`,
18 have a state report, and 12 have neither. Native and forwarded responses are
included. There is no query nonce proving causality, and these windows are not
known EC deadlines: **the 12 unmatched requests are not 12 demonstrated logical
refresh timeouts**. All six peers have candidate state responses and gateway
state acknowledgements during the sequence. Some repeated queries follow
already captured state reports and acknowledgements.

This is a concrete difference from our current `10` → `ac` query transaction.
A controlled `ac`-only trial on the local network is now justified, with retry
limits and timing held constant and success still requiring a fresh state
report. It has not been implemented or run in these passive experiments. A
single sniffer's absence of `10` does not establish that EC never uses it in
other circumstances.

**Switch restart: a different maintenance path.** During the 115.538 s gap
between original-peer frames, EC sends 11 heartbeats, 66 `8c/11` messages and
19 `8c/0a` messages. The first `11` is frame 820, 84.784 s after the last peer
frame; the first `0a` is frame 1071, 109.986 s after it. This is evidence of
maintenance attempts during peer silence, not a timestamp for an internal
EC offline decision. The user did not use the app, so its offline detection
remains unobserved.

After power restoration, `0a`, capability/property and state exchanges resume
on the existing `b1` tag. There are no captured `10` or `ac` requests anywhere
in this second recording. Fifteen native state reports cover all six devices;
all report state bits zero and counters 0/1. That includes 81422527, whose
pre-power-cycle report had bit 0 set. This is a reported startup state, not an
independently verified physical-output observation. No forwarded state reports
were captured during this recovery.

EC's heartbeat counter does not reset in the second experiment. One 20.001 s
gap occurs later between frames 2217 and 2416 (counters 470 and 490); a single
sniffer cannot distinguish a missed reception from an omitted or delayed
transmission. The heartbeats through the actual peer-silent interval continue
at approximately ten-second spacing.

The recordings establish automatic recovery, substantial peer forwarding, and
at least two distinct maintenance sequences. They do not establish an inherent
EC failure rate or resolve our query timeouts by themselves. The authoritative
host state and current firmware/query retry policy remain unchanged.

Evidence:
[gateway-outage analysis with query-by-query matching](development/ec-edge-gateway-off-20261025.json),
[gateway-outage Nordic capture](../captured/development/ec-edge-gateway-off-20261025-nrf52840.pcapng),
[switch-outage analysis with per-peer recovery](development/ec-edge-switches-off-20261025.json),
and [switch-outage Nordic capture](../captured/development/ec-edge-switches-off-20261025-nrf52840.pcapng).
The recordings contain 9188/2562 total frames and 5582/514 recognized EC frames,
respectively; every recognized EC checksum is valid. Unrecognized layouts were
retained in the raw captures and excluded from protocol counts. Original pcaps
and capture metadata remain on 10.1.1.120 under
`/home/sfires/livolo-sniffer/ec-edge-gateway-off-20261025` and
`/home/sfires/livolo-sniffer/ec-edge-switches-off-20261025`.

### Sonoff AC-only versus paired queries — 2026-10-25

Release **0.8** adds an explicit, capability-gated AC-only mode to the existing
MCU transaction. The ordinary daemon still defaults to `10` → `ac`. Nordic on
10.1.1.120 recorded 48 read-only requests to local switch **46715621**, interleaved
in paired/AC/AC/paired blocks, 24 per variant. Both variants used the same Sonoff
image, daemon and radio session at 460800 baud. The original EC network remained
active: 252 `b1` frames from EC and all six peers were captured, with no original
`10/ac` queries in this comparison window. No output commands were injected.

The unused preparation slot remains reserved for AC-only. MCU timer targets
are unchanged: 235.500 ms for the preparation-to-AC slot, 4.012 s after AC before
the next round, three rounds, four CCA attempts per transmitted frame with
25/50/75 ms backoff, and a 14 s deadline. Omitting `10` does not allocate its
retry budget to extra ACs. It also cannot incur that omitted frame's CCA delay.

| Measurement | Paired `10` → `ac` | AC-only |
| --- | --- | --- |
| Confirmed / requested | **17/24** | **20/24** |
| Timed out | 7 | 4 |
| Confirmed in first round | 4 | 3 |
| Query frames sent and independently captured | 102 | 54 |
| CCA retries | 7 | 1 |
| Median AC-to-AC interval | 4247.747 ms | 4247.716 ms |
| AC-to-AC interval range | 4247.722–4273.205 ms | 4247.694–4273.164 ms |
| Median request duration, including failures | 4.977 s | 4.977 s |

The 0.031 ms difference between median AC intervals confirms that this did not
repeat the earlier baud-related spacing change. Baseline `10`→`ac` spacing has
median 235.542 ms (235.511–261.013 ms); CCA backoff explains longer intervals.
All 156 successful query transmissions were independently captured with valid
checksums. Their encoded phase differs from Nordic's heartbeat-relative timing
by −0.416 to −0.008 ms. Every confirmed request has a captured `fa/ac` and native
state report, and no later query transmission after the fresh report.

Failed paired requests are 1, 17, 20, 33, 37, 40 and 48. Five have `fa/10` but no
`fa/ac` or state; 37 and 48 have neither reply. Failed AC-only requests are 3,
34, 38 and 39; none has `fa/ac` or state. Thus none of the eleven timeouts is a
captured state report lost only in the host's confirmation path. The single
sniffer cannot prove what every receiver heard, and absence of a reply does
not identify why the switch did not respond.

The MCU session stayed unchanged. RX, UART-output and radio drops, framing,
parity and command-input errors did not increase. **One UART RX overrun did
occur**: its counter changed between status samples at Unix times
1792925707.860812 and 1792925710.320972, overlapping successful paired request
41. All expected query transmissions are accounted for. This observation does
not explain the RF-silent failed requests, but the separate transport defect
remains unresolved; this was not a clean UART-integrity run.

Compared with the earlier Sonoff 0.7 EC-on test (21/24 across two response-wait
settings), today's paired result is lower. The contemporaneous interleaved
comparison is more useful than attributing this sequential difference to the
new mode. AC-only used 48 fewer transmissions overall but was not faster at the
median and still failed four transactions. The three-success advantage is a
small single-device sample, not established reliability improvement. Interleaving
also cannot exclude a longer-lived preparation effect from a preceding paired
request on a later AC-only request.

The original EC restart capture contains 30 `ac` requests and no captured `10`,
but it involved six different peers, a recovery sequence, forwarding, and a
different higher-level repetition schedule. Its individual unmatched requests
must not be treated as logical transaction timeouts or compared directly to
our 17/24 and 20/24 completion counts. A remaining concrete difference is the
**200 ms phase grid**: original query fields have median distance zero and
maximum 0.992 ms from that grid, whereas today's AC fields have median distance
30.168 ms (paired) and 32.023 ms (AC-only). A later controlled experiment can
isolate send-slot alignment relative to the heartbeat epoch; simply rounding
the phase field would not establish correct transmission timing. No such
alignment change was made here.

The normal paired-query daemon was restored after the test. Sonoff firmware and
host report **0.8**, MQTT is connected, MCU heartbeats and phase reference are
active, registry and broker records are preserved, and there is no runtime
override or timing/configuration change. The bootloader remains at 115200.
The shared scheduler tests (54), UART tests (38), focused host scheduler tests
(9), and full host suite on the Pi (112) pass. The initial isolated test directory
lacked its capture fixture; the full suite passed once that existing fixture
was supplied. The initial host package install lacked a build backend; the
isolated-build retry succeeded before radio trials began.

Evidence: [per-request analysis and decoded packets](development/sonoff-ac-only-comparison-20261025.json),
[Nordic capture](../captured/development/sonoff-ac-only-comparison-20261025-nrf52840.pcapng),
and [restored deployment verification](../host/verification/deployment-sonoff-ac-only-20261025.json).
Raw capture and metadata remain under
`/home/sfires/livolo-sniffer/sonoff-ac-only-20261025` on 10.1.1.120; build, UART,
trial, restoration and test logs remain under
`/home/sfires/livolo-ec/sonoff-ac-only-20261025` on 10.1.1.21.

### Sonoff AC-only queries on the 200 ms grid — 2026-10-25

Release **0.9** implements explicit `QUERY_GRID` transactions. An eligible AC
attempt waits for the next 200 ms boundary relative to the last successful
heartbeat's radio-entry timestamp. The radio path still stamps its actual
entry time; it does not round the encoded phase to simulate alignment. A new
heartbeat updates the reference, a slot missed by over 1 ms is skipped, and
phase-reference loss terminates the transaction without an off-grid fallback.
The feature is opt-in; ordinary paired and unaligned AC-only queries are unchanged.

Both comparison modes retain three rounds, four CCA attempts per transmitted
frame, 25/50/75 ms CCA backoff minima, the 235.5 ms preparation eligibility slot,
4.012 s response wait, and the 14 s deadline. Grid deferral also applies to CCA
retries. It necessarily increases actual spacing; no extra retry or deadline
extension compensates for that wait. Persistent authority remains on the Pi.

The initial run confirmed two unaligned and two aligned requests before an
`ERR SESSION` interrupted unaligned request 5. The host trace contains the
correct session ID; the UART RX-overrun counter increased from 0 to 1 across
the interruption. Nordic captured an AC acknowledgement and repeated state
reports for that request. It is a transport-interrupted unknown result, not an
RF timeout. That entire recording is preserved separately.

The unchanged repeat used the planned unaligned/aligned/aligned/unaligned order.
It stopped at aligned request 46 with `ERR SYNTAX`, a further UART RX-overrun
increase from 1 to 2, and a host session teardown. Request 46 remains unknown;
no response was captured during its observation window. After automatic
recovery, only the two already-planned requests 47 and 48 were run, in a separate
recording/session. No failed query was rerun or replaced. The table therefore
covers all 48 planned requests, **not one uninterrupted session**:

| Measurement | Unaligned AC-only | Grid-aligned AC-only |
| --- | --- | --- |
| Requested | 24 | 24 |
| Confirmed | **18** | **14** |
| Exhausted retries without fresh state | 6 | 9 |
| Unknown after transport interruption | 0 | 1 |
| Confirmed in first round | 8 | 12 |
| Captured AC transmissions | 48 | 46, including two from interrupted request 46 |
| Median AC-to-AC spacing, complete-capture completed requests | 4247.716 ms | 4400.174 ms |
| Interval range for those requests | 4247.695–4273.196 ms | 4400.145–4600.211 ms |
| Median encoded phase distance from the 200 ms grid | 36.519 ms | 0.031 ms |
| Maximum encoded phase distance from the grid | 96.824 ms | 0.198 ms |
| Observed RF offset from nearest grid boundary | −96.678 to +67.354 ms | **+0.084 to +0.579 ms** |

Alignment was demonstrably achieved on air. For comparison, the original EC's
30 recovery queries had median encoded grid distance zero (maximum 0.992 ms)
and observed RF offsets of +0.243 to +2.312 ms. Its RF age minus its encoded
phase was +0.243 to +1.320 ms; these are different measurements because one
original field was itself off-grid. Matching the grid does not reproduce EC's
multi-peer recovery, forwarding or higher-level repetition schedule.

Every completed successful query has a captured AC acknowledgement and fresh
native state report, followed by no further query transmission. All 15 logical
timeouts used three successful MCU-reported AC sends, with no captured AC reply
or state report. No query recorded a missed grid slot. Failures cluster across
both modes, including requests 10–15 and 26–29; this is not evidence that phase
alignment alone determines response availability.

There is one independent-observation gap: unaligned request 12 reports three
successful sends, but Nordic captured only its first and last, separated by
8.521 s. That gap spans a missing middle observation and is excluded from the
complete-capture interval statistics above. One receiver cannot distinguish
sniffer loss from a radio/firmware false-success. The other completed requests'
reported transmission counts match Nordic. Two additional frames belong to
unknown request 46; its retained MCU status is a last pending snapshot, not a
terminal result.

Across the main comparison and its restoration there was one UART RX overrun,
with no increments in RX drops, UART-output drops, radio drops, framing, parity
or command-input errors. The two-request completion had no increments in those
counters. Together with the initial interrupted run, this task observed two
UART overruns associated with command/session failures. The grid change does
not fix that separate transport problem, and neither interrupted request is
classified as a logical radio timeout.

The earlier unaligned AC-only trial confirmed 20/24; the new contemporaneous
unaligned control confirmed 18/24. Aligned requests more often succeeded in the
first round, but confirmed fewer overall in this sample. Even treating unknown
request 46 as potentially successful would not reverse the observed count
ordering. This small single-device experiment does **not establish a general
causal reliability penalty**, but supplies no reason to enable alignment by
default. Alignment also increases round spacing, so this comparison cannot
separate phase placement from the extra waiting. Both networks remained active.

After completion, host and Sonoff report **0.9**, the daemon is active/enabled,
MQTT is connected, MCU heartbeat and phase reference are active, and normal
paired-query mode is restored with no runtime override. Registry and broker
records match the original pre-0.9-deployment backup; configuration is unchanged.
The application remains at 460800 and the untouched bootloader at 115200. Checks
passed: 60 scheduler tests, 38 UART/parser tests, 10 focused host scheduler tests,
and the full 113-test host suite on the Pi.

Evidence: [comparison with every request and all limitations](development/sonoff-ac-grid-comparison-20261025.json),
[initial interrupted run](development/sonoff-ac-grid-interrupted-20261025.json),
[main Nordic capture](../captured/development/sonoff-ac-grid-main-20261025-nrf52840.pcapng),
[completion capture](../captured/development/sonoff-ac-grid-completion-20261025-nrf52840.pcapng),
[initial interrupted capture](../captured/development/sonoff-ac-grid-interrupted-20261025-nrf52840.pcapng),
and [restored deployment](../host/verification/deployment-sonoff-ac-grid-20261025.json).
Build and initial logs remain on 10.1.1.21 in
`/home/sfires/livolo-ec/sonoff-ac-grid-20261025`; main and completion logs use
`sonoff-ac-grid-repeat-20261025` and `sonoff-ac-grid-tail-20261025`. The same three
names under `/home/sfires/livolo-sniffer` on 10.1.1.120 retain the original pcaps
and capture metadata. No source retries, output control or additional trial
was performed to replace an unfavorable result.


## Sonoff UART DMA receive fix, release 0.10 — 2026-10-25

The 0.9 grid experiments exposed MCU UART receive overruns, not just query
response failures. For example, the Pi transmitted the complete
`265537115 QUERY_GET d87b4ba3d1c05285 46` request at 1792926850.089414;
the MCU returned `ERR SYNTAX` about 5.3 ms later. A previous interruption returned
`ERR SESSION`. Each run increased the hardware RX-overrun counter; framing and
parity counters stayed zero. Before this deployment, 0.9 still reported two RX
overruns. The exact radio ISR or critical section responsible was not isolated.
The receive-service deadline was the architectural weakness: software had to
empty the small USART FIFO while sharing the CPU with RAIL.

Release 0.10 replaces per-byte RX interrupts with four continuously linked
2048-byte LDMA blocks. The 8 KiB ring receives independently of CPU interrupt
service. A bounded main-loop consumer stages at most 256 bytes, then rechecks
producer progress and hardware errors before feeding the existing parser. It
detects overtaking and conservatively rejects data when elapsed time could hide
a complete lap, including preemption while copying. On loss, it discards the
partial command through newline. STATUS adds DMA-error, resynchronization,
high-water and maximum polling-gap counters. TX remains interrupt-driven.

The application remains at **460800**, Gecko bootloader at **115200**. The
bootloader was preserved. Firmware and daemon report **0.10**. Authoritative
network records remain on the Pi. The five host/scheduler/phase source hashes
are identical to their pre-change values: 235500 µs pair/preparation eligibility,
4012000 µs response wait, three rounds, four CCA attempts per frame with
25/50/75 ms backoff, 14-second deadline and 50 ms host polling delay. The optional
200 ms alignment mode and default paired mode are unchanged. The larger status
buffer is MG21-only; rebuilding after scoping that declaration produced the
identical Sonoff application hash.

Verification used the original EC network running throughout and an independent
Nordic on 10.1.1.120. Its 17 minute 44.5 second recording contains 8384 packets,
including 573 original-network frames. [Capture](../captured/development/sonoff-uart-dma-20261025-nrf52840.pcapng)
and [complete evidence](development/sonoff-uart-dma-20261025.json) retain all
failures and test interruptions.

| Same 48-query comparison | 0.9 | 0.10 |
| --- | --- | --- |
| Unaligned AC-only | 18 confirmed / 6 timed out | 19 confirmed / 5 timed out |
| Aligned AC-only | 14 confirmed / 9 timed out / 1 UART-unknown | 22 confirmed / 2 timed out |
| Unaligned median AC-to-AC | 4247.716 ms | 4247.725 ms |
| Aligned median AC-to-AC | 4400.174 ms | 4400.173 ms |
| Aligned on-air grid error | +0.084 to +0.579 ms | +0.077 to +0.439 ms |

All 75 query transmissions in the new 48-query comparison were captured. Its
3593 UART requests had no repeated IDs, missing replies, error replies or loss
counter increments. Successful queries stopped after fresh reports. The seven
RF timeouts remain failures; the better success count is not proof that DMA
improves RF reliability, because these runs occurred at different times.

A separate completed stress run issued **6000 additional requests with zero
UART retries**: 2000 exact CAPS replies, 2000 maximum-length CHECK requests and
2000 CRC-validated CHECK packets with exact expected ACK bytes. CHECK never
transmits RF or applies device state. These ran alongside production paired
queries, original-network RX traffic and MCU heartbeats. All 6000 passed in
223.99 seconds. Its eight paired queries produced seven fresh states and one
RF timeout. Median paired spacing was 235.548 ms and median AC-to-AC spacing
4247.757 ms; CCA retries explain larger individual gaps. One firmware-reported
middle preparation (`10`) frame in request 8 was not heard by Nordic; its AC
frames and final confirming state were captured. This remains an explicit
single-receiver observation gap, not proof of receiver loss or false TX success.

Two earlier load-harness interruptions are retained. The first stopped after
two successful paired queries because its copied AC-only assertion incorrectly
allowed only three frames; paired mode permits six across the same three rounds.
The continuation completed the remaining fourteen queries, but its load worker
stopped after 100 verified requests when a root-owned runtime directory rejected
progress writes. The final complete run used a preflight-tested, daemon-writable
StateDirectory subdirectory. Neither issue required firmware or retry changes.

Across all four instrumented sessions there were **13217 matched UART
requests**, no repeated IDs, no error or missing replies, and zero hardware RX
overruns, framing/parity errors, DMA faults, resynchronizations, parser errors,
UART TX drops or RF/log drops. Peak DMA backlog was 103 of 8192 bytes and maximum
observed main-loop RX polling gap was 3348 µs through final verification. Unit
and regression checks passed: 9 DMA-consumer, 40 parser, 62 scheduler, 113 host
and 4 integrity-tool tests. The DMA tests cover wrap, possible invisible laps,
producer overtaking, errors, preemption while staging, supported baud rates and
long streams with radio-sized processing pauses.

The normal daemon was restored, enabled, ready and MQTT-connected, with no test
override or temporary load directory. Registry, broker settings and config were
preserved. The final normal-service query also timed out: all six query frames
were captured, along with a switch `07` announcement, but no state report. UART
counters stayed zero. The last confirmed outputs were both OFF; that final query
did not refresh their state. Across the 72 workload queries, 62 confirmed and 10
timed out; the final health query is an additional retained timeout. Nordic
captured 184 of the 185 firmware-reported query frames including that final check.

The UART fix is verified under these bounded workloads on this Sonoff. RF/query
reliability remains unresolved; no further RF timing or retry changes were made.

## Remaining RF timeouts with stable UART — 2026-10-25

This is an **offline investigation**, using the complete firmware 0.10 Nordic
recording, its retained transaction results and serial RX traces, plus original
EC recovery/pairing/control captures. No firmware, daemon, query timing, retry
limit or running device configuration was changed. Reproduce it with
`python3 tools/analyze_stable_uart_rf.py`; the
[comparison JSON](development/sonoff-stable-uart-rf-20261025.json) retains input
hashes, per-attempt RF measurements, packet references and decoded serial
corroboration. Optional serial traces live under `.local`; if unavailable, the
script explicitly marks that corroboration unavailable. The capture/result
comparison can be rerun with those local captures and result files; those raw
artifacts are not included in the public source distribution.

### Where the failures occur

Including the final normal-service health query, **62 of 73 transactions
confirmed and 11 timed out**. The earlier 72-query workload count excludes that
last timeout. Both interrupted test harness sessions are retained; their
completed transactions are not discarded or reclassified.

All **45 transmissions belonging to the 11 timed-out transactions** are present
in Nordic, with valid application checksums, correct destination/suffix/tag,
and the same query body as successful requests and original EC queries.
Every failed transaction includes a native switch announcement or keepalive;
none includes a captured `fa/ac` or state `05`. Serial RX traces independently
agree for all ten workload timeouts; the final health query has the retained
unconfirmed MCU/host result and Nordic evidence, rather than a separate serial
trace. There is no late native state between a timeout and the next test, or
between the final timeout and the end of the recording.

Two failed paired transactions demonstrate partial bidirectional communication:

| Transaction | Captured `10` replies | Subsequent `ac` attempts |
| --- | --- | --- |
| `load_permissions_interruption`, request 13 | Frames 5458, 5508 | 5459, 5510, 5542; no `fa/ac` or state |
| `load`, request 7 | Frame 6740 | 6742, 6763, 6800; no `fa/ac` or state |

All frame numbers in this section, unless another capture is named, refer to
[the firmware 0.10 Nordic capture](../captured/development/sonoff-uart-dma-20261025-nrf52840.pcapng).
Thus a successful `10` reply does not guarantee that the following `ac` will
be accepted or answered. Lack of state cannot be explained solely by the
switch being offline, or by the host losing a completed result.

There are **130 captured AC attempts**: 62 with a subsequent native state and
68 without. These are attempts, not 130 independent transactions. Nordic sees
`fa/ac` for 61 of the 62 answered attempts; for the remaining one (request 30,
AC frame 2401), the serial trace contains `fa/ac` and Nordic captures both
state copies. This is a concrete example of a missed sniffer packet, not a
protocol failure. All captured `fa/ac` responses in local test windows are
followed by state. First-state delay is **126.827–617.068 ms**, median
**405.667 ms**, well below the unchanged 4012 ms response wait. The 62 distinct
native report bodies advance counter 247 through 255 and then 0 through 52
without a gap; token remains `54`. This supports the observed distinction
between answered and unanswered requests but does not reveal the switch's
internal query-acceptance logic.

### Timing is associated with success, but alignment is not sufficient

Both answered and unanswered AC attempts carry accurate phase stamps: their
median encoded-minus-observed heartbeat ages are **−0.221 ms** and **−0.220 ms**;
all errors are within **0.407 ms**. Incorrect zero query trailers are no longer
the explanation.

Folding each AC transmission onto the 200 ms cycle of the preceding local
heartbeat gives this descriptive distribution:

| Offset within heartbeat cycle | AC attempts | Attempts followed by state |
| --- | ---: | ---: |
| 0–60 ms | 75 | 54 |
| 60–120 ms | 11 | 3 |
| 120–180 ms | 33 | 0 |
| 180–200 ms | 11 | 5 |

The association appears in both modes: the 120–180 ms interval contains 14
unanswered unaligned AC-only attempts and 19 unanswered paired-mode attempts.
However, aligned AC-only still has seven unanswered attempts among 29 sends,
including two complete three-round timeouts. Other offsets also have both
outcomes. These observations must not be turned into a hard-coded receive
window or a promised success rate; attempts are correlated and this partition
was inspected retrospectively.

The four consecutive failed AC-only transactions 18–21 coincide with a moving
switch announcement phase. Announcements 1604/1693/1793/1880 occur at heartbeat
cycle offsets **37.187/71.084/104.993/138.188 ms**, with three successive
intervals approximately **13.834 s**. This is not simply UART scheduling jitter:
the aligned queries remain within 0.402 ms of the local gateway grid while the
peer's observed activity changes phase. It is consistent with a changing
receive/synchronization state, but announcement time is only a proxy for it.
In particular, failed AC 1566 and successful AC 2401 have almost identical
announcement-relative phases (162.815 and 162.575 ms), so announcement phase
alone is also insufficient. Delayed announcements, resynchronization, packet
loss and firmware scheduling remain possible explanations.

### Differences from original EC and the next discriminating test

The original recovery capture still shows 30 AC-only sends, 18 candidate
windows with state and 12 with neither state nor query reply. All six peers
have state responses during recovery. Those windows end at the next same-peer
query or four seconds; they are **not known EC logical transaction boundaries**.
They cannot be compared as a 12/30 timeout rate against our 11/73. EC retries
sometimes 200 or 600 ms apart and has multiple forwarding peers, whereas our
bounded test transactions address one switch. Original recovery packets also
sometimes carry nonzero route hints. The earlier original pairing captures
query this exact test switch with our same body/suffix/direct route; there is
no demonstrated gang-count or basic query-format mismatch.

A concrete remaining protocol mismatch is **automatic acknowledgement phase**.
All **328 local `8c/fa` or direct `8c/08` replies** in the stable-UART recording
have trailer `000000`. In contrast, all **14 original-EC replies in that same
recording**, and all **287 replies across the four original comparison
captures**, carry nonzero trailers. Original trailers generally track heartbeat
age; comparisons exclude ages over 10.05 s to avoid treating an uncaptured
heartbeat as a ten-second phase error. Local native-report ACK latency has a
median around **1.85 ms**, versus approximately **2.05–2.09 ms** for EC, depending
on the capture.

The implementation explains the zero trailers: `ec_make_ack()` in
`firmware/gateway/ec-protocol.c` clears the output and leaves the last three
bytes zero; the fast send path does not supply a phase stamp. We have corrected
scheduled query phase while retaining this older acknowledgement behavior.
Whether the switch consumes ACK phase for synchronization is **unproven**.
Successful exchanges also receive zero-trailer ACKs, so the mismatch is not a
sufficient explanation by itself.

The most useful next controlled change is to phase-stamp automatic replies at
actual transmit entry, with a valid heartbeat reference, **while leaving query
cadence, retry limits and ACK dispatch timing alone**. Compare state response,
peer announcement phase and keepalive behavior against this baseline. Keep ACK
latency adjustment as a separate experiment; changing both would obscure which
field matters. No such firmware change or RF experiment was performed here.

### What the interference evidence can and cannot say

Answered and unanswered AC transmissions have the same Nordic RSSI range
(**−64 to −52 dBm**, medians −55/−54 dBm). No decoded original-EC packet starts
within ±5 ms of any of the 130 AC transmissions. Four AC transmissions have
other captured frames within that interval: two answered and two unanswered.
Five of the ten workload timeouts have **zero CCA retries**; successful CCA is
not proof that the receiver experienced no interference. These checks do not
show an EC-collision signature explaining the failures. They also do not rule
out hidden transmitters, unreceived/corrupt frames, or collisions with replies:
Nordic RSSI is measured at Nordic, and timestamp proximity is not a PHY
collision measurement. The earlier EC-off experiment already showed that
removing EC did not eliminate timeouts.

The evidence therefore favors investigating receive-window/synchronization
compatibility before extending the response wait. It does not establish a
single root cause or prove the original network inherently unreliable.

## Sonoff phase-stamped automatic ACKs, release 0.11 — 2026-10-25

Release **0.11** enables heartbeat-phase stamps on the Sonoff's automatic replies
to approved `05`, `15`, `a0` and direct `08` reports. The existing immediate,
no-CCA dispatch path is retained, with no queue, delay or retry added. The
trailer is encoded at radio-call entry; `ACKTX` now records those actual bytes.
Without a live heartbeat reference, it sends the existing zero-trailer reply
immediately. `CHECK` remains an unstamped template builder. The feature is
advertised as `auto_ack_phase=tx_entry_or_zero_v1` and is enabled only in the
Sonoff build. The TI module was not flashed or activated.

Firmware and host both report 0.11 at **460800 baud**. The application-only
update preserved the Gecko 2.4.2 bootloader at **115200 baud**. The build adds
92 bytes of application text; BSS is unchanged. Tests passed: 33 ACK/MG21
scheduler checks, 40 legacy parser checks, 32 scheduler checks, 9 DMA checks and
113 host checks. The ACK tests cover actual logged bytes, unchanged sequence
and checksum, immediate single-send/no-CCA behavior, missing/stale references,
clock wrap, and inherited query cancellation/retry behavior. Initial packaging
preflights found a missing test fixture and macOS archive metadata; both were
corrected before flashing, and the failed preflight logs remain in the bundle.

### Bounded RF comparison

Nordic recorded 5220 packets over approximately 701 seconds, including setup
and the final ordinary-service check. The main workload repeated the same
48-query ABBA ordering of unaligned/aligned AC-only requests, then made 24
normal paired `10`→`ac` requests. Requests retained the three-second
between-transaction pause. No output-control commands were issued, and the
original EC remained active (265 decoded `b1` frames during the workload).

| Query mode | 0.10 baseline | Phase-stamped ACKs, 0.11 |
| --- | ---: | ---: |
| Unaligned AC-only | 19/24 confirmed | 18/24 confirmed |
| Aligned AC-only | 22/24 confirmed | 24/24 confirmed |
| Normal paired `10`→`ac` | 21/24 confirmed | 18/24 confirmed |
| Total workload | 62/72 confirmed | 60/72 confirmed |

All 24 aligned requests completed on their first round. The 12 timeouts in the
new workload are requests **1, 9, 20, 29, 41, 48, 49, 51, 56, 57, 62 and 65**.
None has a captured `fa/ac` or native state report. All six failed paired
requests have a captured `fa/10` (frames 3142, 3375, 3731, 3796, 4212 and 4371),
so the switch remains reachable but the subsequent snapshot exchange fails.

This result **does not demonstrate an overall reliability improvement**.
Alignment performed better in this sample, while the other modes did not.
The firmware runs are sequential, not a randomized concurrent comparison.
Also, baseline paired requests span several daemon sessions and partly overlap
an explicit UART stress workload; the new paired group uses one session without
that load. The aggregate difference cannot establish that stamping caused a
regression, nor does 24/24 establish reliable aligned queries under all conditions.
The normal daemon remains in paired mode; this experiment did not change its
query-mode default.

### Wire timing and transport verification

All **278 captured automatic replies** in the workload have nonzero phase
trailers. Their encoded-minus-observed heartbeat ages range from **−0.132 to
+0.257 ms**, median **+0.062 ms**. This verifies the intended wire change.

| Measurement | 0.10 baseline | 0.11 |
| --- | ---: | ---: |
| Median native-report→ACK delay | 1.847 ms | 1.855 ms |
| 95th-percentile ACK delay (observed order statistic) | 1.874 ms | 1.869 ms |
| Maximum observed ACK delay | 2.648 ms | 2.944 ms |
| Median paired `10`→`ac` gap | 235.546 ms | 235.546 ms |
| Median paired AC→AC gap, complete capture/no CCA retries | 4247.743 ms | 4247.749 ms |
| Median unaligned AC→AC gap, complete capture/no CCA retries | 4247.725 ms | 4247.722 ms |
| Median aligned RF grid error | +0.241 ms | +0.241 ms |

The approximately **8 µs median ACK difference** is measured, not an inserted
wait. One new ACK has the 2.944 ms outlier; baseline also has occasional slower
replies. Dispatch policy stayed unchanged. All aligned requests completed in
one round, so this run provides no on-air aligned retry interval to compare.

Hashes verify that shared query scheduling, MG21 scheduling, host service and
scheduler, phase encoding, and MG21 radio/UART implementation sources stayed
unchanged. Wire requests still carry **235500 µs preparation eligibility** and
**4012000 µs response waits**, and every retained MCU transaction has its
**14-second deadline**, **three-round maximum** and existing CCA bounds.
All **6280 traced UART requests** have matching replies, no repeated request
IDs and no error responses. UART, parser and radio-loss counters remain zero,
including the final normal-service check. The largest DMA backlog is 41 bytes;
maximum observed polling gap is 3594 µs.

Capture gaps remain explicit: Nordic contains **173 of 179** MCU-reported
workload query sends. Requests 21, 32, 48, 56, 62 and 69 each lack one frame;
request 32 has no captured query at all but has a captured fresh state matching
the successful MCU result. Fifteen of 293 successful UART `ACKTX` records have
no matching Nordic packet. These gaps are not reclassified as logical query
failures or proof of collisions. Spacing comparisons exclude incomplete
transactions where a missing intermediate frame would double a measured gap.

### Restored service and evidence

The test override was removed and the normal service restarted. Its separate
final refresh **confirmed after two rounds/four sends**, with Nordic query
frames **5098, 5100, 5145, 5147** and state frames **5149, 5152**. It reports both
outputs OFF, counter 113, token `54`. The service is active/enabled, ready and
MQTT-connected; authoritative registry, MQTT settings and configuration are
preserved. Nordic recording is stopped. This extra successful refresh is not
included in the 72-workload table.

Evidence: [complete comparison](development/sonoff-ack-phase-20261025.json),
[Nordic capture](../captured/development/sonoff-ack-phase-20261025-nrf52840.pcapng),
and [deployment/restoration record](../host/verification/deployment-sonoff-ack-phase-20261025.json).
Run `python3 tools/analyze_ack_phase.py` from the repository root with the
preserved test bundle in `.local/sonoff-ack-phase-20261025` to repeat the checks.
The bundle is also saved at `/home/sfires/livolo-ec/sonoff-ack-phase-20261025`
on 10.1.1.21; original Nordic capture/metadata are under
`/home/sfires/livolo-sniffer/sonoff-ack-phase-20261025` on 10.1.1.120.

The automatic-ACK phase mismatch is now corrected and independently verified.
The remaining timeouts still occur before `fa/ac` or state reception, so this
change alone does not explain or eliminate them. Query timing and retry limits
remain unchanged.

## One-gang discovery and binding on Sonoff 0.11 (2026-11-01)

Evidence: `analysis/development/onegang-pairing-20261101.json` and
`captured/development/onegang-pairing-20261101-nrf52840.pcapng`.
The bounded Nordic recording contains 2,900 packets, including 694 recognized
EC frames with valid application checksums. Unsupported frames are retained.

A newly powered, user-confirmed one-gang switch advertised identity `66780945`
and suffix `0001` in passive `09` traffic. Its physical pairing operation then
produced `01` announcements with tag `ff`. The daemon registered slot 1 with an
explicit one-output profile and the observed suffix, preserving two-gang peer
`46715621` in slot 0. A targeted 120-second window reproduced the existing
FA/FA/10/AC/AC admission sequence. The new peer adopted tag `a7` and delivered
state to gateway `2a992b12`; the daemon confirmed pairing and closed admission
about 3.2 seconds after the request. The user confirmed the indicator stopped.

| Verification | Result |
|---|---|
| One-gang read-only query | First round, two query frames; fresh counter 6, mask `01`, ON |
| Physical button press | User confirmed OFF; fresh counter 7, mask `01`, OFF received by daemon |
| Existing two-gang read-only query | First round, two query frames; fresh counter 2, mask `03`, both OFF |
| Final gateway | Ready, both peers online, admission closed |

Firmware, query timing, retry limits and ACK rules were unchanged. No output
control command was sent. The one-gang query had zero CCA retries; the existing
peer query had two CCA retries within the existing bound. This is a functional
binding/profile check, not a reliability benchmark.

All 316 native frames from the new switch used suffix `0001`. Its full-state
reports used mask `01`, corroborated by the user's one physical button and
observed ON/OFF transition. Together with known one-gang EC peer `75710834`
(suffix `0001`) and two-gang peer `46715621` (suffix `0002`, full mask `03`),
this supports distinguishing these tested profiles. The host still requires an
explicit output count; automatic inference and universal model identification
are not implemented. A single masked state-change report alone would not prove
the total number of outputs.

The new one-gang device reported the same property values as the original
two-gang pairing capture: `24→02`, `25→04`, `26→00`, with report counters 2 and 3.
These fields therefore do not directly distinguish the tested gang counts; in
particular `25→04` does not mean four physical outputs. No native `a0` report
was captured from the new device in this session.

Routing remains an important limitation. Before our admission, Nordic captured
two `ff` state reports addressed to original EC gateway `9bf4ea38`, which replied
with `b1` ACKs (frames 1667–1668 and 1687–1688). After adopting our tag, the switch
still first sent several reports to that original gateway, then retried toward
ours. The successful query state reached ours at frame 2558; the physical OFF
report reached ours at frame 2724 after four copies addressed to original EC.
Thus successful pairing did not immediately eliminate the other gateway from
the switch's routing behavior. This capture alone does not establish why it
chose that destination or prove another registration with EC.

Property `15` key `26` also fell back to repeated broadcasts. Our current
`ec_make_ack()` deliberately only permits broadcast ACKs for state opcode `05`,
so these property broadcasts receive no automatic ACK from us. The later query
was followed by direct property reports, including key `26` addressed to ours
at frame 2606. Preserve this behavior for a separate routing/ACK investigation;
do not mistake the repeated property packets for a gang-profile mismatch.

## One-gang MQTT output failures after binding (2026-11-01)

The successful binding/profile check above did **not** establish reliable output
control. The user subsequently reported that Home Assistant could not turn on
`66780945`. Durable daemon records confirm that HA MQTT requests reached the
correct peer with mask `01`, value `01`, but timed out without fresh matching
state. HA's non-optimistic entity returned to OFF; the user confirmed that the
physical output did not change.

Evidence: `analysis/development/onegang-control-20261101.json` and
`captured/development/onegang-control-20261101-nrf52840.pcapng` (2,195 packets,
342 recognized EC frames, all recognized application checksums valid).
Firmware, daemon, MQTT discovery, retry limits and timing were unchanged.

| Test | ON confirmed | OFF confirmed |
|---|---:|---:|
| Initial direct MQTT reproduction, original EC on | 1/1 | 1/1 |
| User Home Assistant attempt while Nordic recorded | 0/1 | Not requested |
| Three direct MQTT cycles, original EC on | 1/3 | 3/3 |
| Three identical cycles, original EC off | 1/3 | 3/3 |
| Total | 3/8 | 7/7 |

All direct MQTT tests used the same mask/value payload as HA, adding a request
ID only for correlation. Each command retained the six-attempt, 0.2-second
host retry interval and four-second confirmation deadline. Nordic saw all six
transmissions of the user's failed HA request (frames 351, 353, 356, 358, 359,
361), correctly addressed with suffix `0001`, tag `a7`, opcode `02`, mask/value
`01/01`. No native state response was captured in that window. The four failed
ON requests in the controlled repeats likewise had no captured native state
response, including no response addressed to original EC. One such failure had
five of six attempts visible to Nordic; the other three had six.

Successful early requests still showed the routing issue from pairing: the
switch first addressed reports to original EC, then to ours. Those retries can
delay confirmation, but do not explain windows with no captured state report.
No original EC heartbeat was captured during the EC-off test. Turning that
gateway off therefore did not eliminate the failure, although its switches
remained powered and this was not a fully isolated RF environment.

Compared with the original EC capture for one-gang `75710834`, our commands
have the same basic `8c/02` layout, suffix `0001`, mask/value encoding and valid
additive checksum. Gateway/device IDs, network tag, transaction token, sequence
and phase trailer naturally differ. This excludes a demonstrated fixed
one-gang mask/suffix formatting error; it does not establish full protocol
compatibility. Failure is intermittent even through an identical MQTT path.
Phase/receive-window behavior and replay/deduplication remain hypotheses, not
identified causes. The small sequential trials do not prove a token/checksum
rule or an ON-specific failure mechanism.

No speculative code correction was deployed. The final confirmed state was OFF
(counter 17, token 18); both peers remained online, admission closed. The Nordic
capture was stopped cleanly and saved. The user was told the EC-off comparison
was complete and original EC could be restored.

## Output retry wait: 200 versus 400 ms (2026-11-01)

Evidence: `analysis/development/output-retry400-20261101.json` and
`captured/development/output-retry400-20261101-nrf52840.pcapng`. Reproduction
scripts, UART trace and complete trial/controller records are preserved under
`.local/output-retry400-20261101`; remote evidence is under the matching
`/home/sfires/livolo-ec/` directory. Nordic capture is from `10.1.1.120`.

Original EC does not exhibit only one observed repeat interval. For direct
`8c/02` commands with the same subject and application payload, the earlier
`original-ec-75710834-control-01` capture has 16 successive gaps under four
seconds: nine near 200 ms and seven near 400 ms (median 206.038 ms). The later
`original-ec-live-20261025` capture has 28: 23 near 200 ms and five near 400 ms
(median 200.038 ms). These are observed packet gaps, not proof of an exact
internal retry timer: an unseen transmission can turn two 200 ms intervals
into one observed 400 ms gap.

The live experiment used four blocks in order **200, 400, 400, 200 ms**. Each
block contained two cycles on each of the three outputs. A cycle requested the
opposite of the starting state, then restored that state. Commands ran serially
with a one-second pause after each outcome. Only labelled `retry400-` requests
used a 400 ms host wait; the temporary process wrapper retained six total
attempts, one transaction token, phase stamping, and the four-second overall
confirmation deadline. Queries, firmware, automatic ACKs and MCU heartbeat
scheduling were unchanged. Both variants ran within the same daemon/radio
session. The installed package and configuration were not edited.

| Output | 200 ms confirmed | 400 ms confirmed |
|---|---:|---:|
| `66780945`, output 1 | 6/8 | 7/8 |
| `46715621`, output 1 | 6/8 | 4/8 |
| `46715621`, output 2 | 4/8 | 4/8 |
| Total | **16/24** | **15/24** |

Nordic verified the actual timing change. The main adjacent-gap clusters had
medians **213.729 ms** and **413.586 ms**; larger gaps are retained in the JSON
rather than treated as exact retry intervals. Median time from request to
observed confirmed API result was 0.257 s versus 1.291 s. The independent
capture contains 87 and 92 output packets respectively; UART recorded 98 and
104 attempts, with at most six per command. CCA outcomes and sniffer misses can
reduce captured counts. No native state report was captured in any of the eight
200 ms or nine 400 ms failed-command windows, even when reports addressed to
other gateways were considered.

No original EC gateway heartbeat appeared anywhere in this capture. This
comparison therefore was not observed with an actively transmitting original
EC gateway, although other EC switches remained in the environment. UART/radio
receive errors and drops remained zero in the collected adapter diagnostics.
The capture ended cleanly with 1,992 packets; all recognized EC checksums passed.

There is **no demonstrated overall benefit** from doubling the host wait in
this small sequential trial. The one-gang result improved by one confirmation,
but the two-gang first output declined by two; neither difference establishes
causality. These counts include same-state restore requests after failed
transitions, each requiring a fresh matching report. This experiment tests a
longer host wait, not exact alignment to EC's 200 ms transmit grid: UART/radio
overhead remains present in both variants.

The controller removed its runtime systemd override and restarted the ordinary
0.11 daemon. Source/configuration hashes were unchanged, pairing stayed closed,
and the default **200 ms** retry wait was restored. Fresh final state queries
confirmed `66780945` OFF (two query rounds) and `46715621` output 1 OFF / output 2
ON (three rounds), matching the starting states. Both devices were online.

## Reinspection: original EC heartbeat grid and burst rhythm (2026-11-01)

See `analysis/development/ec-pulse-20261101.md` and its JSON evidence; reproduce
with `python3 tools/analyze_ec_pulse.py`. Ten original captures establish that
108/116 eligible gateway output packets and 61/64 first observed attempts lie
within 1 ms of a heartbeat-relative 200 ms grid after accounting for clock drift.
First attempts occupy 38 different slots in the ten-second heartbeat cycle;
there is no observed decoded packet in the preceding 50 ms for any of those
64 starts. This supports a clocked transmission schedule, not a requirement to
receive a particular immediately preceding RF message before each command.

In the five-minute live recording, 59 native state reports from six peers fall
8.6–18.7 ms after a grid edge. Across the inspected original captures, 294
matched reactive gateway replies follow their native reports by median 2.081 ms.
State-report slot phase is not the same quantity as command-to-report latency.

Some 400 ms retry gaps now have stronger evidence for intentional slot skipping:
a 13-packet same-command burst has consecutive MAC sequences and relative slots
`0,2,3,5,6,8,9,11,12,14,15,17,18`. Two shorter bursts corroborate a two-per-three
slot pattern, with different skipped-slot phases. This does not yet establish
a universal 600 ms superframe or fixed per-peer slots.

Our recent output retry experiment started at arbitrary grid phases and measured
about 214/414 ms gaps; only 4/159 eligible transmissions fell within 2 ms of a
local grid edge. Increasing a relative host wait does not recreate original EC's
absolute transmit alignment. A controlled MCU-aligned output transaction test
is warranted before changing retry counts or adopting a universal skip pattern.
The existing recordings suffice for that next experiment; more EC capture is
optional for understanding the skip rule, not required to identify this pulse.

## Heartbeat-grid output control verified on Sonoff (2026-11-01, release 0.12)

Moving output-command transactions onto the MCU's absolute heartbeat-relative
200 ms grid improved confirmation from 12/24 to 24/24 in a same-firmware ABBA
comparison across both test switches (one and two gangs). All aligned commands
confirmed after two transmissions, within the unchanged six-attempt / four-second
bounds. Nordic measured median retry intervals of 200.013 ms and absolute grid
error of 0.057 ms, compared with host-timed 213.661 ms and 47.858 ms. This supports
the timing hypothesis but does not isolate alignment from precise retry cadence
and MCU cancellation. The existing read-only query still timed out for the
one-gang peer; output success must not be generalized to queries.
See [complete evidence and limits](development/output-grid-20261101.md).
