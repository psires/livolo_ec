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
