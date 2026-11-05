# Native API v1 implementation contract

This contract coordinates the daemon, Python client, HA integration and app.
API v1 is separate from UART v3 and the daemon/firmware release version.

The machine API is a separate aiohttp listener configured through an `api`
object: `enabled` (default false), `host` (default `127.0.0.1`), `port` (default
8090), and `token_env` (required when enabled). Every request, including the
WebSocket upgrade, requires `Authorization: Bearer <token>`. Never accept
credentials in URLs or send them in snapshots. Browser CSRF does not apply to
this machine API. The existing browser API remains separate.

`GET /api/v1/info` returns:

```json
{"api_version":1,"gateway":"2a992b12","tag":"a7","host_version":"0.12","simulated":false}
```

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
`progress_percent`, `request_id`, `status`, `error`, `recovery`,
`rollback_supported`, and optional `release_notes`/`release_url`. Null latest
version/candidate means no validated bundled artifact is available. Overall
progress remains null. The separate browser snapshot may expose measured transfer
progress, which does not imply installation or trial confirmation. `recovery`
may block controls even when `in_progress` is false.

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
