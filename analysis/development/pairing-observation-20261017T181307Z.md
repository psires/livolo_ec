# Switch-only pairing observation — 2026-10-17

Both receivers recorded channel 11 for five minutes, starting at approximately
20:13:08 Europe/Warsaw. The user activated pairing on switch `46715621` and
explicitly left the EC gateway unchanged. The user later reported that pairing
stopped. Event-log timestamps record when those confirmations were saved, not
the exact physical actions. Our tools only received radio traffic.

**Outcome:** a registration burst followed by ordinary announcements, with no
observed registration acknowledgement or native tag transition to `b1`.
This supports an unsuccessful pairing attempt, rather than a completed join.

## Capture integrity

| Measurement | TI CC2652P | Nordic nRF52840 |
| --- | ---: | ---: |
| All packets | 1,919 | 2,236 |
| Recognized EC packets | 522 | 528 |
| Valid EC application checksums | 522 | 528 |
| Native test-switch packets | 293 | 293 |
| Native registration packets (`9e/01`) | 269 | 269 |

All 293 native test-switch packets match byte for byte across receivers, including
multiplicity. They comprise 269 registrations, 20 announcements, two state
reports, and two opcode-08 requests. All retain tag `ff`.

Across the 297.745-second interior overlap, 492 EC frames match, with 28 TI-only
and 34 Nordic-only frames. Those differences involve other transmitters, including
forwarders; neither receiver is a complete reference for the whole network.
The TI UART had zero malformed records and zero sequence discontinuities.
No unrecognized frames beginning with the current EC MAC prefix `01 88` were
found in either capture. This does not exclude other protocol layouts/channels.

## Sequence and new observations

Frame references and relative times below use the Nordic file, whose timestamps
are suitable for response timing. TI timestamps are UART receipt times.

1. At +13.415 and +18.414 seconds, the switch sends state reports to the existing
   gateway address. Gateway ACKs (frames 80 and 130) follow in 2.046 and 2.053 ms.
   Both reports carry mask/state `02/02`, with report counters 2 and 3. There is
   no basis here to infer two on/off transitions from two same-state reports.
2. Registrations run from frame 138 at +19.216877 seconds to frame 933 at
   +91.618138 seconds: **269 broadcasts spanning 72.401 seconds**. Median
   inter-packet interval is 200.088 ms; this is a burst duration, not a precisely
   measured UI pairing timeout.
3. The registration payload begins `00 02 03 05 34 01 04 00`. Byte 35 of the
   MAC frame (seventh data byte) rises from `04` to `3c`, generally remaining
   constant for several packets. It then stays at `3c` for the final 49 packets,
   spanning 14.607 seconds. It is a timer/retry/progress candidate, not a decoded
   gang count. Route flags advance through `00`, `01`, `02`, and predominantly
   `03` (6, 6, 6, and 251 native registrations respectively).
4. Five installed source identities forward registration-like packets with the
   same test-switch subject: `75710834`, `67181844`, `81422527`, `72715996`, and
   `47778828`. Nordic captures 45 such packets and TI 57. Of these, 44 Nordic
   and 56 TI records match an earlier native registration's complete application
   body and MAC sequence within one second. Do not equate the raw forwarding
   counts with counts of uniquely originated registration requests.
5. A clear forwarding example is Nordic **147 → 148**, separated by 11.651 ms:
   immediate source changes from `46715621` to `75710834`, destination changes
   from broadcast to gateway `9bf4ea38`, route slot A becomes `46715621`, and tag
   changes from `ff` to `b1`. The application body and MAC sequence are preserved.
   The 44 matched Nordic forwarding delays range from 5.597 to 12.809 ms.
   This demonstrates installed switches relaying a candidate device's requests
   toward the gateway. It does not establish topology or failure recovery.
6. Two additional exchanges appear at +41.221 and +71.621 seconds: switch
   `9e/08` requests to the gateway, answered by device-specific `8c/08` packets
   in 2.020 and 2.015 ms (frames **394 → 395**, **710 → 711**). Replies echo the
   MAC sequence and subject, with eight zero data bytes. This looks like a
   keepalive/status exchange; its exact semantics remain unproven. It is distinct
   from the known broadcast gateway heartbeat with zero subject.
7. No `fa` acknowledgement of registration opcode `01` is captured. The gateway
   nevertheless answers state reports and the two opcode-08 requests. Thus a
   gateway reply is not by itself evidence of registration, and tag `ff` does
   not imply that the switch has forgotten the gateway address.
8. Announcement data changes from `00 00 7f 2a ...` before the burst to
   `00 00 3e 2a ...` during it, then back to `00 00 7f 2a ...` at frame 959,
   +95.426 seconds. This is correlated with pairing activity, but the changed
   field's units/meaning are unknown. Periodic announcements continue through
   +288.243 seconds with tag `ff`.

**Later counter evidence:** the following gateway-enabled pairing attempt starts
its registration field at `3d`, immediately after this attempt's final `3c`.
That continuity argues against a simple elapsed-pairing timer that resets on
entry. A continuing request/retry counter is a better candidate; exact increment
and persistence rules still require testing.

No opcode `0b`, or `fa` referring to `0b`, appears among the decoded frames.
The missing exchange from the original successful pairing remains unresolved.

## Decoder change and next experiment

The decoder now distinguishes device `9e/08` requests and device-specific
`8c/08` replies from broadcast heartbeats. It no longer assigns heartbeat counter
fields to the latter replies. A regression test uses the captured request/reply
bytes; all 15 tests pass, including the original five-capture evidence suite.
The generic decoder's `matched_relays` summary still covers its original
gateway-to-switch forwarding rule; the switch-to-gateway analysis above is
recorded separately in the details JSON.

Next, record a separate experiment with EC gateway pairing explicitly enabled,
starting both captures before either pairing action. Compare gateway behavior
before and after admission opens, capture the registration ACK and tag change
if they occur, and search again for the missing `0b` exchange. No firmware
transmission or replacement-gateway registration has been attempted.

## Saved evidence

- [TI capture](../../captured/development/pairing-observation-20261017T181307Z-cc2652p.pcapng)
- [Nordic capture](../../captured/development/pairing-observation-20261017T181307Z-nrf52840.pcapng)
- [Packet comparison](pairing-observation-20261017T181307Z-comparison.json)
- [Detailed timeline, counts, and forwarding matches](pairing-observation-20261017T181307Z-details.json)
- [Decoded summaries](pairing-observation-20261017T181307Z/summary.json)
- [Decoded packet index](pairing-observation-20261017T181307Z/decoded_packets.csv)
- [User-action confirmations](pairing-observation-20261017T181307Z-events.jsonl)
- [Capture execution results](pairing-observation-20261017T181307Z-run.json)
