# Persistent local firmware catalogs

The software 0.4.0 catalog workflow retains daemon/radio compatibility **0.13**.
The public packaged catalog remains empty. Firmware publication, operator approval
for a controlled trial, and live hardware qualification are separate decisions.
The catalog helper validates exact board, compatibility version, application
layout, payload digest and required qualification/notices. A `qualified: true`
flag and qualification record are trusted operator metadata, not a publisher
signature or evidence that a live update has succeeded. TI packages additionally
require their configured Ed25519 verification key.

## Home Assistant app

Select `firmware_catalog: local` in app options to use **`/data/firmware`**.
The default `bundled` selection keeps using the catalog shipped in the image.
Only these selections are supported; HTTP clients cannot supply filesystem
paths or download locations. A missing, malformed or incompatible local catalog
disables firmware installation with a blocking reason while ordinary switch
control remains available. It never silently falls back to the packaged catalog.

The Web UI is optional (`web_enabled: false`). Catalog provisioning is a local
administrator operation; HA firmware status, preflight, installation and progress
use the authenticated native API and do not require a Web listener or ingress.
Changing app options uses the normal Supervisor app restart. Subsequent catalog
publications are observed without restarting the app or reconnecting its radio.

Prepare an already reviewed catalog directory containing `manifest.json` and all
referenced payloads, qualification records, notices and any verification key.
Do not copy private signing keys. On an HA administration shell with Docker
access, stage that directory in the app's persistent data, then publish it:

```sh
# Find the installed repository app's actual container name with docker ps.
LIVOLO_APP_CONTAINER=YOUR_INSTALLED_APP_CONTAINER
docker cp ./reviewed-catalog "$LIVOLO_APP_CONTAINER":/data/catalog-incoming

docker exec --user 0 "$LIVOLO_APP_CONTAINER" \
  livolod-firmware-manage catalog \
  --source /data/catalog-incoming \
  --destination /data/firmware \
  --manifest-sha256 REVIEWED_SOURCE_MANIFEST_SHA256
```

The helper creates catalog directories as 0755 and published files as 0644.
If the destination already exists, ensure runtime UID/GID 10001 can traverse its
directories and read its files; do not relax permissions on the rest of `/data`
or on credential files.

Use a new staging directory for each transfer; never transfer directly over the
active catalog. The optional `--manifest-sha256` binds publication to the exact
reviewed source manifest. The command requires no daemon configuration, does not
open SQLite or the radio, and does not submit a firmware job. All copied bytes
are validated again from a private staging snapshot before publication.

Payloads and supporting files are stored by content hash under `objects/`.
Only `manifest.json` is replaced at the commit point, after files have been
written and synced. Previous manifests are retained under `history/`; objects
are not automatically deleted while a job might still need them. The printed
source and published manifest hashes differ because publication rewrites file
references to these immutable objects. Candidate IDs and artifact hashes remain
unchanged. Interrupted publication leaves either the previous or new manifest
visible; it never deliberately exposes a half-populated catalog. Local publishers
are serialized with a separate lock that does not interfere with radio ownership.

The catalog stays in `/data` across Supervisor container recreation and software
upgrades. Existing `/data/firmware-zbgw04-0.13` trial files can be used as a reviewed
source for this helper; the old practice of copying into the installed Python
package is no longer needed. Retain an independent reviewed source and normal
HA app backups. To withdraw all offers, publish a valid matching-version manifest
with an empty `artifacts` array. Do not delete object files during an active job.

## Standalone daemon

Set `firmware_bundle_dir` to an absolute persistent directory. Run the same
`catalog` subcommand with the reviewed source and that destination. For Docker,
publish into the host directory mounted at the configured container path; a
read-only daemon bind mount is supported because publication replaces files
inside the directory instead of replacing the mounted directory itself.

## Refresh, admission and progress

A service-owned watcher checks catalog revisions every five seconds, with only
one scan active at a time. Filesystem inspection and changed-artifact validation
run outside the daemon event loop. Each referenced file remains subject to the
existing per-file limit; the complete referenced catalog is limited to **16 MiB**.
Unchanged files avoid repeated artifact parsing. Helper availability is checked
again independently. Results from an obsolete radio observation/configuration
are discarded; refresh never opens a UART, resets the radio, changes stored
network/device state or erases cached `VERSION`/`UPDATE_STATUS` facts.

Publication, replacement, removal and invalidation update the same native/Web
snapshot and native push stream, without requiring a Web visit. Installation
admission still rereads the selected hash-bound artifact before requesting a
handoff, and the worker validates it again before mutation. A catalog change
cannot retarget an admitted durable job: its candidate, version, TI signing key
and recovery state remain independent of the currently advertised catalog.

Native snapshots add optional `transfer_percent`: a finite value from 0 to 100,
or null when no measured transfer progress is available. It describes the byte
transfer phase only. Overall `progress_percent` remains unknown, and transfer
100% does not mean installed or verified. Completion requires the existing
board/version checks and restored radio session before the durable job becomes
`confirmed`. Neither provisioning nor candidate discovery automatically flashes
firmware. Real radio qualification remains a separately authorized operation.
