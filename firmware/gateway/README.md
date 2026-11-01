# CC2652P volatile radio adapter — UART v3

Release **0.12** uses the same gateway core on this TI module, the
[Sonoff ZBDongle-E and ZB-GW04 v1.2](../mg21/README.md), and the
[Nordic PCA10059](../nrf52840/README.md). All three families implement volatile
peer tables, timed queues, heartbeats, complete state-query transactions,
heartbeat-aligned output transactions and phase-stamped automatic replies.
See [third-party notices](../THIRD_PARTY_NOTICES.md) for pinned build inputs.
Dated release sections below preserve implementation and qualification history.
The build, GPIO and signed-update instructions on this page are TI-specific.

The TI module owns radio timing and bounded volatile execution state. The Pi owns
all authoritative network/device records, pairing and routing decisions, command
intent, state tracking and MQTT integration. The MCU executes the host-authorized
bounded transactions. No application network state
is written to flash. The factory CCFG and ROM bootloader remain intact.

**Host integration:** [UART_PROTOCOL.md](UART_PROTOCOL.md) is the current contract.
Use CAPS instead of assuming capacity/features. Modern configuration uses a fresh
host-provided session, one network identity/tag, and up to 256 peer slots with
independent ACK masks. NETWORK/boot disarm; peer-table edits require disarmed
state. ARM, ACK and SEND carry the session so an old send fails after reset or
session replacement. All leases expire within 60 seconds without renewal.

The radio boots unconfigured on channel 11, 0 dBm, UART 115200 8N1 by default. It receives raw
frames without IEEE address filtering or automatic IEEE ACKs. Transmit framing is
currently restricted to the verified EC envelopes and self-addressed diagnostics;
it is not an unrestricted radio API. Pairing and routing remain host policy. Scheduler-capable TI executes a
host-authorized heartbeat cadence under the existing lease.

## Timed queues and heartbeats — release 0.6

The TI build enables `SCHEDULER=1` by default; `SCHEDULER=0` retains the older
host-driven path for controlled comparisons. MG21 gained the shared scheduler
in 0.7, and Nordic in 0.12.
The [UART contract](UART_PROTOCOL.md#ti-timed-queues-and-heartbeat-ownership--release-06)
describes queue commands, credits, deadlines, cancellation and heartbeat ownership.
Completed jobs retain their results in RAM until drained. Reset, session replacement,
disarm or lease expiry flushes pending work. No network records are stored in flash.

The first stage schedules process dispatch with the TI hardware timer; RF CCA and
transmission still use the existing radio driver. It does not promise an exact
RF SFD deadline. Heartbeats advance from an absolute ten-second epoch, preserving
the four-attempt CCA limit and 25/50/75 ms backoff. Release 0.7 also moves complete
query rounds and fresh-state stopping onto the MCU. Release 0.12 adds separate
heartbeat-grid output transactions; earlier output transactions were host-driven.
One-shot queue jobs are restricted to
self-addressed diagnostics and read-only queries; output commands cannot be queued.

The daemon negotiates heartbeat ownership and verifies a first live phase anchor
before readiness or trial confirmation. Its periodic status polls observe the
schedule; they do not drive transmission. Unsupported adapters retain their
existing behavior. `hardware_scheduler.py` tests queued probes during host silence,
heartbeats under UART traffic and lease cancellation; it is an explicit on-air
hardware test and requires the daemon to release the UART first.
`hardware_query.py` verifies complete read-only query execution during host silence
and continuous UART traffic. It also requires exclusive UART access and restores
no network authority on its own after exit; its caller must restart the daemon.

## Sources

- `ec-gateway.c`: UART parser, session/lease enforcement, events and radio callback.
- `ec-peers.c/.h`: RAM-only table and ACK-policy dispatch, bounded to 256 slots.
- `ec-protocol.c/.h`: captured EC packet/checksum rules and response construction.
- `ec-mac.c`: promiscuous raw RX integration with Contiki's radio driver.
- `uart0-arch.c`: nonblocking UART ring/callback implementation.
- `ec-scheduler.c/.h`: eight volatile timed jobs, retained outcomes and periodic heartbeats.
- `ec-scheduler-ti.c`: hardware timer wakeup into Contiki process context.

Automatic ACKs run before UART logging. Eligible native packets must match a
configured immediate source, subject and network tag. Each peer mask selects
state, property, capability and direct keepalive responses; broadcast state
reports are a separate opt-in bit. No automatic admission, announcement response,
broadcast keepalive response or ACK-of-ACK is provided. Per-peer masks are radio
rules, not device capability discovery or an authoritative registry.

RX keeps its v2 field layout. Optional RXMETA records contain a high-resolution
MAC callback timestamp, not an RF SFD timestamp. Sequence gaps and RX/UART/meta
loss counters expose logging loss. At 115200 baud a busy mesh can exceed UART
capacity; logs are not guaranteed lossless. Commands use a one-entry reply cache;
see the contract for retry and unknown-outcome handling.

Legacy CONFIG installs one full-ACK peer and permits legacy ARM/ACK/TX. This is
retained for existing lab tools, but new host software must use NETWORK and the
session-protected command forms. PING now reports version 3. The old
`tools/check_gateway_hardware.py` is a v2 verifier; use the v3 harness below.

## Build and recovery

Use the pinned Contiki/TI SDK and board adapter from the receiver README:

```sh
cd /home/sfires/livolo-ec
python3 firmware/receiver/prepare_board.py vendor/contiki-ng
cd firmware/gateway
make -j4 build/simplelink/launchpad/codm-cc2652p/ec-gateway.bin
cd ../..
python3 tools/cc26xx_gpio.py bootloader
python3 tools/probe_cc26xx.py --port /dev/ttyAMA0 --bsl-source vendor/cc2538-bsl
python3 tools/flash_cc26xx.py firmware/gateway/build/simplelink/launchpad/codm-cc2652p/ec-gateway.bin --port /dev/ttyAMA0 --bsl-source vendor/cc2538-bsl --reset
```

Only one process may own the UART. Coordinate with the host-daemon owner before
flashing/resetting. GPIO23 is RESET, GPIO24 is FLASH; bootloader recovery is DIO15.
These defaults are configurable on the **Pi side**. GPIO numbers use BCM
numbering; they are not physical header positions or TI DIO numbers. The TI's
FLASH signal remains DIO15 under the current verified CCFG. Changing Pi wiring
does not change the firmware image.

The development profile is `tools/hardware/pi5-codm.json`. Copy it for another
installation and set `chip`, `chip_label`, `port`, `reset_gpio` and `flash_gpio`.
The chip path can vary between kernels; inspect the controller before selecting
it. The tool checks its label and `GPIO<n>` line names, refuses already claimed
outputs, and retains active-low open-drain operation with pull-ups. It requires
libgpiod 2.x. Another controller profile is not a claim of hardware validation.

```sh
# Resolve settings without opening GPIO or UART; safe while the daemon runs.
python3 tools/cc26xx_gpio.py status --config tools/hardware/pi5-codm.json --show-config
# Example alternate wiring, still only inspecting the configuration:
python3 tools/cc26xx_gpio.py status --reset-gpio 17 --flash-gpio 18 --show-config
# Only after stopping/releasing the daemon's UART:
python3 tools/cc26xx_gpio.py bootloader --config tools/hardware/pi5-codm.json
```

Flags override profile values, which override the existing Pi 5 defaults.
No GPIO wiring is required for manual ROM flashing: the user can hold FLASH,
pulse RESET, release FLASH, and use the ROM probe/flasher through UART. GPIO
control is an optional development/recovery transport, not a network requirement.

Known-good remote rollback images:

- `backups/ec-gateway-v2-broadcast-acks.bin`: last verified single-peer gateway.
- `backups/ec-receiver-passive-pin-fix.bin`: verified receive-only firmware.

The previous verified v3 baseline is SHA-256
`a21fb791f2e73b7e7e83d4e99b4361f63368d9be4958dce1f61116597488802c`, full-flash
CRC32 `a2597eb0`, preserved remotely as `backups/ec-gateway-v3-pre-phase.bin`.
Its factory CCFG is unchanged. The ELF uses text 46876, data
1120, BSS 14200 bytes on the actual 352 KiB flash / 80 KiB SRAM CC2652P module.

The earlier phase-extension image was SHA-256
`c15d9b87cdec11761526cbac674eff3732b26b755251b23318a9ed315fe2a69f`, ROM-verified
CRC32 `f7c0613b`, with identical factory CCFG. Its ELF text/data/BSS are
48198 / 1120 / 14216 bytes. It adds explicit `tx_entry_v1` stamping and a
self-addressed diagnostic probe; raw SEND and automatic ACK behavior remain
compatible. Fourteen probes were independently captured exactly once, with a
maximum measured trailer-to-RF phase difference of 0.816 ms. Host release 06 now
explicitly enables experimental phase sends. Captures 05 and 06 independently
validate actual 41-byte command stamping; capture 06 has matching native ON and
OFF responses, plus a human-confirmed physical button event between them. Its
command phase residual is 0.621–0.788 ms and state ACK delay about 1.9 ms.
Reliable unattended control is not yet established: capture 05's ON was
unconfirmed, and capture 06 did not exercise an uninterrupted ON hold. The
increased retry ceiling is not proven causal; ON was reported after two sends.
See `analysis/development/gateway-phase-01.md`,
`analysis/development/gui-toggle-restore-05.json`,
`analysis/development/gui-toggle-restore-06.json`, and the UART contract.

## UART updates and release versions

Release numbers use `major.minor`. Root `VERSION` is authoritative for the host
and gateway application; `python3 tools/project_version.py` checks drift and
`--write` propagates an intentional release change. Bootloader releases also use
major.minor but change only when the resident bootloader changes. UART protocol 3
and JSON schema numbers retain their separate compatibility meanings.

The signed UART update implementation is in `firmware/update`, with a resident
bootloader in `firmware/bootloader`. The [UART contract](UART_PROTOCOL.md#signed-uart-updates--release-02)
records exact commands, package format, flash layout and recovery states. It uses
vendored Monocypher 4.0.3 for Ed25519 verification and SHA-512; upstream source,
license and download checksum are retained under `firmware/update/third_party`.

The initial installer contains the bootloader, signed application manifest and
application, preserving this board's factory CCFG. Install it once with the
manual buttons or optional configurable GPIO tool. Routine packages contain
only a signed application manifest and image: no bootloader, CCFG, calibration
or network records. The host transfers the package using normal UART, then the
MCU restarts in a trial boot. The matching daemon confirms it only after restoring
and checking its radio session. Unconfirmed reset/timeout restores the previous
signed application. GPIOs remain an independent recovery route.

### Build and package

Use the pinned Contiki/TI SDK described above, and install signing dependencies
from `firmware/update/requirements-build.txt` in an isolated Python environment.
Generate a private signing identity outside source control; `.local/` is ignored.
Back up that private key securely: losing it prevents producing routine updates
accepted by that installed bootloader. Only the public key belongs on the gateway.
Open-source builders can generate their own keys; no cloud service is required.

```sh
python3 tools/firmware_package.py keygen .local/firmware-keys
make -C firmware/bootloader CONTIKI=/absolute/path/to/contiki-ng
make -C firmware/gateway UPDATE_SUPPORT=1 CONTIKI=/absolute/path/to/contiki-ng \
  build/simplelink/launchpad/codm-cc2652p/ec-gateway.bin
python3 tools/firmware_package.py sign \
  firmware/gateway/build/simplelink/launchpad/codm-cc2652p/ec-gateway.bin \
  --version 0.4 --key .local/firmware-keys/signing.pem --output gateway-0.4.ecupd
python3 tools/firmware_package.py verify gateway-0.4.ecupd \
  --public-key .local/firmware-keys/public.key
python3 tools/firmware_package.py factory \
  --bootloader firmware/bootloader/build/ec-boot.bin --package gateway-0.4.ecupd \
  --original backups/original-module.bin --public-key .local/firmware-keys/public.key \
  --output first-install.bin
```

An existing legacy build directory must be cleaned before switching linker layout
or version overrides. Use separate build trees for releases. First-install
packaging checks the bootloader's public key and retains factory configuration;
`flash_cc26xx.py` additionally checks hardware identity and ROM recovery settings.
Do not distribute a factory configuration copied from an unverified board type.
The private key is never required by the update receiver or ordinary users.

### Remote upkeep

Install the matching daemon release with its `update` optional dependencies first.
Then the service-managed command performs the stop, exclusive UART transfer,
software activation, daemon restart and confirmation check:

```sh
sudo /opt/livolod/bin/livolod-firmware --config /etc/livolod/config.json \
  --package gateway-0.4.ecupd --public-key gateway-public.key --service livolod
```

Run it on the gateway, for example over SSH. There is no GPIO dependency.
The CLI requires a strictly newer firmware release and matching host release.
It checks the signature, board, layout, exact image digest and installed public
key before transfer; downgrade/reinstall requires an intentional recovery path.
Interrupted pre-commit transfers can restart. A matching committed package
resumes at activation. Ambiguous results are resolved using persistent status,
not by blindly overwriting a candidate. Do not run another UART client concurrently.

The radio is unavailable during transfer and restart. A 60-second trial window
requires the matching daemon to restore and confirm promptly. Network/device data
remain in host SQLite; MCU flash stores only software, keys and update metadata.
Bootloader updates remain separate from normal application updates.

#### Development TI query application 0.7 — 2026-10-24

The development CC2652P runs **0.7 at 460800**, with host 0.7 and the preserved
0.2 signed-update bootloader. `query=transaction_v1` adds one retained, volatile
complete state-query transaction beside the existing timed jobs and heartbeat.
Both opcodes, CCA retries, round timing, deadline and fresh native-report stopping
execute on the MCU. The Pi independently validates the retained frame and owns
all authoritative persistent state. MG21 retains the host query fallback.

The build uses 66915 bytes text, 1120 data and 16624 BSS, an additional 288 bytes
BSS over 0.6. Package SHA-256:
`9b0e13ee019f2b36c2a660fae3b823430bd12ce93ddde809ce5deefe1bc9c8ce`.
See the [transaction contract](UART_PROTOCOL.md#complete-ti-state-query-transactions--release-07)
and [Nordic comparison](../../analysis/development/ti-query-mcu-comparison-20261024.md).

#### Earlier TI scheduler application 0.6 — 2026-10-24

The preceding scheduler comparison ran **0.6 at 460800**, with host 0.6 and its original
signed-update bootloader 0.2. The production UART updater transferred and confirmed
the application without GPIO or ROM bootloader entry. The image uses 64963 bytes
text, 1120 data and 16336 BSS; this is the verified 352 KiB / 80 KiB CC2652P.
The package SHA-256 is
`1396e9b652f28a73e6b9799085e30a604480252ab8d84fdcc1860c534e48f959`.

Hardware checks cover 6000 UART requests, eight retained queue slots, cancellation,
lease expiry, twelve seconds of host silence and twelve seconds of UART load.
The [scheduler comparison](../../analysis/development/ti-scheduler-comparison-20261024.md)
records the independent Nordic timing and bounded query results. This stage
provides timed one-shot queues and autonomous heartbeats; query retry/freshness
logic remains on the Pi. It does not reserve RF-core SFD deadlines.

#### Earlier development TI application 0.4 — 2026-10-24

The preceding comparison ran the update-enabled **0.4 application at 460800**.
Its resident **0.2 bootloader** is preserved. A signed application-only transfer
from the previous 115200 application was followed by a coordinated daemon baud
migration and successful trial confirmation. This one-time migration used an
explicit controller; the ordinary updater still requires baud-preserving updates.

The package SHA-256 is
`4ff851b26ec830a03cd7a7f80252387f27fd7ecef2ebccaf860a51ac0dcb49ce`.
The verified board remains CC2652P, 352 KiB flash / 80 KiB SRAM; the build's
text/data/BSS sizes are 61914 / 1120 / 14596 bytes. It advertises
`phase_queries=10_ac_v1` and a 65536 Hz phase clock.

The dry UART-v3 checks passed, followed by **6000/6000** alternating long CAPS
replies and maximum-size CHECK requests, with no mismatches, timeouts or new
reported input/drop counters. TI does not yet expose the MG21-specific hardware
RX error counters, so this is a bounded transport check, not proof against every
UART failure mode. See the [TI query comparison](../../analysis/development/ti-query-comparison-20261024.md)
for on-air results and the final deployment state.

## Verification

```sh
python3 -m unittest discover -s firmware/update/tests -v
python3 -m unittest discover -s firmware/gateway/tests -v
PYTHONPATH=host python3 -m unittest discover -s host/tests -v
```

The update-core tests execute the real C verifier/state machine against NOR flash
semantics, including interrupted/partial erase and programming at every tested
transaction step, invalid manifests, transfer bounds/replays and confirmation.
They do not substitute for hardware power-fault qualification. Live deployment
and rollback evidence is recorded separately in `analysis/development`.

Release `0.2` introduced signed updates on the development CC2652P. Hardware checks passed
for signed UART transfer, daemon confirmation, timeout rollback, incomplete
transfer reset and persistence across a confirmed software reboot. Routine
checks used no GPIO operations or switch output commands. The initial trial
exposed stale VIMS reads after flash writes; both bootloader and application now
disable cache/line buffers around flash operations and restore them afterward.
See [deployment evidence](../../host/verification/deployment-uart-update-0.2.json)
and [deployment notes](../../host/DEPLOYMENT.md) for identities and recovery paths.

## Verification

From the repository root:

```sh
python3 -m unittest discover -s firmware/gateway/tests -p 'test_*.py'
python3 -m unittest discover -s tools -p 'test_*.py'
```

The 12 new tests compile the actual UART parser, callback and table implementation
against a deterministic radio/clock shim. The 28 existing tests include real
capture vectors for the same C packet core. Neither test suite simulates RF
propagation or proves timing. A host C compiler is required.

`tests/hardware_v3.py` exercises the actual board with new log/output filenames.
Its default mode uses synthetic peer identities and makes no RF transmissions;
`--air` additionally sends three self-addressed diagnostics with duplicate UART
requests and read-only queries to the already paired spare in slot 255. It never
sets outputs. `--boot-only --stale-session ...` checks reset/old-session behavior.
These are lab tests that replace the radio configuration; do not run alongside
the daemon or against a production table.

Hardware verification passed all 13 main checks. Nordic captured exactly three
diagnostic frames despite duplicate requests, and all 12 MCU ACKs from the real
peer with a full 256-entry table. ACK SFD intervals were 2.152–2.156 ms (median
2.153 ms), versus about 1.87 ms for the earlier single-peer firmware. The bounded
linear lookup has measurable cost at the last slot; this is not a zero-cost NIC.
There were no RX/UART/meta logging drops during this test. A physical reset then
cleared configuration, all peers and the session, and rejected the old SEND.

See [verification and handover](../../analysis/development/gateway-v3.md). The
host-side agent has been given the v3 contract and exclusive radio ownership.

## Application UART baud

Both TI and MG21 accept `UART_BAUD=115200|230400|460800|921600` at build time.
The default is 115200; use a clean TI build when changing it so the UART driver
and shared capability response agree. The standalone v3 hardware checker takes
`--baud 460800`; host hardware tools use the daemon configuration. Set the daemon JSON `serial_baudrate` to
the same value. Startup and `CAPS baud=` report the compiled application rate.
No live negotiation or network persistence is introduced. Higher rates require
validation on each board/USB bridge; accepting a build value is not a hardware
qualification. MG21 460800 verification is in `host/DEPLOYMENT.md`.

ROM/Gecko bootloader transport stays at 115200. The TI signed updater communicates
with the running application at its configured rate; ordinary application
updates must preserve that rate. Changing it requires a coordinated host config
migration, including the matching rate for any rollback image.

## TI release 0.12 qualification (2026-11-01)

The cod.m CC2652P build enables `EC_AUTO_ACK_PHASE=1`, matching the MG21
profiles. It uses the shared MCU heartbeat, state-query and aligned output
transaction implementation with TI's **65,536 Hz** scheduler clock. The
200 ms grid uses fractional tick positions; output limits remain six attempts
and four seconds. The Pi retains authoritative network and device state.

`tests/test_output_grid_ti.py` exercises the native-clock fractional slots,
32-bit wraparound, confirmation cancellation and automatic ACK phase stamps.
The build and signed application package remain release **0.12**. This board
is CC2652P (352 KiB flash / 80 KiB SRAM), not CC2652P7.

See [the dated TI hardware verification](../../analysis/development/ti-012-20261101.md)
for results and the deployment handoff. For the Docker deployment, stop/start
with `livolodctl`; the updater's native `--service livolod` option must not
start a second daemon. After trial activation, start the matching Docker
release promptly so it restores the volatile radio configuration and confirms
the image within the bootloader's 60-second trial window.
