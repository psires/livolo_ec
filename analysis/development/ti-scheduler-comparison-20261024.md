# TI timed queues and heartbeats — 2026-10-24

Application and host **0.6** are deployed on the CC2652P at **460800 baud**.
The signed UART updater confirmed the application; bootloader **0.2** is retained.
The Pi owns all authoritative network state. The MCU holds only volatile jobs,
peer/ACK caches and a heartbeat template under the existing authorization lease.

The scheduler makes heartbeat cadence substantially tighter and executes queued
frames during host silence. It does **not** resolve state-query timeouts in this
bounded experiment: **17/24 confirmed**, compared with **18/24** on TI 0.4.

## Implemented boundary

Eight RAM slots accept one-shot, absolute-deadline sends and retain pending or
terminal results until explicitly drained. Job identities cannot be replayed
within a session. Queue-full, cancellation, lateness and transmit results are
explicit. NETWORK, reset, DISARM or lease expiry flushes pending work. Queued
frames are restricted to self-addressed diagnostics and phase-stamped read-only
`10/ac` queries; queued output commands are rejected.

A separate heartbeat slot uses an absolute ten-second epoch. A TI ClockP timer
wakes the Contiki process, which calls the existing radio driver. This is **MCU
process scheduling, not RF-core absolute SFD reservation**. CCA and driver delay
still follow dispatch. Missed slots are skipped without catch-up bursts. Definite
CCA busy preserves four total attempts and 25/50/75 ms backoff; retries do not
shift the periodic epoch. MAC sequence allocation is shared on the MCU while it
owns heartbeats. Native ACK handling remains ahead of UART logging.

The daemon negotiates the capability, installs the heartbeat template, verifies
its first phase anchor and monitors ownership. Its query loop still uses
`SEND_PHASE`; the timed queue API is available but complete query transactions
and native-response matching are a later stage. Query freshness, early stopping,
three-round limit, 14-second deadline and four CCA attempts per send remain on
the Pi and unchanged. MG21 retains the capability-based host-heartbeat fallback.
See the [UART contract](../../firmware/gateway/UART_PROTOCOL.md#ti-timed-queues-and-heartbeat-ownership--release-06).

## Hardware qualification

- All **6000 UART integrity requests** passed with no mismatch, timeout or new
  reported input/drop counters. TI still lacks separate hardware UART
  overrun/framing/parity counters.
- All eight queue slots were filled; another job was rejected. Pending results
  could not be drained. Cancelling job 4 retained its terminal outcome.
- During **12 seconds without UART reads or writes**, seven queued self-addressed
  probes transmitted and results remained available. Nordic captured each once,
  matching the returned frame bytes. Job 4 was absent.
- MCU-reported dispatch lateness was **0.076–0.153 ms**. The five ordinary
  approximately 200 ms RF intervals were **199.991–200.083 ms**; the gap spanning
  cancelled job 4 was **400.007 ms**, against a 400.009 ms target difference.
- During a further **12 seconds of continuous UART load**, all **768 CAPS
  requests** succeeded. The three test heartbeats carried counters 1000, 1010,
  1020, with RF intervals **10.000801 and 10.000855 seconds** across host silence
  and UART load. No heartbeat slot was missed.
- A job due after a one-second lease expired did not transmit. TX count stayed
  unchanged, authority was cleared, and the job disappeared on rearm. Nordic
  also captured no frame bearing that job's identifier.

The deterministic C/parser suite passed **63 tests**, including queue ordering,
wrap, cancellation, retained results, session/lease invalidation, skipped slots
and the four-attempt heartbeat CCA policy. All **106 host tests** passed; version
consistency tests were also rerun after the final 0.6 metadata change. The signed
image uses 64963 bytes text, 1120 data and 16336 BSS on the verified 352 KiB flash /
80 KiB SRAM CC2652P. Stack/runtime allocations are additional to BSS.

## Nordic comparison

The same independent Nordic on `10.1.1.120` recorded channel 11. Both TI trials
used the same switch, 460800 baud, a 224 ms host query-pair delay, three rounds
and a 14-second deadline. Each interleaved 24 requests in six ABBA blocks:
normal 4.000 s response wait versus temporary 4.023 s, twelve of each. Neither
trial sent output-setting commands.

| Measurement | TI 0.4, host heartbeat | TI 0.6, MCU heartbeat |
| --- | ---: | ---: |
| Heartbeat RF interval median | 10.037377 s | **10.000805 s** |
| Heartbeat RF interval range | 10.017983–10.144293 s | **10.000772–10.000854 s** |
| Intervals measured | 23 | 25 |
| Accumulated excess above nominal cadence | 1.003719 s | **0.020178 s** |
| Fresh state confirmations | 18/24 | 17/24 |
| Normal wait confirmations | 9/12 | 7/12 |
| Extra 23 ms wait confirmations | 9/12 | 10/12 |
| Query-pair RF median | 235.540 ms | 235.565 ms |
| Query-pair RF maximum | 571.144 ms | 703.000 ms |
| Captured query phase error, absolute maximum | 0.796 ms | 0.797 ms |

The new heartbeat interval spread is about **82 microseconds** in this window.
Its roughly 0.8 ms systematic excess per ten seconds is measured on Nordic's
clock; no oscillator calibration was performed, so it is not an absolute clock
accuracy measurement. Scheduler counters show 27 successful heartbeats including
warm-up, zero missed slots and zero CCA retries during the query trial. The
largest recorded heartbeat dispatch lateness was ten MCU ticks, about 0.153 ms.

Query timing remains host-driven. Three pairs exceeded 250 ms: **436.493,
352.704 and 703.000 ms**, all in requests that eventually confirmed. Thus these
outliers are not sufficient to explain failure, and fixed heartbeat scheduling
does not remove them. Round-start medians were **4247.615 ms** with the normal
wait and **4270.288 ms** with the extra wait. The latter remains 3.617 ms shorter
than the older MG21/115200 reference; the inherited `round_matched` experiment
label does not mean an exact cross-board timing match.

Nordic captured **115/116** reported queries and all **42 queries in the seven
timeouts**. The missing frame belonged to successful request 15. Every final
TX metadata record matched capture bytes, and all 17 fresh state counters
**87–103** matched OFF/OFF reports. No query followed a fresh confirmation.
All relevant decoded EC checksums were valid. The complete capture contains
5764 frames, including qualification and service restoration.

Timeouts were requests **1, 7, 8, 9, 11, 12 and 24**. Every one received `fa/10`
but no `fa/ac` or native state report. None had a late state report before the
next query: the first six had 7.041–7.306 seconds of observation after their last
attempt; the final request had at least 4.020 seconds through trial completion.
All 24 requests completed within the existing bounded policy.

One radio session was retained throughout the query run. Reported RX/UART-drop,
input-error, RX-gap and ACK-failure deltas were zero; all **228/228 TX attempts**
and **85/85 native ACK attempts** succeeded at the driver level. The combined
unsupported/malformed RX count increased by 1458; this is not an all-errors-zero
claim. TI hardware UART error and radio-drop counters remain unavailable.

## Interpretation and final deployment

The captures verify that MCU scheduling removes Linux/UART cadence drift from
heartbeats and supports bounded queued execution while the host is silent.
They do not establish improved query reliability. These are small sequential
single-device samples; heartbeat ownership, shared MAC sequence allocation and
scheduler monitoring changed together. The 10/12 versus 7/12 wait comparison
also does not justify changing the permanent retry policy, especially given the
earlier trials' inconsistent result.

The next useful stage is one bounded MCU query transaction with explicit
fresh-report cancellation and retained terminal results. That would control
query-pair and round timing independently of UART while preserving the existing
limits. RF-core scheduling/timestamps and hardware UART diagnostics remain
separate improvements. A strict proprietary receive window is still unproven.

TI **0.6** remains active at **460800**. The daemon is active/enabled, MQTT is
connected, and switch `46715621` is online with its latest fresh state **OFF/OFF,
counter 103**. Registry and MQTT values match the pre-test backup. Configuration
changed only from Sonoff's serial port to `/dev/ttyAMA0`; normal timing is restored
to **224 ms / 4.000 s / three rounds / 14 seconds**, with no runtime override.
Sonoff is explicitly verified disarmed. No output commands were sent.

Evidence: [per-query capture verification](ti-scheduler-comparison-20261024.json),
[queue/cadence details](ti-scheduler-details-20261024.json),
[deployment, build and hardware checks](../../host/verification/deployment-ti-scheduler-20261024.json),
[Nordic capture](../../captured/development/ti-scheduler-comparison-20261024-nrf52840.pcapng),
and [earlier TI baseline](ti-query-comparison-20261024.md).
Cross-host event matching uses a 250 ms margin and exact frame/counter matches;
observed report-clock offsets were 33.5–42.0 ms. All RF timing above uses Nordic
timestamps alone. Private backups and detailed logs remain under
`/home/sfires/livolo-ec/ti-scheduler-20261024`; capture originals also remain on
the Nordic Pi under `/home/sfires/livolo-sniffer/ti-scheduler-20261024`.
