# Public firmware bundle acceptance

A firmware download and an automatic updater offer are separate approvals.
Every complete project release must contain an accepted application artifact
for all four exact boards. Only boards whose specific updater lifecycle also
passes enter the installed daemon catalog. Manual-only firmware is retained
in the public bundle, with its limitations and recovery instructions.

`tools/firmware_release.py` performs offline admission and export. It never
builds, signs, flashes, contacts a vendor or approves a licence. Its review
records are human attestations bound to exact bytes; a field saying `physical`
is not itself proof that a physical test happened. The ZigBee specialist supplies
real records, the Tech Lead reviews scope and redistribution, and the Release
Engineer inspects the evidence before freezing the inventory. The tool rejects
missing results; it does not substitute for these reviews.

## Inputs

Prepare a private staging directory using only explicitly reviewed files:

```text
candidate/
  spec.json
  artifacts/                 # Four application-only containers
  keys/                      # TI raw public verification key only
  notices/                   # Exact SDK, runtime and project notices
  qualification/             # One public review record per board
  evidence/                  # Sanitized build, physical-test and rights evidence
```

The specification has exactly `schema: 1`, `version` (radio major.minor),
`source_commit` (full frozen firmware SHA), and `artifacts` (four records).
Each artifact record contains `board`, `artifact`, `qualification`, `notices`
and, for TI only, `public_key` and `bootstrap`. Every file reference is an object with `path`
and `sha256`; paths are relative to the specification directory and confined
to their assigned subdirectory. `notices` is a nonempty list of references.
Unreferenced files are not copied. Never place signing secrets in a release
staging tree; the export allowlist is an additional protection, not a secret
scanner. Symlinks, traversal, empty files, changed hashes and oversized inputs
are rejected. Each input file is limited to 1 MiB, matching the runtime catalog;
retain larger notice collections as separately referenced component documents
without omitting or changing their terms. The immutable payload snapshot is inspected for exact board,
version, application layout and container integrity; TI signatures are verified.
The TI application must also embed the selected release public key exactly once.

TI's mandatory `bootstrap` component has `version: "0.2"`, `artifact`,
`qualification`, and `notices`. It is a raw project bootloader, never a merged
factory image or another board's CCFG. Its separate qualification uses scope
`public_firmware_bootstrap`, the bootloader's version/hash, the same source,
all public checks below and `automatic_update.eligible: false`. Structural
inspection checks the supported flash/vector layout, marker and selected key;
it does not authenticate boot code or replace the independent physical
first-install/recovery review. Bootstrap files never enter the application
updater catalog. Same-version historical bootloaders have different hashes,
so filenames and version markers cannot select the qualified bytes.

Required board identifiers:

- `codm-cc2652p`
- `sonoff-zbdongle-e`
- `zb-gw04-1v2`
- `nordic-pca10059`

The qualification file has exactly these fields:

| Field | Required value |
| --- | --- |
| `schema` | Integer `1` |
| `scope` | `public_firmware_download`; local-trial approval is rejected |
| `board`, `version`, `source_commit`, `artifact_sha256` | Exact matching artifact and frozen source identities |
| `technical_reviewer`, `redistribution_reviewer` | Named accountable reviewers; nonempty strings |
| `limitations` | Nonempty list describing the actual support and recovery limits |
| `checks` | Every public check below, passed with retained evidence |
| `automatic_update` | Separate eligible or blocked decision |

Each check contains exactly `result: "passed"`, `kind`, a meaningful `summary`
and a nonempty `evidence` list of hash-bound references under `evidence/`.

| Public check | Evidence kind |
| --- | --- |
| `build_inputs`, `repeat_build` | `build` |
| `firmware_tests` | `test` |
| `boot_identity`, `basic_control`, `manual_install`, `manual_recovery` | `physical` |
| `redistribution` | `review` |

Build evidence must identify the exact SDK/toolchain, linked inputs and notices,
fresh source export and repeated application bytes. Physical evidence must name
the actual board and image, operator, date, setup, result and bounded limitations.
Redistribution evidence must identify the applicable exact dependency terms and
how this delivery satisfies them. Do not fill these records from an old artifact's
result without a reviewed, technically justified evidence binding.

For a manual-only artifact, `automatic_update` is exactly
`{"eligible": false, "reason": "<specific remaining lifecycle gate>"}`.
For an eligible artifact it contains `eligible: true`, `reviewer`,
`tested_runtime_image` (an immutable pre-publication image reference ending in `@sha256:<digest>`), and
`checks`. All four updater checks require passed `physical` evidence:
`installation`, `exclusive_ownership`, `restart_reenumeration`, and
`interruption_recovery`. This must describe the packaged runtime and that exact
board's update/recovery path. No board inherits another board's qualification.

The tested runtime digest is deliberately a pre-publication qualification image.
It cannot be the digest of an image that embeds its own qualification record:
that would be a circular hash dependency. Final image qualification must retain
a detached record of the final digest, executable/helper/configuration byte
comparison against the tested runtime, and final installed-image smoke checks.
Changes affecting the tested execution path require renewed physical testing;
adding evidence files alone does not prove runtime equivalence. This detached
final-image gate is part of package publication, outside the firmware inventory.

## Export and recheck

```sh
python3 tools/firmware_release.py assemble \
  --spec /absolute/path/candidate/spec.json \
  --output /absolute/path/new-public-bundle
```

The output directory must not already exist. It contains only reviewed inputs,
canonical `release.json`, `SHA256SUMS`, and a derived `catalog/manifest.json`.
The catalog can be empty or contain a qualified subset without dropping any
required artifact from the public download inventory. Its candidate payload
bytes are identical to the corresponding public download bytes.

Freeze the printed SHA-256 of `release.json` in the software release metadata.
Before staging into packages or containers, independently supply that expected
hash, source and compatibility version:

```sh
python3 tools/firmware_release.py verify \
  --directory /absolute/path/public-bundle \
  --inventory-sha256 <reviewed-release-json-sha256> \
  --source-commit <frozen-firmware-source-sha> \
  --version <radio-major.minor>
```

Verification rechecks every approval and file, derives the catalog again and
rejects extra files or altered updater eligibility. This pin authenticates the
selected reviewed inventory within the release process; it is not a digital
signature or a substitute for trusted release metadata.

## Software and container delivery

Create a deterministic transport archive after the inventory is accepted:

```sh
python3 tools/firmware_release.py pack \
  --directory /absolute/path/public-bundle \
  --inventory-sha256 <reviewed-release-json-sha256> \
  --source-commit <frozen-firmware-source-sha> \
  --version <radio-major.minor> \
  --output /absolute/path/new-firmware.tar.gz
```

The returned hash identifies the compressed archive. Freeze a
`host/release/firmware.json` descriptor in the software source with exactly
`schema: 1`, `inventory_sha256`, `source_commit`, `version`, `archive_url`
(public HTTPS) and `archive_sha256`. Its radio version must match the software's
explicit compatibility target. No descriptor is created until the real public
bundle passes review. The independent firmware source SHA can precede the
software release SHA that records its final inventory and transport hashes.

Standalone, HA app and both container builders use the same validator. They
can take `--firmware-bundle` for an already staged local directory; otherwise
they fetch only the pinned archive, check its compressed hash, reject unsafe
archive paths/links, and revalidate the entire public inventory. Missing or
changed files abort the build; there is no empty-catalog fallback.

A clean source catalog is required before injection into a private build
export. The standalone archive carries `firmware-release/`; both images and
HA app build contexts deliver it under `/usr/share/livolo-ec/firmware-release`.
Only independently updater-qualified payloads enter the daemon wheel/catalog.
The complete manual inventory, bootstrap and notices remain alongside it.
Generated `FIRMWARE-LICENSES.txt` mappings identify each exact notice without
creating a new licence grant. When updater payloads enter the daemon wheel or
sdist, only its temporary build metadata changes to
`BSD-2-Clause AND LicenseRef-Livolo-Firmware-Mixed` and includes that mapping as
a licence file. Empty-catalog daemon packages and client/integration software
retain BSD metadata. Source-project licensing is unchanged.
Image metadata labels the mixed firmware terms, and records the inventory,
source, transport and per-file context hashes. The actual license documents
and accepted channel presentation remain required; a label does not grant rights.

Installed-image checks compare the runtime catalog and payload bytes against
that same pinned public bundle using `verify_installed`. CI additionally builds
real wheels, source packages, archives and both ARM64 images using explicitly
non-flashable synthetic fixtures. Those checks prove delivery mechanics, not
hardware qualification or redistribution approval. Fixture images are local to
the disposable runner, never pushed, and have no radio devices attached.

`host/release/firmware-policy.json` makes a distribution newer than **0.5.0**
fail packaging unless it pins the accepted all-board bundle. Historical builds
remain reproducible. Explicit non-publishing container candidates and HA app
`--candidate` builds can omit pending public firmware so physical qualification
can proceed with controlled local catalogs. They retain candidate metadata and
cannot take the publication path; the HA context includes a candidate marker.
The release packagers never enable this bypass. A later software-only exception would require an explicit,
reviewed policy change; omitting one board is not a workaround.

## Current qualification boundary

No real public qualification record or payload is approved by adding this
tooling. Existing `.local/release-0.13` records remain local-trial evidence and
cannot pass the public gate. The distributed runtime catalog remains empty.
The owner selected `livolo.sfires.net` for the shared Nordic serial namespace
and laptop custody for the dedicated TI release key. The owner also confirmed
accepting the Gecko SDK licence. Physical tests, identification of the applicable
MG21 agreement revision and final notice review remain open. See
[distribution decisions](FIRMWARE_DISTRIBUTION.md),
[qualification plan](FIRMWARE_QUALIFICATION_PLAN.md), and
[release acceptance](RELEASE_PROCESS.md).
