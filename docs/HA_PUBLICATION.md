# Home Assistant installation and update publication plan

Status: proposed, reviewed 2026-11-05. This document does not publish a repository,
container image, catalog entry, or update channel. The existing `ha-0.1.1` and
`ha-0.2.0` releases are manual-install distributions and remain immutable.
Source inspection for this plan used `c2b2f5f`.

Implementation update: the authorized channel work now provides
`homeassistant/export_channel.py`, component-root ZIP packaging, original
project branding, pinned source and packaging-file hashes, reusable validation,
and ordinary SemVer tag-triggered release workflows. HA integration/app source
is 0.2.1. The production root catalog remains withheld until public image checks
pass; `homeassistant/catalog/repository.yaml` is its unpublished template.
`homeassistant/stage_supervisor_probe.py` creates the separate no-radio
0.2.0/0.2.1 validation catalog. These artifacts are not proof of public channel
installation, default catalog acceptance, or live deployment; those outcomes
must be recorded separately after execution.

## Recommended channel layout

Keep development in `psires/livolo_ec`. Publish the assembled integration through
a small, generated distribution repository, provisionally
`psires/ha-livolo-ec`. Use the main repository as the Supervisor app repository
after adding its repository metadata and a prebuilt-image reference. A third
repository for apps is optional, not required.

| Delivery | Proposed channel | Version authority |
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

## Implementation and publication sequence

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

   Start the new channel with a reviewed packaging release, proposed **0.2.1**,
   whose tag matches its integration manifest. Do not relabel or replace the
   already published main-repository 0.2.0. Record the source commit and explain
   any differences from it. Publish only after the complete assets and checksums
   are ready. Keep older channel releases for rollback.

3. **Build and verify HA app images.** Follow the companion container publication
   plan for exact-source builds, smoke tests and image provenance. Use a distinct
   HA-wrapper image, provisionally `ghcr.io/psires/livolo-ec-ha-app`. Build the
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

Acceptance checks before announcing the new channels:

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

The publisher needs authority to create the public integration distribution
repository, set its metadata and enable release workflows. These are proposed
actions, not completed ones. App/standalone GHCR packages must be public for
anonymous pulls: first publication is private by default, and package visibility
is a separate setting from repository visibility. An account/package administrator
may have to perform that visibility change. Test it instead of assuming that a
public source repository makes its images public.
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
