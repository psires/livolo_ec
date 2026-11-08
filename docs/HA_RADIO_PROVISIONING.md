# Isolated HA radio provisioning contract

Prototype baseline: 2026-11-08. This models guided installation for exactly
`zb-gw04-1v2`. It cannot operate a radio: there is no serial implementation,
subprocess, hardware backend, HTTP listener, CLI, app registration or production
`Service`/`NativeAPI` connection. The fake worker exists only under `host/tests`.
Neither its success nor its artifact inspection qualifies a physical installation.
The existing routine-update API, catalog gates and newer-version checks are unchanged.

The owner selected the connected ZB-GW04 for later coordinated physical work.
That authorization does not turn this model into a usable installed service.
ZigBee owns exact-board/loader/recovery evidence, Release Engineering owns the
candidate runtime and backups, and the lead gates the live plan. The physical
target has a generic CH340 transport without a unique USB serial; a future
profile must bind reviewed physical topology and attachment identity rather
than require a nonexistent serial or infer board identity from USB bridge IDs.

## Direct-call interface

`host/livolod/provisioning.py` exports `ProvisioningModel`. Its constructor takes
a dedicated `ProvisioningStore`, a private ASCII credential, a server-owned
modeled target, and `{candidate_id, data}` with immutable in-memory GBL bytes.
The existing exact-board application inspector validates those bytes, including
compiled version/board, tags and permitted flash ranges. The model does not
load a public catalog or declare the synthetic artifact qualified.

Call `model.request(token, action, payload)`. Every response has
`schema: 1`, persistent `instance_id`, `simulated: true`, and `ok`.
Successful responses contain `data`; failures contain a stable `error` code.
The credential is compared in constant time and is never persisted or echoed.
There is no URL or network authentication transport yet. The instance ID is
stored independently of gateway/tag, USB identity, firmware version and token.

| Action | Exact payload | Result data |
| --- | --- | --- |
| `info` | `{}` | `operation: provision`, profile, `physical_install_supported: false`, `model_install_supported: true`, `network_ready: false` |
| `targets` | `{}` | Sole modeled target/evidence and candidate ID |
| `preflight` | `target_id`, `candidate_id` | Review plan, artifact SHA-256, target/candidate/profile/version, instance, expiry and required confirmation |
| `start` | `request_id`, `plan_id`, `instance_id`, `confirmed: true` | Newly admitted or previously recorded job; never starts a worker |
| `job` | `request_id` | Existing durable job, without submitting work |

Handles are bounded opaque strings, not paths or URLs. Extra keys, commands,
serial overrides and helper/download locations are rejected. A plan lasts 120
seconds, enforced using a monotonic clock; `expires_at` is UTC seconds for display.
The in-memory plan binds every reviewed target field and the candidate's actual
digest. Expiry, changed evidence/bytes/candidate, or a different instance rejects
new admission. Restart discards unsubmitted plans. At most 32 plans are retained.

Confirmation must be the boolean `true`, not a truthy value. The same request
and plan return the original durable job even after plan expiry or restart.
Reusing a request ID with different content fails. A consumed plan cannot admit
another request ID. Unresolved work blocks new plans/jobs. The journal retains
up to 1024 requests without evicting old idempotency records; a full journal
rejects new admission. No retry invents another request ID automatically.

Target facts distinguish operator-confirmed board evidence from transport
identity, attachment generation, uniqueness, ownership, mode and supported-loader
evidence. These are **modeled facts supplied by tests**, not live assertions.
Unknown/conflicting owner, ambiguous/absent target, unconfirmed board or
unsupported loader block admission. A future physical implementation must obtain
independent evidence for each; setting these booleans is not a hardware check.
Already-running Livolo applications are directed to managed updates at preflight.

## Durable phases and outcomes

The direct client may review, confirm and observe. Only the trusted test worker
calls `before_transfer`, `progress`, `transferred`, `verify` or `fail`; none are
facade actions or hardware callbacks. The modeled phases are:

1. `awaiting_bootloader`: confirmed job recorded, no transfer performed.
2. `transferring`: fresh modeled target continuity/ownership and unchanged
   artifact checked; possible-write boundary committed before the fake worker runs.
3. `verifying_application`: transfer ended, still pending.
4. `application_verified`: exact modeled board/version/protocol 3/460800 baud,
   disarmed state and ACK-off status match, with target continuity retained.

| Status | Meaning |
| --- | --- |
| `pending` | Admitted model job, not verified |
| `confirmed` | Simulated application verification only |
| `rejected` | Worker failure before any possible transfer |
| `unknown` | Interrupted work or failure after possible transfer; recovery required |

Every job contains instance/request/plan/target/candidate IDs, artifact digest,
profile/version, stage/status, transfer percentage, error/recovery codes and
`application_verified`. It always retains `network_ready: false` and
`setup_required: true`. Transfer 100% is not confirmation. There is no estimated
overall percentage. The final presenter text must say **firmware verified;
normal daemon setup required**; it must not create a hub or enable pairing.

Pending jobs reopen as `unknown`, stage `interrupted`, error `service_restarted`,
recovery `recovery_required`, including interruption before a transfer checkpoint.
No worker is replayed. Failed postflight also retains recovery. The prototype
has no clear-recovery, resume, reset, rollback or physical recovery operation.
Storage errors propagate to the caller; they never grant the worker permission
to continue. A lost response is reconciled through `job` or the same request,
not interpreted as success or permission for a second flash.

Important codes include `authentication_failed`, `invalid_request`,
`confirmation_required`, `instance_changed`, `selection_changed`, `plan_unknown`,
`plan_expired`, `plan_changed`, `plan_used`, `request_conflict`, `job_unresolved`,
`unsupported_profile`, `board_evidence_required`, `ownership_unresolved`,
`loader_unverified`, `target_ambiguous`, `target_absent`, `target_changed`,
`wrong_target_mode`, `artifact_invalid`, `artifact_changed`,
`application_mismatch`, `invalid_transition`, and `invalid_progress`.

## Storage and isolation

`provisioning_store.py` uses an independent private SQLite journal and a
single-owner local file lock. It refuses an existing gateway database and stores
no API token, artifact bytes or executable paths. The application ID distinguishes
the model journal from unrelated state. Commits use SQLite synchronous FULL.
The directory is a trusted local test-owned directory, not remotely supplied.

This lock is **not a physical-device lease**. The model does not establish
exclusion against another database, ZHA, Zigbee2MQTT, another app, or a surviving
hardware worker. Cross-process hardware leases, app namespace restrictions,
loader re-enumeration and orphan-worker lifetime remain real-backend requirements.
The test worker cannot outlive the process and supplies no such evidence.

`host/tests/provisioning_fixture.py` provides `ProvisioningFixture(root)` for the
HA test presenter. It uses synthetic nondeployable GBL bytes and a deterministic
clock. `call`, `plan`, `submission`, `transfer`, `finish`, `restart` and `close`
exercise the same facade/journal. Production code must not import this fixture.

Run the focused suite from the prepared virtual environment described in
[Contributing](../CONTRIBUTING.md):

```sh
PYTHONPATH=host:host/tests python \
  -m unittest test_provisioning -v
```

The fixture imports existing GBL test helpers, which also import `cryptography`.
Use the project's test dependencies, not an unprepared system Python. The suite
covers explicit confirmation/authentication, idempotency, consumed/expired/stale
plans, changed instances/targets/artifacts, owner/loader failures, protected flash
ranges, journal errors, restart at each pending phase, mismatched postflight,
transfer-versus-success and live-database refusal. Serial opens, enumeration and
subprocess creation are forbidden during the complete fake lifecycle test.

## Next physical implementation gate

The proposed first real scope is an exact ZB-GW04 already placed in its preserved
Gecko bootloader, with a reviewed application-only GBL and no generic reset
profile. Existing Livolo-to-Gecko entry does not prove stock-firmware entry.
Same-version installation must use a separate explicit provisioning operation,
never bypass managed-update admission. Current historical loader evidence must
be freshly verified; PD4 is transport readiness, not a boot pin.

The real controller needs a physically bound target/profile, an enforceable
ownership handoff, a durable physical-mode journal that rejects model jobs,
bounded worker supervision, actual loader/app verification, and a reviewed
recovery path. Bootstrap mode must prevent the normal daemon reconnect/restore
loop from opening the port. Its authenticated pre-network API must not fabricate
a network identity. Only explicit post-verification handoff may start normal
daemon restoration using preserved configuration; HA then verifies normal
`info`/`snapshot` identity before creating or resuming a hub.

The helper's return code alone does not prove image or factory-state preservation.
No mass erase, loader/SE/token writes, stock-configuration backup claim, public
catalog promotion, release, deployment or physical operation is part of this
prototype. See [the UX plan](HA_PAIRING_UX_PLAN.md),
[firmware qualification](FIRMWARE_QUALIFICATION_PLAN.md), and
[firmware recovery](FIRMWARE_RECOVERY.md).
