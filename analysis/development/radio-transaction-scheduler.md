# Volatile radio scheduler and remaining transaction stages

Status: TI release **0.7** implements volatile timed jobs, absolute heartbeat
cadence and a complete bounded read-only query transaction. RF-core absolute SFD
scheduling, multiple concurrent query transactions and output jobs remain future
stages. The Pi retains all authoritative persistent network state.

The [UART contract](../../firmware/gateway/UART_PROTOCOL.md#complete-ti-state-query-transactions--release-07)
defines eight one-shot slots, a separate heartbeat slot and one retained query
transaction. TI ClockP wakes Contiki at deadlines; radio work still uses the
existing blocking driver. Queries now own both opcodes, CCA retries, round timing
and exact-peer fresh-report stopping. The host independently validates the
retained frame and persists state. See the [0.7 comparison](ti-query-mcu-comparison-20261024.md)
and [0.6 heartbeat-only comparison](ti-scheduler-comparison-20261024.md).
The design below includes remaining stages; output transactions and hardware SFD
reservation are not implemented yet.

## Recommendation

Move time-sensitive execution onto the radio MCU in stages: scheduled sends,
periodic heartbeats, then bounded query transactions. Keep admission, pairing
policy, device registry, network/routing decisions, desired state, persistence
and MQTT/Home Assistant on the Pi. A radio transaction engine with volatile
queues is sufficient for this step; implementing mesh routing would need
separate evidence about the proprietary routing protocol.

The current adapter already delegates modulation, demodulation and frame checks
to radio hardware, and performs native ACKs and phase stamping on the MCU. The
change is to move scheduling and bounded transaction execution out of individual
UART request/response exchanges. It is not a replacement of a software-defined
PHY.

This is worthwhile even before the proprietary timing rules are completely
understood. Today the host's sleep starts after a UART reply, so a baud change
changes the actual radio interval. Heartbeat scheduling also includes Linux
scheduling and UART round trips. Removing those dependencies makes protocol
experiments more controlled and operation less sensitive to host load. It does
not prove that tighter timing alone will eliminate query timeouts: captures show
valid queries reaching the air without a subsequent state report, but do not
expose the switch's rejection reason or establish a universal receive window.
The stock pairing capture places queries about 0.600, 0.800 and 1.000 seconds
after a heartbeat, but an older capture separates `10` and `ac` by 2.201 seconds.
A hard-coded universal 200 ms query requirement would exceed the evidence. See
the [stock comparison](../protocol.md).

## Execution boundary

| Pi daemon | Radio MCU, RAM only |
| --- | --- |
| Authoritative network identity and peer registry | Installed session and bounded peer/ACK cache |
| Pairing/admission and routing policy | Explicitly authorized frame templates and ACK rules |
| Operation intent, desired state, persistence | Bounded jobs, retry counts and deadlines |
| MQTT, UI, recovery and firmware distribution | Stable heartbeat cadence and radio scheduling |
| Interpret and persist terminal results | Timestamped receive events and matched transaction results |

The host can submit a query job and drain events without staying in the timing
path for each attempt. Only a fresh matching native state report confirms a
query; UART acceptance, transmit completion and native query ACKs remain distinct
outcomes. Firmware must retain the daemon's current freshness and identity
checks, including what counts as a fresh report after a counter wrap or restart.

A job should carry a session generation, operation ID, peer, operation kind,
not-before time, deadline, attempt limit and explicit timing profile. Use MCU
clock units with an advertised frequency and a bounded future horizon; compare
wrapping timestamps safely. Clock mapping to the host is for planning and
observability, not a promise that two machines share a precise wall clock.

Return ACCEPTED/queue-full immediately. Emit separate TX-complete, native-reply
and terminal events. Operation IDs must have documented deduplication semantics
so a UART retry cannot create a second physical transaction. A session change,
reset, DISARM or lease expiry cancels queued work. A reconnected host must not
replay an uncertain output command automatically. Firmware reset restores no
network authority from flash.

## Timing and queue design

Use hardware timers, radio command scheduling and interrupt/DMA-driven UART;
a CPU busy loop is unnecessary. Reserve time for automatic ACKs and heartbeats,
then schedule transaction work fairly across peers. Report conflicts explicitly:
a busy channel or receive operation can prevent an exact transmission. A job
with a missed deadline must fail or follow its specified deferral rule, never
silently execute an old output command late.

Generate periodic heartbeat targets from an absolute epoch (`epoch + n * period`),
rather than adding ten seconds after a host round trip. Skip/report missed slots
instead of sending a burst to catch up. The phase anchor generation and encoded
query phase must correspond to the heartbeat actually transmitted. Finalize
phase data as close to the scheduled radio operation as the driver allows;
measure CCA delay and the actual transmit timestamp. A scheduled start trigger
is not necessarily the RF start-of-frame delimiter.

Keep separate bounded queues for jobs, RX frames and UART events. Reserve space
for terminal results and control replies. Bulk diagnostics may be dropped with
explicit counters; congestion must not block ACKs or the timing engine. Advertise
queue credits/capacities and provide queue-depth, overflow, late-send, CCA-busy,
retry and cancellation counters. Expose planned and actual transmit timestamps.

Do not put an unrestricted autonomous command generator on the MCU. Use bounded,
versioned profiles for the operations we have observed, initially queries only.
Preserve the existing session/lease and permitted-frame validation. Keep legacy
SEND during migration and negotiate capabilities instead of inferring them from
the board model.

## Hardware feasibility and current limitations

The pinned TI driver already uses `RF_scheduleCmd`, but the current transmit path
allows arbitrary scheduling delay and waits for completion. The SDK's
`CMD_IEEE_TX` structure has `startTime`, `startTrigger` and a transmitted-frame
`timeStamp`. A proper implementation needs a board-driver interface exposing
those facilities and their timing semantics, rather than merely putting a FIFO
in front of today's blocking `NETSTACK_RADIO.send`.

TI documents absolute-time radio triggers as a way to use one radio clock for
scheduled operations. Its radio timer runs at four ticks per microsecond; the
current application's phase clock is only 65536 Hz. Those are distinct clocks
and need an explicit mapping and wrap handling.
[TI radio operation triggers](https://dev.ti.com/tirex/content/simplelink_cc13xx_cc26xx_sdk_8_32_00_07/docs/proprietary-rf/proprietary-rf-users-guide/rf-core/radio-operation-commands.html),
[IEEE TX command definition](https://software-dl.ti.com/simplelink/esd/simplelink_cc13xx_cc26xx_sdk/8.30.00.121/exports/docs/rflib/html/cc13x2__cc26x2_2driverlib_2rf__ieee__cmd_8h_source.html),
[TI radio timer conversion](https://dev.ti.com/tirex/content/simplelink_cc13xx_cc26xx_sdk_8_32_00_07/docs/rflib/html/_r_f_c_c26_x2_8h_source.html).

MG21 RAIL also supports scheduled transmission, including absolute targets and
an explicit policy for a scheduled TX that meets an ongoing reception. Its
existing immediate-send wrapper would need to expose completion asynchronously.
The installed SDK uses RAIL 2 naming; the current documentation includes that
compatibility API. Neither target needs a new PHY for this design.
[Silicon Labs scheduled TX configuration](https://docs.silabs.com/rail/3.1.2/rail-api/rail-schedule-tx-config-t).

TI's current UART writer masks interrupts while copying into its ring and lacks
separate hardware RX overrun/framing/parity counters. The MG21 writer was already
changed to publish staged data without that long masked copy. Even if a bounded
TI test passes, a scheduler implementation should keep UART critical sections
short and add equivalent receive diagnostics before claiming load independence.

The verified development TI module is CC2652P with 352 KiB flash and 80 KiB SRAM,
not CC2652P7. The 0.7 update-enabled build uses 66915 bytes text, 1120 data and
16624 BSS, including the queue. Queue sizes must be budgeted alongside stack, SDK/runtime allocations
and the signed-update layout; free RAM is not simply the difference from BSS.

## Incremental validation

1. Add hardware RX/TX timestamp reporting, asynchronous one-shot scheduling,
   queue credits and missed-deadline diagnostics. Verify actual RF timing against
   Nordic, including CCA contention, timer wrap and cancellation.
2. Move the ten-second heartbeat to an MCU timer while retaining the existing
   authorization lease. Compare cadence under host CPU load and UART saturation.
3. The bounded query transaction and fresh-report stopping rule are implemented
   in 0.7 with unchanged attempt limits. Further qualification should repeat both baud rates with the same measured
   radio schedule; vary protocol timing one parameter at a time.
4. Only after query validation, add output transactions with deduplication,
   restart/session cancellation and uncertain-outcome tests. Preserve the rule
   that a native matching state report establishes confirmation.

Acceptance should include RF timing distributions, successful fresh reports,
complete terminal outcomes, bounded memory/queue overflow, no output replay after
reconnect, and correct loss-of-host behavior. A faster UART alone cannot provide
these guarantees. This proposal does not change the deployed retry policy or
claim that the proprietary network is fully understood.
