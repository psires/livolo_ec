# TI 0.4 versus Sonoff: query timing and transport — 2026-10-24

TI reproduces the remaining state-query problem. At 460800 baud it passed a
6000-request UART integrity check, but only **18/24 read-only refreshes confirmed**.
The normal and extra-23-ms response-wait variants each confirmed **9/12**. Nordic
captured every query in the six timed-out requests. This resembles the Sonoff
failure pattern and does not identify an MG21-specific cause.

The recommendation is to move heartbeat and bounded transaction scheduling onto
the radio MCU, with volatile queues and hardware timing, while keeping all
authoritative persistent network state on the Pi. This reduces a demonstrated
host/UART timing dependency; it is not a claim that the switch's precise timing
rules are known or that scheduling alone will cure the timeouts. The
[proposed execution boundary and staged design](radio-transaction-scheduler.md)
are documented separately. No scheduler implementation or retry-policy change
was made in this experiment.

## Build, update and transport verification

The actual development module is **CC2652P, 352 KiB flash / 80 KiB SRAM**, despite
the initial P7 description. The shared application was built as **0.4**, with
signed updates and `UART_BAUD=460800`, using the pinned Contiki/TI SDK. It uses
61914 bytes text, 1120 data and 14596 BSS. Both targets share the gateway protocol
core; their platform/UART/radio drivers remain different.

The old 0.2 application initially did not answer at 115200; a GPIO reset recovered
it. That initial silence was not diagnosed. A signed application-only UART
transfer then installed 0.4, with coordinated host migration to 460800. The
resident **0.2 bootloader was preserved**, and the daemon confirmed the new trial.
Its final update status reports `trial=0` and matching candidate/current image
digests. No factory configuration or peer registry was reflashed.

The signed package SHA-256 is
`4ff851b26ec830a03cd7a7f80252387f27fd7ecef2ebccaf860a51ac0dcb49ce`.
Build identity and source hashes are in [the build manifest](ti-build-0.4-20261024.json).
Gateway-core tests passed **38/38**, signed-update tests **7/7**, and UART-harness
parser tests **4/4**. The target build completed successfully.

The UART-v3 hardware dry checks passed, including all 256 peer slots, ACK masks,
session guards, lease expiry, duplicate-request handling and overlong input
rejection. The first run exposed a harness bug: it required lifetime TX=0 even
though seven daemon heartbeats had already been transmitted. The test now checks
that TX and automatic-ACK counters remain unchanged. The original failure and
successful repeat are preserved. The two deliberate overlong lines explain the
final lifetime `input_errors=2`; neither arose during the integrity/query runs.

**6000/6000** alternating long CAPS replies and maximum-size CHECK requests then
passed in 104.7 seconds, with no mismatches, timeouts, transmissions or new
input/RX/UART-drop counters. This exercised single-attempt requests without
transport retries. TI does not expose separate UART RX overrun/framing/parity
or radio-drop counters; those fields are unavailable, not zero. Its UART writer
still masks interrupts while copying, so a passing bounded test is not proof
against all load-related failures. The MG21-specific UART overrun bug did not
reproduce in this test.

## Query comparison

Sonoff was explicitly disarmed before TI was armed. The Pi retained the same
network identity, switch `46715621`, registry, MQTT settings and host 0.4 source.
Nordic captured independently on Pi `10.1.1.120`, channel 11. No pairing or
output-setting commands were sent.

Twenty-four serialized refreshes interleaved ABBA timing variants, twelve each.
Both used the permanent 224 ms host pair delay, three rounds and the 14-second
scheduling deadline. Only the response wait alternated between 4.000 and 4.023 s.
Each subsequent request started three seconds after the preceding result. A
fresh matching native state report remained the sole confirmation criterion.

| Radio / response wait | Confirmed | Median RF pair spacing | Median RF round-start spacing |
| --- | ---: | ---: | ---: |
| TI / 4.000 s | **9/12** | 235.525 ms | 4247.012 ms |
| TI / 4.023 s | **9/12** | 235.568 ms | 4270.200 ms |
| Sonoff / 4.000 s, two previous runs | **16/24** | 237.454 ms | 4251.082 / 4250.882 ms |
| Sonoff / 4.023 s, two previous runs | **19/24** | 237.407 ms | 4274.143 / 4274.217 ms |

The older 115200 MG21 reference had 237.019 ms pair spacing and 4273.905 ms round
spacing. TI's pair median is within 1.480 ms of it, but its extra-wait round median
is still **3.705 ms shorter**. The harness label `round_matched` is inherited from
Sonoff; it must not be read as exact matching on TI. Identical host delays do not
produce identical on-air timing across targets. No further delay tuning was
performed during this board comparison.

Only six TI requests confirmed in the first round; twelve confirmed after
retries and six exhausted them. The normal-wait group had four first-round
successes and the extra-wait group two. This small sequential, single-switch
sample cannot establish equivalent reliability across boards or isolate a
23 ms scheduling effect. It gives no reason to make that extra wait permanent.

## What the captures show

Nordic captured **108/110** reported successful query transmissions. The two
unseen frames belong to successful requests 18 and 21. All 24 final TX metadata
records match captured bytes, all 18 fresh counters match native reports, and no
query follows the first captured confirming state report. Every confirmation
reported OFF/OFF, counters 69 through 86.

All **36/36 queries in timed-out requests** were captured, with correct identity,
suffix, payload and EC checksums:

- Requests 7, 8, 11 and 14 received `fa/10` but no `fa/ac` or state report.
- Requests 12 and 16 received no captured query reply.
- No late state report appeared before the next request, with **7.038–7.106 s**
  observed after each failed request's final query.

The phase trailers differ from measured heartbeat age by at most **0.796 ms**,
versus at most 0.407 ms in the two Sonoff runs. These are TX-entry phase stamps,
not hardware SFD timestamps. Valid encoding and Nordic reception do not prove
that the switch received or accepted a packet.

TI also shows timing variability that a fixed host sleep does not control. Four
of 53 captured query pairs exceeded 250 ms: **571.144, 366.177, 303.691 and
296.773 ms**. The first two occurred in failed requests and the latter two in
successful requests. Two previous Sonoff runs each had one pair over 250 ms,
with maxima 273.807 and 274.172 ms. No local heartbeat appears between the
members of these four slow TI pairs. Their per-attempt host/UART timing was not
recorded, so their individual causes remain unresolved; aggregate CCA/TX
counters cannot attribute them. They are not evidence of a strict receive
threshold by themselves.

During the query window, 23 heartbeat intervals ranged **10.017983–10.144293 s**,
median **10.037377 s**. They accumulated **1.004 s** beyond an exact ten-second
cadence. This resembles Sonoff's approximately 10.04 s cadence, and differs from
the stock EC capture's approximately 10.0007 s. The host currently schedules the
next heartbeat after UART work and checks it in a 50 ms maintenance loop. This
is a concrete reason to give the MCU an absolute periodic timer.

The TI session remained unchanged. RX gaps, reported UART/RX drops, new input
errors and ACK failures were zero; **86/86 automatic ACKs succeeded**. Aggregate
TX counts rose 222 attempts versus 220 successes; the two unsuccessful attempts
were not individually attributed. Unsupported/malformed RX increased 1113 and
must not be presented as a UART corruption count. Captured state ACK delays were
approximately 1.869–1.890 ms.

All RF interval/phase calculations use only Nordic timestamps. Cross-host report
matching uses a 250 ms window plus exact packet bytes and counters; observed
report offsets were 47.5–55.9 ms. The larger cross-host offset is not phase error.
The Nordic capture contains 4703 frames and covers the complete experiment.

## Final state and next step

**Sonoff is restored as the active gateway at 460800.** The daemon is active and
enabled, MQTT is connected and the switch is online. Its latest state report is
OFF/OFF, counter 86, from the TI trial. The exact pre-test configuration, registry
and MQTT settings were preserved. The temporary runner/systemd override was
removed; normal query timing remains 224 ms, 4.000 s, three rounds and 14 seconds.

TI remains installed with confirmed application **0.4 at 460800**, bootloader
**0.2**, and is explicitly **disarmed with ACKs disabled**. Do not open its UART
at the old application rate or arm both gateways with the same identity.

The next architectural step should be an asynchronous scheduled-send interface
with timestamps and bounded queues, followed by MCU-owned heartbeat cadence and
query transactions. Preserve session/lease cancellation, no replay on reconnect,
and fresh-report confirmation. Hardware scheduling should expose missed slots
and channel-busy outcomes rather than promise impossible exact sends. The Pi
continues to own authoritative persistent state. This qualification did not test
output-command reliability, multiple devices or long-term stability.

Evidence: [query and Nordic verification](ti-query-comparison-20261024.json),
[timeout/heartbeat details](ti-query-details-20261024.json),
[deployment, dry checks, UART integrity and restoration](../../host/verification/deployment-ti-comparison-20261024.json),
[Nordic capture](../../captured/development/ti-query-comparison-20261024-nrf52840.pcapng),
[previous Sonoff comparison](sonoff-query-round-spacing-20261024.md).
