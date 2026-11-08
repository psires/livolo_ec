# XIAO MG24 and ESP32-C6 support plan

Planning baseline: 2026-11-07; RF progress updated 2026-11-08. Both boards are
connected to the development Raspberry Pi and have completed initial diagnostic
transport, bounded recovery, synthetic radio cross-capture and passive reception
from both test switches. Neither board is
currently a supported Livolo gateway. This document
tracks the remaining work; hardware findings belong to the linked verification
records, not to the planned acceptance criteria below.

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
The experimental C6 adapter now uses pinned ESP-IDF v5.5.1. SDK API availability
alone does not establish these hardware properties.
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
- Keep required release membership separate from updater/backend implementation
  capability. Schema-1 membership is now frozen independently of
  `firmware_bundle.BOARDS`, and artifact validation uses explicit board/layout
  dispatch. Preserve those safeguards: experimental adapters must not require
  a six-board release or fall through to an MG21 parser. Keep historical
  four-board bundles verifiable; a candidate specification cannot choose its
  own smaller required set. Retain omitted/unknown-board and backend-extension
  regressions. Review the existing 1 MiB file and 16 MiB
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

## Bring-up results and next handoff

Both boards are connected to the development Pi. The initial transport-only
applications answer diagnostic commands and reject network/control commands;
neither initializes the radio. The MG24 uses its onboard CMSIS-DAP recovery
path and requires DTR for its serial bridge. The ESP32-C6 uses ROM recovery
and native USB Serial/JTAG. Both passed restoration and verification of the
original application sectors touched by bring-up, followed by diagnostic
reinstallation. This is a bounded recovery result, not interrupted-update or
full factory-restoration qualification. A separate MG24 high-flash page changed
outside the programmed application pages; the cause is unproven and both dumps
are retained. The loader and device information were unchanged. See the
[bring-up record](verification/xiao-bringup-20261107.json) and the
[MG24](../firmware/mg24/README.md) and
[ESP32-C6](../firmware/esp32c6/README.md) build/recovery notes.

The raw-radio adapters now pass a bounded channel-26 cross-capture baseline:
each board transmitted 20 synthetic frames and the other received all 20, with
ten 39-byte and ten 41-byte frames in each direction. MG24 captures also passed
20 independent software FCS checks; C6 reception relies on the SDK's valid-CRC
metadata. The passing run had no missing or duplicate expected frames, failed
transmits, queue drops or latched faults, and both radios finished inactive.
See the [RF verification record](verification/xiao-rf-20261108.json) for exact
artifacts, failed preflight attempts, preservation checks and evidence limits.

A separate follow-up after the MG24 USB cable replacement passed with four
frames per direction: one of each length with CCA enabled, and one of each
length with the ACK-request flag set. All eight frames were independently
received, with no observed classic IEEE ACKs. This does not qualify busy-channel
CCA, addressed-unicast ACK behavior or ACK timing.

The C6 initially became unresponsive over USB before RF activation. The same
image worked after application reinstallation, but the cause remains unresolved;
this result does not establish sustained transport reliability. The MG24
physical Standard/Sense variant and PCB revision still need visual confirmation.
The nearby, isolated test switches are `46715621` (two gang) and `66780945`
(one gang), both removed from the HA-managed network. A separate channel-11
passive stage received validated native frames from each switch on each XIAO
without manual stimulus. The first 30-second capture counted 30/14 target frames
on MG24 and 31/14 on C6. A second 30-second capture with MG24 reason counters
counted 30/4 on each board, with 34 shared full target payload hashes. These
counts establish reception, not a packet-loss rate or complete capture of every
transmission.

Both passive runs retain a **failed** strict capture-integrity assessment:
MG24 reported 58 aggregate drops initially and 33 in the recheck. The recheck's
33 occurrences were all RAIL frame errors, with zero FIFO overflow, hold,
adapter-validation or calibration failures. All 448 delivered MG24 frames in
that run had valid original FCS, and its accepted-RX count matched delivery;
portable-ring loss was not observed. RAIL frame errors can cover CRC, decoding
or illegal-length errors, so these are not all identified as bad CRC. The new
counters cannot retrospectively classify the earlier 58 occurrences. C6 had
no reported drops in either capture. Aggregate accounting, queue sizes and the
harness acceptance rules remain unchanged.

C6 health diagnostics also established USB resets across close/reopen under the
tested host settings. Three cycles began at 41 ms uptime and remained responsive
for seven seconds with no watchdog or I/O errors. After a separate five-second
closed interval, the first reopened sample reported 31 ms uptime and a USB reset
reason, supporting a reopen-associated reset rather than an immediate close-only
reset. At that stage the OS/control-line trigger and original stall cause were
unproven; the subsequent controlled comparison below isolates a host-side fix
for the tested reopen reset.
Exclusive ownership and known-idle state are required before opening the port;
a post-open status check cannot preserve state that opening already reset.

See the [passive/USB verification record](verification/xiao-rx-20261108.json),
[passive bench procedure](XIAO_RX_BENCH.md) and
[synthetic RF procedure](XIAO_RF_BENCH.md) for artifacts and evidence limits.
Gateway control stays disabled. Watchdog, sustained serial/radio load, timing,
pairing and updater qualification remain open gates. Existing radios, services
and HA are outside this bring-up's modification scope. No experimental image is
included in a release or an automatic-update catalog.

### Stability investigation, November 8

A traced channel-26 comparison on the unchanged passive-diagnostic images
received all 20 C6 test frames on MG24, with valid original FCS. C6 received
19 of 20 MG24 test frames, missing sequence 17. MG24 also counted one frame
error; other receive-reason counters were zero. Both radios stopped cleanly.
The strict result remains **failed**. Unrelated traffic was present, and syscall
tracing adds host timing overhead; this does not establish a packet-loss rate
or explain the earlier channel-11 errors.

The C6 USB comparison kept firmware unchanged. Stock pyserial 3.5 cleared DTR
and RTS separately and reset uptime to 41 ms on each of three opens. A combined
`TIOCMBIC(DTR|RTS)` preserved uptime through all three sessions and both
five-second closed gaps, with zero watchdog or I/O error counters. This operation
is now used only by the experimental XIAO bench tools, with explicit identity
checks and no stock fallback. See the [USB comparison](XIAO_C6_SERIAL.md) for
the trace, reference-manual mechanism and kernel/electrical evidence limits.

A separate C6 `xiao-usb-1` change yields one RTOS tick after at most 256 consumed
input bytes. Native tests of the actual application loop demonstrate the
previous scheduling gap under continuously ready, reply-free input and verify
bounded yielding and parser recovery afterward. This is a robustness fix;
neither it nor the host-open comparison establishes the cause of the historical
intermittent USB stall. Watchdog policy, console and stack settings are unchanged.

A temporary MG24 `xiao-reject-1` image then collected rejected bytes separately
in one 30-second receive-only window. It reported 18 CRC-error packet statuses,
retaining the first eight and counting ten omitted samples. All eight retained
samples had invalid original FCS under an independent software check. All 274
normal MG24 receive events had valid original FCS; C6 delivered 208 events.
Both boards received two native reports from `46715621` and four from `66780945`.
Other MG24 receive-reason counters were zero. The strict assessment remains
**failed**, and rejected bytes never entered normal receive events.

This classifies the sampled captured bytes, not the cause of corruption. It does
not retroactively classify the earlier 33/58 errors or prove that all rejected
traffic is ambient noise. Six strong, application-checksum-valid target-source
payloads in the same window were seen only by C6; these forwarded/non-native
reports preserve an unresolved reception discrepancy. The temporary image was
removed afterward, and all 1,572,864 bytes of MG24 flash matched the saved
pre-test image exactly. Its normal CRC policy remains unchanged.

The C6 scheduling candidate passed one RF-off target check with 6,400 newline
bytes and a 1,025-byte overlength input, followed by valid-command recovery and
a seven-second hold. Accepted host bytes matched firmware read counters exactly;
uptime continued, with zero watchdog, I/O or pending-output errors and no RF
activity. Full readback matched the candidate and preserved every byte outside
application erase pages `0x10000..0x40000`. Final health confirmed `xiao-usb-1`
with RF off; the port was closed and unowned. This short paced test does not
qualify sustained load or establish the historical stall's cause.

The [stability verification record](verification/xiao-stability-20261108.json)
retains artifact hashes, measured results, preservation checks and open gates.
Pairing and control remain on hold. Existing radios, services and HA were not
changed, and no experimental firmware was added to a release or update catalog.

### Sustained investigation, November 8

The installed C6 `xiao-usb-1` image completed a ten-minute RF-off test through
one USB connection. The fixed schedule covered idle operation, paced newline
input, discarded overlength input, a higher-rate phase and recovery idle.
All 3,014,953 accepted bytes were accounted for, uptime remained continuous,
and all 21 health samples reported zero watchdog, read-error, write-error and
pending-output values. The longest measured request took 13.135 ms.
Independent raw-log review found no unknown or ROM lines; the scoped kernel
query found no USB events. This is a bounded paced-load result, not a
reproduction or resolution of the original
intermittent stall, nor a continuously-ready-input qualification.

The subsequent passive test planned twelve 45-second windows with ten-second
RF-off gaps. Its first window completed and stopped cleanly, but failed the
delivery gate, so the remaining eleven windows were **not run**. MG24 accepted
626 frames into the portable receive path and delivered 617, with 37 aggregate
drops: 28 frame errors and a residual of nine. C6 accepted 542 and delivered
520, with 22 drops. Both boards delivered three native reports from `46715621`
and twelve from `66780945`. All 617 delivered MG24 frames passed an independent
original-FCS check.

Source and timing evidence strongly support overflow of the portable receive
queue, which has seven usable slots, for the nine and 22 delivery deficits.
The core increments its receive counter before checking queue capacity, and
STOP preserves the queue for draining. C6 firmware output counters also match
all 61,695 captured bytes in the audited interval, supporting loss before USB
output. Bursts with C6 callback gaps below 2 ms were delivered roughly 21 ms
apart, consistent with the input wait and output yield in its main loop.
MG24 diagnostic output is bounded by its 115200-baud UART. The installed images
have no dedicated queue-overflow counter, so the missing payloads and precise
overflow times cannot be identified.

A separate controlled channel-26 experiment then completed twelve windows,
alternating transmission order and sending twenty fixed test frames per board
in each window, with 200 ms spacing and ten-second RF-off gaps. It preserved
the failed passive receipt and verified its exact stopped state before starting
fresh test windows; no board reset or firmware write was used. All 240
transmissions per board completed, but C6 captured 234 MG24 frames and MG24
captured 236 C6 frames. No portable receive-to-delivery deficits were observed
in these windows. MG24 reported 29 frame errors with all other reason counters
zero; C6 reported no drops. One window passed the strict assessment and eleven
failed. These measured capture misses cannot be assigned to a specific RF or
receiver cause, nor extrapolated to a general packet-loss rate.

This queue evidence does not explain MG24's separate 28 frame errors or earlier
captures whose accepted and delivered counts matched. Successful and absent
forwarded reports also have overlapping spacing after native reports; a fixed
receiver-recovery threshold was not established. MAC source fields and equal
payloads do not identify physical transmitters or individual transmission
attempts. The passive result remains **failed**; pairing and control stay on
hold while receive-queue telemetry, output scheduling and RF discrepancies
remain open qualification work.

The [sustained verification record](verification/xiao-sustained-20261108.json)
contains audited measurements, artifact hashes and remaining qualification
limits. Both XIAOs finished RF-off with their ports closed. No firmware was
flashed, and existing radios, services, HA, releases and update catalogs were
unchanged.
