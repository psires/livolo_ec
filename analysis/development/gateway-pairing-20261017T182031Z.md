# Gateway-enabled pairing and app control — 2026-10-17

Both radios recorded channel 11 for five minutes beginning at approximately
20:20:32 Europe/Warsaw. Capture started before the user enabled gateway pairing
and then switch pairing. The user confirmed that the EC app showed the switch
successfully added. Subsequent output testing was explicitly **EC app only**.
Event-log times mark when confirmations were recorded, not exact button times.
Our TI firmware and Nordic sniffer remained receive-only throughout.

**Pairing succeeded:** registration acknowledgements appeared, native switch
traffic changed from `ff` to `b1`, and the new tag persisted for the remaining
four minutes. This was a repeat registration of the same previously used switch,
not a documented factory-reset commissioning experiment.

## Capture agreement

| Measurement | TI CC2652P | Nordic nRF52840 |
| --- | ---: | ---: |
| All frames | 886 | 1,871 |
| Recognized EC frames | 193 | 222 |
| Valid EC application checksums | 193 | 222 |
| Native test-switch packets | 40 | 40 |
| Packets from/about the test switch | 65 | 65 |

**All 65 target packets match byte for byte, including multiplicity.** They
include 40 native switch packets and 25 gateway packets with its subject ID.
Eight native packets use `ff`; the subsequent 32 use `b1`.

Whole-network agreement is lower: the 296.768-second interior overlap has 188
common EC packets, three TI-only and 30 Nordic-only packets. TI saw no packets
from weak source `47778828` in this run. Target agreement does not imply complete
network coverage. The TI UART had no malformed records or sequence gaps.
No undecoded `01 88` MAC-prefix frames were found in either file.

## Registration timeline

Frame references and relative times below use the Nordic capture.

| Frames | Relative time | Observation |
| --- | ---: | --- |
| 262–263, 290–291 | 39.631–44.433 s | Two `ff` state reports receive ordinary gateway ACKs; this precedes registration |
| 292, 298, 300 | 44.834–45.432 s | Three broadcast `9e/01` registrations, tag `ff`, identical application body, MAC sequences `6d`, `6e`, `6f` |
| 301–302 | 45.619–45.819 s | Gateway `8c/fa` acknowledges opcode `01`, using its own sequences `1e`, `1f` |
| 305–306 | 46.019–46.021 s | Gateway query `10`; switch reply is the first native packet tagged `b1` |
| 308–310, 316 | 46.219–47.031 s | Gateway repeats query `ac`; two switch responses are captured |
| 312–337 | 46.631–47.832 s | State and property reports, with gateway ACKs |
| 378 onward | 51.439 s onward | Periodic switch announcements retain `b1` |

The first registration ACK is 785.016 ms after the first registration, or
186.362 ms after the third. The second ACK follows 200 ms later. Both ACKs occur
before the first native `b1` packet; this does not establish which ACK was decisive
or that one ACK alone is sufficient. The last joined-state packet is at
+288.458 seconds, 242.437 seconds after the first.

The observed request data is `00 02 03 05 34 01 3d 00`. The ACK data is
`01 00 02 03 05 34 01 3d`: acknowledged opcode plus the request's first seven
data bytes, matching the established ACK envelope. In the registration ACKs:

- destination and subject are `46715621`, subject suffix `0002`;
- immediate source is gateway `9bf4ea38`, tag `b1`;
- routing slot A is zero, routing slot B is `46715621`;
- flags are `1e`, and MAC sequence is from the gateway's sequence stream;
- family `8c` additive checksum applies; the timing trailer varies.

The same routing-slot-B/flags structure appears in original pairing frames
140 and 143. Flags `1e` also appear in later app control commands, so they must
not be labeled simply as a "pairing enabled" bit. Gateway broadcast heartbeats
keep flags `0e` and parameter 7 before and after registration; this run does not
prove that heartbeat parameter is a live device count or pairing-mode indicator.

The request byte at MAC offset 35 continues from the previous failed attempt's
`3c` to this attempt's `3d`. That argues for a continuing counter rather than a
fresh elapsed-pairing timer. Its complete increment and persistence rules remain
unknown. A response to `ac` also contains `3d`, an additional correlation rather
than a decoded field definition.

Query `10` returns `10 45 19 7b 38 00 00 00`, identical to the old successful
capture. The `ac` replies are `ac 00 03 05 00 00 3d 00` and
`ac 00 03 02 00 00 06 00`. Property `15` reports again give `24→02`, `25→04`,
`26→00`. The value 4 remains insufficient evidence of four exposed gangs.

No opcode `0b`, acknowledgement referring to `0b`, or capability opcode `a0`
appears in either decoded capture. Thus this observed repeat-registration path
succeeded without a captured `0b` exchange. It does not establish that a fresh,
factory-reset device can omit the initialization exchange seen indirectly in
the original capture, or that there was no relevant earlier retained state.

## Confirmed app control

Eight command packets represent five transactions. Bit positions refer to the
protocol's state bitfield; left/right physical-button mapping is not established.

| Command frames | Mask | Requested state | Token | Report frames | Report counter |
| --- | --- | --- | --- | --- | --- |
| 1209, 1210 | `02` | `00` (bit 1 off) | `16` | 1211 | 8 |
| 1219, 1221 | `02` | `02` (bit 1 on) | `9d` | 1222 | 9 |
| 1234 | `02` | `00` (bit 1 off) | `77` | 1235 | 10 |
| 1242, 1247 | `01` | `01` (bit 0 on) | `2f` | 1243, 1248 | 11 |
| 1252 | `01` | `00` (bit 0 off) | `98` | 1253, 1256 | 12 |

Reports echo the command token and confirm the requested states. First-command
to first-report latency is about 214–222 ms in this sample. Gateway ACKs to state
reports arrive in 2.044–2.122 ms (nine observed ACKs, including pre-registration
reports). Fast ACK handling therefore belongs on the radio MCU, not in an
unbounded Linux/Python/MQTT scheduling path.

The two `2f` command copies have different MAC sequences but identical application
payloads. The repeated reports retain counter 11 and token `2f`; the `98` reports
similarly retain counter 12 while MAC sequence changes. Neither receiver captures
ACKs to the first reports in those two transactions. This supports retry-aware
handling; repeated packets are not additional user actions. Deduplication must
account for device/operation/token/counter and a bounded transaction lifetime,
not just MAC sequence or token alone.

## Implementation implications and remaining limit

Together with the failed attempt, this supplies a concrete candidate registration
exchange, gateway admission contrast, follow-up queries, two-output control, and
ACK/retry evidence. It does not yet validate a replacement gateway transmitting
that sequence. The next implementation work is an explicit UART command interface
and MCU-side transmit/ACK handling, followed by controlled testing with a spare
device. Factory-reset commissioning and the missing `0b` exchange remain separate
questions. No firmware change or radio transmission was made by our tools here.

## Evidence

- [TI capture](../../captured/development/gateway-pairing-20261017T182031Z-cc2652p.pcapng)
- [Nordic capture](../../captured/development/gateway-pairing-20261017T182031Z-nrf52840.pcapng)
- [Whole-network comparison](gateway-pairing-20261017T182031Z-comparison.json)
- [Full target timeline and control transactions](gateway-pairing-20261017T182031Z-details.json)
- [Decoded summaries](gateway-pairing-20261017T182031Z/summary.json)
- [Decoded packet index](gateway-pairing-20261017T182031Z/decoded_packets.csv)
- [User confirmations](gateway-pairing-20261017T182031Z-events.jsonl)
- [Execution results](gateway-pairing-20261017T182031Z-run.json)
