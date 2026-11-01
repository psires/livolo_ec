# Sonoff query spacing comparison — 2026-10-24

At 460800, increasing the temporary host delay from 200 to 224 ms restored the
median on-air `10` → `ac` spacing to the earlier 115200 reference. Eleven of
twelve refreshes confirmed, compared with two of twelve in the preceding
short-spacing Sonoff run. This supports query-pair spacing as a contributor to
the failures, but one timeout remains and these are sequential small samples.

| Run | Application UART | Host pair delay | Median RF pair spacing | Confirmed |
| --- | ---: | ---: | ---: | ---: |
| Earlier ZB-GW04 reference | 115200 | 200 ms | 237.019 ms | 10/12 |
| Sonoff, corrected UART firmware | 460800 | 200 ms | 213.397 ms | 2/12 |
| Same Sonoff firmware, spacing adjustment | 460800 | 224 ms | 237.302 ms | 11/12 |

The latest two runs used the same Sonoff 0.4 application, gateway identity and
test switch `46715621`, with Nordic on `10.1.1.120`. No flashing, pairing or
output-setting commands were performed. The host service source hash remained
`f27711401c5c6d3587e1c9834f19c53c03b47f39a2e72dd8ff83c88939399497`.
Only a temporary in-memory `REFRESH_QUERY_INTERVAL` override changed. Limits
remained **three rounds, four seconds of response wait and a 14-second deadline**.
Requests were separated by three seconds after completion.

## Independent Nordic verification

Nordic captured **54/54** reported query transmissions, and all twelve latest
TX metadata records match captured bytes. Complete-pair spacing ranged from
235.650 to 276.478 ms; its median was only **0.283 ms above** the 115200 reference.
The comparison matches typical pair spacing, not every individual interval.

Median round-start spacing was 4251.085 ms, versus 4273.905 ms at 115200 and
4227.409 ms in the short-spacing Sonoff run. With the four-second response wait
unchanged, faster UART handling after `ac` still shortens the interval between
rounds by approximately 23 ms relative to 115200. The experiment therefore does
not reproduce the entire 115200 timing sequence exactly.

All 54 query phase trailers matched the independently captured heartbeat-relative
RF age within **0.402 ms**. Fresh native state reports matched daemon counters
23–33 and all reported OFF/OFF. There were no captured queries after any fresh
confirmation. Successful requests used one to three rounds; retries remain
necessary.

Request **4** exhausted all three rounds. Its three pair intervals were 237.762,
237.841 and 236.502 ms. Nordic captured a native `fa/10`, but no `fa/ac` or `05`
state report in that request window. This is the same incomplete reply pattern
seen in the earlier failures, despite now matching typical pair spacing.

The trial retained one UART session. UART RX errors, overruns, framing/parity
errors, input errors, UART/RX/radio drops, RX sequence gaps and ACK failures all
had zero deltas. All 51 automatic ACK sends succeeded. Aggregate radio counters
recorded 120 attempts and 115 successful sends; they do not identify the cause
of each unsuccessful attempt. The host's combined malformed/unsupported RX
counter increased by 1396 and is not itself a UART corruption counter.

Native counters and exact TX bytes supplement cross-host request windows with
a 250 ms margin. Counter-matched report timestamps differed by approximately
127–136 ms between the Pis. All RF spacing and phase calculations use Nordic's
clock alone. The capture ran to a clean exit and covers the complete trial.

## Restored deployment and interpretation

The temporary service override was removed automatically. The normal daemon is
active/enabled at **460800**, MQTT is connected, and the switch is online with
its last reported state OFF/OFF. The persistent configuration, registry and MQTT
settings were preserved. The deployed host delay is again **200 ms**; this was a
test, not a permanent scheduling change. Firmware and host versions remain 0.4.

The 11/12 result provides evidence that the shortened query-pair gap at 460800
was contributing to rejection. It does not establish an exact acceptance
threshold or a complete fix. The earlier 115200 reference also used a different
MG21 board. A repeated interleaved spacing comparison would be needed to separate
the timing effect more confidently from changing RF conditions.

Evidence: [full request and packet analysis](sonoff-query-matched-spacing-20261024.json),
[Nordic capture](../../captured/development/sonoff-query-matched-spacing-20261024-nrf52840.pcapng),
[restoration verification](../../host/verification/deployment-sonoff-spacing-20261024.json),
and [preceding Sonoff run](sonoff-migration-20261024.md).
