# Experimental XIAO MG24

Status: transport-only bring-up, not a supported gateway or updater target.
The application never initializes RAIL or transmits. It shares the bounded
[experimental parser](../experimental/README.md), not a copy of gateway policy.
Full protocol/peer/phase/scheduler reuse and a raw RAIL adapter remain subsequent
work, gated by independent capture and exact-board testing.

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
The initial app uses the reset clock configuration and does not configure the
39 MHz radio oscillator. RX is interrupt-buffered; TX and parsing run in the main
loop. This simple transport is not yet qualified under sustained radio load.

Build with a separate Gecko SDK 4.4.6 checkout, commit
`59d2160fe448b13730a91c0f019a8b4c53c43eb2`, including Device/EFR32MG24,
CMSIS, emlib, common and bootloader headers:

```sh
make -C firmware/mg24 GSDK=/absolute/path/to/gecko-sdk BUILD=/tmp/ec-mg24-build
```

The bring-up used Arm GNU 14.2.1. Device startup, system code, emlib, CMSIS and
application-property headers retain their upstream licence terms; the adapted
linker retains its notice. No RAIL archive is linked in this diagnostic image.
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
no automatic update, power-loss recovery or radio qualification is claimed.

Observed preservation caveat: the final full readback differs from the initial
stock backup at 53 bytes in the high page at `0x08174000`, outside the two pages
programmed by our commands. These look like appended records after the stock app
was restored and run; attribution is unproven. The changed page stayed identical
on a later diagnostic-app read. Loader and factory device-info bytes match the
backup. Both dumps are retained; that high page was not rewritten. This is not
proof of a byte-identical full-chip factory restoration.
