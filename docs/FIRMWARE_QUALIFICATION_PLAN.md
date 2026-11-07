# Exact-board firmware qualification plan

Prepared on 2026-11-07, initially against software baseline `c306cbf`; the frozen
0.14 candidate packet below supersedes the initial build prerequisites. This is
an execution plan, not a qualification result. Release engineering owns artifact versions,
public-download acceptance and catalog eligibility; these are separate gates.
Any dependency, signing-key or USB-identity change requires newly identified
artifacts. Published or recorded 0.13 bytes must not be replaced under that name.

## Available equipment

Read-only SSH inventory on 2026-11-07 found the following. No UART was opened,
service changed, GPIO driven, firmware flashed or switch operated by this audit.

| Board | Observed location and role | Availability for physical tests |
| --- | --- | --- |
| Nordic PCA10059 | Standalone Pi `10.1.1.21`, application USB device; standalone daemon running | Requires an agreed bench maintenance window and a person able to operate SW2/unplug it |
| cod.m CC2652P | Fresh exclusive VERSION/CAPS/update-status probe on `.21` reports application 0.12, bootloader 0.2 and the development key | Application-reported trust is not independent bootloader readback; release-key reprovisioning and physical recovery still need an operator |
| Sonoff ZBDongle-E | CP210x USB device on sniffer Pi `10.1.1.120`; no capture process found | Previously the independent sniffer; repurposing it removes that reference receiver until another receiver is assigned |
| ZB-GW04 | Owner will provide a dedicated test unit later; exact revision and baseline remain to be identified | Await the dedicated unit. The active HA gateway at `10.1.1.40` is excluded from qualification; the earlier [maintenance proposal](ZBGW04_HA_MAINTENANCE.md) is superseded |

The bench Pi has GNU Arm GCC 14.2.1 and the existing pinned SDK trees. Builds
can run in fresh temporary directories without owning a radio. The local Mac
has a native C compiler but no ARM cross-compiler or container runtime on PATH.
The owner confirmed they can operate the radios and both isolated one- and
two-gang test switches are available. Scheduling, board-specific recovery
access and authorization for each live maintenance operation remain pending.

The initial inventory above was passive. A subsequent
[TI read-only baseline](verification/ti-readonly-baseline-20261107.json) used
three documented commands at 460800 with exclusive ownership and restored the
UART settings. It made no reset, GPIO, service, network or firmware changes.
Its reported key differs from the new release key; no key migration occurred.

## Evidence already available

The four 0.13 artifacts and their original source binding remain in
[the trial record](FIRMWARE_013_TRIAL.md). A fresh run of all 200 existing firmware
tests passed on 2026-11-07: 183 shared gateway, seven TI update, nine MG21 UART
and one Nordic clock test. These are simulated/native-compiled tests.

Fresh isolated ARM builds of all four profiles using the existing pinned SDKs
and GCC 14.2.1 reproduced the recorded 0.13 application bytes exactly. No SDK
or radio was modified. The subsequent Nordic USB configuration change also
preserved default application bytes. A compile-only synthetic identity changed
exactly four descriptor bytes, release mode rejected development defaults, and
switching the same build directory back restored the original binary. Two new
native tests cover identity selection and invalid/unqualified identity rejection.
This validates build behavior, not any right to use the synthetic identifiers.

Only PCA10059 has an observed 0.12-to-0.13 Web installation followed by normal
daemon restoration and restart. Its registry was empty. TI and both MG21 boards
have offline 0.13 evidence plus bounded earlier 0.12 hardware results. Neither
those earlier runs nor a byte-identical rebuild establish a new release's
complete physical update/recovery qualification.

## Common test sequence

### Frozen 0.14 candidate packet

Firmware source is `0fd515ca156823e513d2d86f0507b8da9b8558e8`. Two fresh
builds produced identical application/bootstrap bytes and canonical packages.
The Nordic ZIP has normalized metadata with unchanged member bytes. The signed
TI package was produced locally with the laptop-held release key; its embedded
application key and the bootstrap key match the tracked public key. No existing
radio trust was changed.

| Board / artifact | SHA-256 |
| --- | --- |
| cod.m CC2652P 0.14 signed application | `fba500a304e9a16884c2fc0ea57772397f239f199e5fc4daae6a4d5bd660798c` |
| cod.m CC2652P bootstrap 0.2 with release key | `3405e403b0175258a7bc8282dbb58c922eac46f6ed211fc8ff36e70d804baa8b` |
| Nordic PCA10059 0.14 DFU ZIP | `83d230089be60e60bf4d16f4049cf8725f9d9533478877bb0845ddac880a262a` |
| Sonoff ZBDongle-E 0.14 GBL | `87de29744fd7b991e0da8cdfda0fd160e12bd749764d4bca8c1337683804a7e6` |
| ZB-GW04 1v2 0.14 GBL | `b1358ed6f1ec77043544ca44e84f90d8214817b36d8185eea341ed70b67ba4c7` |

The [readiness record](verification/firmware-readiness-20261107.json) binds the
build/input/notice inventory and distinguishes completed offline work from
pending hardware acceptance. Private staging is
`.local/release-0.14-candidate-0fd515c/`. These artifacts have not been publicly
released or admitted to an updater catalog. The 751 input hashes were collected
after both builds, so the inventory does not claim immutable during-build SDK
snapshots. Tech Lead approved the exact per-board notice mapping with inventory
SHA-256 `cc139af7280d530954f3484533ce5608a91b64a8803f1fbb936f4fe808b4f930`.
Final deliverables must retain these files and present the reviewed terms in
the download/image channels; that publication check remains pending.

For ZB-GW04, the owner will supply a dedicated test unit later. The active HA
gateway will remain in service; the earlier 90-minute HA outage proposal and
its pending approval question are withdrawn. The staged private bench runtime,
candidate artifacts and notices remain available for the dedicated unit.

On arrival, identify its exact board revision, USB path, existing application,
bootloader and physical recovery route before selecting an artifact. The
current candidate is specifically for `zb-gw04-1v2`; neither that identity nor
the HA gateway's 0.13 baseline and confirmed button access transfers to a new
unit. Preserve the dedicated unit's own recoverable baseline. Use an isolated
database/network and the available test switches. Reassess bench-host ownership
and any Nordic service interruption when scheduling; no such interruption is
scheduled or authorized now. Qualification of the actual HA updater remains a
separate test, not a consequence of successful manual bench testing.

### Execution checks

1. Identify the exact board and exclusive owner. Preserve the current artifact,
   application/bootloader identities, configured USB profile, catalog and a
   consistent database/configuration backup. Record the final candidate hash,
   source revision, SDK/toolchain inputs, notices and helper versions.
2. Probe current `VERSION`, `CAPS` and supported update status without changing
   network state. Confirm exact board, UART protocol 3 and application rate
   460800. Nordic USB uses the rate as compatibility metadata.
3. Run negative preflight cases for wrong board, version, digest, signing key
   where applicable and baud. Each must fail before a mutating command. Use
   synthetic bad packages for these checks, never intentionally program them.
4. Perform one exact-artifact update through the production maintenance path.
   Record worker ownership and stages, transport transitions, restored
   capabilities, peer/session restoration and durable final status. Transfer
   completion alone is not success. Restart the daemon and verify no replay.
5. With a confirmed physical recovery route and an available operator, exercise
   the board-specific interruption/recovery cases below on a bench device.
   Preserve logs from failures as well as successes. Do not infer automatic
   rollback for boards that only have a recoverable factory bootloader.
6. Verify application transport integrity with the existing bounded UART tests
   and diagnostic counters. For RF/control acceptance, use isolated one- and
   two-gang test switches, a distinct network identity and an independent
   receiver. Verify output commands, physical state reports, pairing closure,
   lease expiry and host-silence behavior without changing retry/timing policy.

RF/control trials require explicit access to those test switches; active HA
devices are not implied test targets. Same-version update rejection must not be
bypassed to manufacture an upgrade test. Select a valid earlier bench baseline
or an explicitly scoped recovery operation.

## Board-specific recovery prerequisites

### cod.m CC2652P

This is the verified 352 KiB flash / 80 KiB RAM CC2652P, not a CC2652P7 target.
Historical setup uses resident signed-update bootloader 0.2, UART `/dev/ttyAMA0`,
BCM GPIO23 RESET and BCM GPIO24 FLASH. Verify configured GPIO chip/line identity
before use; retain configurable Pi pin assignments.

Confirm the installed bootloader key before selecting a package. Existing
test-key radios must retain that trust anchor unless an explicit one-time
reprovisioning is chosen. A project release key needs a matching bootloader and
application; a normal signed update cannot silently rotate the bootloader key.
The publisher's private key must never be required on end-user gateways.

First installation must use the board's own preserved CCFG/factory bytes and
the verified ROM recovery configuration. Do not distribute another module's
captured factory configuration. Have the ROM/button or verified GPIO recovery
route ready before bootloader changes.

Test normal signed transfer, exact image digest and trial confirmation after
session restoration, durable restart, interrupted transfer, and an intentionally
unconfirmed trial's reset/timeout rollback. Verify the previous signed image
and its digest after rollback. Recheck that authoritative network state remains
on the host.

### Sonoff ZBDongle-E

Preserve its existing Gecko bootloader and exact known-good sniffer/gateway
recovery GBL. Historical bootloader identity is Gecko 2.4.2 at 115200; probe the
actual unit before relying on that value. Have physical boot/reset access and
the pinned flashing helper available.

Exercise application `BOOTLOADER`, Gecko transfer at 115200, application return
at 460800, normal host restoration and restart. Check 6000 bounded integrity
requests and UART/DMA error counters. Any interrupted-upload recovery test
must preserve the bootloader and token areas. Replacing the sniffer requires a
separate reference receiver for subsequent RF comparison.

### ZB-GW04 v1.2

Use the exact CH340/PD4 profile. Do not assume Sonoff RTS/DTR boot-entry behavior
applies. Identify and preserve its bootloader before selecting recovery steps.
Use the same update, restoration and transport checks as Sonoff, but record
separate evidence for this board. Sonoff results cannot qualify this target.

The current HA installation is excluded until a spare or an operator-approved
maintenance plan is available. A maintenance plan must include a backup,
expected lighting interruption and restoration of the original gateway role.

### Nordic PCA10059

Retain the factory MBR/DFU bootloader and known-good application-only DFU ZIP.
Prepare the qualified isolated nrfutil 6.1.7 helper, exact physical USB topology
and separate application/DFU serials. Factory DFU remains `1915:521f`.

Confirm physical SW2 access. In earlier stalled-application bring-up, a press
while powered did not always enter DFU; holding SW2 while plugging in was the
working fallback. Do not promise an unattended reset path based on that result.

Test application-to-DFU-to-application update and recovery after manually
entering DFU. Verify exact identity matching across reconnects, the new
application USB identity if changed, preserved database/settings and restart.
No automatic rollback is provided. A new USB identity requires coordinated
host selection/publisher support; changing descriptors alone is insufficient.

## Completion record

For each exact board, retain candidate hash, source/build inputs, version,
initial/final hardware identity, helper/key identity, backup/recovery reference,
operator and timestamps, executed steps, results, failed attempts and explicit
limitations. Release engineering decides manual-download acceptance separately
from automatic updater eligibility. Open licensing/signing/USB conditions must
remain visible even if a physical test passes.
