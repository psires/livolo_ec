# MG21 gateway: ZB-GW04 v1.2 and Sonoff ZBDongle-E

This target runs the existing Livolo UART v3 gateway and packet/peer/phase core
on an EFR32MG21A020F768IM32. It is a raw IEEE 802.15.4 radio application, not an
EmberZNet Zigbee coordinator. The Pi remains the owner of network records and
uses the existing daemon without a protocol change. Current release **0.12**
includes MCU timed queues, heartbeats, bounded state-query/output transactions,
phase-stamped automatic ACKs and the validated DMA UART receive path. Both named
boards have bounded hardware qualification; later sections retain dated results
from older releases. See [third-party notices](../THIRD_PARTY_NOTICES.md) for SDK
licence exceptions and the exact linked dependency inventory.

Select the board explicitly when changing hardware:

| `BOARD` | USB bridge | RTS handling |
| --- | --- | --- |
| `zb-gw04-1v2` (default) | CH340 | PD4 held ready |
| `sonoff-zbdongle-e` | CP210x | No RTS pin driven |

Both profiles use USART0 TX PB1 / RX PB0, a 38.4 MHz HFXO with CTUNE 128,
and no external RF amplifier switching. Application UART is 8N1 without flow
control, default 115200. `VERSION`, `CAPS` and startup report the selected board.
The packager reads a compiled board marker; legacy binaries without that marker
are labelled `mg21-unspecified`, rather than assigned a board by filename.

```sh
make -C firmware/mg21 BOARD=sonoff-zbdongle-e UART_BAUD=460800 BUILD=build-sonoff
python3 tools/mg21_gbl.py firmware/mg21/build-sonoff/ec-mg21.bin firmware/mg21/build-sonoff/ec-mg21.gbl
```

Changing board or baud rebuilds all objects, even when reusing a build directory.
Unknown board names fail the build. The Sonoff wiring is cross-checked against
the [pinned maintainer profile](https://github.com/darkxst/silabs-firmware-builder/blob/49fda29e1bc068b0a153bf5fd5899a58d04c01f8/manifests/sonoff/zbdonglee_zigbee_ncp.yaml).
Its probe found Gecko 2.4.2 at 115200. The corrected UART build passed 6000
integrity requests and a ten-minute live observation on Sonoff; RF query
reliability remains unresolved. See the [Sonoff verification record](../../analysis/development/sonoff-migration-20261024.md).

Build with `make -C firmware/mg21 UART_BAUD=460800` and set the daemon JSON
`serial_baudrate` to 460800 for the faster transport. Supported build values are
115200, 230400, 460800 and 921600; changing the rate rebuilds all objects.
`CAPS baud=` reports it. The original Gecko bootloader remains at **115200**.
Send `BOOTLOADER` at the application rate, then flash with
`--bootloader-baudrate 115200 --probe-method bootloader`; reconnect to the new
application at its build rate. This setting is not negotiated live.
The initial ZB-GW04 test found UART byte loss at 460800 with Nordic active on
the same Pi. A later separated-host CAPS check passed, but subsequent incoming
command errors also appeared on Sonoff. Diagnostic counters identified UART RX
overruns; staging outgoing messages with interrupts enabled corrected that
reproduced failure in a 6000-request Sonoff test. Later query runs still recorded
RX overruns; release 0.10 replaces that receive path with DMA as described below.
This does not retrospectively
explain the ZB-GW04 USB bulk-IN truncation. See
[deployment and comparison evidence](../../host/DEPLOYMENT.md) for hardware scope
and the separate, unresolved RF query reliability measurements.
Do not assume another MG21 dongle has this wiring. The board settings were
cross-checked against the [board maintainer's profile](https://github.com/darkxst/silabs-firmware-builder/blob/49fda29e1bc068b0a153bf5fd5899a58d04c01f8/manifests/easyiot/zb-gw04-1v2_zigbee_ncp.yaml).

## Runtime

`main.c` supplies the platform adapter for `../gateway/ec-gateway.c`:

- Channel 11, 0 dBm, promiscuous reception, hardware CRC checking, no IEEE ACKs.
- RX strips the PHY length byte; RAIL strips FCS. TX adds the PHY length and lets
  RAIL generate FCS. The shared core always sees at most 125 MAC bytes.
- RAIL retains received packets until the main loop can process them. Application
  ACKs precede UART formatting and bypass CCA. Host sends use a single CCA.
- The interrupt-driven 8 KiB UART queue drops complete writes on exhaustion.
  Its main-loop producer stages bytes with interrupts enabled and publishes
  the head only after the complete write. The TX interrupt consumes published
  bytes. An independent 8 KiB linked DMA ring receives bytes during radio calls;
  the main loop validates producer progress before feeding the command parser.
  `radio_drops` and `uart_rx_errors` supplement the shared logging counters.
  `uart_rx_overruns`, `uart_framing_errors` and `uart_parity_errors` distinguish
  receive FIFO exhaustion from framing/parity faults. Counts describe observed
  sticky hardware flags, not an exact count of missing bytes.
- Millisecond lease/status clock and 1 MHz RAIL phase clock, both 32-bit.
  `phase=tx_entry_v1` still means radio-call entry, not RF SFD; phase scale remains
  65500. The host learns these frequencies from CAPS.
- Startup is unconfigured/disarmed. Sessions, 256 peers, masks, leases and retry
  cache are volatile and use the same policy implementation as the TI target.

Release **0.7** also builds the shared MCU scheduler: eight retained timed jobs,
an absolute ten-second heartbeat, and one complete read-only state-query
transaction. It keeps the TI limits of three rounds, four CCA attempts per
opcode, 25/50/75 ms backoff and a 14-second deadline, stopping on a fresh valid
report. Authoritative network state remains on the Pi.

MG21 advertises `scheduler_hz=1000000`, matching its wrapping RAIL/phase clock;
the host scales durations using this capability. Do not reuse TI tick counts.
The existing main loop continuously checks deadlines; the wake adapter is a
no-op because the CPU never sleeps. RF calls remain in main-loop context, with
TX-entry diagnostics rather than a promise of hardware-scheduled RF SFD timing.
The shared 0.7 host client in this repository supports both clock rates. Earlier
TI-only scheduler clients reject the MG21 frequency and must be updated before
switching the daemon to this image.

In the 2026-10-24 comparison, Sonoff 0.7 and matching host support ran at
460800 with TI disarmed. The original EC network stayed running. Nordic captured all 92 query
sends in a completed 24-request comparison: **21 confirmed, three timed out**,
matching the preceding TI 0.7 count. Normal query-pair spacing was
235.522–235.569 ms; one bounded CCA retry extended a pair to 261.031 ms.
All 21 retained reports matched the capture, with no further queries after
confirmation. Host-silence and UART-load query checks also confirmed.

An earlier attempt stopped on its second request with `ERR SYNTAX` and one
recorded UART RX overrun; Nordic subsequently captured the switch's state.
The full repeated run had no new UART errors, but the initial failure remains
unresolved and must not be counted as an RF query timeout or silently discarded.
This is a small coexistence test, not long-term qualification.
[Build and dry verification](../../host/verification/deployment-sonoff-mcu-20261024.json),
[active deployment](../../host/verification/deployment-sonoff-query-mcu-20261024.json)
and [Nordic comparison](../../analysis/development/sonoff-query-mcu-comparison-20261024.json)
retain the image hashes, measurements and initial interrupted run.

## Build

Tested with Debian's `arm-none-eabi-gcc` 14.2.1 and newlib on the development Pi.
The SDK's documented toolchain is GCC 12.2.1. Gecko SDK **v4.4.6**, commit
`59d2160fe448b13730a91c0f019a8b4c53c43eb2`, supplies device startup, CMSIS,
emlib and RAIL. The SDK is an external dependency with its own licenses.

A complete SDK checkout works. To fetch only the dependencies used here, install
Git LFS first, then from the repository root:

```sh
GIT_LFS_SKIP_SMUDGE=1 git clone --depth 1 --filter=blob:none --sparse \
  --branch v4.4.6 https://github.com/SiliconLabs/gecko_sdk.git vendor/gecko_sdk
GIT_LFS_SKIP_SMUDGE=1 git -C vendor/gecko_sdk sparse-checkout set \
  platform/Device/SiliconLabs/EFR32MG21 platform/CMSIS/Core/Include \
  platform/emlib platform/common platform/bootloader/api \
  platform/radio/rail_lib/common platform/radio/rail_lib/protocol \
  platform/radio/rail_lib/chip/efr32/efr32xg2x \
  platform/radio/rail_lib/plugin/pa-conversions \
  platform/radio/rail_lib/autogen/librail_release
git -C vendor/gecko_sdk lfs pull --include='platform/radio/rail_lib/autogen/librail_release/librail_efr32xg21_gcc_release.a'
make -C firmware/mg21
python3 tools/mg21_gbl.py firmware/mg21/build/ec-mg21.bin firmware/mg21/build/ec-mg21.gbl
```

Verify the RAIL archive SHA-256 before building:
`630a5f8a07aaf034c3de83cd99b832ba4eefa658d8332bc12e537f1df6fee9f0`.
The build links all radio interrupt implementations explicitly. It reserves
8 KiB stack and bounds the application to `0x4000..0x30000`, within this part's
768 KiB flash / 64 KiB RAM. The packager checks vectors, flash-resident Gecko
application properties and Livolo MG21 product identity. It emits only header,
application, program and CRC-end tags, with no bootloader, SE or token writes.
The linker file is adapted from the SDK's Zlib-licensed device linker script;
its upstream notice is retained.

## Installation and recovery

The observed ZB-GW04 has **Gecko Bootloader 1.9.2** and the Sonoff **2.4.2**.
Preserve each board's existing bootloader. This
serial bootloader does not provide full-flash backup/readback through its menu;
no factory flash backup was obtainable through this interface. Application
replacement was verified through Gecko's upload validation, startup, UART
checks and independent RF capture, not a full-flash readback hash.

Only one process may own the port. Stop `livolod` before updating the adapter
that it uses. Select the exact device; never let a tool guess between the TI,
Nordic sniffer and MG21.

```sh
python3 -m venv .local/mg21-flasher
.local/mg21-flasher/bin/pip install universal-silabs-flasher==0.0.31
.local/mg21-flasher/bin/universal-silabs-flasher \
  --device /dev/serial/by-id/usb-1a86_USB_Serial-if00-port0 \
  --probe-method bootloader flash --firmware firmware/mg21/build/ec-mg21.gbl
```

For initial entry use the board's BOOT/RESET buttons. Once running this firmware,
the UART request `123 BOOTLOADER\n` disarms TX/ACK, replies `RSP 123 OK BOOTLOADER`,
flushes UART boundedly, and enters the preserved Gecko menu without GPIOs.
This command is MG21-specific. At the menu, `2` starts the application again.
Reset clears the network, peer table and session. The daemon restores them.

The tested Sonoff also supports automatic initial entry through CP210x RTS/DTR:
use `--bootloader-reset rts_dtr` with `universal-silabs-flasher` and its exact
serial-number-bearing `/dev/serial/by-id` path. This enters the stock bootloader
without a button press. Keep its upload rate at 115200 even when the application
uses 460800. A downloadable stock image is a recovery option, not a backup of
the installed application or its Zigbee configuration.

These GBL files are **unsigned application recovery images**. Firmware correctly
advertises `update=none boot_version=unknown`; the observed bootloader version is
recorded separately. The TI `uart_signed_v1` updater, board ID, flash layout,
signed packages and trial rollback do not apply. Do not use `livolod-firmware`
for MG21. Gecko upload is recoverable by re-entering the bootloader after an
interruption, but has no last-good application rollback or signature guarantee.
A future signed MG21 update design needs its own board/layout/package validation.

## Host configuration

Set `serial_port` to the MG21 stable device path shown above. This CH340 identity
has no unique serial number, so multiple identical adapters need a verified
`/dev/serial/by-path` path or a dedicated udev mapping. Preserve gateway identity,
network tag, device registry and MQTT settings when switching radios. Stop and
disarm the old adapter before using the same network identity on the new one.

[Hardware verification and deployment](../../analysis/development/mg21-port.md)
records the measured results and remaining limits. Offline checks:

```sh
python3 -m unittest discover -s firmware/gateway/tests -p 'test_*.py'
python3 -m unittest discover -s tools -p 'test_mg21_gbl.py'
```

After stopping the daemon, the configured/disarmed adapter can be tested for
UART integrity with long commands and long replies. This does not arm the
radio or send RF commands, and does not retry a failed request:

```sh
python3 firmware/gateway/tests/hardware_uart_integrity.py \
  --port /dev/serial/by-id/<verified-adapter> --baud 460800 --samples 6000 \
  --output integrity.json --log integrity.uart.log
```

It alternates exact CAPS comparisons with 125-byte non-EC CHECK payloads, checks
the final loss/error counters and session, and retains all mismatches. Run the
existing dry `hardware_v3.py` checks first if the adapter is unconfigured.

Release **0.8** adds the capability-gated AC-only comparison mode described in
[the UART contract](../gateway/UART_PROTOCOL.md#optional-ac-only-comparison--release-08).
It reserves the original preparation slot and retains all per-frame retry,
response-wait and deadline limits. The normal daemon continues using paired
`10`/`ac` queries unless a caller explicitly requests the comparison mode.

Release **0.9** adds an explicit heartbeat-relative 200 ms grid for AC-only query
experiments. It schedules actual radio calls and retains real phase stamping,
three-round/four-attempt limits and the 14-second deadline. Default queries stay
paired and unaligned. See the [UART contract](../gateway/UART_PROTOCOL.md#heartbeat-relative-ac-grid--release-09).


Release **0.10** replaces interrupt-per-byte reception with four linked LDMA
blocks forming an 8 KiB circular buffer. Channel 0 is reserved for UART RX;
RAIL DMA is not enabled. TX keeps its existing interrupt-driven queue. The
main loop stages at most 256 bytes, checks the producer pointer and sticky
hardware errors again, then feeds the existing line collector. Commands remain
in process context. UART baud, bootloader baud and radio scheduling are unchanged.

The consumer detects overtaking and conservatively discards data when elapsed
wall time could hide a whole DMA lap (including CPU preemption while copying).
It allows 5% baud/clock margin plus 16 in-flight bytes. A loss poisons the partial
command through the next newline; surviving fragments cannot become commands.
This is bounded buffering without flow control, not unlimited receive capacity.
The free-running RAIL microsecond clock must continue while the CPU runs; halted
debug sessions are outside that timing guarantee.

STATUS additionally exposes `uart_rx_dma_errors`, `uart_rx_resyncs`,
`uart_rx_highwater` (bytes) and `uart_rx_poll_gap_us`. Hardware error counters
count observed sticky-flag events, not the exact number of corrupted/lost bytes.
A resynchronization can indicate a conservative timing discard without a
hardware overrun. The host's normal 50 ms query-result polling is unchanged.

The [EFR32xG21 reference manual](https://www.silabs.com/documents/public/reference-manuals/efr32xg21-rm.pdf)
sections 20.3.2.9 and 25.7.27 describe the small USART receive FIFO and the LDMA
next-write pointer used here. The exact radio routine that delayed the old RX
interrupt has not been isolated; the observed overrun flags establish receive
service starvation, while DMA removes that CPU service deadline.

```sh
python3 -m unittest discover -s firmware/mg21/tests
```

The [0.10 verification report](../../analysis/protocol.md#sonoff-uart-dma-receive-fix-release-010--2026-10-25)
records a successful 6000-request concurrent UART load and 13217 matched requests
across instrumented sessions, with zero UART loss/error counters. Nordic timing
comparison and remaining RF failures are preserved separately from UART results.

### Sonoff automatic ACK phase (0.11)

The `sonoff-zbdongle-e` build enables `EC_AUTO_ACK_PHASE` and advertises
`auto_ack_phase=tx_entry_or_zero_v1`. Automatic replies to approved peer reports
(`05`, `15`, `a0`, and direct `08`) receive heartbeat-age trailers at radio-call
entry. The immediate no-CCA reply path, sequence echo, checksum, peer/lease
filters and query scheduler remain unchanged. `ACKTX` records the actual
stamped bytes. `CHECK` remains a pure template builder with a zero trailer.

Before the first successful heartbeat, or after its reference expires at
12 seconds, the reply retains the legacy zero trailer and is sent immediately.
No persistent state or host-authoritative network data is added. Release 0.12
also enables this behavior for `zb-gw04-1v2`; the two MG21 profiles now advertise
the same ACK-phase capability. TI and Nordic also enable phase-stamped automatic
ACKs in 0.12. The timestamp is radio entry, not RF SFD; independent captures
measure actual reply latency and phase error.


### ZB-GW04 v1.2 0.12 qualification (2026-11-01)

`BOARD=zb-gw04-1v2` now enables the same automatic ACK phase stamping as Sonoff.
The application at 460800 passed 6,000 exact UART requests and a 601-second live
observation without new transport errors or reconnects. The final isolated
control sequence confirmed 24/24 requests on the one- and two-gang switches;
Nordic captured all 48 successful output transmissions. Earlier blocks with
possible user interaction are retained separately. The previous reply-truncation
problem did not reproduce; this bounded run is not long-term qualification.
The preserved Gecko 1.9.2 bootloader remains at 115200. See the
[complete evidence](../../analysis/development/zbgw04-012-20261101.md).
