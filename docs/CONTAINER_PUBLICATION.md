# Container publication

Status: standalone and HA app **0.3.0** are public for `linux/arm64`, with daemon
**0.13**. Anonymous verification checked image/config digests, source labels,
SPDX SBOM and SLSA provenance. The exact images passed persistent-data upgrades
from standalone 0.2.0 and HA app 0.2.1 using isolated containers with no radio.
See the [0.3.0 publication record](../host/docker/publication-0.3.0-20261105.json).
The HA app catalog advertises 0.3.0 only after those gates passed.

The initial HA upgrade test assumed radio-derived diagnostics were present in
the offline baseline. The test now reads installed package metadata; the entire
HA upgrade and publication sequence passed on retry. Published application
source and release tags were unchanged.

Earlier 0.2.0/0.2.1 image digests and checks remain in the
[historical publication record](../host/docker/publication-20261105.json).
Actual Supervisor testing belongs to that earlier qualification; the live HA
host was unchanged for 0.3.0. See [HA_PUBLICATION.md](HA_PUBLICATION.md).

## Implemented release controls

The workflow `.github/workflows/container-release.yml` runs on an explicit
`host/docker/releases.json` push to `main`, or manual dispatch. New entries are
disabled until reviewed. The manifest pins the source tag and full commit, Python
base, Dockerfile frontend, BuildKit image, Buildx version and test broker. Actions
are pinned by commit. Only the publishing job receives `packages: write`.

`tools/container_prepare.py` builds a minimal context from `git archive` at the
exact source commit, verifying the tag and component version. An external
Dockerfile overlay constrains Python build tooling, preserves matching third-party
Python source archives, and adds notices, source/build metadata and Debian package
inventory. Application code and the historical wrapper are copied unchanged.
The generated Dockerfile, original recipe hash and every context file hash are
recorded. Current-source Dockerfile ignore rules also include `host/PACKAGE.md`.

Each enabled release runs on `ubuntu-24.04-arm`. Buildx pushes a unique candidate
with SBOM and maximum provenance, then the workflow pulls and tests that exact
digest. Checks include package consistency, CLI/helper imports, source checksums,
architecture/labels/attestation descriptors, and real container lifecycle tests.
`tools/container_check.py` owns a temporary internal Docker network and temporary
state only; it publishes no host ports and maps no radio devices. Run it only on
disposable Docker hosts: its `172.30.32.0/24` test network models the Supervisor
trusted-peer address and must not overlap a real Supervisor installation.

Standalone tests use the installed simulator and an isolated MQTT broker, verify
retained online status, web assets/CSRF rejection, authenticated HTTP/WebSocket
API, registry writes and persistence after container replacement. HA tests run
the real wrapper with a nonexistent serial path, check privilege dropping and
untrusted-ingress rejection, and use a private trusted-peer container to check
ingress assets/origin enforcement. They also restore root-owned database files
between containers to test ownership repair. This models the proxy boundary; it
does not claim to test Supervisor authentication itself.

Set `previous_image` to an already published **digest** to test a real version
upgrade. HA 0.2.0 establishes the baseline; the 0.2.1 request must reference its
verified digest. When `previous_image` is null, the evidence explicitly reports
first installation and same-image recreation, not a version upgrade. The HA
specialist separately owns disposable Supervisor app installation/update tests.

`tools/container_registry.py` first checks anonymous candidate pulls using an
empty Docker credential store. If the new package is private, the job stops
before creating a full version tag. The maintainer can set package visibility
to public and rerun. Successful publication copies the tested OCI index without
rebuilding; an existing different version digest causes failure, never overwrite.
The final tag is checked for digest equality and anonymous access. Disable a
completed manifest entry before requesting another release; retrying a complete
build is not a mechanism to replace an existing version.

Every job uploads evidence, including on failure: source/context recipe metadata,
Docker/build tooling versions, candidate index and image metadata, Python source
and license inventory, Debian package inventory, runtime logs and successful
publication metadata when available. Buildx registry attestations remain attached
to the promoted index. Inventory/source files also ship inside the image under
`/usr/share/livolo-ec/`, with project notices under
`/usr/share/licenses/livolo-ec/`. Base-image Debian package copyright material
remains under `/usr/share/doc`; the package inventory and pinned base identify
the corresponding upstream source distribution. Review this inventory before
claiming the release's third-party obligations complete.

Local validation command (Python 3.12 or newer):

```sh
python3 tools/container_test.py
python3 tools/container_prepare.py
```

The first command exercises historical context assembly, request validation,
immutable promotion, public-access gating and corrupt-source rejection. Actual
container runtime results must come from CI; local helper tests alone do not
qualify a release. Keep source/image publication distinct from deployment to
either live testbed.

## Existing artifacts and version boundaries

The Pi's installed `livolod:0.12` ARM64 image and the original offline archive
predate the native API and integrated updater work. Restoring that image on the
standalone testbed does not test the forthcoming standalone bundle or a newly
built image. Preserve its original verification record and checksums.
The current HA testbed likewise runs an older local app image. Neither local
installation establishes that a public GHCR image exists.

Separate image repositories serve the standalone entrypoint and HA wrapper:

| Published image | Version authority | Exact source |
| --- | --- | --- |
| `ghcr.io/psires/livolod:0.2.0` | Standalone distribution `0.2.0` | `standalone-0.2.0`, commit `b371ca9` |
| `ghcr.io/psires/livolo-ec-ha-app:0.2.0` | HA app `config.yaml` version | `ha-0.2.0`, commit `6fe41cb` |
| `ghcr.io/psires/livolo-ec-ha-app:0.2.1` | HA app `config.yaml` version | `ha-0.2.1`, commit `c75839d` |

The daemon and radio compatibility version remains **0.12**;
`livolod --version` continues to report it. Client
and standalone distribution versions are **0.2.0**. Historical `ha-0.1.1`
contains HA app/client **0.1.0**, so its app image would be tagged `0.1.0`, not
`0.1.1`. Never rebuild historical releases from current `main` under old tags.

Do not move the published Git tags `ha-0.1.1` or `ha-0.2.0`. Treat full image
version tags as write-once by workflow policy: refuse an existing tag unless its
digest exactly matches the recorded release. Registry tags alone do not provide
immutability. Record the OCI index and platform digests; deployments requiring
fixed contents should pull by digest. A dependency/base refresh needs a new
distribution or app patch version even when daemon 0.12 is unchanged. Omit
`latest` initially; any future `stable` alias must be explicitly documented as
mutable. GitHub documents digest-based pulls in its
[container registry guide](https://docs.github.com/en/packages/working-with-a-github-packages-registry/working-with-the-container-registry).

## Registry access and metadata

Publish through repository-owned GitHub Actions to GHCR using `GITHUB_TOKEN`,
with `contents: read` and `packages: write` only on the publishing job. Link both
packages to `psires/livolo_ec`. Add OCI source, revision, version, description and
license labels. Set the OCI version to the image's distribution/app version and
record daemon 0.12 separately, for example in `net.sfires.livolo.daemon-version`.
The Dockerfile currently ties its version label and `RELEASE_VERSION` argument
to daemon 0.12. For the frozen `standalone-0.2.0` source at `b371ca9`, retain
`RELEASE_VERSION=0.12` and supply the distribution label through Buildx metadata
or `--label org.opencontainers.image.version=0.2.0`. Apply the corresponding app
version label when building an HA image. Record the external build recipe,
label overrides and tool inputs alongside the source commit; do not edit or
retag the frozen release. A future source release can introduce separate label
arguments while preserving the daemon compatibility assertion.

New GHCR packages default to private. A maintainer must make each intended
distribution package public and verify repository Actions access, including for
any package originally pushed manually. Public containers support anonymous
pulls; a public source repository alone is not sufficient. Check access from a
fresh Docker configuration with no stored credentials before advertising an
install command. Do not distribute a PAT to users.
[GHCR authentication, linking and visibility](https://docs.github.com/en/packages/working-with-a-github-packages-registry/working-with-the-container-registry)

Pin third-party workflow actions to reviewed commit SHAs. If adding GitHub's
signed artifact attestations, grant the attestation job the required
`attestations: write` and `id-token: write` permissions; basic registry pushing
does not itself require those permissions. Keep pull-request validation free of
publishing credentials and do not publish from untrusted PR code.
[GitHub publication workflow](https://docs.github.com/en/actions/tutorials/publish-packages/publish-docker-images)

## Build and release sequence

1. **Freeze release inputs.** Record the source commit, immutable source tag,
   distribution/app/daemon versions, Dockerfile and wrapper hashes, dependency
   constraints, base-image digest, Python version and builder versions. Pin the
   Python base by digest; the current `python:3.13-slim-bookworm` tag floats.
   Exact dependency versions alone are not a reproducibility claim: record and
   validate downloaded artifact hashes and constrain build tooling too.

2. **Build distinct contexts.** Standalone uses the root context and
   `host/docker/Dockerfile`; HA uses the assembled context produced by
   `homeassistant/build_release.py`, including its wrapper. Run assembly from the
   release checkout and pass that filesystem context explicitly to Buildx.
   Docker's default Git context ignores files generated by earlier workflow
   steps. Audit the context against secrets, local databases and RF captures.
   [Docker GitHub Actions context behavior](https://docs.docker.com/build/ci/github-actions/)

3. **Build ARM64 first.** Publish `linux/arm64` initially, corresponding to HA's
   `aarch64`. Use a native ARM64 Linux runner for build and runtime tests when
   available. A future `linux/amd64` variant can join the same OCI index after
   its own image and wrapper checks pass; the standalone Python package's x86-64
   checks do not qualify the container. Do not advertise ARMv7/32-bit support.
   Buildx supports multi-platform registry publication and native platform jobs.
   [Docker multi-platform builds](https://docs.docker.com/build/ci/github-actions/multi-platform/)

4. **Test the exact candidate.** Push to a non-release candidate reference and
   capture its digest. Pull and run that digest for each advertised platform;
   promote the tested manifest instead of rebuilding after tests. Run
   `host/docker/smoke.py` as UID 10001, `pip check`, all CLI help/version checks,
   config validation, and flasher import/help. Test read-only root, temporary
   storage, persistent state ownership, SIGTERM and restart behavior. Verify web
   HTML/JS/CSS and CSRF checks, authenticated HTTP/WebSocket API, SQLite integrity
   and retained MQTT status with a disposable broker. Simulation must remain
   separate from live network identities and credentials.

5. **Test the HA wrapper separately.** Exercise `/data` preparation, privilege
   dropping, serial group access, missing-radio startup, ingress trust checks,
   bearer API and Supervisor restart/backup behavior. A standalone non-root
   smoke check does not test the privileged preparation wrapper. Qualify the
   advertised HA OS/Supervisor baseline before changing its app-store metadata.

6. **Attach provenance and inventories.** Request `sbom: true` and
   `provenance: mode=max` when pushing registry candidates. Use BuildKit secret
   mounts, never build arguments, for credentials: maximum provenance includes
   build arguments. Record the final index digest, each platform digest, test
   results and source/build inputs in release metadata. Docker documents these
   [attestation options and export limits](https://docs.docker.com/build/ci/github-actions/attestations/).

7. **Promote and verify.** Serialize publication per image/version, check for an
   existing version tag, promote the tested candidate, and verify digest equality
   and anonymous pulls again. Publish release notes, checksums, source/license
   material and optional offline archives. Only then advertise the version in
   Compose examples or HA app metadata. Preserve previous version digests for
   rollback. App metadata must not point to an image still being built.

For HA, set `image: ghcr.io/psires/livolo-ec-ha-app` without a tag; Supervisor
selects the app's configured version. Keep the image's platform index consistent
with the app's `arch` list. The standalone image cannot substitute for the HA
wrapper. Follow the companion HA plan for repository discovery and user update
delivery. [HA image configuration](https://contributors.home-assistant.io/apps/reference/configuration/)

## Distribution contents and qualification gates

Include daemon/web assets, runtime dependencies, MQTT support, supported host
updater helpers, licenses and source notices. The current Dockerfile includes
the exact `universal-silabs-flasher` source archive and checks its SHA-256;
preserve that archive and its license alongside any offline release. Audit the
full dependency and base-image inventory for required notices/source material.
The BSD label describes project code and does not relicense third-party files.
Do not claim that including one helper's source completes that inventory.

The official firmware catalog remains empty. Include its empty manifest and
report firmware installation unavailable; do not add dummy keys, private signing
material or unqualified radio binaries. Installing/upgrading the host must not
flash a radio automatically. A helper import test is not a hardware flash test.

For each subsequent public image, repeat these release gates:

- Freeze the exact release source and build recipe; distinguish old local image
  evidence from the new digest's installed-artifact tests.
- Complete native ARM64 image checks and HA wrapper qualification where relevant.
- Confirm GHCR namespace access, public visibility and anonymous pull success.
- Verify distribution/app version labels, platform manifest and immutable-tag
  handling; protect historical releases from overwrite.
- Complete the third-party inventory and record firmware limitations.

The new standalone package plan correctly separates distribution 0.2.0 from
daemon 0.12, includes daemon/client wheel and source packages plus configuration,
systemd and recovery documentation, and tests installed wheel/source variants
on Linux ARM64 and x86-64 with Python 3.13. The client is a Python library, not a
new switch-control shell command. State explicitly that dependencies require
network access and that no firmware payload or Docker image is in this bundle.
Because multiple historical wheels share daemon version 0.12, upgrades must use
the exact checksummed release artifact and its documented reinstall procedure;
`pip install --upgrade` by version alone cannot distinguish those builds.

No new live deployment, commit, push or registry publication is authorized by
this plan itself.
