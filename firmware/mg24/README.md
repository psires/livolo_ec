# Experimental XIAO MG24

Status: experimental transport and bounded raw-radio diagnostics, not a supported
gateway or updater target. The application boots with RF off. An explicit
`RFSTART` opens a 60-second channel-26 synthetic test window; see the
[bench contract](../../docs/XIAO_RF_BENCH.md). `RFLISTEN` opens only a 60-second
channel-11 receive window, with automatic ACKs disabled and transmission rejected
by the core and adapter. It shares the bounded
[experimental parser](../experimental/README.md). Production protocol, peer,
phase and scheduler support remain subsequent work.

The inspected board exposes Seeed's SAMD11 CMSIS-DAP and CDC bridge. Its USB
product includes `(Sense)`, which does not independently establish whether the
physical board has the Sense sensors or its PCB revision. The build selects
EFR32MG24B220F1536IM48; observed device info indicates the MG24 family, 1536 KiB
main flash and 256 KiB SRAM. Stock vectors and application properties identify
a loader at `0x08000000` and application at `0x08006000`. This is not the MG21
layout. The experimental linker reserves the first 24 KiB and limits the app to
176 KiB. Calibration/security/bridge firmware is outside the installation scope.

USART0 uses PA8 TX/PA9 RX at 115200 8N1, through the onboard bridge. **DTR must
be asserted** for this unit to forward serial bytes; RTS remains deasserted.
RF builds select the board's 39 MHz HFXO with CTUNE 95 before initializing UART.
PB4 low/PB5 high selects and enables the onboard antenna. UART RX is interrupt
buffered; parsing runs in the main loop. Held RAIL RX packets are copied
and released in bounded main-loop batches, retaining original FCS bytes.
This transport is not yet qualified under sustained radio load.

The `xiao-uart-1` candidate services TX from a bounded interrupt using the
existing 640-byte output buffer. Each invocation copies at most eight ready
hardware slots; the IRQ is configured at the lowest NVIC priority. The main
loop continues radio polling and lease expiry while output is pending, and
alternates command replies and RF records under competing load. The buffer
cannot be reused until all bytes have been copied into UART hardware. Baud,
bridge firmware and the seven-slot receive ring remain unchanged. Native timing
fixtures reproduce a burst overflow in the previous polling loop and avoid it
with the candidate, but their service costs are simulated, not hardware latency
measurements. Target qualification remains separate.

This candidate also advertises `adapter_diag=handoff_v1`, exposing receive,
hold, copy, release and core-forward counters through idle-only `RFADAPTER`.
See the [adapter contract](../../docs/XIAO_ADAPTER_DIAGNOSTICS.md). It preserves
the health and queue diagnostics introduced below.

The bounded target run completed with coherent transport and handoff accounting,
but passive reception still lost 35 records to queue overflow in four of twelve
windows. Native and synthetic RF capture discrepancies also remain. This is an
experimental diagnostic baseline, not a qualified gateway; see the
[UART/handoff verification](../../docs/verification/xiao-mg24-service-20261108.json).

The `xiao-queue-1` diagnostic build preserves read-only `RFHEALTH` with separate
32-bit modulo occurrence counts for failed packet holds, FIFO overflow, frame
errors, invalid packet status/length, details lookup failure, invalid packet
layout and calibration failure. Counts clear while inactive before each adapter
start attempt (`scope=last_start_attempt`), including failed attempts; STOP and
health reads preserve them. This instant precedes the shared core's successful
session counter reset. Simultaneous FIFO/error flags count both reasons but keep
the existing single aggregate drop, and one frame may produce multiple hardware
events. These are diagnostic occurrences, not an exact lost-packet denominator.
The first failed packet predicate determines its adapter reason; release order,
queue sizes, RX acceptance and aggregate drop policy are unchanged.

`CAPS queue_diag=ring_v1` additionally exposes the shared core's `RFQUEUE`
snapshot after STOP and complete event drain. It separates portable queue-full
drops from the adapter reasons above and records producer-observed occupancy
and adapter-timestamp-to-format age. These counters reset at setup and on a
successful shared-core start. The UART remains at 115200 baud with seven usable
receive slots; this build does not increase throughput or hide burst losses.
See the [queue diagnostics contract](../../docs/XIAO_QUEUE_DIAGNOSTICS.md).

Two 30-second passive windows received valid native reports from both the known
one-gang and two-gang switches. The initial capture had 58 aggregate drops and
therefore failed the strict bench integrity check. A single instrumented recheck
received 448 accepted frames, all delivered with independently valid FCS, and
classified all 33 aggregate drops as RAIL frame-error events. All other reason
counters were zero. RAIL frame errors can include CRC, block-decoding or illegal
length errors; this does not establish their RF cause or attribute the earlier
58 events. The recheck remains a strict failure, with no acceptance relaxation.
See the [passive reception receipt](../../docs/verification/xiao-rx-20261108.json).

For separately reviewed rejected-frame classification, build in a new directory
with `REJECT_DIAGNOSTICS=1` (requires `RF_DIAGNOSTICS=1`). This private
`xiao-reject-1` variant advertises `radio_tx=0`, `rf_diag=none` and
`reject_diag=untrusted_v1`. It starts in inactive channel-11 passive mode;
RFSTART/RFTX are fenced before adapter effects and the transmit path is compiled
out. It enables RAIL's ignore-CRC option only to retain `READY_CRC_ERROR` evidence.
Such packets increment aggregate drops and never enter normal CRC-valid RX events.
Other frame-error events retain their separate diagnostic counter. The ordinary
build keeps its CRC policy and exact baseline image.

The private build retains the first eight untrusted records, each limited to
128 bytes including PHR and any received FCS. `RFREJECT 0` through `RFREJECT 7`
read immutable records only while fully inactive, drained and unfaulted.
Metadata includes RAIL status, reported/stored lengths, truncation and details
availability. Packet lengths include PHR and any received FCS bytes. RSSI is in
dBm, LQI is SDK-provided, and ticks are poll-time microseconds modulo 2^32.
`layout_complete=1` means only consistent
PHR/length bounds; `fcs=original` identifies the retained final two bytes, not
their validity. Bad-frame source addresses are not trusted identities. Compare
software radio CRC and application checksum separately, preserving incomplete
or unavailable details. RAIL's ready-CRC-error status alone is not proof of a
complete ambient frame.

Additional health counters `reject_seen`, `reject_stored`, `reject_omitted`,
`reject_truncated` and `reject_invalid` saturate at `UINT32_MAX`; stored is capped
at eight. They clear only while inactive before a start attempt. Reads and STOP
do not drain/reset evidence. Multiple events or first-eight sampling cannot
provide an RF loss denominator. The reviewed procedure is one bounded passive
capture, stopped before at most eight reads, followed by restoration of the exact
approved baseline and full-flash verification. Restore the union of all touched
erase pages from that unit's backup, including any diagnostic-only tail pages.
This private image is not a release bundle or a new acceptance policy.

### Private event timeline

`TRACE_DIAGNOSTICS=1` selects the separate `xiao-trace-1` receive-only build.
It requires RF diagnostics and cannot be combined with rejected-frame sampling.
The normal CRC policy, PHY, portable queue and UART scheduling remain in use;
additional sync/filter/abort callbacks and bounded RAM stores add diagnostic
load. `RFSTART` and `RFTX` are refused before core side effects, and the adapter
also refuses TX and channels other than 11. This is experimental instrumentation,
not a firmware release or supported gateway image.

After verified STOP and complete drain, `RFTRACE` returns storage accounting.
`RFTRACE EVENT index`, `RFTRACE ANCHOR index`, and `RFTRACE MARK index` read
individual records without consuming them. Capacities are 4096 callback records,
512 accepted-packet anchors and 128 lifecycle/calibration markers. Counters
saturate; no buffer overwrites old evidence. A callback record contains the full
merged 64-bit SDK mask, not an assumed single event or packet. Nested callback
entry or a failed single weak-CAS reservation sets `reentry=1` and refuses the
trace write without delaying the original radio handler; this invalidates
completeness claims for the epoch.

All timestamps use the RAIL microsecond clock modulo 2^32. Callback timestamps,
legacy main-loop poll timestamps and SDK packet timestamps are distinct fields.
Anchors retain the full MAC and original FCS before portable queue admission.
The raw SDK position is retained, and frame-end conversion uses an initialized
copy with the derived PHR+MAC+FCS byte count. Conversion failure remains explicit
and neither discards accepted RX nor substitutes a poll timestamp. `end_status`
is `UINT32_MAX` when conversion was not attempted because raw time was invalid.
The epoch increments modulo 2^32 on start attempts and must be paired with a
host-verified boot/session identity. `callback_max_us` covers the measured body
from entry through original handling, excluding final accounting; it is neither
isolated added overhead nor a worst-case execution bound.

Callback and marker recording freezes after synchronous STOP. Anchors may
still be appended during the normal held-packet drain. A collector must verify
stable stopped/drained counters and identical summaries before and after the
entire dump. Omitted records, invalid timestamps, reentry or unstable snapshots
prevent completeness claims. Shared packet bytes and nearby anonymous error
events support only candidate temporal associations, not proved identities of
failed transmissions. A reviewed bounded trial must restore the exact normal
application pages and verify the complete flash afterward.

A single reviewed 30-second passive trial retained all 855 callback records and
268 accepted-frame anchors without trace or portable-queue overflow. MG24 had
14 frame-error events and four early receive aborts; the original strict capture
assessment remains failed. A fit using 20 unique shared packets passed the
preregistered gates, with a maximum held-out residual of 2.84 microseconds.
Six of seven interior C6-only forwarded observations were inside the fitted
interval. Four overlapped anonymous MG24 frame-error episodes, one overlapped an
early abort, and one overlapped a different accepted, FCS-valid MG24 frame by
about 769 microseconds. This last case supports competing transmissions as one
cause of receiver disagreement; it does not identify every anonymous error or
establish a general loss rate. Diagnostic callback changes remain an observer
effect. The exact pre-trial normal MG24 full flash was restored and verified;
C6 was not reprogrammed or reset. Both radios ended inactive and closed.
See the [event-correlation receipt](../../docs/verification/xiao-rx-correlation-20261108.json).

Build with a separate Gecko SDK 4.4.6 checkout, commit
`59d2160fe448b13730a91c0f019a8b4c53c43eb2`, including Device/EFR32MG24,
CMSIS, emlib, common, bootloader and RAIL/PA inputs:

```sh
make -C firmware/mg24 GSDK=/absolute/path/to/gecko-sdk BUILD=/tmp/ec-mg24-build
```

The bring-up used Arm GNU 14.2.1. Device startup, system code, emlib, CMSIS and
application-property headers retain their upstream licence terms; the adapted
linker retains its notice. RF builds link the GSDK's xg24 release RAIL archive;
hydrate its LFS object and verify its hash against the pinned pointer before
building. `RF_DIAGNOSTICS=0` builds the transport-only variant in a separate directory.
This source/build result is not a redistribution approval or release bundle.

Recovery uses the independently powered SAMD11 CMSIS-DAP, an exact probe serial,
pyOCD 0.46.1 and SiliconLabs GeckoPlatform_EFR32MG24_DFP 2025.12.1. Use
`auto_unlock=false`; no chip erase/unlock/security provisioning. Initial factory
backup required a halted CPU, followed by resume. pyOCD reports AP discovery
warnings on this unit; successful main-flash readback is checked separately.

Private bring-up programs the **BIN only at `0x08006000` with sector erase**,
after preserving original flash. Restore only the touched original 8 KiB pages
from that unit's backup and verify them before reinstalling. Do not infer UART
Gecko updates or factory restoration from mere debugger enumeration. The exact
hardware outcomes and artifact hashes belong to the bring-up verification record;
no automatic update, power-loss recovery or production radio qualification is
claimed. The bounded raw-radio results are recorded in the
[RF verification receipt](../../docs/verification/xiao-rf-20261108.json).

The initial cross-capture passed 20 frames in each direction. After the USB cable
was replaced, a separate four-frame-per-direction CCA/ACK-request check passed.
All 24 synthetic frames received by MG24 retained FCS bytes that passed an
independent software CRC check. No classic ACK was observed; capture blind spots
and addressed-unicast ACK behavior remain unqualified. Both runs ended with RF off.

Initial transport-only bring-up preservation caveat: that stage's final full
readback differs from its initial stock backup at 53 bytes in the high page at
`0x08174000`, outside the two pages programmed during that earlier stage. The RF
image now occupies ten pages at `0x08006000..0x0801a000` (end exclusive); its
readback preserves the high page exactly against the pre-RF snapshot.
The earlier 53 changed bytes look like appended records after the stock app
was restored and run; attribution is unproven. The changed page stayed identical
on a later diagnostic-app read. Loader and factory device-info bytes match the
backup. Both dumps are retained; that high page was not rewritten. This is not
proof of a byte-identical full-chip factory restoration.
