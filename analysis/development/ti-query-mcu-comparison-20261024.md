# Complete TI MCU state queries — 2026-10-24

Firmware and daemon **0.7** now execute complete read-only query transactions on
TI: both opcodes, CCA backoff, round timing, deadline and fresh-report stopping.
The Pi retains authoritative network state and independently validates/persists
the returned native frame. The existing **three rounds, four CCA attempts per
opcode and 14-second deadline** are unchanged.

Nordic verifies that query timing no longer has the preceding host-loop outliers.
**21/24 refreshes confirmed**, versus **17/24** on 0.6. Three timeouts remain, so
this is a timing improvement and an encouraging small reliability sample, not a
complete protocol fix or long-term qualification.

## Implementation and deployed timing

One retained volatile query slot sits beside the eight one-shot jobs and the
heartbeat slot. `QUERY` accepts a validated opcode-10 template, peer output mask
and timing profile. Firmware generates ac and schedules all remaining work.
`QUERY_GET`, `QUERY_CANCEL` and `QUERY_DROP` expose progress, cancellation and
explicit result consumption. Session job IDs cannot be replayed. NETWORK, reset,
DISARM and lease expiry clear all work; no network records are written to flash.

Confirmation requires a 9e/05 report after acceptance and before the deadline,
with exact peer, subject, suffix, tag, destination, CRC and output-mask checks.
Mask zero, repeated counters and counter wrap remain valid for a fresh read
observation. A query ACK cannot confirm. The MCU retains the confirming raw frame
and callback metadata even if bulk UART logging drops it. The host validates it
again and prevents delayed older RX records from overwriting newer state.
There is no query nonce proving causal response, and callback time is not RF SFD
time for a frame already buffered in the driver.

The MCU uses **15434 ticks** between successful 10/ac TX entries (235.504 ms),
then **262930 ticks** after ac (4.011993 s). These explicitly replace the previous
224 ms host sleep plus UART overhead, matching the prior measured TI wire timing.
The temporary extra-23-ms variant uses 264438 ticks after ac. MCU ClockP wakes
process-context execution; this still uses the existing radio driver and does
not reserve RF-core SFD deadlines. Heartbeat priority and CCA policy are retained.

No host polling operation schedules a transmission. Queries stop before every
further attempt once confirmed, including CCA retries. Uncertain radio errors
terminate without automatic replay. The host's command worker remains serialized;
multiple simultaneous queries and output transactions on the MCU are future work.
Older TI/MG21 capabilities retain the previous host query path.
See the [UART contract](../../firmware/gateway/UART_PROTOCOL.md#complete-ti-state-query-transactions--release-07).

## Qualification

- **68 firmware and 109 host tests passed.** Coverage includes six-send/three-round
  exhaustion, four-attempt CCA backoff, fresh-report suppression of the next
  opcode/retry, invalid identity/suffix/CRC/mask rejection, repeated counters,
  wrap, cancellation, result retention and host recovery without a bulk RX event.
- All **6000 UART integrity requests** passed with no mismatch, timeout or new
  reported input/RX/UART-drop counters.
- One complete transaction ran during **14.5 seconds without UART reads/writes**.
  Another ran during **880 continuous CAPS requests** over 14.5 seconds. Both
  exhausted exactly six sends/three rounds, with no fresh report. Nordic captured
  all twelve transmissions. These establish autonomous timing and bounded
  completion, not successful confirmation during those two windows.
- MCU dispatch lateness in both hardware windows was at most ten ticks,
  **0.153 ms**. The later 24-query run reached **0.519 ms** maximum.
- The initial hardware wrapper selected the old queue-test executable and exited
  before query RF work. The controller restored the daemon. Only the wrapper was
  corrected; the passed UART checks were retained and the query qualification
  was rerun successfully. No additional firmware flash was needed.

The production signed UART updater installed and confirmed 0.7, preserving
bootloader **0.2** and application baud **460800**. The verified board is CC2652P,
352 KiB flash / 80 KiB SRAM. Text/data/BSS are **66915 / 1120 / 16624 bytes**;
the transaction adds 288 bytes BSS over 0.6. Stack/runtime allocations are
additional. Package SHA-256:
`9b0e13ee019f2b36c2a660fae3b823430bd12ce93ddde809ce5deefe1bc9c8ce`.

## Nordic comparison with 0.6

The same TI, switch and separate Nordic Pi (`10.1.1.120`) were used on channel 11.
Both runs interleaved 24 requests in six ABBA blocks: twelve normal waits and
twelve with an extra 23 ms. Retry limits and the intended wire intervals were
preserved. Neither run sent output-setting commands.

| Measurement | 0.6 host query loop | 0.7 MCU transaction |
| --- | ---: | ---: |
| Fresh confirmations | 17/24 | **21/24** |
| Normal-wait confirmations | 7/12 | **11/12** |
| Extra-wait confirmations | 10/12 | **10/12** |
| Query-pair RF median | 235.565 ms | **235.653 ms** |
| Query-pair RF range | 234.963–703.000 ms | **235.602–235.686 ms** |
| Pairs exceeding 250 ms | 3 | **0** |
| Normal round-start median | 4247.615 ms | **4248.093 ms** |
| Extra-wait round-start median | 4270.288 ms | **4271.111 ms** |
| Captured absolute phase-error maximum | 0.797 ms | 0.817 ms |

Pair spacing spread is now about **84 microseconds**. Its median differs from
the previous run by only 0.088 ms. Normal/extra round medians remain within
0.479/0.824 ms of their earlier values. Normal round intervals were
4248.060–4248.528 ms; extra-wait intervals were 4271.042–4271.167 ms. The inherited
`round_matched` label still does not imply exact equality to the older MG21/115200
reference, whose median was 4273.905 ms.

There were **94 dispatch attempts, 94 successful query sends and zero query CCA
retries**. Nordic captured **93/94** queries, including all **18 queries in the
three timeouts**. The missing frame was request 22's final ac; its native `fa/ac`
and confirming state report were captured. Consequently 23/24 final TX metadata
records match a captured query, not 24/24.

All **21 retained confirming frames** matched Nordic, with state counters
**104–124**, all OFF/OFF. No captured query followed the first matching state
report, and no transaction added attempts beyond the original limits. All
relevant decoded EC checksums were valid. The complete capture has 4557 frames,
including hardware qualification and service restoration.

Requests **2, 8 and 23** timed out. Each received `fa/10` but no `fa/ac` or native
state report. No late state followed before the next query, providing
**7.107–7.153 seconds** of observation after the final attempt. Their complete
on-air query coverage and accurate phase stamps leave an RF/protocol response
issue unresolved; these failures are not explained by missing host sends.

Heartbeat median remained **10.000807 seconds**. One MCU-reported heartbeat CCA
retry corresponds to intervals **10.026059 and 9.975565 seconds** around counters
180/190/200: the delayed slot was followed by a return to the fixed epoch, with
no missed slot or catch-up burst. Eighteen measured intervals accumulated only
0.014543 seconds above nominal. This is Nordic-relative timing, not oscillator
calibration or an absolute SFD guarantee.

One session was retained. Reported input-error, RX/UART-drop, RX-gap and ACK-failure
deltas were zero; native ACK counters were **100/100**. General TX counters were
214 attempts / 213 successes, consistent with the observed heartbeat retry;
query transaction counters independently show zero query CCA retries. General
STATUS and scheduler snapshots are sampled at different times. The combined
unsupported/malformed RX counter increased by 1017. TI still lacks separate
hardware UART overrun/framing/parity and radio-drop counters.

## Final state and interpretation

TI and the daemon remain on **0.7 at 460800**. The service is active/enabled, MQTT
is connected and switch `46715621` is online with latest fresh state **OFF/OFF,
counter 124**. Configuration, registered devices and MQTT settings match the
pre-test backup. The temporary timing override is removed. The normal MCU
profile is active, with unchanged retry limits; Sonoff remains explicitly disarmed.

Moving execution onto the MCU removed the measured host/UART spacing outliers
while retaining early stopping and bounded outcomes. The 21/24 result is better
than 17/24, but small sequential samples cannot establish a durable reliability
gain or the proprietary receive rule. The extra wait supplied no benefit in this
run, so the normal profile is retained. Multi-device load, long-term operation,
hardware RX diagnostics and RF-core timestamp/scheduling work remain unqualified.

Evidence: [per-query verification](ti-query-mcu-comparison-20261024.json),
[retained transactions and autonomous windows](ti-query-mcu-details-20261024.json),
[deployment and build evidence](../../host/verification/deployment-ti-query-mcu-20261024.json),
[Nordic capture](../../captured/development/ti-query-mcu-comparison-20261024-nrf52840.pcapng),
and [0.6 baseline](ti-scheduler-comparison-20261024.md).
Cross-host event matching uses exact frames/counters and a 250 ms margin;
report-clock offsets were 25.1–35.8 ms. RF spacing uses Nordic's clock alone.
Private backups and detailed logs remain in
`/home/sfires/livolo-ec/ti-query-mcu-20261024`; original sniffer files are also
preserved under `/home/sfires/livolo-sniffer/ti-query-mcu-20261024` on the Nordic Pi.
