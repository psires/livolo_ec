# Radio readiness for Home Assistant packaging

Initially reviewed from source and saved records on 2026-11-01; updated after
the separately user-authorized ZB-GW04, TI and Nordic 0.12 ports and hardware qualification.
See the [ZB-GW04](zbgw04-012-20261101.md) and [TI](ti-012-20261101.md) qualification records. “Runnable” below means an implementation previously executed
on the specified board; it does not qualify every current feature on that board.

| Exact board / build profile | Livolo UART v3 gateway status | Application transport | Bootstrap and subsequent updates | Qualification gaps / HA packaging status |
| --- | --- | --- | --- | --- |
| **Sonoff ZBDongle-E**, tested USB CP210x unit; EFR32MG21A020F768IM32; `firmware/mg21`, `BOARD=sonoff-zbdongle-e` | **Runnable; verified 0.12 gateway baseline, currently repurposed as a passive sniffer.** MCU heartbeats, queries and heartbeat-aligned output transactions; Sonoff automatic ACK phase stamping; DMA UART RX. | USART0 TX PB1 / RX PB0 through CP210x; deployed **460800**, 8N1, no hardware flow control. Build default 115200. | Tested CP210x RTS/DTR entry (or BOOT/RESET buttons), then application-only `.gbl` via preserved Gecko **2.4.2 at 115200**. Our running application exposes `BOOTLOADER` for subsequent button-free entry. | **Primary supported board for initial packaging.** Two registered switches, one and two gangs, 24/24 aligned control confirmations in the latest bounded test. Long-term/many-peer qualification remains limited. MG21 has `update=none`: no TI signed updater or automatic rollback. |
| **cod.m CC2652 Raspberry Pi module**, factory-identified CC2652P / RF-BM-2652P2; `firmware/gateway`, `BOARD=launchpad/codm-cc2652p` | **Runnable; qualified at 0.12, now disarmed after HA handover**, signed-update bootloader 0.2. MCU heartbeat/query/aligned output transactions and phase-stamped automatic ACKs tested at the native 65,536 Hz timer rate. | Header UART `/dev/ttyAMA0`; tested application **460800**, 8N1. Build default 115200. | Initial signed installer through TI ROM loader at **115200**, using FLASH/RESET buttons or configurable Pi GPIO. Routine signed `.ecupd` transfer through the application UART, software trial activation and host confirmation; previous signed application rollback is implemented and tested. Requires the matching installed signing key and `UPDATE_SUPPORT=1` build. | **Tested alternative, bounded qualification.** Signed UART update 0.7→0.12 and host trial confirmation passed without GPIO; 6,000 exact UART requests passed. Initial controls were 20/24 confirmed, settled repeat 24/24; initial missing/old-token reports remain a follow-up item. TI lacks the MG21 hardware UART/radio loss diagnostics. This is **352 KiB flash / 80 KiB SRAM, not CC2652P7**; do not ship this image as a P7 or generic CC26xx image. |
| **ZB-GW04 v1.2**, tested CH340 board; EFR32MG21A020F768IM32; `firmware/mg21`, `BOARD=zb-gw04-1v2` | **Runnable; qualified at 0.12, currently used by the HA app.** DMA UART, MCU scheduling, aligned output transactions and automatic ACK phase stamping are enabled. The clean control run confirmed 24/24 commands. | USART0 PB1/PB0 through CH340; PD4 held ready. Tested at **115200 and 460800**; build default 115200. | Initial BOOT/RESET entry; subsequent application `BOOTLOADER` command tested. Application-only `.gbl` through preserved Gecko **1.9.2 at 115200**. No TI signed-update/rollback path. | **Tested alternative, bounded qualification.** 6,000 exact UART requests and a 601-second live observation had no transport failures or reconnects; earlier reply truncation did not reproduce. Long-term/many-peer qualification remains open. No unique CH340 serial on the tested unit: use a verified physical USB path/udev mapping when multiple similar devices exist. |
| **Nordic PCA10059**, user-confirmed official nRF52840 dongle; `firmware/nrf52840` | **Runnable at 0.12; former standalone deployment retired after HA handover.** Startup clock deadlock fixed. Final application passed 12 dry checks, scheduler checks, 6,000 USB requests and 24/24 output confirmations; 603.611 seconds of continuous observation had no new transport/queue losses or session reset. | Native USB CDC on the former standalone Pi; advertised 460800 is host compatibility metadata, not a UART clock. | Preserved Nordic Open DFU Bootloader, application-only USB DFU. Official sniffer recovery package saved. Application `BOOTLOADER` self-reset entry verified. A separate hardware stall-recovery test failed and was removed; normal software DFU/reflash and clean restart now pass; see the [bring-up record](nrf52840-012-20261101.md). | **Tested alternative on this official board; bounded two-device qualification.** No TI signed-update/rollback support or qualified autonomous stall recovery. Development USB VID/PID needs an appropriate allocation before product distribution. |

## Packaging contract

- Select exact board profiles and inspect `CAPS` (`proto`, `board`, `baud`,
  `fw_version`, scheduler/query/output/ACK capabilities and `update`). A CP210x or
  CH340 USB identity alone is not proof of the required radio or flashed firmware.
- TI/MG21 source accepts application baud builds of 115200, 230400, 460800 and
  921600. This is a build-time choice, not live negotiation or qualification of
  every board/rate combination. Preserve bootloader baud independently.
- The Pi/daemon owns the registry, network identity, tokens and persistent state;
  MCU execution state is volatile. HA packaging must not create another UART
  owner or treat the Nordic sniffer protocol as a gateway transport.
- The Home Assistant app now owns the active ZB-GW04 and authoritative network
  state. The former standalone Pi Docker deployment is stopped with restart
  disabled; its native service also stays disabled. Follow the coordinated
  handover procedure in `host/DEPLOYMENT.md` before any rollback. Do not run a
  second gateway with the same network identity or use the TI updater's
  systemd `--service` option to manage a container.
- Query unreliability is tier 3 by user decision. Preserve the working Sonoff
  0.12 control timing and use unsolicited state reports as the primary source;
  do not require a successful query as proof that a switch or board is supported.

## Evidence and reproducibility

These records describe bounded development runs. Raw local logs and captures
referenced by them are not all included in the source distribution. The active
HA deployment supersedes earlier ownership statements in historical reports.

- [Sonoff 0.12 control/capture results](output-grid-20261101.md),
  [deployment record](../../host/verification/deployment-sonoff-output-grid-20261101.json).
- [TI identity](module-identity.json), [identity interpretation](bringup.md),
  [TI 0.12 hardware comparison](ti-012-20261101.md),
  [earlier TI 0.7 query comparison](ti-query-mcu-comparison-20261024.md),
  [TI bootstrap/update build instructions](../../firmware/gateway/README.md).
- [ZB-GW04 port and bootloader verification](mg21-port.md),
  [Sonoff migration and unresolved ZB-GW04 transport issue](sonoff-migration-20261024.md),
  [MG21 board/build/update profiles](../../firmware/mg21/README.md).
- [Sonoff replacement sniffer and independent comparison](sonoff-sniffer-20261101.md).
- [Nordic gateway build and recovery](../../firmware/nrf52840/README.md).
- [Nordic receive-only capture wrapper](../../tools/capture_nrf.py),
  [latest independent Nordic capture metadata](output-grid-20261101/grid.json).
- [Standalone Docker ownership workflow](../../host/docker/README.md).

Some older target READMEs describe the release they originally introduced
(for example MG21 lacking the scheduler, or all output transactions remaining
host-owned). Those historical statements do not override current source and
the dated board-specific verification records above. Only the explicitly linked board-specific runs add hardware validation to this
review; no broad TI, Silicon Labs or Nordic chip-family compatibility is claimed.
