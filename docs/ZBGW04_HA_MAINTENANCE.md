# ZB-GW04 Home Assistant maintenance plan

## Current direction: 2026-11-08

The owner has resumed HA radio-flashing development and explicitly authorized
destructive testing of the **currently connected ZB-GW04** on the HA machine.
The earlier requirement to wait for a dedicated spare no longer applies to
that unit. Sonoff MG21, Nordic and TI modules can be moved later at the Tech
Lead's request; they are not part of this first hardware window. MG24/C6 work
remains paused and both XIAOs remain experimental.

This authorization concerns the selected radio, not deletion of HA's
configuration, paired-device registry, database or other radios. Release
engineering owns fresh backups and deployment restoration; the ZigBee
specialist owns exact-board identification and firmware/recovery checks.
Coordinate a single hardware owner before opening the radio. Prefer an
application-only operation that preserves Gecko and recovery regions when it
answers the test; permission to erase the unit is not a reason to erase it
unnecessarily.

The historical observations and staged artifacts below are references only.
Recheck the current deployment, physical identity, ownership, artifact hashes,
host compatibility and recovery route, and prepare the bounded execution and
restoration plan before stopping services or programming. Routine updates from
Livolo firmware and first installation from stock firmware are distinct tests;
one does not qualify the other. The current permission does not itself qualify
an artifact for publication.

## Previous decision: 2026-11-07

**Superseded by the authorization above:** the owner will provide a dedicated ZB-GW04 for
testing later. The active HA gateway is no longer a qualification target. The
90-minute outage proposal and its pending approval question are withdrawn; no
HA outage or Nordic bench-service stop is scheduled. Follow the current
[dedicated-unit qualification plan](FIRMWARE_QUALIFICATION_PLAN.md).

The material below is retained as historical planning and artifact/recovery
reference, not an active execution request. The staged bench runtime can be
reused after identifying the dedicated unit, but its revision, firmware,
bootloader and recovery route must be verified independently. Do not assume
that this HA unit's baseline, USB mapping or button access describes the spare.

## Historical proposal

Prepared 2026-11-07. **Planning only: no permission to stop, move, reset, flash
or reconfigure the active HA gateway has been given.** The owner requested a
maintenance plan rather than identifying a spare board. Execution needs a
separate agreed window, exact artifacts and an available physical operator.

This plan preserves the existing HA installation and its radio network while
obtaining exact-board evidence. It does not authorize testing arbitrary recovery
failures on the household's only working gateway. Release engineering owns the
final candidate, compatibility target, hashes and acceptance record.

## Facts and items to confirm

| Item | Evidence available when drafting | Required confirmation before execution |
| --- | --- | --- |
| HA host | November 7 HA specialist verified management SSH and Docker/HA CLI access to `10.1.1.40` | Recheck access at window start; keep credentials private |
| Application owner | HA specialist's November 7 read-only observation: `3fbd5450_livolo_ec`, app 0.5.0, daemon 0.14.0, compatibility 0.13 | Recheck at window start; preserve exact immutable image and configuration |
| Radio | November 7 existing daemon status: `zb-gw04-1v2`, firmware 0.13, ready, last firmware job confirmed | Fresh daemon-owned CAPS/VERSION evidence and USB identity/path at window start; application rate must remain 460800 |
| Bootloader | November 1 physical test: Gecko 1.9.2 at 115200 | Actual unit's preserved bootloader and working manual entry, verified during the authorized window |
| Network | November 7: gateway `2a992b12`, tag `a7`, three registered switches with five outputs reported online | Recheck registry, slots/profiles and command state at window start |
| HA identity | November 7 stored registry: integration 0.5.0, 12 enabled entities and four devices; only Bedroom device has an assigned area | Preserve private exact-ID export; verify live loaded HA state separately |
| Old deployment | November 7: `local_livolo_ec` 0.1.0 stopped, manual boot, watchdog/automatic updates disabled | Confirm it cannot restart and compete for this UART |
| Catalog | November 7: persistent `firmware_catalog=local`, `/data/firmware`, schema 1 compatibility 0.13 | Recheck manifest/payload hashes, backup and recreation behavior |
| Access to device | Owner confirmed radio operation, isolated one-/two-gang switches, and accessible buttons labeled BOOT and RESET | Verify the entry sequence on this exact board before programming; agree the window |

Sources: [HA migration](verification/ha-app-migration-0.3.0-20261105.json),
[preflight](verification/ha-zbgw04-0.13-preflight-20261105.json),
[catalog refresh](verification/ha-catalog-refresh-20261106.json), and
[ZB-GW04 0.12 hardware run](../analysis/development/zbgw04-012-20261101.md).
The HA specialist supplied the November 7 observations at 15:08–15:10 UTC from
existing status without UART commands. The active image ID is
`sha256:f0cca0732895704ee817df35419798ec97a1719bb1f7f607f1fb12e97b939480`;
the last confirmed firmware request is `bc5720c3-2d51-441c-90a5-a2b98a2c0eb4`.
The catalog retains the 0.13 artifact hash listed below. This proves reported
current state, not the full provenance or execution history of that update.
The HA specialist also confirmed these current configuration and storage facts:

- Active image `ghcr.io/psires/livolo-ec-ha-app:0.5.0`; app autostart enabled,
  watchdog and automatic updates disabled, Docker restart policy `no`.
- Generic CH340 identity `1a86:7523`, no unique USB serial;
  `/dev/serial/by-id/usb-1a86_USB_Serial-if00-port0` resolves to `ttyUSB0`.
  The generic by-id name alone does not establish physical board identity.
- `/data` is bound from `/mnt/data/supervisor/apps/data/3fbd5450_livolo_ec`.
  Authoritative database `/data/state.sqlite3` has a WAL; generated `daemon.json`,
  protected `options.json` and `/data/firmware` must be preserved with ownership
  appropriate to the app's UID 10001.
- Registered devices are `46715621` (two outputs), `47778828` (two outputs,
  Bedroom) and `66780945` (one output). MQTT is explicitly disabled in current
  configuration. Older stored MQTT preferences are not evidence of an active
  connection; no broker retained-topic audit was performed.
- Stored HA entry/entity/device identities were read and supplied separately
  for the private maintenance packet. The live HA state request failed, so
  storage inspection does not establish the live loaded integration state.
- Current YAML automation/script inspection found no direct Livolo references.
  Indirect, template-based or external command producers remain unverified.

Exclusive USB file-descriptor ownership is not yet established. Other installed
radio tools must be checked during preparation; their configuration alone does
not prove they have no open handle. No fresh backup or restore was performed.
No same-version 0.13 update should be forced for this plan.

## Operator and recovery prerequisite

The exact board is **ZB-GW04 v1.2**, not a generic CH340 dongle. The application
profile drives **PD4 low as the CH340 RTS-ready signal**. PD4 is **not** an
established boot-selection pin or a documented recovery-button substitute.
Do not ground an unidentified pad, alter wiring or apply Sonoff CP210x RTS/DTR
entry recipes to this board.

Normal entry uses the running application's bounded `BOOTLOADER` command through
the maintenance owner. Physical BOOT/RESET entry is the fallback described by
the board workflow, but the operator must identify the actual buttons and
confirm the sequence for this unit. Have the correct board documentation or a
technician-confirmed mapping before any flash. If that cannot be established,
postpone the flash; an inaccessible recovery button is a real prerequisite.

Preserve Gecko and token/security-engine regions. Application-only GBL writes
are limited to `[0x4000, 0x30000)`. Gecko's serial menu does not provide a full
flash backup/readback; do not describe a saved GBL as a dump of this unit.
There is no TI-style last-good-image rollback. Recovery means explicitly
uploading a separately verified known-good application through the preserved
bootloader, then restoring the host's authoritative configuration.

## Prepare the maintenance packet before downtime

The HA specialist and Release Engineer must supply a private execution packet
with these fields completed. Credentials and database contents must not enter
the public qualification report.

- Exact HA app slug, current image digest and available rollback image; current
  daemon software and firmware compatibility versions; confirmed host access.
- Current persistent app options/configuration, authoritative SQLite path,
  catalog directory/manifest/payload hashes, serial mapping, device permissions,
  native API credentials and MQTT settings, stored in a restricted backup.
- A consistent SQLite backup made with the SQLite backup API, or a cold copy
  after the owner is stopped. Check backup integrity and retain its WAL/state
  consistently; copying only a live main database file is insufficient.
- A supported HA backup and a private snapshot of the config entry, device and
  entity IDs, names, areas, disabled states, relevant automations and current
  physical/last-reported output states. Verify that the backup can be retrieved
  from another machine before taking the gateway offline.
- One exact candidate GBL, full SHA-256, source/input/notice records, selected
  version, board `zb-gw04-1v2` and matching host compatibility. Stage it locally;
  downloading a replacement during recovery is not the recovery plan.
- The known-good return GBL and its full verified hash, compatible host image,
  pinned `universal-silabs-flasher==0.0.31`, required permissions and a reviewed
  manual recovery command using the confirmed device path and 115200 rate.
- Named operator, permitted test outputs, start time, stop-new-tests deadline,
  communications method and agreed fallback lighting arrangements.

Historical fallback artifact retained locally (not the current radio version):
`ec-mg21.gbl`, firmware 0.12, SHA-256
`87e426ab7b65f9178acadfb7a85326c52bf987543d58ba40fd944bd0f21dc7de`.
Reinspect those actual bytes and confirm they match the intended baseline
before adopting them for recovery with its compatible host. The currently
reported 0.13 application uses the retained trial image hash
`5dc5624d45c48bd3003012a0391922d288a03d2ff22f00a2e92757b70028241c`
and is the first current-version return candidate to verify and stage. It is
not automatically the next release candidate. New bytes need their assigned
new version and hash.

### Frozen first-window proposal

The November 7 offline preparation selects the following exact artifacts for
review. This is a manual bench qualification followed by restoration of 0.13
to HA; it does not include a production 0.14 upgrade or HA updater qualification.

| Purpose | Version/source | SHA-256 |
| --- | --- | --- |
| ZB-GW04 application candidate GBL | 0.14, source `0fd515ca156823e513d2d86f0507b8da9b8558e8` | `b1358ed6f1ec77043544ca44e84f90d8214817b36d8185eea341ed70b67ba4c7` |
| Private bench daemon wheel | 0.15.0, compatibility 0.14, source `a410b2c4c0d9ccf35e06d6725067c434291a62bc` | `ec9dcd5706e9dbcf1ef3dc0082e6fb417a5333ac75d413b2f9128545bce5a54c` |
| Return application GBL | 0.13 | `5dc5624d45c48bd3003012a0391922d288a03d2ff22f00a2e92757b70028241c` |
| Retained HA image, ARM64 platform | Published app 0.5.0, daemon 0.14.0, compatibility 0.13 | `f0cca0732895704ee817df35419798ec97a1719bb1f7f607f1fb12e97b939480` |

Both GBLs passed offline board/version/layout/CRC inspection using the built
bench wheel. Its installed-wheel simulator test passed upgrade from published
daemon 0.14.0 to 0.15.0 and explicit rollback, preserving catalog bytes, registry
and request history. These checks do not establish physical operation.
The wheel contains no release firmware catalog and is not an HA image.

Private artifacts, inspection results and test logs are staged under
`.local/release-0.14-candidate-0fd515c/bench-packet/`; candidate firmware and
hash-bound notices are in the sibling `review-staging/` directory.

The active 0.5.0 container also has two private daemon overlays, recorded in the
[diagnostic receipt](verification/ha-pairing-diagnostics-deployment-20261107.json)
and [profile-fix receipt](verification/ha-pairing-profile-deployment-20261107.json).
Preserve these exact modules and the current HA integration in the fresh backup.
Recreating the published image alone loses the overlays. User pairing attempts
after those receipts may change the registry: capture current state at the
window, not merely the earlier three-switch baseline.

Before requesting execution approval, assign the bench host and independent
receiver, stage its pinned helper/runtime dependencies, and define the
stop-new-tests deadline and recovery budget. The owner has confirmed accessible
BOOT and RESET buttons; this is not yet evidence of successful bootloader entry.
The first window excludes injected interruption tests and production-output
commands. Isolated one-/two-gang control checks require the owner's explicit
approval in that window. Restore the return 0.13 application before reconnecting
to the retained compatibility-0.13 HA deployment.

The 0.14-to-0.13 return is an explicitly reviewed **manual Gecko recovery**:
stop the bench daemon, prove exclusive ownership, enter the preserved Gecko
bootloader through the confirmed physical route, and use the pinned helper at
115200 to upload the hash-bound return GBL. Then verify VERSION/CAPS at 460800
before returning the board to HA. The normal compatibility-0.14 updater refuses
this downgrade, and the maintenance CLI's recovery operation is Nordic-specific.
Do not relax either guard, retag the 0.13 artifact or reset an update journal.

Offline bench preparation is complete in a new private environment on `.21`:
`/home/sfires/livolo-ec/bench-zbgw04-014-20261107`. The exact wheel above,
`universal-silabs-flasher==0.0.31`, pyserial 3.5, 34 dependency wheels and the
reviewed notices are staged. Dependency validation and nine version/help/offline
artifact checks passed. The local receipt under
`bench-packet/pi-receipt/readiness.json` has SHA-256
`6d87490cf5280130a27504b3db208133a4c539749136d29a97308700aa788045`.
No existing service, SDK, radio or network configuration changed during staging.

Board-maintainer [physical-entry guidance](https://github.com/darkxst/silabs-firmware-builder/issues/99#issuecomment-2167321397)
describes holding BOOT while pressing and releasing RESET with USB power
connected, then releasing BOOT after a few seconds. This unit has **not** passed
that check. During the authorized window, require the expected Gecko identity
at 115200 before uploading; abort if it is absent. PD4 is not this entry method.

Reviewed return command template, **not executed**:

```sh
/home/sfires/livolo-ec/bench-zbgw04-014-20261107/venv/bin/python \
  -m universal_silabs_flasher \
  --device '<EXACT_REIDENTIFIED_ZBGW04_PORT>' \
  --bootloader-baudrate 115200 --probe-method bootloader \
  flash --firmware /home/sfires/livolo-ec/bench-zbgw04-014-20261107/artifacts/zb-gw04-1v2-return-0.13.gbl
```

Resolve the port only after physical board identification and exclusive
ownership are established. No force, cross-flashing, downgrade override or GPIO
reset option is included. Verify the hash again before use and VERSION/CAPS
after transfer; helper completion alone does not authorize HA reconnection.

Proposed budget: reserve 90 minutes with the owner present. Stop starting new
tests at minute 45 and use the remaining time for the manual 0.13 return,
restoration of HA and the Nordic standalone service, and identity/health checks.
If physical entry or backups cannot be confirmed, abort before programming.
This is a scheduling budget, not a guarantee of recovery time: if recovery is
unfinished, leave the affected services safely stopped and continue the agreed
recovery procedure with the owner. Never shorten verification to meet the clock.

Offline inspection can be prepared without opening the radio:

```sh
livolod-firmware-manage inspect \
  --board zb-gw04-1v2 \
  --artifact /absolute/path/to/reviewed-zbgw04.gbl \
  --version CANDIDATE_MAJOR_MINOR
```

Replace placeholders only after the packet is reviewed. Verify the independently
recorded SHA-256 as well as the inspection result. A valid GBL CRC proves neither
trusted provenance nor correct operation. Public catalog admission is separate
from authorization for an explicitly scoped qualification trial.

## Isolation and downtime design

Budget a maintenance window with recovery time reserved; do not promise a fixed
short outage before physical entry is confirmed. HA control, automations and
remote state updates for these switches will be unavailable while their gateway
is absent. Local button behavior during disconnection must not be relied on
without the owner's confirmation. Leave the original EC network and unrelated
HA integrations alone.

Use two stages. First qualify manual boot/recovery and transport on a bench,
then perform a separately bounded production return or actual HA updater trial.
Bench success alone does not qualify the HA updater path.

The bench instance must use a separate empty database and a new, reviewed test
network identity, with pairing closed, MQTT/HA discovery disabled and no copy
of production peers. Keep UART integrity checks disarmed/nontransmitting.
Do not restore the production database on a bench daemon. If RF tests are
required, assign isolated test switches and an independent receiver explicitly;
production lights are not implicit test loads.

The proposed bench host is the standalone Pi `10.1.1.21`, but it already owns the
Nordic gateway. Using it requires a separately approved, backed-up stop/mapping
change and subsequent restoration of that deployment. Do not assume its UART
or USB mappings are free. The Sonoff on `10.1.1.120` is the proposed reference
receiver, subject to confirmed availability. Never arm two radios with the
production network identity.

## Authorized execution sequence

These are future execution steps, not actions already performed.

1. HA specialist records fresh read-only daemon/API and HA identity snapshots.
   Confirm pairing is closed, no update/recovery job is unresolved and no
   output transaction is in progress. Record the initial physical output states
   with the operator. Stop if current identity differs from the reviewed packet.
2. After explicit approval to begin downtime, pause only the identified
   automations/integration paths that can issue commands to this gateway. Record
   their original enabled states. Confirm there are no queued or retained output
   requests that would replay on reconnect; do not purge unrelated MQTT topics.
3. Record and temporarily control the app's automatic restart/watchdog policy
   using the HA specialist's supported procedure. Stop the sole active daemon
   before moving the board or running an external helper. Verify the old app
   remains stopped and no process owns the UART. Never delete ownership locks.
   Take the cold data backup if a consistent online backup was not already made.
4. The operator moves the identified board to the reviewed bench setup if that
   stage is selected. Check its USB identity and exact application board marker
   under exclusive ownership. Confirm manual Gecko entry before programming;
   if entry fails, return the unchanged application and postpone the trial.
5. Perform only the reviewed application transfer. Bootloader communication is
   115200; application communication is 460800. Capture entry, upload result,
   exact post-flash VERSION/CAPS and startup. Do not flash bootloader, SE or token
   tags. Do not inject upload failures during the first successful baseline run.
6. On the isolated bench, verify startup unconfigured/disarmed and bounded UART
   integrity with counter deltas. Broader RF/control or interruption tests need
   their own assigned devices, operator approval and recoverable baseline.
   Preserve failed attempts; do not repeat destructive operations blindly.
7. Before returning the board, stop the bench owner and verify it cannot restart.
   Select either the qualified candidate plus compatible HA host or the recorded
   previous application/host pair. A firmware target 0.14 cannot use a catalog
   intended for compatibility 0.13; software package version alone is irrelevant.
   If a host app upgrade is needed, it must be separately staged, backed up and
   approved in this window rather than improvised after the radio is flashed.
8. Reconnect the same board to its original HA USB path. Verify device mapping
   and permissions, then start exactly one approved daemon. Restore authoritative
   network/peer/session state through normal daemon startup, not ad hoc UART
   writes. Check readiness and fresh identity before enabling command producers.
9. If qualifying the actual HA updater, select a valid earlier baseline and
   the reviewed candidate, use the normal durable Web/HA install request and
   record one request ID through restoration and confirmation. The daemon owns
   quiescence and UART handoff; do not stop it midway or launch a second helper.
   This planned stage necessarily restores the production network and requires
   explicit approval for that exposure. Do not reset/delete its journal or bypass
   same-version/downgrade rejection to force a test.
10. Complete the restoration checks below. Re-enable only the controls and
    policies paused in this window, after the operator confirms the lights are
    correct. Any restart qualification is one deliberate daemon restart after
    success, with the verified catalog persisted; no HA Core restart is assumed.

## Abort, recovery and return criteria

| Failure point | Required response |
| --- | --- |
| Before first mutation: missing backup, wrong board/hash/version, unavailable operator or unproven recovery | Do not enter flashing. If the service was stopped, restore the unchanged reviewed deployment and verify it before ending the window. |
| Bootloader entered, upload not started | Start the intact application through the known bootloader route; restore its host. Recheck identity rather than assuming a reset succeeded. |
| Upload failed/interrupted or application fails to start | Retain logs/journal; keep normal daemon stopped to avoid port races. Use the verified physical entry and pinned helper to upload the known-good exact-board application. Never erase/replace Gecko or tokens as an improvised fix. |
| Application starts but board/version/baud or USB path is wrong | Do not arm/restore production control. Resolve the mismatch under exclusive ownership or recover the previous image. |
| Worker reports 100%/transferred but daemon is not ready | Treat outcome as unconfirmed. Inspect durable status and hardware identity; do not declare success or issue duplicate install requests. |
| Recovery entry fails or reviewed image does not boot | End qualification, retain evidence and escalate to the physical technician. Leave automation disabled for this gateway; use the agreed lighting fallback. No guessed pads, mass erase or unreviewed image. |

The deadline stops **new tests**, not a flash already in progress. Let bounded
workers reach their documented outcome; do not power-cut an upload merely to
meet the schedule. Recovery may exceed the reserved window and requires an
explicit status update to the owner.

Software rollback uses the pinned previous host image and a compatible
consistent data backup when needed. Firmware recovery is a separate manual
operation; the normal updater will reject downgrades/same-version reinstallation.
Preserve the post-failure journal and state before any backup restoration.
Never overwrite the database merely to hide `unknown`/`recovery_required` or
make a failed job appear confirmed. Use the supported reconciliation path or
have the HA/Unix specialists define a recovery procedure before proceeding.

## HA and network restoration checks

- Exactly one daemon owns the correct ZB-GW04. Expected VERSION/CAPS, application
  rate, normal readiness and new session are verified. Pairing remains closed.
- Gateway ID/tag, registered IDs, slots, profiles and gang counts match the
  private before-state. No switches were deregistered or re-paired. Volatile MCU
  session IDs may change; persistent network identity must not.
- The original HA config entry, device/entity IDs, names, areas and disabled
  states remain intact. No replacement integration, duplicate MQTT discovery
  entities or duplicate command owner appeared. Relevant MQTT/native connections
  are restored according to the original design.
- SQLite integrity passes. Catalog/config hashes match the intended retained or
  explicitly changed configuration. App recreation must not silently discard
  the selected catalog; the older temporary container-layer activation is not a
  durable rollback/upgrade strategy.
- Existing lights have plausible fresh availability/state reports. Obtain
  operator confirmation of physical states; do not force stale saved output bits
  back onto lights that changed during maintenance. Any output test uses only
  the outputs expressly approved for this window, restores the agreed state,
  and records physical behavior. Query success is not required to confirm the
  flash and is not a reason to change timing/retry policy.
- A successful actual updater job is `confirmed` after restoration, retains the
  exact selected hash/request ID and survives the planned daemon restart without
  replay. A recovered previous application must be recorded as recovery/failed
  candidate outcome, never successful candidate installation.
- Original app boot/watchdog/automatic-update and automation settings are
  restored deliberately, while the superseded app remains stopped. The operator
  confirms normal use can resume before the maintenance window closes.

## Record and unresolved execution inputs

Publish only sanitized board/version/hash, source/input/helper identities,
timestamps, tested operations, outcomes and limits. Keep credentials, private
backups, detailed HA entity exports and network records protected. Separate
manual recovery evidence from actual HA updater evidence in the release matrix.

Before scheduling, the HA specialist must verify fresh recoverable backups,
live HA state and exclusive UART ownership; the operator must confirm physical recovery access; Release
Engineer must freeze the candidate/return artifacts and compatible host pair.
The owner has confirmed operator and test-switch availability. They must still
approve the downtime, bench-host changes, permitted switch tests
and recovery budget. None of those execution approvals is implied by this plan.
