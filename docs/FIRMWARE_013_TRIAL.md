# Firmware 0.13: standalone update trial

This work prepares four application-only images for a controlled update test on
the standalone Pi. It does not publish a production firmware channel. The
numeric daemon/firmware version is **0.13**, with UART protocol **3** and
application baud **460800**. The existing bootloaders are preserved.

## Exact images

| Board | Container | SHA-256 |
| --- | --- | --- |
| cod.m CC2652P | Signed `.ecupd` | `7045a77cf7f5caf863215a6505e4fc17c2636ce0b71ade6e2d9d0b6bb87a8e10` |
| Sonoff ZBDongle-E | Application `.gbl` | `bdced8cc50f70419a5d5c8d44aa6236cc5c1bca4bf12b1766ab7cfbb813adb0e` |
| ZB-GW04 v1.2 | Application `.gbl` | `5dc5624d45c48bd3003012a0391922d288a03d2ff22f00a2e92757b70028241c` |
| Nordic PCA10059 | Application DFU `.zip` | `cdfd23a9da61cf8c1dddf2c9353e3638d14c8f8021f28886bf11abf84ce52e86` |

Generated images, signing keys and operator deployment files remain outside Git.
The development output directory is `.local/release-0.13`. Only the TI public
verification key accompanies its package. Its existing private signing key was
used locally and was not copied to the build host or catalog.

## Completed offline evidence

All four application images pass the shared strict board/container/version,
flash-range and integrity checks. The TI package also passes Ed25519 signature
verification. No bootloader image or factory settings are included.

Independent builds from clean objects produced byte-identical application
binaries. The toolchain is GNU Arm Embedded GCC 14.2.1 20241119, Debian package
15:14.2.rel1-1. `build-inputs.json` records hashes for 675 external source,
header and runtime/archive inputs. The source archive and its per-file manifest
bind the working source snapshot. Compiled inputs were subsequently verified
against reviewed commit `e2449c8ba9b71a8c74cfe7d95960587d915d4df1`. Of the 135
snapshot files, only three noncompiled README files differ from that commit.
`source-commit-binding.json` records that comparison.

The offline firmware tests passed: 183 gateway, seven signed-update, nine MG21
and one Nordic clock test, **200 total**. These tests do not establish the
behavior of a real USB firmware update.

The exact application bytes were compared with the hardware-tested 0.12 images:

- Sonoff and ZB-GW04 each differ in five ASCII version bytes and the application
  properties version integer.
- Nordic differs in five ASCII version bytes and its USB `bcdDevice` version.
- TI differs in five ASCII version bytes and two compiler-generated Contiki
  protothread continuation labels, which changed from source line 1165 to 1189.
  Both continuation references changed together; the surrounding instructions
  and all other application bytes are identical.

There are no changes to radio packet handling, query timing, retry limits,
output scheduling or persistent network ownership in these firmware images.
The bounded 0.12 hardware evidence is documented for
[TI](../analysis/development/ti-012-20261101.md),
[Sonoff](../analysis/development/output-grid-20261101.md),
[ZB-GW04](../analysis/development/zbgw04-012-20261101.md) and
[Nordic](../analysis/development/nrf52840-012-20261101.md).
That evidence supports an installation trial; it is not a new 0.13 endurance
test, larger-network qualification or guarantee of state-query reliability.

## Local catalog preparation

`tools/firmware_catalog.py` consumes an operator-reviewed specification containing
absolute artifact, qualification-record, public-key and notice paths. It reruns
artifact validation and binds each record to the exact board, version and hash.
The record must explicitly approve `standalone_local_update_test` and record
passing inspection, repeat-build, baseline-comparison, test and notice checks.
It preserves the review limitations and creates a new directory atomically;
it refuses to overwrite an existing catalog. It never opens a radio.

```sh
python tools/firmware_catalog.py \
  --spec /absolute/path/catalog-spec.json \
  --output /absolute/path/new-catalog
```

This local approval does not authorize public automatic update offers. The
default catalog shipped with the source stays empty. A deliberately configured,
read-only catalog mount is used for the standalone trial, with matching daemon
version 0.13. A 0.12 daemon must not be pointed at this 0.13 catalog.

The archive carries the project license, Monocypher notices and provenance,
the firmware dependency inventory, and the collected exact dependency notices.
Public binary redistribution review remains separate, including the Silicon
Labs MSLA-covered headers and applicable runtime/archive notices. The factory
bootloaders, stock recovery packages and vendor flashing helpers are not bundled.

## Live qualification boundary

Nordic update qualification is scoped to a dedicated standalone test deployment;
the separate Home Assistant gateway is excluded. The **0.12 → 0.13** trial
completed successfully on 2026-11-05 through the actual browser interface:

1. The Web preflight passed all eight checks: image integrity, expected board,
   helper, connected radio, live board identity, protocol, rate and version.
2. The review dialog displayed the exact Nordic image hash listed above. One
   **Install firmware** click submitted one durable update job.
3. The recorder observed application → factory DFU → application, followed by
   transfer completion, normal restoration and a confirmed job. The narrowly
   published device nodes followed the selected board through both USB modes.
4. The page displayed **Firmware installation verified**, installed version
   **0.13**, radio ready and MQTT connected. The empty test registry, network
   identity and saved settings were preserved.
5. One controlled daemon restart preserved the confirmed journal unchanged and
   restored radio/MQTT readiness. SQLite integrity passed. Post-restart
   preflight rejected a repeat 0.13 installation with `version_not_newer`.

This qualifies one observed Nordic update lifecycle on the named deployment,
including daemon restart recovery. It does not qualify arbitrary USB layouts,
power-loss recovery, a Nordic rollback mechanism, or new RF/endurance behavior.
The other three images retain the offline/carry-forward evidence above; they
did not receive a new 0.13 live update test in this session. Default deployments
still require explicit profile/helper/device setup and a matching trusted
catalog before automatic Nordic installation becomes available.

The tested runtime was the standalone development image
`livolod:dev-013-nordic-r3`, image ID
`sha256:04efa6beb2edfefa1e6cf6e6dd9e0b7a3097c6cf62e17576094e83eaa7094218`.
The local catalog archive SHA-256 was
`bb8f016e95658e68845e9cb1af233bd5739e62448f1489188ae700b43d62dac7`.
Firmware images and their hashes remained unchanged throughout qualification.
The [sanitized lifecycle and restart record](verification/nordic-web-0.13-20261105.json)
contains the preflight checks, observed stages, USB transitions and preserved
state checks.

A post-success Web wording bug incorrectly described the same-version candidate
as missing. Commit `2a966b9` corrects that message to report the installed
firmware as up to date; it does not change flashing or require another update.

### Pre-flash deployment finding

The first container rollout could identify and stat the narrowly published
device nodes, but Linux refused to open them because the host's `/run` mount
has `nodev`. Startup verification caught this before any firmware job or
`BOOTLOADER` command. The previous container and configuration were restored;
the radio remained on 0.12. The revised publisher uses `/dev/livolod-usb` on the
host's device filesystem, keeping the same exact-identity checks, restricted
nodes and container device permissions. This is a deployment correction, not a
failed firmware transfer or evidence of RF instability.
