# XIAO MG24 and ESP32-C6 support plan

Planning baseline: 2026-11-07. Both boards are newly supplied by the owner and
will be connected to the development Raspberry Pi. Neither board is currently
a supported Livolo target. This document assigns future work; it is not a
hardware qualification result or an instruction to flash attached devices.

## Product scope and sequence

Provide the same local Livolo gateway experience on both boards: standalone
daemon/CLI, Web UI and native Home Assistant integration. Reuse one-button
pairing and the existing logical device model. Initial support targets a wired
serial connection to the host; ESP32-C6 Wi-Fi gateway operation is separate
future scope. No cloud service is introduced.

Start with hardware inventory and two bounded radio feasibility investigations.
MG24 is the provisional first full port because the existing MG21 implementation
uses Silicon Labs RAIL; this is a reuse hypothesis, not binary compatibility.
Run the ESP32-C6 radio/timing investigation alongside it. Prefer whichever board
first demonstrates both correct radio behavior and a recoverable installation.
Do not hold a qualified board behind an unrelated blocker on the other board.

Keep the shared Livolo packet, peer, commissioning and scheduler policy separate
from MCU radio/timer/transport adapters and exact board definitions. Avoid copying
the complete MG21 implementation into two divergent gateways. Retain UART v3 and
native API revision 1 where their contracts remain sufficient; the Release
Engineer assigns software and firmware compatibility versions after review.

The active HA gateway is excluded from this work. Existing bench services and
reference sniffers retain their ownership until a test arrangement is agreed.
Use an isolated test network/database and dedicated one- and two-gang switches.
Never run two armed gateways with the same network identity simultaneously.

## Board assessment

| Target | Proposed route | Main questions before implementation |
| --- | --- | --- |
| XIAO MG24 | Shared protocol core, MG24-specific RAIL/device adapter and explicit XIAO board profile | Exact standard/Sense variant and revision; oscillator, UART/USB bridge and pins; startup/linker/flash layout; installed loader and independent recovery; which MG21 adapter portions really transfer |
| XIAO ESP32-C6 | Shared protocol core with ESP-IDF IEEE 802.15.4 adapter, timer and serial transport | Raw Livolo frame acceptance, FCS/length conversion, disabled IEEE ACK handling, interrupt/buffer ownership, turnaround and timing; USB Serial/JTAG versus UART; ROM recovery and partition layout |

Seeed documents an MG24 serial bridge and warns that its recovery behavior
differs from other XIAO boards. Establish the actual recovery path before changing
the application; do not treat an Arduino escape mechanism as a guaranteed
bootloader for our firmware. Confirm debugger availability, rather than assuming
one has been supplied. [MG24 board guide](https://wiki.seeedstudio.com/xiao_mg24_getting_started/)

The documented MG24 console uses USART0 PA8/PA9 through an onboard
SAMD11/CMSIS-DAP bridge. Validate that route on the supplied revision; it is not
the MG21 PB1/PB0 profile or the separate header UART. The documented
EFR32MG24B220F1536IM48 target uses a 39 MHz HFXO, unlike our 38.4 MHz MG21 boards.
First evaluate SWD recovery through the onboard CMSIS-DAP bridge with pinned
debugger software/device support; an external debugger is a fallback, not an
assumed prerequisite. Leave bridge firmware and descriptors unchanged.
[Zephyr XIAO MG24 board definition](https://docs.zephyrproject.org/latest/boards/seeed/xiao_mg24/doc/index.html)

Reuse the packet/peer/phase/scheduler policy at source level. MG24 still needs
its own device startup, RAIL archive and PA configuration, clock/pin setup,
linker ranges and USART/LDMA audit. Assess SDK compatibility without forcing an
unnecessary SDK change onto existing MG21 targets.

The ESP32-C6 board has an antenna switch controlled by GPIO3/GPIO14 and a BOOT
input on GPIO9. The board profile must specify the intended antenna and reset
wiring. [ESP32-C6 board guide](https://wiki.seeedstudio.com/xiao_esp32c6_getting_started/)

Its native USB Serial/JTAG is fixed-function hardware, with download-mode and
sleep/disconnection behavior that requires its own transport policy. Do not
copy Nordic USB descriptor assumptions or allow ordinary serial opening to
trigger a reset unnoticed.
[Espressif USB console documentation](https://docs.espressif.com/projects/esp-idf/en/stable/esp32c6/api-guides/usb-serial-jtag-console.html)

ESP-IDF exposes promiscuous reception, ACK controls, raw transmit with optional
CCA, hardware FCS and radio callbacks. These APIs make a port plausible, but do
not prove acceptance of Livolo frames or the timing of our application ACKs.
In particular, test the existing `01 88` frame prefix, absence of unintended
IEEE ACKs and hidden retries, and exact FCS/length handling. Auto-pending controls
must not be mistaken for disabling auto-ACK.
The reviewed v5.5.1 header is feasibility evidence, not a selected SDK version.
[Espressif IEEE 802.15.4 API](https://github.com/espressif/esp-idf/blob/v5.5.1/components/ieee802154/include/esp_ieee802154.h)

## Assigned work packages

| Work | Lead and reviewers | Deliverable and acceptance |
| --- | --- | --- |
| X1: Inventory and recovery | ZigBee + Unix | Exact board/revision/MCU, USB identity/path, transport, existing loader/application, flash map and recoverable baseline; documented physical recovery access |
| X2: Raw radio feasibility | ZigBee | Per-board minimal receive/transmit experiment with independent capture: valid Livolo bytes/FCS, filtering, CCA versus immediate application ACK, timing and recovery to RX; stop/go finding before full port |
| X3: Shared-core firmware ports | ZigBee; Unix contract review | Small board profiles and MCU adapters, explicit compiled board identity, bounded queues/callbacks, disarmed startup, protocol/scheduler compatibility and existing-target regression results |
| X4: Host and maintenance support | Unix; ZigBee review | Exact-board transport and firmware identity, simulator fixtures, honest preflight/manual-only behavior, bootstrap/recovery procedure; updater backend only after recovery is proven |
| X5: Web and HA behavior | Web + HA; Unix review | Shared capability-driven pairing/control/update presentation, offline contract and persistence tests; no board-specific integration or browser flasher |
| X6: Physical qualification | ZigBee + Unix; HA/Web acceptance | Separate exact-board evidence for control/pairing and for installation/update/recovery; retained captures, frozen hashes, failure observations and rollback/recovery outcomes |
| X7: Release acceptance | Release Engineer; Tech Lead scope review | Reproducible artifacts, notices, quality/compatibility checks, qualified catalog and distribution verification; all publication/version/channel operations remain with Release Engineering |

X1 and source-only investigation can proceed before radio tests. X2 gates the
full X3 implementation; X4/X5 fixtures can proceed once identities and contracts
are agreed. Start dependency and redistribution-input review alongside X2;
bind the final built bytes at release freeze. X6 requires a selected build and
a concrete bench arrangement. X7 follows accepted evidence. No release date or
promised compatibility version is
assigned before the feasibility results.

## Firmware and host contracts

- Allocate canonical exact-board identifiers after inventory; keep standard and
  Sense variants distinct unless reviewed hardware evidence permits one profile.
  Never identify a board solely by a common USB VID/PID, filename or MCU family.
- Match compiled image identity, live VERSION/CAPS identity, artifact metadata,
  layout and compatibility. Re-enumeration must recover the same device or fail
  clearly; no guessing among multiple serial ports.
- Preserve packet length/FCS semantics, CRC validation, bounded RX/TX queues,
  application ACK ordering, timer units/wrap behavior, lease expiry, duplicate
  suppression, and disarmed startup. Keep debug/boot logs away from the protocol
  stream. Initially disable optional Wi-Fi/BLE workloads on ESP32-C6. Isolate
  USB console buffering from the radio path; a host that stops reading must not
  stall radio timing. Bound ISR ownership/copy/release and worker queues.
- Review the MG21-specific GBL parser, board marker, linker assumptions and
  bootloader entry before any reuse for MG24. A matching container format does
  not authorize cross-chip flashing. Design an explicit ESP32-C6 image/partition
  validator and recovery adapter instead of disguising it as an existing board.
- Separate required release membership from updater/backend implementation
  capability. Currently `tools/firmware_release.py` derives required membership
  from `firmware_bundle.BOARDS`; experimental adapters must not accidentally
  require a six-board release. Keep historical four-board bundles verifiable.
  Freeze/version the required set in independently reviewed release policy;
  a candidate specification cannot choose its own smaller required set. Test
  omitted required boards, duplicate/unknown boards and an experimental backend
  that must not change required membership.
  Replace implicit non-TI/non-Nordic routing to MG21 in `firmware_artifacts.py`
  with explicit format dispatch. Review the existing 1 MiB file and 16 MiB
  bundle bounds against measured new images without removing defensive limits.
- Dry-run/preflight must not reset, enter a loader, flash or change configuration.
  Retain artifact, expected-board, helper, live-identity, board, protocol, baudrate
  and version checks. Propose a reviewed contract revision if a check genuinely
  needs different semantics; do not silently remove a check.
- Separate initial factory provisioning, manual maintenance and qualified
  automated updates. A control-capable board can initially require manual
  firmware maintenance. No Install action is offered until backend, artifact,
  identity and recovery gates are satisfied.

## Web and Home Assistant acceptance

Keep the existing Pair new device / Cancel / status flow and confirmed-only
notifications. Sessions and jobs started through one interface must be visible
through the others. Treat board and candidate IDs as opaque values; capabilities
and validated backend results determine actions.

Parameterize offline tests for both board identities, including unknown/missing
capabilities, manual-only maintenance, no catalog, missing exact-board artifact,
unqualified artifact, stale preflight, wrong board/version, disconnect/reconnect,
lost replies, failed verification and recovery. Transfer completion is not
installation success. Catalog disappearance must never look like a successful
update, and a firmware-update limitation must not imply control is unsupported.

Preserve entry/entity/device IDs, names, areas and options through software
upgrade and reconnection. Never replay output, pairing or installation requests
after a lost reply. Hardware replacement preserves the existing network only
when the network identity and radio state are explicitly migrated and tested;
unchanged HA IDs alone do not establish successful radio migration.

## Physical and release gates

Before testing, freeze the board identity, artifact hashes, test arrangement,
timing tolerances, sample counts and pass/fail criteria. Record all failures;
do not turn a successful build or simulator result into a hardware claim.

1. Verify a recoverable baseline and exclusive serial ownership; preserve
   factory/calibration/security data. No irreversible security provisioning is
   part of initial bring-up. Prove a boot/transport-only, disarmed application
   with VERSION/CAPS, watchdog/reboot and recovery before radio experiments.
2. Establish passive receive correctness, then controlled transmit/ACK and timing
   evidence with an independent receiver. Exercise serial load and bounded
   scheduling without relying solely on host logs.
3. Test pairing, identification, physical state reports and each output on
   isolated one- and two-gang switches, including the newly supported two-gang
   announcement signature. Cover cancel/expiry/conflict and restart persistence.
4. Verify serial integrity, reconnect/hotplug, host restart, lease expiry and
   longer operation. Compare timing distributions, attempts/deadlines, fresh
   report cancellation, simultaneous peers and timer wrap against the existing
   validated baseline. Keep retry policy unchanged; state queries may time out
   on existing radios, so assess regressions rather than promising zero RF
   timeouts. Report measured failure rates and any limitations.
5. Separately qualify bootstrap, wrong-image rejection, upgrade, interruption,
   recovery, post-install identity/version verification and rollback where
   actually supported. Qualify standalone and isolated HA app device access;
   manual bench installation alone does not qualify the HA updater.
6. Release Engineering pins SDK/toolchain/components/build configuration,
   inventories linked third-party inputs and redistributable terms, reproduces
   builds and binds per-board hashes to notices and qualification evidence.
   The project's BSD-2-Clause licence does not replace vendor terms. MG21's
   reviewed notice mapping is not blanket MG24 approval; ESP-IDF also requires
   component-specific review, not an assumed uniform Apache licence.
   [Espressif licence guidance](https://docs.espressif.com/projects/esp-idf/en/stable/esp32c6/COPYRIGHT.html)
7. Enforce intent-and-parameter comments on every function/declaration and Linux
   kernel style for project-owned C, plus the existing language/style checks.
   Run shared firmware and existing-board regressions after adapter refactoring.
8. Promote each board explicitly from investigation to experimental and then to
   its qualified support tier. Preserve the four existing board profiles and
   their release requirements. Add new boards to the required release matrix
   only when their support is accepted; do not advertise experimental work as
   stable or make it silently block unrelated maintenance releases.

Future releases must deliver the same qualified firmware bytes through GitHub
artifacts and applicable standalone/HA image catalogs, with matching emergency
CLI instructions. A board may enter the public bundle after exact-artifact
build/rights/boot/basic-control/manual-install/manual-recovery acceptance while
`automatic_update.eligible` remains false. Keep qualified manual-only artifacts
in the full release inventory. Only separately qualified updater entries enter
installed catalogs, and every catalog payload must match its public download
hash. Use the existing [release process](RELEASE_PROCESS.md),
[bundle contract](FIRMWARE_RELEASE_BUNDLE.md) and
[qualification plan](FIRMWARE_QUALIFICATION_PLAN.md).

## Immediate next handoff

The owner is wiring both boards to the development Pi. Once connected, agree
the inventory operation and identify the new devices without claiming existing
radio ports or disturbing running bench services. Confirm the MG24 variant and
recovery access, then have ZigBee and Unix propose the exact first firmware
experiment and recovery procedure. This planning task performs no device probe,
flash, live HA change or release publication.
