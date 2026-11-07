# Independent daemon package versioning — proposal

Status: architecture approved by the Tech Lead / PO on **2026-11-07**.
Implementation was subsequently authorized on 2026-11-07 for an offline-tested
source change. The daemon development version is allocated as **0.14.0**, with
firmware compatibility **0.13** and coordinated distribution/HA versions unchanged.
The source implements this separation; acceptance requires the checks below on
the integrated commit. Publication, deployment and hardware qualification remain
separate gates. Inspection baseline:
`7a15d627132c515a0537b3b92f7520ac152c3b9e`. Accepted pairing behavior is unchanged.

## Approved architecture

Separate the installable daemon package version from its exact radio firmware
compatibility target. Keep native API revision 1, UART revision 3, the existing
firmware catalog schema and all TI signature/identity/trial checks intact.
This is a software identity change, not broader firmware compatibility support.

| Identity | Authority | Meaning |
| --- | --- | --- |
| Project distribution | `host/release/version.json:distribution_version` | Coordinated standalone, container and HA delivery version |
| Daemon package | `host/release/version.json:daemon_version`, propagated to `host/pyproject.toml` and `livolod.__version__` | Immutable Python distribution identity and CLI software version |
| Radio compatibility | Root `VERSION`, propagated to a named `FIRMWARE_COMPAT_VERSION` constant and recorded as `firmware_compat_version` in release metadata | Exact firmware target admitted by the daemon; currently `0.13` |
| Firmware image | Signed/package metadata and runtime CAPS | Actual two-component board image version, separate from software version |
| Wire contracts | Explicit native API and UART revision fields | API 1 and UART 3; neither inferred from a package version |

Daemon packages use ordinary three-component numeric versions and must increase
under Python package ordering. A package `0.5.0` would be older than the existing
daemon `0.13`; do not copy the overall distribution number into it. For example,
daemon `0.14.0` with firmware compatibility `0.13` would be a valid transition.
This example became the allocated development version on 2026-11-07. Do not use an epoch or
local-version suffix to conceal version reuse. No existing artifact is relabelled.

Keep root `VERSION` as the radio compatibility/build target for the first
transition; changing every firmware build input is unnecessary. Update its
documentation so it no longer implies the daemon wheel version. Retain the
two-component, bounded numeric firmware parser; use a separate validated
three-component parser for daemon software identifiers. Never feed software
versions into firmware, Nordic DFU integer encoding or TI image-version parsing.

## Runtime separation and TI invariants

Today `__version__` is used for both identity reporting and safety decisions.
Replace only its compatibility uses with `FIRMWARE_COMPAT_VERSION` in:

- `host/livolod/version.py`: diagnostic firmware target and numeric comparison;
- `firmware_manager.py`, `firmware_preflight.py`, `firmware_worker.py`:
  candidate selection, local admission, worker revalidation and restoration;
- `firmware_cli.py` and `firmware_update.py`: catalog provisioning, manual
  artifact checks and the legacy signed-update entry point;
- `service.py`: TI trial confirmation equality.

Keep `firmware_bundle.py` catalog schema 1 unchanged. Its legacy `host_version`
field means the radio compatibility target; document that explicitly. The
strict field validator currently rejects extra fields, so do not inject daemon
package metadata into existing catalogs. Existing reviewed `0.13` catalogs,
artifact hashes, IDs, signatures and immutable job targets remain byte-for-byte
valid. Internal parameter names can remain `host_version` for this change with
corrected docstrings; a broad rename adds risk without changing behavior.

The admitted artifact must still equal the explicit firmware compatibility
target and match board, application layout, baud, UART revision and trusted
signature/key requirements. The worker repeats admission checks. TI confirmation
still requires the exact running firmware target plus the existing admitted-job
hash/status/readiness checks and durable confirmation. A daemon-only upgrade
must not make `0.14` firmware eligible when compatibility is `0.13`, confirm an
unrelated trial, or create an update job. Existing behavior for older running
non-trial firmware is preserved; no new minimum firmware policy is introduced.

## CLI, API and user-visible reporting

- `livolod --version` reports the daemon package version and must equal installed
  wheel metadata. Add a structured, read-only `--version-info` output containing
  daemon version, firmware compatibility target, native API revision and UART
  revision; it must not load a service configuration or open a radio/database.
- Apply consistent read-only software version reporting to
  `livolod-commission`, `livolod-firmware-manage` and `livolod-firmware`.
  Preserve any existing firmware artifact `--version` argument; use
  `--version-info` where that name already means an artifact version.
- Add `daemon_version`, `firmware_compat_version` and explicit wire revisions
  to native `/info`, sanitized diagnostics and MQTT `bridge/info` as applicable.
  Keep API 1's existing `host_version` value as a deprecated compatibility alias
  (`0.13`), rather than silently changing its two-component historical meaning.
  Label that alias in the API contract. New clients display `daemon_version`;
  old clients still receive the same `host_version` value. Any future removal or
  semantic change of the alias requires a separate contract decision.
- Extend explicit native/HA diagnostic allowlists. Test old-client/new-daemon
  and new-client/old-daemon behavior; absent new fields remain optional. A new
  client may display legacy host version as legacy/unknown software metadata,
  but cannot derive compatibility or feature support from that display value.
- Web/HA show daemon software, installed radio firmware and compatibility target
  with distinct labels. Firmware update entities retain actual installed and
  selected candidate versions; upgrading software cannot create a firmware
  update offer by comparing firmware to the daemon package number.

## Packaging and release metadata

Split `tools/project_version.py` validation/propagation into independent software
and radio domains. Default checks validate both; explicit writes state which
domain changes. Software writes must not touch root `VERSION`, firmware headers
or catalog bytes. Radio writes retain refusal to relabel a nonempty catalog.
Validate all propagation rules before writing to avoid partial version updates.

Update standalone/HA packaging, container preparation, Docker build assertions,
release workflow version checks and installed-image probes to compare:

1. wheel metadata and `__version__` against the daemon package version;
2. root `VERSION`, the runtime compatibility constant and catalog target against
   the firmware compatibility version;
3. container/app/integration tags against their distribution versions.

Use explicitly versioned release metadata for the new interpretation. New records
must contain both daemon and firmware compatibility fields. Readers retain a
documented legacy path for old records where the two identities were equal;
only validated historical two-component values may use that fallback. Reject
ambiguous new records missing compatibility. Do not rewrite published metadata,
checksums, manifests or tags. Historical source assembly must continue to use
the selected old source and its old version rules.

Keep OCI distribution version labels, and add separate daemon-package and
firmware-compatibility labels. Replace software-version-based firmware decisions
in `host/docker/smoke.py`, container probes and workflow helper checks with
explicit compatibility/capability checks. Retain narrowly scoped historical
expectations for old upgrade baselines. Do not remove gates just to make a new
version pass. Add `livolod-commission` to installed command smoke checks.

## Migration and rollback

No configuration, database, network identity, HA entry/entity identity, firmware
catalog or radio migration is intended. Install the new monotonically increasing
daemon wheel/container normally. Preserve persistent catalogs and device/job
state. Test a software upgrade and rollback against simulated active/terminal
job state; do not migrate or replay existing request IDs. Downgrading software
requires an explicitly selected old artifact and compatible database backup,
not moving a published tag or replacing an old wheel.

Release notes must explain why daemon software no longer matches radio firmware,
the changed CLI display and the retained API compatibility alias. Update the
release-process numbering policy, native API documentation, maintenance help,
standalone/container instructions and HA diagnostics descriptions together.
Existing scripts expecting two components from `livolod --version` must move to
the explicit compatibility field; this reporting change is a documented
migration even though persistent state needs no conversion.

## Required acceptance tests

| Area | Required evidence |
| --- | --- |
| Version propagation | Package bump changes only software metadata; compatibility stays `0.13`; mismatched/missing fields fail; firmware parser rejects SemVer; old two-component package upgrades to the proposed version using real package ordering |
| TI and catalog safety | Package version differs from firmware target while exact valid `0.13` candidates still pass; wrong firmware target, key/hash/board/baud/protocol still fail; wrong trials never send UPDATE_CONFIRM; matched trials preserve all existing checks |
| Maintenance lifecycle | Manager, worker, preflight, manual/legacy CLI, restart reconciliation and terminal job behavior all use compatibility, never software identity; existing local catalogs remain valid without rewriting |
| Runtime reporting | CLI metadata equality; version-info has no side effects; API/MQTT/diagnostic fields consistent; legacy alias preserved; mixed old/new clients; Web/HA firmware offers unchanged by a software-only bump |
| Installed packages | Clean wheel and sdist installs, actual `pip` upgrade from daemon `0.13`, all console scripts including commissioning, dependency checks and simulated service operation |
| Containers and HA | Exact ARM64 image install/upgrade/persistence tests, independent version labels, HACS/manual ZIP client compatibility, no identities lost, old historical packaging/export tests still pass |
| Final candidate | Full host, HA/client, firmware/protocol, Web and packaging suites plus CI on the final committed versioned source; exact artifact inventory and hashes |

Tests must deliberately set daemon package and compatibility versions to
different values. Tests that continue to set both to `0.13` can hide a missed
coupling. Include a guard/review for remaining `__version__` imports in all
firmware decision paths. All implementation validation is offline/simulated
unless a separate hardware trial is authorized.

## Implementation ownership and scheduling boundary

When the separate implementation task is scheduled, Unix owns runtime constants,
firmware call sites, CLI/API/MQTT reporting and lifecycle tests; HA/Web own explicit display and mixed
version-client tests; ZigBee independently reviews preservation of TI, firmware
parsing and catalog invariants. Release engineering owns metadata/propagation,
packaging/container/history tests, final numbering and artifact verification.
Coordinate source ownership before edits and integrate the migration separately
from the accepted pairing feature.

The approved separation retains the API 1 `host_version` compatibility alias
and catalog schema 1. Every exact TI, signature, board, hash, baud and
trial-confirmation check must be preserved. Upgrade ordering and tests with
intentionally different software and compatibility versions are mandatory
acceptance criteria. Domain specialists may plan against this direction;
the separately authorized source implements this architecture. The daemon
development number is allocated above; coordinated distribution numbers remain
for the later release-candidate step.

Architecture approval does not waive the complete-release requirements: firmware
payload distribution, exact-board qualification, notices and signing readiness
remain separate blockers even when no firmware source change is needed.
