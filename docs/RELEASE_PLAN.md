# Open-source release assessment and plan

Assessment date: 2026-11-01. Status: proposed release design; nothing has been
published, deployed or flashed by this assessment.

Distribution follow-up, **2026-11-05**: the manual HA
[0.1.1](https://github.com/psires/livolo_ec/releases/tag/ha-0.1.1) and
[0.2.0](https://github.com/psires/livolo_ec/releases/tag/ha-0.2.0) releases and the
[standalone daemon/CLI 0.2.0](https://github.com/psires/livolo_ec/releases/tag/standalone-0.2.0)
are now published. The standalone bundle preserves daemon/firmware version 0.12.
The agreed [container publication plan](CONTAINER_PUBLICATION.md) and
[HA installation/update channel plan](HA_PUBLICATION.md) supersede the earlier
distribution proposals below. In particular, a dedicated integration repository
is recommended; the main repository can host the Supervisor app catalog, so a
third app repository is optional. Public container images and HACS/Marketplace
publication remain future work; software publication has not qualified firmware
payloads or changed the empty catalog.

Follow-up: the owner selected **BSD-2-Clause** on 2026-11-01. The root `LICENSE`
now records that decision. References below to selecting a licence describe the
assessment baseline; dependency notices and release checks remain separate work.
The subsequent [source finalization record](SOURCE_VALIDATION.md) documents the
committed implementation, passing source checks, tests and target builds, while
preserving the outstanding binary-distribution and publication work.

The owner subsequently requested a daemon-owned firmware updater shared by the
web UI, HA's standard update entity and an emergency command-line procedure.
The Unix and ZigBee specialists agreed that no MCU protocol change is needed
for the initial TI signed-UART and exact-board MG21 Gecko backends. Nordic
installation remains gated on a qualified DFU runtime and USB device tracking.
The baseline gaps below describe the original assessment; they are not a claim
that completed source/licence work is still missing. The
[updater validation record](SOURCE_VALIDATION.md#firmware-updater-follow-up)
reports 253 passing tests from committed source and the remaining hardware gates.

The current implementation is viable for a **board-specific developer preview**
after the release gates below. It does not yet support a general plug-and-play
release with qualified firmware bundles. The shared updater implementation is
described below; real installation/recovery qualification remains outstanding.
This assessment combines source
inspection with the ZigBee Specialist, Unix Sepcialist, Web Specialist and
HomeAssistent Specialist reviews. Hardware results are existing qualification
records, not new tests performed for this document.

## Assessment baseline and release blockers

- The inspected Git base is `44cd4b1`; substantial newer source and hardware
  evidence remain modified or untracked. Freeze reviewed commits before building
  anything labelled as a release. Do not sweep local captures into Git wholesale.
- The daemon and radio firmware report `0.12`, the HA integration `0.1.1`, the HA
  app `0.1.0`, and the independent client `0.1.0`. Native API revision 1 and UART
  revision 3 are separate compatibility identifiers.
- The original ARM64 standalone Docker archive labelled `0.12` predates the
  native API. The later HA app image includes it. They must not be presented as
  interchangeable builds or published by silently replacing a version tag.
- There is no project-level licence. The owner must choose one and add package
  metadata and notices before presenting the project as open source. Inspect
  actual linked/distributed dependency inputs, rather than assuming an SDK-wide
  licence covers every component. The pinned Gecko SDK explicitly places the
  RAIL release archive directory used here under Zlib; this is not blanket
  clearance to redistribute the entire SDK. [Pinned Gecko terms][gecko-license]
- A fresh `tools/check_documentation.py` run fails on newer functions and
  parameters. Restore the intent/parameter documentation and Linux-style C
  requirements in `CONTRIBUTING.md` across the new code before release.
- Source CI installs `requirements-dev.txt`, which lacks the new daemon's
  `aiohttp` dependency. Install the actual package and test dependencies in a
  clean environment; then rerun source, firmware-core, host and HA checks.
- Current CI does not build/publish the four radio profiles or release images.
  Base images, build tools and transitive Python dependencies are not fully
  pinned. Record source/dependency/toolchain identities and verify clean builds.
- Hardware qualification is bounded: two switches, three outputs, named boards,
  short endurance observations. Larger networks, long runs, fresh commissioning,
  recovery and USB hotplug need further qualification. Query reliability remains
  limited; do not require a successful query as proof that normal control works.

Before public visibility, review tracked files and history for local credentials,
private configuration and unnecessary deployment/capture data. Preserve useful
regression fixtures and publish a sanitized qualification summary with hashes.
This document is not a completed licence or disclosure audit.

## 1. Container publication

Use **GitHub Container Registry (GHCR)** as the primary registry:

| Proposed package | Purpose | Initial architecture |
| --- | --- | --- |
| `ghcr.io/psires/livolod` | Standalone daemon, web UI, native API and optional MQTT | `linux/arm64` |
| `ghcr.io/psires/livolo-ec-ha` | Supervisor app wrapper around the same daemon build | `aarch64` in HA metadata |

These are proposed names, not existing published packages. GHCR supports public
anonymous pulls and repository-linked Actions publication using `GITHUB_TOKEN`.
Package visibility must be checked separately; verify an anonymous pull before
announcing availability. [GHCR documentation][ghcr]

Public GitHub packages are free under the documented package billing model;
build-runner costs are separate. Docker Hub can be a later mirror, but its free
pull limits make it less attractive as the only origin. A self-hosted registry
adds maintenance without a current requirement. [GitHub billing][billing]
[Docker Hub limits][docker-limits]

Release workflow:

1. Build from a reviewed commit with a pinned base digest and locked, hashed
   dependencies/build tools. Pin CI actions and record the effective build flags.
2. Run package, container and simulator/native-API smoke tests; qualify the actual
   ARM64 artifact on hardware. Add amd64 after its own image tests; do not claim
   ARMv7 support merely because the Python source is portable.
3. Publish a unique release tag and a commit tag; preserve their digest mapping.
   Examples after allocating the next release: `0.13`, `sha-<commit>`. Keep
   preview/channel labels separate from the numeric firmware version. Do not
   promote `latest` until a stable release is qualified.
4. Attach source/revision/version/licence OCI labels, dependency inventory, SBOM,
   provenance and checksums. Test anonymous pull and install by digest.
5. Keep an offline image archive as a GitHub Release asset for disconnected use.

Retain the existing non-root runtime, read-only root filesystem, dropped
capabilities, persistent database and explicitly selected serial device. Document
device-group permissions and recreation after USB changes. Do not use the
development unauthenticated nginx example as the public installation default.

## 2. Standalone release package

Offer Docker Compose as the first supported installation path, with a native
Linux Python package for users who do not run containers. Both use the same
daemon, database, native API and firmware artifacts as HA deployments.

| Deliverable | Proposed contents and acceptance |
| --- | --- |
| `livolo-ec-<release>-standalone.tar.gz` | Digest-pinned Compose file, example configuration/environment, startup and backup instructions, licence/notices, compatibility manifest; install on a clean ARM64 host without the source checkout |
| `livolo_ec_daemon-<release>-py3-none-any.whl` and sdist | Daemon plus static web assets; test install/import/CLI from built distributions, not source paths |
| Optional offline bundle per OS/architecture/Python | Image archive or hashed dependency wheelhouse, installation instructions and checksums; test without network access |
| Native service files | Dedicated user, writable state/config locations, serial permissions, systemd unit, logging and explicit backup/restore/uninstall instructions |
| Release metadata | Source commit, build flags, dependency/toolchain versions, hashes, supported boards and host/firmware compatibility, known limits and recovery links |

Publish assets on GitHub Releases rather than relying on expiring CI artifacts.
Wheel/sdist publication on PyPI is useful once names and licensing are settled;
use Trusted Publishing instead of a permanent upload token. A Debian package is
a later convenience, after filesystem paths and upgrade behavior stabilize.
[GitHub Releases][releases] [PyPI Trusted Publishing][pypi]

The updated Docker recipe includes the TI cryptography dependency and pinned
MG21 helper; native installations select the `[flash]` extra. The revised image
still needs a real build and board-specific installation/recovery qualification.
Nordic manual DFU retains a separate maintenance environment. Validate the
declared Python minimum against resolved dependencies and tests before promising
a support range.

An upgrade must back up configuration and SQLite consistently, preserve network
identity, pairing data and token history, and document database compatibility
before offering application rollback. Only one radio may own a network identity,
including across different machines. Migration must disarm the old owner and
transfer its final state; starting an old database is not a safe rollback.

## 3. Home Assistant and HACS

HACS is feasible, but installs only the custom integration. HA OS users also need
the Supervisor app; HA Container users need a separately running daemon. These
three deliverables have independent installation and update paths.

There is an imminent platform transition: the HACS maintainers announce built-in
**Marketplace starting with HA 2026.11**. Our current qualification remains on
2026.10.4. Test both the existing HACS path and Marketplace before claiming newer
HA support. Marketplace explicitly refuses repositories containing HA OS apps,
so keep integration and app distribution repositories separate.
[Maintainer announcement][marketplace-announcement]
[Marketplace repository requirements][marketplace-layout]

Recommended repository arrangement, all still proposals:

| Repository | Role |
| --- | --- |
| `psires/livolo_ec` | Canonical source, issues, daemon and firmware releases |
| `psires/ha-livolo-ec` | Generated, self-contained HACS distribution with integration-only releases |
| `psires/ha-apps` | Small generated Supervisor repository pointing at prebuilt GHCR images |

A monorepo can work with older HACS, but HACS derives release versions from
GitHub releases; mixing firmware and integration release tags creates avoidable
selection ambiguity. Generated distribution repositories keep one source of
truth while separating release streams. Record the canonical source commit in
each generated release and validate the generated result.

HACS preparation:

1. Publish a public repository with root `hacs.json`, README, licence, description
   and topics. Include exactly one integration and all its required runtime files.
   [HACS general requirements][hacs-general]
   [Integration requirements][hacs-integration]
2. The source integration imports bundled `_client`, but that directory exists
   only after `homeassistant/build_release.py` assembles it. Publish the assembled
   tree, never the incomplete source component alone. Keep the standalone client
   source canonical; generated copies must not be edited independently.
3. The current manual ZIP contains `custom_components/livolo_ec/...`. HACS release
   ZIP extraction targets the integration directory directly, so a dedicated
   HACS ZIP must put `manifest.json`, `__init__.py` and `_client/` at its root.
   Prefer an assembled repository tree initially; if using `zip_release`, produce
   and test that separate archive, with `filename`/`hide_default_branch` configured
   appropriately. Keep the manual ZIP separately named. [HACS installer][hacs-code]
4. Add branding, HACS validation alongside hassfest, assembled-package tests,
   release notes and consistent manifest/tag versions. Verify first install,
   upgrade and reload through HACS itself, including bundled client imports.
5. Start as a HACS custom repository. Apply for the default catalog after the
   release/install path passes its additional checks. [HACS inclusion][hacs-include]
6. The documented qualification is HA Core **2026.10.4** on HA OS **18.4** with
   Python **3.14.2+** tests. Use the tested Core version as an initial conservative
   minimum unless an older version is deliberately tested; this is not a claim
   that every older version is incompatible.

The Supervisor repository needs root `repository.yaml` and app metadata pointing
to its own versioned GHCR image. Keep the repository URL stable because it affects
the Supervisor app identity. Test fresh installation from this actual repository,
not just local app staging. Supply token/discovery onboarding, backup/restore and
upgrade instructions; do not hardcode the development `local-livolo-ec` hostname.
[HA repository guide][ha-repo]

Migration from the local app changes its Supervisor identity and internal
hostname. Explicitly migrate configuration/database and update the integration
endpoint; do not assume Supervisor transfers the local app's data automatically.

Keep firmware updating in the daemon/app. HA Core should expose a firmware
`UpdateEntity` backed by the daemon's maintenance API, with installation/progress
features only when supported by the selected board and host deployment.
[Update entity contract][ha-update]

## 4. Radio firmware distribution and updates

There are **three MCU families and four board profiles**, not three universally
interchangeable binaries:

| Board profile | Current evidence | Bootstrap and routine update | Limits |
| --- | --- | --- | --- |
| cod.m CC2652P / RF-BM-2652P2 (`launchpad/codm-cc2652p`) | 0.12; 6,000 UART requests; 24/24 settled controls | One-time signed bootloader via TI ROM; thereafter signed `.ecupd` through application UART | Bootloader 0.2; matched public key; trial confirmation/previous-image rollback; 352 KiB flash, not P7 |
| Sonoff ZBDongle-E (`sonoff-zbdongle-e`) | 0.12; 24/24 controls and independent RF evidence | Preserve Gecko 2.4.2; application-only `.gbl`; software `BOOTLOADER` or initial buttons/RTS-DTR | Unsigned application, no last-good rollback; this physical unit is now a sniffer |
| ZB-GW04 v1.2 (`zb-gw04-1v2`) | 0.12; 6,000 UART requests; 24/24 controls; HA app exercised | Preserve Gecko 1.9.2; application-only `.gbl`; software `BOOTLOADER` or initial buttons | Unsigned, no rollback; tested CH340 lacks unique serial number |
| Official Nordic PCA10059 (`nordic-pca10059`) | 0.12; 6,000 USB requests; 24/24 controls; software DFU verified | Preserve factory Open DFU; application-only DFU ZIP; software entry or SW2 | Unsigned, no rollback; app/bootloader USB identities differ; autonomous stall recovery unqualified |

Publish a distinct artifact for every board, such as
`livolo-ec-<board>-<release>-application.<ecupd|gbl|zip>`, plus manifest, checksums
and notices. Publish bootstrap/installer material separately, with explicit
factory-configuration preservation and recovery steps. Never substitute a full
flash or merged factory image for a routine application update.

### Agreed delivery model

The owner selected bundled firmware for Docker and the HA companion app, with
the same built firmware published as GitHub Release assets for standalone users.
Build and qualify each board artifact once, then distribute identical bytes and
hashes through both channels. HA's custom integration remains an API client;
the companion app contains the firmware bundle.

Each container release should carry the latest **qualified, compatible** firmware
set at image build time, pinned by its manifest. It must not resolve an arbitrary
moving `latest` during installation. For the present TI confirmation contract,
the bundled application and daemon versions must match. New container releases
can carry new firmware sets without rewriting older image tags or release assets.

Proposed read-only image layout:

```text
/usr/share/livolo-ec/firmware/
  manifest.json
  <board-profile>/<firmware-version>/application.<ecupd|gbl|zip>
  notices/<input-or-component>/...
```

The manifest supplies board/revision identity, versions, host compatibility,
bootloader/update requirements, artifact hashes and lengths, and notice paths.
Container assembly must verify it against the exact files being copied; CI must
compare image-extracted hashes with the GitHub Release assets. The bundle needs
no compiler, SDK checkout or signing private key at runtime. These firmware data
files are host-architecture independent; their flashing tools still need host
architecture and Python/runtime qualification.

The first updater should resolve artifacts from this local bundle. Standalone
users can download the matching release bundle, including manifest and notices,
or select a board-specific asset and its accompanying metadata. Both paths feed
the same board selection and installation logic. This permits offline operation
after the container or release bundle has been obtained. Authenticated remote
discovery can follow when updating independently of the host package is needed.

Bundling supplies the firmware payload, but the current Dockerfile does not yet
include these files or a universal flasher. Installation still requires the
shared maintenance job and qualified board adapters described below. Upgrading
the container/app must not implicitly flash the radio; expose the available
compatible update and require an explicit installation request. Preserve a
supported host/firmware pairing during host rollback as well as upgrade.

The [binary licence packaging requirements](../firmware/THIRD_PARTY_NOTICES.md)
apply equally to firmware inside container layers and GitHub Release assets.
Complete the notices and identified vendor-terms review before distributing
either bundle. The seven validation builds used test inputs, including a dummy
TI public key; they are not the official release payloads.

Build CI must explicitly select board, baud, memory layout and update options.
TI currently defaults to update support disabled; MG21 defaults to ZB-GW04 and
115200. Qualification used 460800 application UART on the tested serial boards;
Gecko/ROM loaders use 115200. Nordic CDC baud is metadata, not a physical UART
clock. Verify both qualified binaries and repeat-build output from pinned GCC
and SDK inputs; documented pinned revisions alone do not prove reproducibility.

Use GitHub Releases for immutable firmware assets. When remote update discovery
is added, use an authenticated release catalog. Prefer a maintained TUF
implementation, hosting metadata
on a stable HTTPS origin such as GitHub Pages. Bundle trusted root metadata with
the installed software; fetching a key alongside an unsigned index establishes
no trust. TUF authenticates discovery/download and addresses stale/rolled-back
metadata; it does not implement MCU installation or add hardware rollback.
[TUF overview][tuf]

The catalog must describe board/revision, MCU/layout, firmware version, artifact
type/length/hash, source/build identity, application and bootloader transport,
required bootloader/key, host/protocol compatibility, release notes, qualification
status and channel. Cache discovery with backoff; avoid per-entity GitHub API
polling. Automatically checking for updates must not silently install them.

Use separate catalog-signing and TI firmware-signing responsibilities. Document
official versus user-provisioned TI keys, key fingerprints, rotation/recovery and
protected signing procedures. Private keys never belong in source, release
images or client downloads. Host verification of MG21/Nordic downloads does not
make their present bootloaders enforce authenticity.

### Shared installation service

Refactor transfer logic into board adapters behind a daemon-owned, durable
maintenance job. Web, CLI and HA clients submit an artifact ID and idempotency
key, then observe the same job. Clients must not independently own the serial
port or submit arbitrary command lines/download URLs.

1. Resolve exact board and compatible artifact; verify/download before downtime.
   Validate device access, bootloader prerequisites and a usable recovery path.
2. Persist job identity and digest; take a consistent state backup, acquire
   exclusive radio maintenance ownership, resolve in-flight work and disarm.
   Suspend normal reconnect/restore behavior while retaining the HTTP service.
3. Execute the selected adapter, handling USB re-enumeration and reporting
   progress. Offer cancellation only at stages where the adapter can safely stop.
4. Reconnect, verify firmware identity and restore the network. Confirm TI trials
   only after the required readiness checks, within the trial window.
5. Persist success, rollback, failure or uncertain outcome. On restart/lost client
   responses, reconcile the existing job instead of replaying activation or
   actuator commands. Preserve recovery logs without secrets.

The older CLI's `--service` systemd stop/start wrapper cannot be called from
inside the serving HA app as the integrated solution. The new daemon-owned
worker keeps its API running and shares an inherited radio lock with the parent
and external flasher. Qualify least-privilege access to both application and
bootloader devices in the actual packaged deployment.

Platform-specific work still required:

- TI: package cryptography, validate signer/board identity and preserve tested
  activation/rollback handling in the shared job.
- MG21: pin and qualify the Gecko flasher, board selection and recovery for each
  bootloader. VID/PID alone cannot identify either the correct board or port.
- Nordic: replace or isolate the legacy `nrfutil 6.1.7`/Python 3.10 maintenance
  environment; qualify the DFU backend on ARM64/HA. Resolve the development USB
  VID/PID `cafe:4001` for distribution and correlate bootloader enumeration with
  the selected device. Define the firmware-to-DFU integer version mapping.
- Containers/HA: qualify re-enumeration/hotplug permissions explicitly. Current
  runtime operation on one serial mapping does not prove DFU access.

### Version compatibility is an implementation constraint

The legacy diagnostics in `host/livolod/version.py` call firmware older than the
installed daemon an available update. Installation clients must use the new
`firmware_update` snapshot, which selects a validated local candidate instead.
The service confirms
a TI trial only when firmware equals the host's exact version. The signed format
also encodes two 16-bit version components. Keep numeric `major.minor` firmware
versions and matching host/firmware release sets for the first preview. Advance
both together when allocating the next release; rebuild and qualify it rather
than simply relabelling an old binary.

If the app/integration/client adopt the project's two-component convention, make
the next version greater than their existing versions and test HACS/HA ordering.
Keep package revision, API revision, UART revision and board identity explicit.
Independent host/firmware schedules require replacing the equality rule with a
tested compatibility contract before the catalog can offer them safely.

## Implementation sequence and acceptance

### Daemon updater implementation scope

The agreed updater keeps the daemon's HTTP services running while it gives a
single worker exclusive radio ownership. Both the web UI and HA `UpdateEntity`
submit a candidate ID and a durable request ID to that service. They do not open
serial ports or supply filenames, downloads or commands. Retries observe the
same job rather than starting another transfer. The emergency CLI uses the
authoritative configuration/database and requires the serving daemon to be
stopped before direct radio maintenance.

Initial integrated backends cover `codm-cc2652p` with its provisioned signed
updater and the exact `sonoff-zbdongle-e`/`zb-gw04-1v2` MG21 profiles. Nordic
remains explicitly unavailable for integrated installation until its DFU runtime
and USB handoff are qualified. Each deployment still needs compatible device
permissions and its own recovery qualification.

Candidates come from an operator-trusted local manifest installed with the host.
They bind exact board, gateway role, host/firmware version, transport rate,
application layout and payload hash. TI also validates the package signature and
installed signing key. Manifest checksums alone are not publisher authentication
for unsigned MG21 artifacts: trust comes from the installed release bundle and
its distribution channel. No qualified payload is included in the source-only
baseline, so installing this code must not advertise a firmware download that
does not exist.

Before enabling this in a public binary release, qualify the actual packaged
image and candidate on each board: successful install, lost response, daemon
termination, power loss, recovery and exclusive device ownership. Verify that
the GitHub firmware asset and the copy inside the container have identical
hashes. A mocked transport test cannot establish flash timing, electrical
reliability, rollback behavior on the real board or container device access.

### Remaining release stages

| Stage | Suggested lead | Deliverable and exit check |
| --- | --- | --- |
| A. Release baseline | Maintainer with all specialists | Owner selects licence; reviewed source commits, notices and scoped evidence; documentation/style and clean CI pass; unique versions and compatibility table |
| B. Standalone preview | Unix | Reproducible ARM64 image/package workflow; fresh Docker/native installation, persistence, backup/restore and anonymous GHCR pull; no ambiguous 0.12 artifacts |
| C. HA distribution | HA | Generated integration and app repositories; HACS/hassfest checks; real install/upgrade/reload, including Marketplace on 2026.11; declared minimum Core tested |
| D. Firmware bundles | ZigBee + Unix + HA | Four explicit build profiles, notices and immutable artifacts; identical firmware hashes in Docker/HA images and GitHub Releases; bootstrap/recovery guides and signing policy; offline install verified per profile |
| E. Integrated updates | Unix + ZigBee + Web + HA | Shared catalog/job API, adapters, web panel and HA entity; concurrency, interruption, restart reconciliation, re-enumeration and TI rollback tests on each supported host/board |
| F. Broader support | ZigBee with host/HA qualification | Longer endurance/larger-network tests; each new radio gets a reviewed profile and separate qualification before stable promotion |

Stages B/C can proceed after A, with D required before claiming bundled firmware
delivery; D need not wait for the integrated updater.
Publish a preview with manual board-specific firmware installation first. Do not
make public source availability depend on completing automatic updates, but do
make every advertised artifact and feature satisfy its own release gate.

For new modules, record exact board/revision and bootloader before flashing.
Add pin/oscillator/RF front-end/layout and transport definitions, license inputs,
bootstrap/recovery instructions, CI target and qualification evidence. Use support
tiers such as experimental, qualified preview and stable per profile; a new chip
or adapter must not inherit support from a shared VID/PID or MCU family alone.

### Sharing chip support across dongles

Use the additional modules to separate three concerns: shared Livolo protocol,
peer and scheduling behavior; MCU-family radio/timer/transport adapters; and
small explicit board profiles for pins, oscillators, RF front ends, USB bridges,
memory layouts and bootloader entry. Extend those layers instead of cloning a
complete implementation for every dongle.

Sonoff ZBDongle-E and ZB-GW04 v1.2 are the current MG21 example: they should share
the MG21 implementation and regression fixes while retaining separate profile
selection, artifacts and hardware qualification. Their different USB bridges and
preserved bootloaders are installation constraints even with the same MCU.
Do not turn multiple boards into one generic binary unless their layouts and
reliable board identification have been deliberately established. A shared MCU
implementation is already useful without requiring a universal image.

For each newly received module, qualify serial integrity, scheduling/control,
software bootloader entry, recovery and container/HA device transitions against
the existing chip-family regression suite. Report board-specific deviations in
the profile and support matrix, so a fix can be shared without silently assuming
that all boards have received the same hardware validation.

## Assessment verification and evidence

Fresh checks performed for this assessment:

- `python3 tools/project_version.py`: passed, reports 0.12.
- `python3 tools/check_documentation.py`: failed on newer function/parameter docs.
- `homeassistant/build_release.py --app` in a temporary output directory: passed;
  manual ZIP contains the manifest and bundled `_client`, and staged app contains
  the native API source. This was packaging inspection, not a HACS install test.

No fresh full-suite, firmware build or hardware run was performed here. Existing
specialist records report 140 host tests, HA tests/hassfest and bounded hardware
qualification; those do not substitute for CI on the eventual frozen release.

Local evidence: `analysis/development/ha-radio-support.md`, board qualification
reports under `analysis/development/`, `host/docker/README.md`,
`host/verification/docker-arm64-0.12-20261101.json`,
`host/verification/native-api-20261101.json`, `homeassistant/README.md`, and
`homeassistant/verification/proximity-comparison-20261101.json`. Some remain
untracked at assessment time and must be deliberately included or summarized in
the release baseline. Current HA ownership is described in `host/DEPLOYMENT.md`;
older reports mentioning an active standalone Pi describe historical deployments.

[ghcr]: https://docs.github.com/en/packages/working-with-a-github-packages-registry/working-with-the-container-registry
[billing]: https://docs.github.com/en/billing/concepts/product-billing/github-packages
[docker-limits]: https://docs.docker.com/docker-hub/usage/pulls/
[releases]: https://docs.github.com/en/repositories/releasing-projects-on-github/about-releases
[pypi]: https://docs.pypi.org/trusted-publishers/
[hacs-general]: https://www.hacs.xyz/docs/publish/start/
[hacs-integration]: https://www.hacs.xyz/docs/publish/integration/
[hacs-include]: https://www.hacs.xyz/docs/publish/include/
[hacs-code]: https://github.com/hacs/integration/blob/main/custom_components/hacs/repositories/base.py
[marketplace-announcement]: https://github.com/hacs/integration/issues/5590
[marketplace-layout]: https://contributors.home-assistant.io/marketplace/reference/repository-structure/
[ha-repo]: https://contributors.home-assistant.io/apps/guides/repository/
[ha-update]: https://contributors.home-assistant.io/core/entities/update/
[tuf]: https://theupdateframework.io/docs/overview/
[gecko-license]: https://raw.githubusercontent.com/SiliconLabs/gecko_sdk/59d2160fe448b13730a91c0f019a8b4c53c43eb2/License.txt
