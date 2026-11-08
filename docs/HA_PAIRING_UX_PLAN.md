# Home Assistant pairing UX and provisioning plan

Planning baseline: **2026-11-08**. The owner selected the **native HA page**:
automatic pairing through Configure and the existing Pair new device button,
manual pairing behind Development, and native Add hub retained. The layout
decision is closed; no custom panel, subentry migration or header modification
is in scope. After acceptance of the bounded
[XIAO steadiness milestone](XIAO_BOARD_SUPPORT_PLAN.md), the lead authorized
source-only Phase 1 implementation on 2026-11-08. The behavior and regressions
are implemented and accepted by Release Engineering source review; this is not
a release or deployment record. Discovery and bootstrap remain design-only.
Existing HA and radios remain unchanged; XIAOs remain experimental and excluded
from gateway provisioning and production firmware eligibility.

## Phase 1: automatic pairing as the normal path

Add an explicit, per-hub **Development** setting, default **off**, preserving
unrelated options. It is separate from HA's per-user Advanced mode.

Normal Configure exposes **Pair new device**, session status/cancel, settings
and ordinary management/firmware checks. Move these three existing options into
Development: manual registration (`add`), reviewed experimental detection
(`discover`, including its review/fallback steps), and targeted pairing (`pair`).
Guard direct and resumed entry to those steps as well as hiding their menu items.
Recheck the current option before submission; turning Development off must not
strand status or cancellation of an already active session. A daemon
without automatic commissioning shows an upgrade/unavailable explanation;
it does not silently restore manual pairing to the normal flow.

The guard must cover asynchronous gaps: in `async_step_detected`, recheck the
latest persisted Development option after awaiting `_discoveries()` and
immediately before `client.command("add", ...)`. A guard only at step entry is
insufficient. The shared `_action` path must likewise guard manual `add` and
`pair` immediately before mutation; ordinary management and automatic session
status/cancel remain outside this Development gate.
Recheck before redisplaying manual error forms after an awaited failure too.
If a command was already submitted, closing the disabled flow retains its
reported outcome and explains that it was neither cancelled nor retried.

Reuse the existing commissioning manager and gateway buttons: one bounded
120-second session, durable request identity, exact-session cancellation and
no automatic POST replay after a lost reply or HA reload. An active or uncertain
session opens its status instead of starting another. Registration alone is not
successful physical pairing. Preserve network, device/entity IDs, names, areas
and ordinary switch controls.

## Native placement: decision and constraints

The original requested layout was **Pair new device** at the top right, with
**Add hub** in a settings dropdown once a verified managed Livolo service is available or a
hub exists. With neither, Add hub would be the primary setup action. The owner
accepted supported native placement instead: retain Configure → Pair new device,
the gateway pairing button, and HA's Add hub placement in this milestone.

The feasibility baseline is HA Core **2026.11.0**, which pins frontend
**20261028.4**. Its native integration page owns the header and dropdown. It
supports a static `config.initiate_flow.user` label for the config-flow button
and additional buttons for genuine config-subentry types. Its protocol
Add device action uses a frontend allowlist. There is no supported custom
integration hook there for arbitrary menu items or relocating Add hub based on
daemon state. `single_config_entry` changes supported hub cardinality; it must
not be used to fake conditional placement in this multi-hub integration.
See the pinned [Core manifest](https://github.com/home-assistant/core/blob/2026.11.0/homeassistant/components/frontend/manifest.json)
and [integration-page source](https://github.com/home-assistant/frontend/blob/20261028.4/src/panels/config/integrations/ha-config-integration-page.ts).

Exact placement on the native page would require upstream HA frontend work;
a custom panel could implement it on a separate page. Neither is selected.
Do not inject into HA's DOM or merely rename Add hub to Pair new device while
it still opens hub setup. Real device subentries could add a native pairing
button alongside Add hub, but require a separate migration/reconciliation design
for Web-created devices and deletion; do not invent session subentries for layout.
Supported extension references: [options flows](https://contributors.home-assistant.io/core/integrations/options-flow/),
[subentries](https://contributors.home-assistant.io/core/integrations/subentries/)
and [custom panels](https://contributors.home-assistant.io/frontend/custom-ui/panel/).

Web's current Devices header already has Pair new device and Advanced setup,
but no Development section; targeted pairing is inside device details. Reuse
its commissioning semantics without adding a new frontend. The daemon Web
interface owns one gateway and cannot create HA hubs: HA must own that routing and multi-hub
selection. Ingress alone cannot alter HA's native header.

## Phase 2: design discovery of an available Livolo service

“Docker available” means an **authenticated, verified Livolo managed service**,
not an installed container engine. Do not inspect a Docker socket or assume
HA Container implies a daemon exists. The current app does not publish discovery;
`async_step_hassio` only supplies an endpoint hint. New app-specific discovery
needs authenticated `info` and `snapshot` agreeing on gateway/tag, compatible
API capabilities, duplicate-network checks and explicit simulation handling.
Retain manual remote-daemon setup. Credentials must stay server-side; any new
credential handoff needs its own contract, not secrets in browser links.
See [Supervisor communication](https://contributors.home-assistant.io/apps/guides/communication/).

| Observed state | Setup/pairing behavior |
| --- | --- |
| No hub; managed service absent or unknown | Offer Add hub/manual connection; do not guess Docker or a hostname. |
| Discovery hint only | Offer verification/connection; never treat the hint as trusted identity. |
| Known app stopped, unreachable or authentication failed | Explain the condition and offer setup/recovery; pairing unavailable. |
| Verified managed daemon, no HA hub | Offer connection before pairing; do not start RF commissioning without a selected configured hub. |
| At least one configured hub | Pair against the selected hub; additional hubs remain available through setup. |
| Authenticated daemon, radio not ready or maintenance active | Keep status/recovery visible; pairing unavailable. |
| Selected hub ready and commissioning supported | Enable automatic pairing, subject to current-session guards. |

These are discovery/setup states, not instructions to modify the native header.
HA retains its own layout. Distinguish service availability from radio readiness. The current
app requires serial path, gateway, tag and token at startup, so discovery does
not itself provide zero-configuration bootstrap support.

## Separate experiment: initial radio provisioning

Existing managed upgrades already use HA's standard firmware update entity:
fresh candidate preflight, one durable install request, daemon-owned maintenance
and worker, measured progress, and verified restoration before success.
Preserve that mechanism and its API (`request_id`, `candidate_id`); do not add
arbitrary firmware paths, helper commands or serial overrides to it.

Converting a stock radio is a different operation: the existing manager expects
an observed Livolo application identity. Design a separate explicit provisioning
state machine rather than relaxing those admission checks. Start with a **fake
MG21/ZB-GW04 profile, transport and worker only**; this selects an offline fixture,
not a qualified hardware target. No automatic flashing on attachment.

HA remains the UI/client. Unix owns the daemon/app bootstrap service design,
with a persistent authenticated service-instance identity and a durable journal
available before a gateway config entry exists. Keep that identity separate
from gateway/tag, USB identity and release version. Advertise an explicit
experimental provisioning capability with no executable live backend initially.
The fake backend must be structurally unable to open a real port or invoke a
flasher; a UI toggle alone is not an isolation boundary.
Reconcile instance replacement rather than trusting stale discovery.
Reuse immutable exact-board artifacts, hashes/provenance, bounded worker
lifetime, idempotent request IDs, recoverable phases and final board/version/
protocol verification. Keep image transfer distinct from verified success.

Physical ownership requires additional design: the existing `radio_guard` is
database-derived, and pyserial exclusive opens do not prove global exclusion
against ZHA, Zigbee2MQTT or another daemon across bootloader re-enumeration.
Specify the exact selected device/profile, ownership conflict handling,
application-to-loader identity continuity, disconnect behavior and recovery
before any physical trial. A proposed physical lease must span port closure and
re-enumeration and be shared by Livolo workers using different databases.
Cross-app isolation on HA OS remains an unresolved deployment contract, not a
guarantee supplied by that lease. Block unknown/conflicting ownership; never stop
other integrations automatically. Bind reviewed application/loader transitions
to exact identity/topology and reject duplicate, substituted or reused nodes;
use bounded waits, not generic first-match scans or DTR/RTS reset guesses.
USB bridge IDs alone do not identify an exact board.
Protect loader/calibration regions and require a reviewed recovery procedure.

The TI bootstrap inspector is structure-only, not permission to flash a loader.
Nordic manual recovery remains a separate CLI workflow. Backend source presence
does not establish a qualified artifact. A later real-board experiment requires
separate scope and hardware evidence; XIAOs are excluded. See
[firmware qualification](FIRMWARE_QUALIFICATION_PLAN.md) and
[current HA firmware behavior](../homeassistant/README.md#radio-firmware-updates).

## Phase 1 implementation boundary

HA's owned files are:

- `custom_components/livolo_ec/const.py`: Development option key.
- `custom_components/livolo_ec/config_flow.py`: settings form, menu grouping and
  shared current-option guard for manual entry, review and submission paths.
- `custom_components/livolo_ec/strings.json` and `translations/en.json`: matching
  setting/menu text and disabled-development explanation.
- `homeassistant/tests/test_development_options.py` (new): real options-flow
  default/persistence, direct/resumed-step guards, and ordinary-action access.
- `homeassistant/tests/test_detection.py`: explicitly enable Development in
  fixtures while retaining all evidence/token/fallback regression coverage.
- `homeassistant/tests/test_commissioning.py`: update normal-menu expectations
  and prove automatic pairing/status/cancel remain usable with Development off.
- `homeassistant/tests/test_integration.py` and `test_end_to_end.py`: explicitly
  opt existing manual-path fixtures into Development and traverse its submenu;
  the lead approved these two additional test-only paths.
- `homeassistant/README.md` and this plan: user instructions and implementation
  status, without claiming deployment.

No commissioning-manager, button, switch, daemon, frontend, manifest-version or
firmware behavior change is included in Phase 1. If tests reveal another
necessary file, coordinate that ownership before expanding scope. Discovery and
bootstrap implementation remain separate work packages; this plan is not their
API specification. No live HA verification is included in this source phase.

## Acceptance and ownership

- **Pairing:** Development off hides and guards all three paths; on exposes
  them; settings persist without changing unrelated options. Cover unsupported
  daemons, the correct hub, concurrent starts, cancellation, lost replies,
  HA reload and confirmed-only success without duplicate registration.
  Suspend `_discoveries()` during reviewed registration, disable Development
  through a separate options flow, then resume the lookup and assert no add
  command is submitted. Also cover already-open manual add/pair forms submitted
  after disabling Development, and continued automatic status/cancel access.
- **Discovery/layout:** cover zero/one/multiple hubs against absent, hinted,
  stopped, unauthenticated, available and radio-unready services; wrong-network
  and duplicate discovery; HA OS and HA Container. Test the chosen UI on the
  pinned frontend, keyboard and narrow/mobile layouts using a disposable HA
  instance. Native header behavior remains unchanged. No implied Docker access.
- **Provisioning:** fake transports must prove detection/preflight perform no
  writes; wrong board/hash/profile, owner conflict and ambiguous USB handoff
  block mutation. Include two databases racing for one device, unresolved
  noncooperating ownership, service-instance replacement, stale/duplicate USB
  identity, unplug at every phase and attempted protected-region writes.
  Worker interruption, observer loss and reload must retain
  job identity without reflashing. Only final verification establishes success;
  recovery remains visible and existing network/entity identity survives.
- **Isolation:** automated tests must not open real serial devices, invoke real
  flash helpers or contact installed HA. The previous 236 passing HA tests are
  baseline evidence; Phase 1 validation is recorded below. Discovery and
  provisioning acceptance criteria remain unimplemented.

Run focused options/detection/commissioning tests, then the full isolated HA
suite once Phase 1 is implemented. Validate translation JSON/parity, Black,
function documentation and the owned diff. Record final implementation results
separately from the planning baseline. A later provisioning suite must inject failures before/after each
durable mutation checkpoint and prove that an orphan worker retains ownership
until exit. A restart after possible mutation produces unknown/recovery-required,
never an automatic repeated erase or flash.

Phase 1 source validation on **2026-11-08**: **69 focused** options/detection/
commissioning tests and **272 full-suite** isolated HA tests passed. The suite
includes disabling Development during successful and failed discovery reads,
and failed in-flight manual and reviewed registration commands without replay.
Black checks on seven owned Python files, function-documentation checks,
English translation JSON parity/placeholders, relative documentation links and
owned whitespace checks passed. Tests use HA 2026.10.4 in the existing isolated
Python 3.14 environment; frontend feasibility was separately checked against
HA 2026.11.0/frontend 20261028.4. No installed-HA or visual UI validation was
performed, and these results establish no release, deployment or RF behavior.

HA owns flows and HA regression tests; Unix owns discovery,
bootstrap/physical ownership and job contracts; Web supplies existing session
semantics without a panel change; ZigBee owns later board/recovery qualification.
The lead gates implementation after bounded XIAO steadiness. Release Engineering owns review,
versioning and publication; this plan changes no release or installed version.
