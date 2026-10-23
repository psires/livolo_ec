# Livolo EC protocol: capture-based reconstruction

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

The mapping of bit 0 to the left/right physical button is unknown. Captures do not independently prove whether unselected bits in `VV` are ignored. There is no gateway `MM=02` example in this dataset, so second-output-only transmission remains an extrapolation.

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

**The value 4 is not evidence of four exposed gangs.** It could be a capability, feature mask, mode, hardware code, or something else. Similarly, neither the `0002` subject suffix nor the `02` in a capability report is proven to encode physical gang count. There are no standard Zigbee endpoints advertised here, and no state bits above bit 1 are exercised. The working interpretation is a two-output state bitfield, with older four-output software compatibility still unproven.

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
