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
buffered; TX and parsing run in the main loop. Held RAIL RX packets are copied
and released in bounded main-loop batches, retaining original FCS bytes.
This transport is not yet qualified under sustained radio load.

The `xiao-rx-2` diagnostic build implements read-only `RFHEALTH` with separate
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

Two 30-second passive windows received valid native reports from both the known
one-gang and two-gang switches. The initial capture had 58 aggregate drops and
therefore failed the strict bench integrity check. A single instrumented recheck
received 448 accepted frames, all delivered with independently valid FCS, and
classified all 33 aggregate drops as RAIL frame-error events. All other reason
counters were zero. RAIL frame errors can include CRC, block-decoding or illegal
length errors; this does not establish their RF cause or attribute the earlier
58 events. The recheck remains a strict failure, with no acceptance relaxation.
See the [passive reception receipt](../../docs/verification/xiao-rx-20261108.json).

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
