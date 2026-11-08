# HA radio flashing release readiness

Initial audit on 2026-11-08 used source
`6ae80d7c15dbea5d96fb7a7ac1f5530952534d7e`. Subsequent updates below record the
resumed target authorization and host-local backup outcome.
The owner subsequently resumed work and authorized destructive testing of the
currently connected ZB-GW04 on the HA host. This supersedes the earlier spare
wait and Sonoff-first proposal. It does not authorize publishing a release or
discarding HA configuration/paired-device state. The Tech Lead requires review
of the concrete backup/artifact/ownership plan before any service stop, flash
or deployment. Release engineering owns
the acceptance inventory and code-quality review; HA owns setup and update UX,
Unix owns the daemon/app execution path, and ZigBee owns exact-board evidence.

## Decision and smallest honest scope

The existing HA updater is a **routine application upgrade** path. It requires
live project `VERSION`/`CAPS`, exact board/protocol/baud, a newer candidate and,
for TI, the matching resident signing key. Stock coordinator firmware cannot
satisfy that contract. Installing the integration or app does not convert it.

Use the currently connected exact ZB-GW04 1v2 as the first engineering target.
ZigBee is the sole hardware operator; release engineering takes filesystem and
runtime inventories/backups without opening the radio. Prefer the preserved
Gecko bootloader and application-only path. Starting provisioning from the
existing project application can exercise the bootloader transfer, but does not
prove entry from factory-stock firmware. This is not a ZB-GW04-only public
release. Other radios remain untouched; do not include the experimental XIAOs.

Keep three acceptance milestones distinct:

1. **Engineering pilot:** an isolated first-install state/store prototype with
   a test-only fake backend and coordinated HA fixtures, followed by a separately
   reviewed exact-board experiment on the authorized connected ZB-GW04.
   The prototype is not an operational stock flasher.
2. **First user-usable software slice:** clearer HA radio status, read-only
   preflight and guidance to the standard update action. This can explain an
   empty catalog and recovery needs now; flashing still requires an accepted
   candidate and qualified backend. Prototype controls must not imply that a
   fake backend programs a board. Publication of this slice needs an explicit
   scope decision if it precedes the complete firmware-bearing release.
3. **Full firmware-bearing release:** the complete inventory and physical gates
   below. A useful diagnostics slice or passing pilot does not close this gate.

The smallest complete release under the existing owner-approved policy still
contains accepted manual-download artifacts for all four required boards and
the separately accepted TI bootstrap. Its runtime update catalog may contain
only the independently qualified subset, initially ZB-GW04 if its tests pass.
Nordic/manual-only downloads must not become automatic offers merely to make
the catalog nonempty. See [bundle acceptance](FIRMWARE_RELEASE_BUNDLE.md).
The source of this requirement is the owner's instruction that every release
include firmware for all supported radio hardware, recorded in
[release process](RELEASE_PROCESS.md). Its concrete schema-1 board set is
`REQUIRED_BOARDS_V1` in `tools/firmware_release.py`; the publication threshold is
`require_firmware_after: 0.5.0` in `host/release/firmware-policy.json`. Earlier
software-only maintenance exceptions are scoped exceptions, not a repeal.

A release advertised as easy first-time flashing **from HA** additionally needs
an explicit stock-conversion path. The current manual computer/CLI handoff can
support an honestly labeled manual-setup release, but cannot meet that claim.
A future conversion flow must select the exact board and device, explain the
loss of its existing coordinator role, establish exclusive ownership and a
recoverable baseline, invoke a fixed backend, and verify the project application
before normal onboarding. It must not weaken routine-upgrade identity checks.
HA/Unix/Tech Lead should settle that flow before implementing or promising it.

## What exists and what remains gated

| Area | Existing evidence | Release gate |
| --- | --- | --- |
| Versions | Main allocates distribution/client 0.6.0, daemon 0.15.0, radio compatibility 0.14; published app 0.5.1/daemon 0.14.1 use 0.13, integration-only 0.5.2 is separate | Freeze the actual candidate and all channel metadata; do not mix maintenance and development identities or change published bytes |
| Public catalog | `host/livolod/firmware/manifest.json` has zero artifacts and compatibility 0.14 | Accepted real bundle, pinned inventory/source, installed catalog verification; an empty catalog cannot offer installation |
| Final candidate artifacts | Four 0.14 applications plus TI bootstrap are hash-bound in the [readiness record](verification/firmware-readiness-20261107.json); repeat builds, signing and notice mapping reviewed | No final 0.14 physical qualification yet; bind every claim to those bytes or rebuild/review a new candidate |
| HA update interface | Standard update entity, read-only preflight, durable request identity, progress/reconciliation and blocking diagnostics | Verify actual install/upgrade/reload from supported published software, including lost replies and preservation of HA/network identities |
| HA app/runtime | ARM64 app wraps daemon image; fixed helper dependencies are included; bundled or persistent local catalog supported | Test exact final image, non-root device access, process handoff and recovery; no AMD64 claim yet |
| Stock conversion | Board-specific local bootloader/ROM/DFU instructions | No HA stock-conversion endpoint/workflow; cannot rely on a Livolo identity from stock firmware |
| Nordic app configuration | Container has isolated Python 3.10/nrfutil 6.1.7; standalone supports explicit USB profile | HA app options/translation do not expose that profile; helper presence alone cannot enable Nordic HA updates |
| Distribution terms | Accepted scoped MG21 route and exact staged notices; Nordic shared namespace selected; TI release key selected and used for staged candidates | Preserve exact notices/agreement/provenance in every final asset and image; verify visible channel presentation. Existing review is not evidence of final delivery |

The current Docker recipe includes `universal-silabs-flasher==0.0.31`, the TI
cryptographic dependency, and the separate Nordic helper. Runtime import/CLI
checks establish availability, not USB permissions, bootloader entry or flashing
reliability. Native installations need the flashing extras; native wheels do not
install the separate Nordic interpreter. The HA integration does not carry or
download radio images. Default bundled delivery should avoid requiring users
to stage catalogs through an administrator shell for ordinary setup.

## Exact-board acceptance matrix

| Board | Initial conversion / recovery | Routine HA eligibility still needed |
| --- | --- | --- |
| `sonoff-zbdongle-e` | Exact application-only GBL, retained Gecko bootloader; historical 2.4.2/115200 and entry method must be checked on the selected unit; application 460800 | Final-byte stock install/recovery, then HA worker installation, ownership, restart and interruption/recovery using the actual packaged helper/runtime |
| `zb-gw04-1v2` | Its own GBL/pins and preserved bootloader; historical Gecko 1.9.2, distinct CH340 transport; do not assume Sonoff reset sequencing | Connected HA unit now explicitly authorized; fresh identity, recoverable backups and root-reviewed plan before mutation. Another MG21 board's evidence does not qualify it |
| `codm-cc2652p` | Release-key bootstrap 0.2 plus signed application, assembled with that unit's preserved CCFG/ROM recovery configuration | Provisioned matching key, signed transfer, exact hash/trial confirmation and interruption/rollback evidence; existing test/owner-key radios cannot silently accept the release key |
| `nordic-pca10059` | Preserve factory MBR/DFU; application-only ZIP; deliberate SW2 recovery and exact application/DFU identities | Public `16c0:27dd` / `livolo.sfires.net:` identity migration, USB handoff/restart/recovery, explicit app configuration and device access; keep HA automatic installation disabled meanwhile |

ZigBee's audit confirms the observed historical Nordic 0.12-to-0.13 integrated
Web trial used standalone software, an empty registry and legacy `cafe:4001`.
It does not qualify HA, public USB identity migration or the final 0.14 image.
The technical checks in the four-board [physical plan](FIRMWARE_QUALIFICATION_PLAN.md)
remain applicable; its earlier exclusion of the connected HA gateway is
superseded by the explicit resumed authorization above.

## Connected ZB-GW04 preflight

Read-only SSH on 2026-11-08 found `app_3fbd5450_livolo_ec`, app 0.5.1,
daemon 0.14.1 and `universal-silabs-flasher` 0.0.31. The container image ID is
`sha256:8ded3663a6769734090dfe9a63d5c8c2a31ef49c55b528e7c5388ad7a6e008e0`;
its start time is `2026-11-07T21:21:28.379790036Z`. HA Core is 2026.11.0.
The app has persistent config/options, catalog and SQLite/WAL files. These
observations establish presence, not a completed backup or successful restore.

The selected candidate application is 0.14 with SHA-256
`b1358ed6f1ec77043544ca44e84f90d8214817b36d8185eea341ed70b67ba4c7`.
The intended return application is 0.13 with SHA-256
`5dc5624d45c48bd3003012a0391922d288a03d2ff22f00a2e92757b70028241c`.
ZigBee must verify actual local/HA bytes and entry/recovery prerequisites.
Neither a saved GBL nor the software backup is a full dump of this radio.

Before mutation, bind the following to a single readiness record:

- Retrievable, protected exact app options/configuration and native credentials,
  persistent catalog, consistent SQLite backup with integrity check, and durable
  firmware journal. Preserve installed runtime overlays as well as image digest.
- HA integration files and entry/device/entity/area/options records, with
  before/after identity comparison; preserve paired devices and network identity.
  A selective registry snapshot must not be described as a full HA backup.
- Candidate/return application validation and hashes; preserved bootloader and
  actual physical recovery method; single radio owner and competing-process check.
- A reviewed HA runtime targeting 0.14, since the active 0.14.1 daemon targets
  radio 0.13. Bind its helper/executable/configuration equivalence to the tested
  runtime and plan restoration to the exact current app/0.13 pairing. A manual
  bench wheel does not establish HA updater qualification.
- Root's execution sequence, bounded interruption/recovery checkpoints, and
  final application/session/database/HA identity verification. Preserve any
  failed-job journal before considering database restoration; never blindly
  restore an older database over changed state.

The first proposed sensitive backup export to a protected laptop directory was
rejected by automatic approval review before execution: explicit authorization
for copying credentials/configuration/database to that destination is required.
No archive was retrieved by that attempt. A second review also rejected export
of the derived hash/count receipt. The accepted safer route retained all backup
bytes and verification receipts on the HA host, in a protected persistent
backup directory. Both archives were verified there, including every member
hash and a temporary SQLite restore with a successful integrity check. The
exact original app image was saved and its image configuration digest verified.
Only command success was returned; no rejected payload was exported.

The Tech Lead accepted this host-local selective backup for app/radio rollback
while the HA host and storage remain intact. It is not a full HA backup and
does not protect against host/disk loss. Reverify locally at execution; preserve
the private archive paths and receipts in the maintenance handoff. No service
stop, deployment or radio action was performed during backup preparation.

The first physical window is a manual prerequisite: exact 0.14 application
installation and disarmed identity verification, preserved-bootloader recovery
to 0.13, then restoration of the unchanged original app/session. It must not be
reported as an HA updater or factory-stock entry qualification. The separate
private 0.14-compatible HA runtime can be prepared offline for a later reviewed
updater trial; it does not block this manual prerequisite or justify admission
bypasses in the currently installed 0.13-compatible daemon.

The private candidate runtime is frozen to the clean initial audit source above,
with daemon 0.15.0 and compatibility 0.14. Use the HA candidate packager and
explicit build arguments, pinned bases and a separate ARM64 build context. Do
not copy the shared working tree, unfinished provisioning prototype or public
release metadata into that runtime. Its image build and installed smoke checks
remain pending; source selection alone is not runtime acceptance.

## Required order of work

1. Agree first-time setup UX versus routine upgrades and the advertised board
   subset with the Tech Lead. Keep complete-download and updater eligibility
   decisions separate. Record the exact source, board and helper identities.
2. Prepare the fixed ZB-GW04 conversion path and negative tests if HA conversion
   is selected; otherwise provide an explicit manual handoff without claiming
   stock flashing inside HA. Do not add arbitrary image paths or shell commands
   to the remote API. Reject unknown/ambiguous devices before mutation.
3. Freeze candidate artifacts, final dependency input provenance, notices and
   release-key fingerprint. Existing staged bytes may be reused only with an
   explicit evidence binding; no historical capture or failed result is relabeled.
4. Run offline tests below, then review the concrete authorized-target trial.
   Qualify public boot/basic control/manual install/manual recovery on all four
   boards, and the full updater lifecycle only for advertised automatic paths.
5. Assemble the accepted four-board public bundle. Include TI bootstrap outside
   the runtime updater catalog. Pin its source/inventory/archive hashes in
   software metadata; do not use the non-publishing candidate bypass to ship.
6. Compare payload and notice bytes across public bundle, wheel/source/archive,
   standalone ARM64 image and HA ARM64 image. Test installed helpers/catalogs
   and preserve persistent local catalogs, database and credentials on upgrade.
7. Bind physical evidence to a pre-publication runtime digest. For the final
   image retain detached executable/helper/configuration equivalence and smoke
   evidence; an image cannot embed its own final digest in its qualification.
8. Only then review publication: immutable assets/tags, public download hashes,
   anonymous image pulls, HA channel discovery and actual installer upgrades.
   Advance app metadata only after exact-image acceptance.

## Offline install and upgrade test plan

These checks use synthetic non-flashable payloads and simulated radios. They
must never be reported as physical qualification or authorize device access.

| Layer | Required cases / existing entry points |
| --- | --- |
| Release bundle | `tools/test_firmware_release.py`: all four boards plus TI bootstrap, manual-only versus automatic subset, wrong hash/source/key/board, missing notices, failed/simulated physical evidence, traversal/symlinks, candidate bypass rejected for publication |
| Installed delivery | `tools/check_firmware_delivery.py`: real wheel/sdist/standalone/HA artifacts with synthetic inventory; `verify_installed` compares inventory, catalog, notices and payload bytes; build both ARM64 images in disposable CI without pushing |
| Catalog/runtime | `host/tests/test_firmware_catalog.py` and preflight/manager tests: empty/missing/corrupt catalog, missing helper, wrong board/baud/key/version, replacement during job, stable admitted bytes, atomic publication and service recreation |
| HA lifecycle | `homeassistant/tests/test_native_firmware_lifecycle.py`, `test_update.py`, `test_preflight.py`: native API with Web disabled, explicit install, lost reply/no replay, HA reload, daemon restart, worker death, wrong resulting identity, confirmation only after restoration |
| Stock-conversion additions | New tests required for any new bootstrap API/UI: no project identity, explicit exact-board choice, ambiguous USB bridge rejected, ownership conflict, consent/entry instructions, missing bootloader, interruption and persistent recovery result; synthetic completion must not claim physical success |
| Installed upgrade | `tools/check_daemon_upgrade.py`, `tools/container_check.py`, HA channel tests: published app 0.5.1/daemon 0.14.1/compatibility 0.13 and integration 0.5.2 to candidate; preserve registry/options/catalog/request history across replacement and rollback; update the current 0.5.0-only baseline matrix to include latest supported maintenance versions |
| Runtime permissions | Final ARM64 image, UID/GID 10001, helper CLI/imports, dynamic device transitions/locks simulated without devices, network-disabled package smoke; separately test actual Supervisor/device permissions on bench |

For a real routine upgrade, use a valid older project baseline; do not bypass
the newer-version requirement for a same-version demonstration. For a stock
conversion, success requires the expected application identity and a separately
tested normal HA session after transfer. A 100% upload, disappeared offer or
successful helper exit alone is not installation confirmation.

## Audit verification

Fresh release-bundle regression: 23 tests passed. Fresh persistent-catalog
regression: 11 tests passed. HA lifecycle/update/preflight: 58 tests passed.
The initial HA run was blocked by sandbox loopback binding; the successful
rerun used local test-server access, not any HA installation or radio. These
working-checkout audit checks do not accept concurrent new-flow edits or
replace the final frozen-source regression and source-quality review.

The separate reviewed HA presentation changes were committed as `5b99669`,
followed by unresolved-job guidance priority fix `897d885`. Independent focused
checks passed 69 and 72 tests respectively; the initial commit's six CI checks
passed. These are software-source changes, not a live deployment, stock flasher
or firmware release. No release policy, version, historical evidence or device
state was changed by the readiness audit. Unrelated host documentation edits
remain outside its scope.
