# Container publication plan

Status: recommendation prepared 2026-11-05; this document does not publish an
image, change a release, or qualify a new deployment. Coordinate HA delivery
with [HA_PUBLICATION.md](HA_PUBLICATION.md).

## Existing artifacts and version boundaries

The Pi's installed `livolod:0.12` ARM64 image and the original offline archive
predate the native API and integrated updater work. Restoring that image on the
standalone testbed does not test the forthcoming standalone bundle or a newly
built image. Preserve its original verification record and checksums.
The current HA testbed likewise runs an older local app image. Neither local
installation establishes that a public GHCR image exists.

Use separate image repositories for the standalone entrypoint and HA wrapper:

| Proposed image | Version authority | First publication scope |
| --- | --- | --- |
| `ghcr.io/psires/livolod:0.2.0` | Standalone distribution `0.2.0` | Exact `standalone-0.2.0` source and recorded build inputs, once frozen and tested |
| `ghcr.io/psires/livolo-ec-ha-app:0.2.0` | HA app `config.yaml` version | Optional historical build from the exact `ha-0.2.0` source, with limitations recorded |
| `ghcr.io/psires/livolo-ec-ha-app:0.2.1` | Proposed next HA app version | New channel packaging only after that version is assigned and tested |

These names are proposed, not download links. The daemon and radio compatibility
version remains **0.12**; `livolod --version` must continue to report it. Client
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

Before the first public image, finish these release gates:

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
