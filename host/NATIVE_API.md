# Native API v1 implementation contract

This contract coordinates the daemon, Python client, HA integration and app.
API v1 and UART v3 are independent of daemon software and radio firmware versions.

The machine API is a separate aiohttp listener configured through an `api`
object: `enabled` (default false), `host` (default `127.0.0.1`), `port` (default
8090), and `token_env` (required when enabled). Every request, including the
WebSocket upgrade, requires `Authorization: Bearer <token>`. Never accept
credentials in URLs or send them in snapshots. Browser CSRF does not apply to
this machine API. The existing browser API remains separate.

`GET /api/v1/info` returns:

```json
{"api_version":1,"uart_protocol":3,"gateway":"2a992b12","tag":"a7","daemon_version":"0.14.0","firmware_compat_version":"0.13","host_version":"0.13","simulated":false}
```

`daemon_version` identifies the installed Python package using numeric
`major.minor.patch`. `firmware_compat_version` is the exact two-component radio
target admitted by this daemon. API 1's `host_version` is retained as a deprecated
**firmware compatibility alias**, not a software version. Its value remains
`0.13` when daemon software is `0.14.0`. `api_version` and `uart_protocol` explicitly
describe their wire revisions; clients must never infer them from either version.

The same five fields are present in snapshot `data.diagnostics`, even while the
radio is offline. They are additive and optional for clients connecting to older
daemons. Missing `daemon_version` means unknown/legacy software identity; do not
relabel `host_version` as a new software version or use it to infer feature support.
Firmware update `installed_version` and `latest_version` still describe actual
radio firmware and a validated artifact. A software-only upgrade cannot create a
firmware offer by comparing the radio version with the package version. Existing
schema-1 firmware catalogs likewise retain `host_version` as their exact radio
compatibility target; no software fields are added to catalog manifests.

`GET /api/v1/snapshot` and the first message on `GET /api/v1/events` (WebSocket)
return this envelope:

```json
{"type":"snapshot","api_version":1,"epoch":"opaque-process-id","sequence":0,"data":{"gateway":"2a992b12","tag":"a7","ready":true,"controls_enabled":true,"simulated":false,"diagnostics":{},"pairing":null,"devices":[{"id":"46715621","name":"Kitchen","slot":0,"outputs":2,"suffix":"0002","availability":"online","state":{"bits":1,"outputs":{"1":"ON","2":"OFF"}}}],"discoveries":[],"results":[]}}
```

Subsequent messages use the same full-snapshot envelope with increasing
sequence numbers in the same epoch. A full snapshot is authoritative: missing
devices are removed, missing state is unknown. Full snapshots simplify v1
resynchronization; sequence gaps do not require event replay. Reconnection
always starts with a fresh snapshot, and a new epoch permits sequence reset.
Generate initial snapshot and subscription registration on the owner event loop
without yielding between them. Coalesce changes per loop iteration, use bounded
per-client queues, and close slow clients instead of blocking the radio loop.
Heartbeat-only protocol frames need not each cause an HA event. State,
availability, registry, readiness, pairing and result changes must be delivered.
Snapshots omit MQTT configuration, credentials, raw packets and browser tokens.

### Experimental device-profile detection

Each discovery may include an additive `profile` object:

```json
{"experimental":true,"status":"detected","outputs":1,"suffix":"0001","reason":"known_pairing_signature","expires_at":1793894000.0,"token":"opaque-review-token"}
```

`status` is `detected`, `unknown`, `ambiguous` or `stale`. Count, suffix and token
are null when not detected. `expires_at` is a Unix timestamp for display;
the daemon checks freshness using monotonic receive time and the current radio
session. Older daemons may omit `profile`; clients must preserve manual setup.
The token binds a reviewed profile, not an authentication privilege.

Use the existing `POST /api/v1/commands` envelope for an explicit automatic add:

```json
{"action":"add","payload":{"request_id":"add-auto-1","device":"66780945","name":"Hall","auto_detect":true,"profile_token":"opaque-review-token"}}
```

Optional `slot` retains its existing meaning. Do not include `outputs` or
`suffix` in an automatic request. The server requires the matching reviewed
token and fresh, unambiguous evidence no older than 60 seconds from the current
ready radio session. It validates every qualifying pairing packet, including
contradictions inside the discovery publication throttle. Counter changes alone
do not change the profile. Conflicts persist throughout a continuous observation
cohort; a quiet gap longer than 60 seconds starts a new cohort and token.

Automatic detection matches the captured pairing suffix **and** payload, not
state bits or generic properties. It does not transmit, pair, update an existing
registry profile or make output changes. Pairing remains a separate targeted
action after the radio restores the added registry. Omit `auto_detect` (or set
it to false) for the unchanged manual `outputs` and optional `suffix` contract;
`profile_token` is rejected on that manual path.

### Firmware maintenance

Newer daemons add `data.firmware_update` to HTTP/WebSocket snapshots. Older
daemons may omit it; clients must tolerate absent optional fields. The object
contains `board`, `installed_version`, `latest_version`, `candidate_id`,
`update_available`, `install_supported`, `blocked_reason`, `in_progress`, `stage`,
`progress_percent`, optional `transfer_percent`, `request_id`, `status`, `error`, `recovery`,
`rollback_supported`, and optional `release_notes`/`release_url`. Null latest
version/candidate means no validated bundled artifact is available. Overall
progress remains null. `transfer_percent` is measured transfer progress (a finite
number from 0 through 100, otherwise null), exposed identically to native and
browser clients. It does not imply installation or trial confirmation. `recovery`
may block controls even when `in_progress` is false.

Catalog availability is refreshed in the background and pushed to subscribers
without a Web request or radio reconnect. Active job identity remains pinned
while a new catalog is advertised after the job finishes. See the
[persistent catalog guide](../docs/FIRMWARE_CATALOG.md).

`GET /api/v1/firmware/preflight` returns a read-only schema-1 report. It accepts
at most one optional `candidate_id` query value; all other or duplicate query
parameters are rejected. Bearer authentication remains required. The browser
equivalent is `GET /api/firmware/preflight` with its normal browser/ingress access
checks. Older daemons may return 404 for these optional endpoints.
Current snapshots advertise `firmware_update.preflight_supported: true`.
Clients should require that capability for the new preflight-driven workflow;
older daemons can retain ordinary device control without firmware installation.

The response contains `schema`, `operation` (`update`), `candidate_id`, `board`,
`installed_version`, `target_version`, `compatible` (true/false/null),
`executable`, `identity_source`, `checks`, `blocked_reason`,
`requires_manual_action`, and `rollback_supported`. Each check has a stable `id`,
`status` (`pass`, `fail`, `unknown`) and `code`. Unknown live identity is not a
successful compatibility check. The endpoint revalidates local catalog bytes
and helper availability against the currently owned session's identity facts;
it sends no UART commands, creates no job and writes no SQLite state. Offline or
busy sessions cannot become executable through this route. This is advisory:
installation still rechecks identity and bytes before mutation. No report acts
as permission to skip those checks. Raw paths, payloads, keys, serial identities
and helper output are excluded.
When the artifact is missing or invalid, verified live `board` and
`installed_version` remain available while candidate/target fields can be null.

Local operator `inspect`/`flash` commands are separate from these HTTP routes.
The APIs do not accept local-artifact paths, helper executables, USB target
overrides or manual Nordic recovery requests.

`POST /api/v1/firmware/install` uses the same bearer authentication and accepts
exactly:

```json
{"request_id":"firmware-1","candidate_id":"qualified-artifact:full-sha256-from-status"}
```

Request IDs match `[A-Za-z0-9_.:-]{1,128}`; candidate IDs match
`[A-Za-z0-9_.:-]{1,256}` and are opaque hash-bound selections. No paths, uploads,
URLs, caller-selected versions or arbitrary commands are accepted. The daemon
validates eligibility, exclusive ownership and the actual artifact again.
The response uses the existing durable result envelope with
`action="firmware_update"`, `candidate_id`, `request_id`, `status`, and available
`stage`, `error`, `recovery`. HTTP acceptance is not firmware success. Inspect
`GET /api/v1/commands/{request_id}` and snapshots for completion. Reuse the same
request ID after an uncertain response; never create a new automatic flash retry.

`confirmed` requires restored exact-board firmware and normal network readiness;
`rejected` may describe a verified return to the previous image; `unknown` with
recovery required needs the documented maintenance procedure. The APIs stay
available while the radio is paused. No candidate, unsupported backend, active
radio work or uncertain recovery state prevents installation. Generic MQTT
commands do not gain a firmware-install action.

`POST /api/v1/commands` accepts:

```json
{"action":"set","payload":{"request_id":"uuid","device":"46715621","mask":1,"value":1}}
```

Actions: `set`, `refresh`, `add`, `rename`, `remove`, `pair`. Delegate validation
and deduplication to `Service.submit`; do not implement a second command policy.
Return its JSON result, HTTP 200, even for a service-level rejection. Invalid
envelopes are HTTP 400, oversized payloads HTTP 413, authentication failure 401.
`GET /api/v1/commands/{request_id}` returns the durable result or HTTP 404.
For pairing, `pairing` is acceptance of the open window, not final confirmation.
For set/refresh, `pending` is not a final outcome. Clients poll the SAME request
ID after ambiguous transport errors; never automatically create a new request.

`GET /health` is authenticated and reports API/process liveness independently
of radio readiness. Radio absence must not force an app restart loop.

MQTT may be omitted, null, or `{"enabled":false}`. Existing MQTT configuration
continues to work. Disabled MQTT must not be restored from SQLite automatically.
The native API must start before/independently of radio readiness. Simulation
is explicitly identified and HA rejects connecting to it unless the config
entry was intentionally created with simulation enabled for testing.

HA stable config/device identity uses `gateway:tag`, plus wire device ID and
channel for entities. A replaced USB adapter with the same logical network
retains those identities. HA owns friendly-name overrides and areas; the daemon
owns the actual network registry. Each output command uses only its selected bit.

Transport implementation ownership: Unix Specialist owns `host/livolod` daemon
policy/API/config/startup plus its tests and dependency metadata, except the
browser `web.py` and `static/` files owned by Web Specialist. Web Specialist
owns ingress configuration validation in a separate helper and will coordinate
any necessary config hook with Unix Specialist. HA Specialist owns
`custom_components/`, `client/`, `homeassistant/`, and this contract.

## Bounded automatic commissioning

Snapshots advertise `commissioning_supported: true` and `commissioning`, which
is null before the first session. Legacy daemons omit these fields; clients must
not infer support from their own version. Submit through the existing command
endpoint:

```json
{"action":"commission","payload":{"request_id":"pair-1","seconds":120}}
```

`seconds` is an integer from 10 through 120, default 120. No device ID or profile
override is accepted. The start request ID is the session ID. Acceptance returns
`status: "listening"`, not success. Duplicate requests retrieve the durable result
without extending the window. Web equivalents are CSRF-protected
`POST /api/commission` and `/api/commission_cancel` with the payload alone.

The snapshot session contains `session_id`, `status`, `seconds_remaining`,
`device`, `name`, `outputs`, `suffix`, `profile`, `registered`, `error`, and
`observations`. Device/profile fields are null until a supported candidate is
selected. `observations` contains at most eight fresh device observations with
`device`, `outputs`, `suffix`, `profile` and `reason`; unknown signatures explain
unsupported devices without registering them. Names default to `Livolo <ID>`;
existing rename and advanced targeted pairing remain available after the session.

Active statuses are `listening`, `registering`, `pairing`. Terminal statuses are
`confirmed`, `expired`, `cancelled`, `conflict`, `failed`, `unknown`. The latest
session persists across reloads/restarts; a process restart converts an active
session to `unknown` and does not resume it. The full original command result is
available through `GET /api/v1/commands/{request_id}` and includes `session_id`,
`device`, `registered`, and `error`. Snapshot metadata remains authoritative.

Only supported unregistered native announcements received after explicit start
in the owned radio session are eligible. A one-second collection period rejects
multiple eligible identities or conflicting signatures for the same identity.
Cached discoveries and retained query reports cannot enroll devices. One session
can add one device. Registration allocates a free radio-supported slot, preserves
existing identities/state and briefly reconciles the disarmed peer table. The
session survives only this intentional reconciliation; unexpected radio loss
ends it as `unknown`. The total deadline includes setup and never extends.

`registered: true` only means the registry was persisted. `confirmed` requires a
checksum-valid native state report from the selected ID and profile, addressed
to this gateway with its tag and valid output mask, after successful admission
transmission in the current session. A foreign-gateway/broadcast report, generic
UART acceptance, CCA non-send, or registry entry cannot establish success.

```json
{"action":"commission_cancel","payload":{"request_id":"close-1","session_id":"pair-1"}}
```

Cancellation requires a distinct request ID and targets exactly one session.
Its command result `confirmed` means the window is closed, not that the device
paired. Already terminal sessions retain their actual outcome. Cancelling an
unseen session ID records a tombstone so a delayed start cannot reopen it; it
never cancels a different current session. Cancellation/expiry stops remaining
scheduled admissions but cannot undo RF already transmitted. A registered but
unconfirmed device is retained for explicit advanced pairing retry; no physical
unpairing or restoration of an old gateway is claimed.

After a lost start reply, read the snapshot and original command ID; never replay
with a new ID automatically. A missing command is not success. Explicitly cancel
the uncertain session before offering another start. Firmware installation,
targeted pairing and registry/control commands are blocked while commissioning
owns the radio policy; snapshot and targeted cancellation remain available.
