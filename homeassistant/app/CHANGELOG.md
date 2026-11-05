# Changelog

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
