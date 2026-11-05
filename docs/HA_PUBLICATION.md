# Home Assistant installation and update publication

Current release: **0.3.0** integration, HA app and standalone distribution,
with daemon **0.13**. The public HACS/Marketplace 0.2.1 → 0.3.0 upgrade checks
preserve the existing entry, entity IDs, names, area, token and simulated state.
The app image separately passed its isolated 0.2.1 → 0.3.0 container upgrade and
anonymous registry checks before the app catalog was advanced.

- [Integration 0.3.0 and public installer evidence](../homeassistant/verification/integration-channel-0.3.0-20261105.json)
- [Container publication and upgrade evidence](../host/docker/publication-0.3.0-20261105.json)
- [Standalone and manual HA release asset checks](verification/software-release-0.3.0-20261105.json)

The live HA host was unchanged. The actual Supervisor qualification below is
historical 0.2.0 → 0.2.1 evidence, not a claim of a new HA OS 0.3.0 trial.
Default-catalog acceptance and migration from a local development app remain
separate work. Software updates do not flash radio firmware.

## Earlier channel qualification

Status on **2026-11-05**: integration releases **0.2.0** and **0.2.1** and the
matching ARM64 app images are public. Public integration installation and upgrade
tests passed; actual Supervisor installation and upgrade passed using an isolated
app without a radio. Root `repository.yaml` publishes the app catalog after
these gates. The existing main-repository `ha-0.1.1` and `ha-0.2.0` manual-install
releases remain immutable. Default-catalog acceptance and migration of the
existing local development app are separate, incomplete work.

## Installation links

- [Integration repository](https://github.com/psires/ha-livolo-ec) and
  [0.3.0 release](https://github.com/psires/ha-livolo-ec/releases/tag/0.3.0).
  [Open in HACS or Marketplace](https://my.home-assistant.io/redirect/hacs_repository/?owner=psires&repository=ha-livolo-ec&category=integration)
  as a custom integration repository, then install and restart HA.
- [Add the HA OS app repository](https://my.home-assistant.io/redirect/supervisor_add_addon_repository/?repository_url=https%3A%2F%2Fgithub.com%2Fpsires%2Flivolo_ec).
  Its URL is `https://github.com/psires/livolo_ec`; app configuration is nested at
  `homeassistant/app/config.yaml`. Configure the dedicated radio, existing
  network identity and generated API secret before starting it.
- [Full setup and local-app migration distinction](../homeassistant/README.md).
  Installing the repository app does not migrate `local_livolo_ec`. Keep the
  existing HA entry and arrange a consistent data transfer before changing its
  endpoint; never start two daemons on the same serial adapter.

## Verified scope

| Check | Evidence and limits |
| --- | --- |
| Public integration releases | Exact-source exports, component-root ZIPs, checksums, HACS validation and hassfest; both version tags are immutable. |
| HACS installation and upgrade | Upstream HACS installer on HA 2026.10.4 installed public 0.2.0 and upgraded to public 0.2.1. |
| Marketplace installation and upgrade | Upstream Marketplace installer on HA 2026.11.0 installed public 0.2.0 and upgraded to public 0.2.1. |
| Integration persistence | Existing entry, entity IDs, custom name, assigned area, credential and simulated daemon state preserved; restart requirement and available update checked. Release metadata was explicitly fetched; subsequent repository refresh was mocked. OAuth and catalog UI were not exercised. |
| Public ARM64 images | Anonymous pulls, labels, SBOM/provenance, wrapper runtime and container upgrade checks; see [container evidence](../host/docker/publication-20261105.json). |
| Actual Supervisor upgrade | HA OS 18.4, Core 2026.10.4, Supervisor 2026.10.5 on Pi 5: install 0.2.0, discover nested configuration and update to 0.2.1; private data, registry, command history, options and secret preserved, UID/GID 10001. |
| Isolation and cleanup | Separate synthetic network and app, no radio device mapping or published ports; original app image/start time and HA entry/three entity records unchanged. Temporary app/repository removed, protected backup retained on host. |

The [integration verification record](../homeassistant/verification/integration-channel-20261105.json)
records public asset hashes, upstream installer versions and results;
[reproduction instructions](../homeassistant/channel_tests/README.md) describe
the isolated test environments. The
[Supervisor verification record](../homeassistant/verification/channel-publication-20261105.json)
contains image digests and exact scope. These tests do not qualify live migration,
fresh physical pairing, USB hotplug, opt-in automatic upgrades, rollback or
breaking-version blocking. Integration tests check the restart requirement and
entry reload, not a full HA process restart. Ingress was excluded from the isolated app test;
previous live preview and container-wrapper checks cover their own scopes.

Switch detection is included from integration/app **0.2.0**. The existing live
development installation was inspected as integration **0.1.1** and local app
**0.1.0**, with a daemon build predating detection. It was deliberately retained
during publication checks. Runtime version **0.12** alone cannot distinguish
that older daemon from the newer detection-capable build. The native detection
and API tests passed again (23 tests), including one-/two-gang registration and
stale evidence rejection; this is not a new live commissioning test.

## Release tooling

`homeassistant/export_channel.py` exports the assembled integration, original
branding and pinned source/packaging hashes. The generated repository validates
and publishes releases on ordinary SemVer tags. Source `c75839d` supplies 0.2.1;
`6fe41cb` supplies the immutable 0.2.0 integration baseline.
`homeassistant/stage_supervisor_probe.py` creates a disposable no-radio catalog
for subsequent isolated qualification. Never connect that app to a live radio
or an existing HA entry.

## Channel layout

Keep development in `psires/livolo_ec`. Publish the assembled integration through
a small, generated distribution repository,
`psires/ha-livolo-ec`. Use the main repository as the Supervisor app repository
with its repository metadata and prebuilt-image reference. A third
repository for apps is optional, not required.

| Delivery | Channel | Version authority |
| --- | --- | --- |
| Native HA integration | Dedicated GitHub repository, HACS and Marketplace | Integration manifest and matching SemVer release tag |
| HA OS daemon app | Main repository's app metadata plus public GHCR image | App `config.yaml` version and matching image tag |
| Standalone daemon/CLI | Main repository releases and separate daemon image | Distribution version; current approved distribution is 0.2.0 |
| Radio firmware | Existing daemon-managed firmware update entity | Exact-board firmware catalog and verified radio version |

The runtime daemon/firmware version remains **0.12**; it is not the standalone
distribution version. Historical HA integration 0.1.1 includes app/client 0.1.0;
HA integration 0.2.0 includes app/client 0.2.0. Preserve these distinctions in
image tags and release metadata. In particular, an HA app image needs the app
wrapper, not the standalone daemon's entrypoint.

Home Assistant 2026.11 includes Marketplace. Installations still on 2026.10.4,
our qualified test baseline, use HACS. The same catalog serves both. The older
`hacs_repository` My Home Assistant link opens HACS on older HA and Marketplace
on 2026.11, so it is the practical common installation badge.
[Marketplace catalog guide](https://contributors.home-assistant.io/marketplace/guides/catalog/)

## Why a dedicated integration repository

HACS requires one integration beneath `custom_components`, not an otherwise
empty repository. Our monorepo satisfies that directory constraint, so separation
is not a formal requirement.
[HACS integration requirements](https://www.hacs.xyz/docs/publish/integration/)

The problem is release selection: HACS reads repository-wide GitHub releases,
filters drafts/prereleases, and selects a release tag without an HA-specific
prefix filter. Its ZIP filename setting chooses an asset after release selection;
it does not select a release stream. Mixing CLI, firmware and HA releases would
therefore risk offering the wrong release or a missing asset. This conclusion
comes from `get_releases` and `common_update_data` in the
[HACS implementation](https://github.com/hacs/integration/blob/main/custom_components/hacs/repositories/base.py).

The documented `hacs.json` keys offer no release-prefix filter. Also, our local
HA test environment's AwesomeVersion parser classifies `ha-0.2.0` as unknown,
while `0.2.0` and `v0.2.0` parse as SemVer. Existing tags remain valid identifiers
for manual distributions; use plain matching `0.x.y` integration tags in the
new channel. A distribution repository avoids changing old releases or imposing
a single release cadence on all project components.
[HACS manifest reference](https://contributors.home-assistant.io/marketplace/reference/hacs-json/)

## Release procedure and remaining work

1. **Prepare the integration distribution.** Generate it from a reviewed source
   commit, including the assembled `custom_components/livolo_ec` directory,
   bundled `_client`, BSD license, README, issue tracker, provenance and CI.
   Keep the default branch installable too; do not copy the incomplete source
   component alone. Add a brand icon and validate its supported location.
   Run HACS validation and hassfest without ignored failures.

   Add a root manifest along these lines:

   ```json
   {
     "name": "Livolo EC",
     "zip_release": true,
     "filename": "livolo_ec.zip",
     "hide_default_branch": true,
     "homeassistant": "2026.10.4"
   }
   ```

   The minimum above reflects the qualified baseline, not a claim that earlier
   HA versions cannot work. Include it in each release. Test 2026.11 explicitly
   before claiming Marketplace qualification.

2. **Build the HACS/Marketplace asset.** `livolo_ec.zip` must have
   `manifest.json`, `__init__.py`, `_client/`, translations and license directly
   at its root. The existing manual ZIP starts with
   `custom_components/livolo_ec/` and must remain a separate artifact. ZIP
   installation extracts into the integration directory. Use the same constant
   asset filename for every channel release, with version and source commit
   verified by the build.
   [Release and ZIP requirements](https://contributors.home-assistant.io/marketplace/guides/releases/)

   The new channel starts with a separately exported **0.2.0** baseline and
   **0.2.1** packaging release, whose tags match their manifests. Do not relabel or replace the
   already published main-repository 0.2.0. Record the source commit and explain
   any differences from it. Publish only after the complete assets and checksums
   are ready. Keep older channel releases for rollback.

3. **Build and verify HA app images.** Follow the companion container publication
   plan for exact-source builds, smoke tests and image provenance. Use a distinct
   HA-wrapper image, `ghcr.io/psires/livolo-ec-ha-app`. Build the
   historical app images from their historical source, under app versions 0.1.0
   and 0.2.0 if those artifacts are being provided. Advertise only tested
   architectures: `aarch64` is currently qualified; add `amd64` after its build,
   wrapper and platform checks pass.

   Publish and verify anonymous pulls before advertising the corresponding
   app version. Never replace an existing version tag with different contents.
   The current official guidance prefers a multi-architecture image and
   composable builder actions; the old all-in-one builder is deprecated.
   [App publishing guide](https://contributors.home-assistant.io/apps/guides/publishing/)

4. **Enable the Supervisor repository.** Add root `repository.yaml` with a
   stable name, URL and maintainer. Set the app's `image` without a tag:

   ```yaml
   image: ghcr.io/psires/livolo-ec-ha-app
   ```

   Supervisor pulls the tag specified by app `version`; a public image removes
   the need to build locally. Current documentation permits app configuration at
   any repository depth, so `homeassistant/app/config.yaml` can remain there.
   Add app README, DOCS and CHANGELOG files, and a
   `supervisor_add_addon_repository` installation badge. Validate this nested
   layout on the supported Supervisor baseline before publishing the channel.
   [Repository layout and links](https://contributors.home-assistant.io/apps/guides/repository/),
   [image configuration](https://contributors.home-assistant.io/apps/reference/configuration/)

5. **Ship custom-repository installation first.** Provide two clearly labelled
   buttons: install the daemon app, then install the native integration. Users
   on HA Container use the standalone daemon image instead of the Supervisor
   app. Validate clean installation, detection, control and upgrade before
   submitting the integration to the shared default catalog. Catalog inclusion
   requires an owner/major-contributor PR to `hacs/default`, passing HACS and
   hassfest workflows, a complete release, repository metadata and branding.
   Review may take months; custom-repository installation does not depend on
   that acceptance.
   [Default inclusion requirements](https://www.hacs.xyz/docs/publish/include/)

6. **Reduce setup to a confirmation.** The existing integration has a `hassio`
   flow, but the app does not publish discovery and the flow still requests
   credentials. Add Supervisor discovery for `livolo_ec` after the API starts,
   advertise the actual internal hostname, and design the trusted credential
   handoff with token rotation and network-identity checks. Handle duplicate
   discovery without replacing an existing entry. Discovery assumes the custom
   integration is installed; it must not be described as installing its code.
   [Supervisor discovery contract](https://contributors.home-assistant.io/apps/guides/services-discovery/)

   Follow with a first-run UI that generates and persists the network identity
   and API secret for new installations, while preserving existing identities
   during migration. Keep adapter selection explicit and show actionable radio
   readiness. Initial conversion from stock adapter firmware remains a separate
   supported-board procedure, not part of ordinary software installation.

## What “automatic updates” means

| Behavior | What users get |
| --- | --- |
| Integration discovery | A setup offer for the running app; separate from installing integration files |
| Integration update discovery | HACS/Marketplace update entity and available-update UI |
| Integration installation | User installs the offered update; HA restart applies the new code |
| Optional integration automation | User-enabled automation can call the update action; it must also account for the required restart |
| Daemon app updates | Supervisor offers a new app version; users may enable automatic updates per app |
| Radio firmware updates | Separate explicit install through the firmware entity, subject to exact-board catalog validation and daemon verification |

Marketplace creates update entities and checks its catalog every six hours.
Custom repositories require the user's GitHub device authorization and are
checked through GitHub every 48 hours. They receive the same update interface,
but catalog inclusion eventually removes that GitHub-connection requirement.
Automatic installation is not the default promise: Marketplace permits update
automations after its warning is accepted, and integration changes still need
a restart. HACS also exposes update entities.
[Marketplace user behavior](https://www.home-assistant.io/integrations/marketplace/),
[HACS update entities](https://www.hacs.xyz/docs/use/entities/update/)

Supervisor updates recreate the app container while preserving `/data`.
`breaking_versions` prevents automatic upgrades across releases requiring
attention. Keep daemon API changes backward-compatible across independently
updated app/integration versions; an integration update must not silently
require a firmware flash. The existing firmware catalog contains no release
payloads, so publishing software alone does not make a radio upgrade available.
[Supervisor update behavior](https://contributors.home-assistant.io/apps/how-apps-work/)

## Migration and release acceptance

The local development app and a repository-installed app have different
Supervisor identities and data directories. The repository URL contributes to
that identity, so choose it once and keep it stable. A normal app update retains
data; changing from the local app to the new repository is a migration.
[Repository identity](https://contributors.home-assistant.io/apps/guides/repository/)

Prepare a migration procedure that stops the old daemon, consistently backs up
and transfers its database/options, preserves gateway/tag/token, and reconfigures
the existing HA entry to the new endpoint. Never run two owners of the same
serial radio. Verify device and entity IDs survive; do not instruct users to
delete/re-add the integration. Retain the previous app and database backup until
the new installation is verified. This migration is not implemented by this plan.

Release qualification checklist (completed subset is recorded above; remaining
hardware/migration and automatic-update cases must not be implied by it):

- Install the exact public ZIP through HACS on 2026.10.4 and Marketplace on
  2026.11; verify bundled imports, existing entry adoption and restart behavior.
- Upgrade between two channel releases; verify entity IDs, names, areas,
  credentials and manual fallback remain intact.
- Pull the app image anonymously, install it through the repository, verify
  `/data` persistence, ingress, API authentication and USB permissions.
- Test compatible independent app/integration updates, repository discovery,
  optional automatic app upgrades, breaking-version blocking and rollback.
- Verify that neither software installation nor automatic updates starts radio
  flashing or opens a pairing window.

## One-time owner and user actions

The owner created the public integration repository and saved its description
and topics. Release workflows are enabled, both integration releases passed
validation, and all advertised GHCR images passed anonymous pulls. For future
packages, check visibility separately from repository visibility: new packages
may require an account administrator to make them public. Test access before
advertising them.
[GitHub container registry access](https://docs.github.com/en/packages/working-with-a-github-packages-registry/working-with-the-container-registry)

Use repository-local workflow tokens for release and package publishing where
possible. A distribution-repository workflow can build a pinned public source
commit with its own token, avoiding a cross-repository write secret. Fully
automatic cross-repository release orchestration may require an owner-installed
GitHub App or scoped token; arrange that only if this design is chosen.
[Workflow authentication](https://docs.github.com/en/actions/tutorials/authenticate-with-github_token)

The owner or a qualifying major contributor must submit catalog inclusion and
respond to its review; acceptance is controlled by the HACS maintainers. Before
inclusion, each end user must add the repositories and authorize GitHub where
required. Users choose whether to enable unattended app updates or integration
update automations. Publication cannot grant those permissions on their behalf.
