# Release ownership and acceptance

Effective 2026-11-07, following the project owner's release-engineering brief.
This is the operating checklist for future project releases. Historical
assessments in [RELEASE_PLAN.md](RELEASE_PLAN.md) remain evidence of their dated
state; this document defines what counts as a complete release going forward.

## Responsibilities

| Role | Responsibility |
| --- | --- |
| Release engineer | Own all release/version/tag/artifact/channel operations, release code quality, the release inventory, candidate freeze, build provenance, combined test evidence, publication verification and final completeness decision. Track blockers through closure. |
| Tech Lead / Product Owner (formerly OpenSource coordinator) | Own scope, priorities, technical acceptance and support-policy decisions; resolve cross-component blockers and review release notes. |
| Unix specialist | Deliver the daemon/CLI and standalone containers; verify installation, upgrade, persistence, firmware-worker packaging and recovery behavior. |
| Home Assistant specialist | Deliver the assembled integration/client and HA app; verify HA validation, installation channels, upgrade/reload, identities and native API behavior. |
| ZigBee specialist | Deliver firmware for every supported board profile; own exact-board compatibility, build inputs, radio qualification, flashing and recovery evidence. |
| Web specialist | Verify the daemon's web UI and its shared API/firmware lifecycle behavior when affected. |

The existing specialist chats were reviewed during onboarding, including the
0.4.0 publication handoffs and the missing-catalog diagnostic work in progress.
These responsibilities establish ownership; they do not assert specialist
acceptance of a particular candidate before its evidence is reviewed.

Specialists implement and test their domains; the Tech Lead / PO integrates
scope and shared contracts and hands off the reviewed candidate. Route release
number allocation, tags, artifact publication and update-channel changes through
the release engineer. In the shared checkout, stage only explicitly owned files
and coordinate before committing.

## Release code quality gate

The release engineer owns the final code quality gate and records its evidence
before accepting a candidate. Automated check success is necessary but not a
substitute for substantive review of the changed code and its contracts.

- Every function and declaration, including private helpers, callbacks and test
  functions, must document its intent and every parameter. Prefer Doxygen-style
  comments/docstrings. Describe purpose, units, buffer sizes/ownership and
  return/error behavior where applicable; merely repeating implementation steps
  does not satisfy this requirement. A documented public declaration may supply
  the contract for its matching definition; verify that the two agree.
- Review edge cases, failure behavior, security/compatibility assumptions and
  test assertions against the documented intent. Reject missing or misleading
  contracts even when the documentation checker passes.
- Enforce precise, consistent style: Linux kernel style for project-owned C,
  pinned clang-format, Black for Python and Prettier for JavaScript, as specified
  in [CONTRIBUTING.md](../CONTRIBUTING.md). Preserve third-party notices and
  justified vendor/protothread exclusions; do not weaken checks to pass a change.
- Run the existing documentation/style/version checks and relevant behavioral,
  installed-package and upgrade tests. CI must cover the exact integrated
  candidate. Record skipped or unavailable required checks as open gates.
- Coordinate specialist review and resolve findings before final quality
  acceptance. Distinguish script/unit tests from actual image/runtime evidence;
  missing Docker execution cannot be reported as a successful container test.

## Required release contents

“Latest” means the latest accepted source frozen for that release, identified by
commit, with fresh builds from that source. Changes accepted after the freeze
require a new candidate and affected checks. A passing earlier release does not
qualify newly built bytes.

| Deliverable | Required coverage |
| --- | --- |
| Radio gateway firmware | cod.m CC2652P / RF-BM-2652P2, Sonoff ZBDongle-E, ZB-GW04 v1.2, and official Nordic PCA10059; one artifact for each exact board profile |
| Unix daemon | Installable daemon/CLI distribution, wheels and source packages, service examples, dependency constraints and install/upgrade/recovery instructions |
| Standalone Docker image | `ghcr.io/psires/livolod`, currently `linux/arm64` |
| HA app Docker image | `ghcr.io/psires/livolo-ec-ha-app`, currently `linux/arm64` (`aarch64` in Supervisor metadata) |
| HA integration | Assembled integration including its matching independent client; manual archive and generated HACS/Marketplace channel |
| Release evidence | Inventory, checksums/digests, source and packaging revisions, compatibility matrix, test results, release notes and known limitations |

There are currently two container products using one supported architecture.
AMD64 is planned, not yet a required container target. Once support is accepted,
add `linux/amd64` to each applicable product's build and runtime test matrix
before advertising it. Native x86-64 package tests do not qualify an AMD64 image.

Preserve the existing support level of each radio; inclusion is not a claim of
stable or universal hardware support. Bootstrap bootloaders, recovery artifacts,
receivers and sniffers must be explicitly included when needed by the advertised
installation path or release scope. No required board may silently disappear
from the inventory to make a release pass.

Distribute a reviewed firmware bundle with exact-board manifests and notices.
Check that public firmware payload hashes match the copies delivered in the
standalone and HA container catalogs. Shipping firmware does not itself enable
an unqualified automatic-update backend. State manual-install and recovery
requirements separately for each board.

## Numbering and provenance

- Use one coordinated `major.minor.patch` distribution version for standalone,
  containers, HA integration/client and HA app. Increment patch for compatible
  fixes, minor for compatible features and major for breaking distribution/API
  changes; record migration requirements explicitly, including during 0.x.
- Preserve the numeric `major.minor` radio compatibility domain
  in root `VERSION`. It is currently `0.13`, while the recorded software
  distribution is `0.4.0`. API and UART revisions are separate identifiers.
- Allocate independent, monotonically increasing `major.minor.patch` daemon
  package versions for changed software. Development daemon `0.14.0` retains
  explicit firmware compatibility `0.13`; coordinated distribution versions
  remain unchanged until release preparation. Do not publish different daemon
  wheels or radio bytes under an existing identity. Firmware admission and TI
  trial confirmation compare against the explicit compatibility target, never
  the daemon package number. A compatibility bump still requires matched firmware
  builds and qualification across the matrix.
- Record both domains in release notes and inventory. Validate root propagation
  with `python3 tools/project_version.py`; separately check distribution metadata
  in `host/release/version.json`, client/integration manifests, HA app and channel
  metadata, and container publication requests. Propagation requires explicit
  `--write software` or `--write radio`. The version check does not replace
  installation or artifact completeness tests. See the
  [versioning migration guide](VERSIONING.md).
- Retain existing channel tag conventions: `standalone-X.Y.Z`, `ha-X.Y.Z`, and
  `X.Y.Z` in the generated integration repository. Never move published tags or
  replace published assets. Corrections get a new version.
- Build from a reviewed committed source export. Record the main source commit,
  generated-channel commit, packaging recipe commit, pinned SDK/toolchain and
  dependency inputs. A working checkout with other specialists' edits is not a
  release source. Reuse the tested candidate bytes for publication.

## Release gates

1. **Scope and freeze:** agree scope with the Tech Lead / PO, enumerate every
   board/product/architecture, assign versions and capture immutable source and
   packaging revisions. List owners and unresolved blockers.
2. **Source checks:** pass the source and HA workflows for the frozen source:
   version consistency, formatting/documentation, protocol tools, host/native
   API/worker, web, firmware, HA/client/packaging and dissector tests. Record any
   skip with its reason; an unavailable required test is a blocker, not a pass.
3. **Artifact builds:** build all required radio profiles and software packages
   from the frozen inputs. Include appropriate payload formats, board/layout/baud
   identity, SDK/runtime notices, signing provenance and bootstrap/recovery
   instructions. A TI build with a dummy key is not a release artifact.
4. **Installed software:** test the actual wheel and source bundle on supported
   native platforms, and each exact container digest on every advertised
   architecture. Verify startup, API/Web/MQTT as applicable, dependency integrity,
   upgrade, backup/restore and persistent state/catalogs across replacement.
5. **HA acceptance:** pass hassfest and channel validation; install and upgrade
   the actual archives/channel artifacts on declared supported HA versions.
   Preserve configuration, device/entity identities and reload behavior. Test
   native operation with Web disabled, unavailable catalogs, withdrawn offers,
   reconnects, active jobs and failure/recovery states. Transfer reaching 100%
   or an offer disappearing must never count as confirmed installation.
6. **Radio acceptance:** review exact-artifact, exact-board evidence for boot,
   identity, UART/USB transport and basic control/state behavior. For every
   advertised update path, verify installation, exclusive device ownership,
   interruption/restart, re-enumeration and board-specific recovery/rollback on
   the packaged runtime. Simulators do not establish RF or flashing reliability.
   Schedule physical trials with the hardware owner; record their scope and
   outcomes separately from offline checks.
7. **Publication and verification:** stage the complete inventory before
   promotion. Verify public downloads against SHA256SUMS, anonymous container
   pulls and immutable digests, provenance, channel discovery and upgrade paths.
   Advance app/catalog update metadata only after its artifacts pass. If a
   channel fails, keep the overall release incomplete and track remediation.
8. **Closeout:** record specialist evidence, technical acceptance and the release
   engineer's completeness decision. Publish notes with compatibility, install,
   upgrade and recovery instructions and known limitations. Retain the previous
   release artifacts and evidence for recovery and investigate release defects
   as new versioned fixes.

A release is complete only when every required item passes. Software-only
deliveries may be identified explicitly as partial deliveries, but do not satisfy
the owner's complete-project-release requirement. Missing firmware, unsupported
claims or failed required tests cannot be waived into a complete status.

## Evidence record for each candidate

Maintain a release record under `docs/verification/` containing:

- distribution and daemon/radio versions, candidate ID and frozen commits;
- one row per required board/product/architecture with artifact location,
  SHA-256 or OCI digest, owner, support level and build provenance;
- required test names, environment, tested commit/artifact digest, results and
  links to durable logs, including explicit pending/failed/skipped outcomes;
- compatibility and qualification limits, migration/recovery instructions;
- publication URLs, verified public hashes/digests, acceptance and remaining
  blockers, with overall status `candidate`, `blocked` or `complete`.

The existing workflows publish components independently. This checklist is a
manual project-level gate until a combined inventory validator and publication
gate are implemented; it does not claim that CI currently enforces completeness.

## Published software-only release 0.5.0 — 2026-11-07

Following the user's authorization to release the latest code, the Tech Lead
accepted the explicit software-only scope for distribution **0.5.0**, daemon
**0.14.0**, client/integration/app **0.5.0** and unchanged radio compatibility
**0.13**. Frozen source: `bd06c6b02f4598eca266b72293abc11900cae419`.
The [verification record](verification/software-release-0.5.0-20261107.json)
contains public asset hashes, source/recipe identities, CI gates, ARM64 image
digests and HACS/Marketplace upgrade evidence. App metadata advanced only after
both public images passed runtime, persistent replacement and anonymous checks.

This software-only delivery does **not** satisfy the complete-release firmware
requirement. The public catalog remains empty. Qualified redistributable images
for every exact board, signing/licence readiness and per-board installation and
recovery evidence remain open gates. AMD64 containers remain planned. No live
installation or physical-radio qualification was performed for this release.
The full-release requirements above remain in force.

## Historical onboarding baseline — 2026-11-07

Repository evidence at `d7c11c6416624fd9baea61cdedd8e4488991d94a` records
software distribution **0.4.0** and daemon/radio compatibility **0.13**. The
[0.4.0 verification record](verification/software-release-0.4.0-20261106.json)
reports installed software checks and explicitly says
`firmware_payloads_bundled: false`, with no new hardware qualification.
The packaged `host/livolod/firmware/manifest.json` has no artifacts.

Consequently, 0.4.0 is a recorded software-only delivery and does **not** meet the
new complete-release definition. Historical release evidence is unchanged.
First full-release blockers are the complete qualified board-artifact set,
binary notice/signing readiness, delivery in the release/container catalogs,
and evidence for each advertised hardware installation/recovery path. Review
these against the current [firmware notices](../firmware/THIRD_PARTY_NOTICES.md)
and [recovery guide](FIRMWARE_RECOVERY.md) before qualification.

The missing-catalog diagnostic/Repairs fix is now committed at
`0029910740c0d519ecbc5bd217897eef5800b02d`, verified as the local HEAD during
handoff. The Tech Lead reports it pushed with green CI; the release engineer has
not independently rechecked those remote runs at that time. The fix is now
included in the 0.5.0 source and verification record above. The original
onboarding review ran the version-consistency check successfully; it did not
rebuild, publish, deploy or qualify a new release.

## Historical candidate acceptance contract

At onboarding, publication remained subject to the complete release gates and
no next distribution version had been allocated. The later 0.5.0 software-only
publication and its remaining firmware gates are recorded above. One-button
automatic pairing was accepted at `7a15d627132c515a0537b3b92f7520ac152c3b9e`. The separately authorized
daemon versioning work allocates development package `0.14.0` with unchanged
radio compatibility `0.13`; it does not publish a new distribution.
Current development validation is offline/simulated only; no live deployment or
radio operations are part of this handoff.

The Tech Lead handoff must supply:

- the exact integrated source SHA, accepted scope and specialist reviews;
- test commands, environments, results and CI links tied to that SHA;
- shared API/behavior changes, migration needs and known limitations;
- firmware changes or an explicit statement that firmware behavior is unchanged,
  plus the remaining full-release artifact/qualification blockers;
- evidence for the accepted HA presentation described below, including device
  creation, confirmed-pairing notification and preservation of network identity.

The Tech Lead accepted automatic creation of child switch devices/entities under
the existing HA network entry, followed by a confirmed-pairing notification with
a device link. Do not fabricate Discovered cards or duplicate configuration
entries. The HA specialist's review reported that the official subentry flow
does not support discovery and that discovery configuration flows require user
confirmation. Record this as the accepted limitation and verify the implemented
alternative in candidate tests; a Discovered-row appearance is not an acceptance
requirement for this feature.

For automatic pairing, acceptance evidence must cover both UI entry points,
bounded pairing sessions, detection and supported-profile validation,
registration/pairing outcomes, duplicate discovery, expiry/cancellation,
concurrent requests, restart/reconnect and failure handling. Show that successful
pairing is distinguished from detection or request acceptance, existing device
identities survive, and devices outside the pairing session are not silently
admitted. The exact behavior must follow the Tech Lead's accepted contract.

Once the integrated candidate is accepted, the release engineer allocates
versions and creates the final release candidate. Any resulting source/version
changes require checks against that final commit before artifact promotion.
Passing these software checks alone does not close the full-release firmware
and hardware qualification gates above.
