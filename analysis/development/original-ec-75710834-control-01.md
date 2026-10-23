# Original EC reference: switch 75710834

Captured on 2026-10-17, 19:56:42–19:58:16 UTC, using only the Nordic receiver
on channel 11. The original EC gateway was restored by the user. The local
daemon remained running, but no local control, reset, flash or pairing action
was performed. The user confirms this is a **one-gang switch** and every app
command worked. Its last captured native state is OFF.

Capture: `captured/development/original-ec-75710834-control-01-nrf52840.pcapng`.
Machine-readable measurements: `original-ec-75710834-control-01.json`.

## Identities and transactions

Original gateway `9bf4ea38`, switch `75710834`, tag `b1`, subject suffix `0001`.
The locally paired two-gang spare uses suffix `0002`. This adds a second
physical-device observation consistent with the suffix describing gang count;
it does not establish its meaning across all device models.

Five distinct application command bodies appear. Exact-body retries are grouped
below. The capture does not establish that all requested slow/rapid steps were
performed, so their labels are not inferred from the requested sequence.

| Action | Token | EC-origin sends | First send to first native report | Counter | ACK observation |
| --- | --- | --- | --- | --- | --- |
| Output 1 OFF | `2d` | 279, 283, 288 | 611.394 ms | `fe` | 289 → 290, 2.095 ms |
| Output 1 ON | `46` | 370 | 212.999 ms | `ff` | 371 → 372, 2.097 ms |
| Output 1 OFF | `04` | 13 sends, 423 through 467 | 201.187 ms | `00` | No matching EC ACK captured |
| Output 1 ON | `78` | 498, 499 | 214.312 ms | `01` | Repeated report 504 → 505, 2.089 ms |
| Output 1 OFF | `68` | 591, 593 | 210.703 ms | `02` | 594 → 595, 2.094 ms |

For token `2d`, the captured attempts occur at 0, 211.997 and 600.053 ms.
The first report follows the third captured attempt by about 11.341 ms.
This is a concrete successful stock transaction extending beyond the local
daemon's approximately 430–457 ms three-attempt transmit spans.

For token `04`, EC continues sending the same body/token over 3.600305 seconds.
Four of these 13 EC-origin attempts are relay-addressed (flags `2e`, `3e` or
`4e`); nine address the switch directly with flags `1e`. All three token `2d`
attempts address the switch directly. Thus the late successful `2d` transaction
does not require inferring a relay path.
The switch emits 11 native state reports, all retaining counter `00` and token
`04`. Their destinations/flags change during this interval, including broadcast
and a later destination of another installed device. No matching gateway ACK
is captured. The user nevertheless reports successful physical operation.
Counter wrap, reception, routing and gateway confirmation behavior are possible
contributors; this single case does not isolate the cause.

## Timing trailer

Nine stock heartbeats are captured, separated by 10.000650–10.000716 seconds.
For all 21 EC-origin output commands, the empirical expression

`phase_seconds = trailer[2] + little_endian_u16(trailer[0:2]) / 65500`

matches elapsed Nordic SFD time since the preceding heartbeat within
0.437–0.820 ms. Examples include `849905` = 5.6 seconds and `c2cf05` = 5.812
seconds. The latter is not on the usual 200 ms command grid. Most captured
command phases fall on that grid; retransmissions carry updated trailers.

Allowing a fitted constant offset and relative clock drift, divisor 65500 gives
an RMS residual of 29.9 microseconds (offset 158.9 microseconds, drift 77.0 ppm).
Divisor 65536 gives 147.2 microseconds RMS (offset 490.4 microseconds, drift
61.3 ppm). Thus 65500 fits these data substantially better. The fitted offset
is a capture/model result, not a validated delay to hard-code into our firmware.
Relayed copies can replace the trailer; these measurements apply only to frames
whose immediate transmitter is the original EC gateway.

This strongly supports a timing-field interpretation. It does not yet prove
the switch's receive-window rules, allowed error, or how to reproduce the field
through host/UART latency. Keeping every retry's trailer zero is a material
difference from the stock implementation.

## Implications for the local implementation

1. A longer bounded retry window has direct stock precedent. Keep the same
   application token/body for retries and stop on a matching native report.
2. Longer retries and accurate timing trailers remain separate experimental
   variables. A longer zero-trailer trial alone cannot validate synchronization.
3. The observed counter wrap must remain valid input. Do not add a rejection of
   counter zero or infer a stock firmware defect from this capture alone.
4. Preserve the distinction between this one-gang reference and the two-gang
   local spare. The reference narrows hypotheses but is not a complete local
   gateway acceptance test.

The original recording contains 726 packets, including unrelated IEEE 802.15.4
traffic and short MAC ACKs. The EC decoder recognizes 196 packets, all with
valid application checksums; 530 packets are outside its supported EC layouts.
