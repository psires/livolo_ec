# Changelog

## 0.5.1

- Recognize the additional observed two-gang pairing payload while retaining
  exact matching, freshness, conflict and confirmation checks.
- Retain bounded pairing rejection evidence and clarify HA session results.
- Package daemon 0.14.1; radio compatibility remains 0.13 (API 1/UART 3).
- Incorporate the private diagnostic/profile fixes into the official image.
- Software-only: no radio firmware, bootloader or trust-key changes.

## 0.5.0

- Add one-action pairing shared by Web, native HA and the commissioning CLI,
  with bounded sessions, explicit confirmation and cancellation.
- Package daemon 0.14.0 separately from radio compatibility 0.13; expose both
  versions while retaining native API revision 1 and UART protocol 3.
- Keep switch control available when firmware catalogs are missing or unusable,
  with clearer diagnostics in integration 0.5.0.
- Preserve persistent configuration, registry, identity and local catalogs.
  Interrupted firmware jobs are retained without replaying installation.
- Software-only release: no radio payloads or new hardware qualification.
  ARM64 remains the supported container architecture.

## 0.4.0

- Allow native HA control and firmware updates with the Web listener disabled;
  existing installations keep Web enabled by default.
- Add a persistent local firmware catalog option at `/data/firmware`, with
  validated administrator provisioning and background availability refresh.
- Keep active job targets independent of catalog changes and revalidate bytes
  before installation admission.
- Expose measured transfer progress to native HA separately from verified
  completion. Preserve durable job tracking across disconnects and restart.
- Retain daemon/radio compatibility 0.13 and the empty public firmware catalog.
  Updating software does not flash a radio.

## 0.3.0

- Add shared read-only firmware preflight to the daemon Web UI and native API.
- Include unified inspection, dry-run and emergency firmware maintenance commands.
- Package daemon 0.13 and the isolated Nordic helper; the HA wrapper keeps the
  opt-in Nordic USB profile disabled.
- Preserve the existing configuration, network identity and persistent registry
  when updating the repository app. Updating this app does not flash a radio.
- Keep the public firmware catalog empty until exact-board payloads qualify for
  public distribution. Update the separate HA integration to 0.3.0 for its new
  preflight and standard firmware-update flow.

## 0.2.1

- Publish the HA wrapper as a versioned GHCR image for repository installation.
- Add app installation, configuration and update documentation.
- Keep the daemon protocol/firmware baseline at 0.12.

Moving from a manually installed local app to the repository app is a separate
migration. They have different data directories; a normal update does not copy
the old database. Preserve network identity, credentials and registry before
migration. Do not run both apps against the same radio.

## 0.2.0

- Add durable radio firmware maintenance jobs and native API status.
- Add experimental detection of the tested one- and two-gang pairing profiles.
- No radio firmware payload is bundled; firmware installation requires a
  validated exact-board candidate and explicit user action.

## 0.1.0

- Initial native API, ingress and ARM64 local-app packaging.
- Used with the historical integration release 0.1.1.
