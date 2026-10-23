# Livolo web UI

The UI runs inside `livolod`. It uses the daemon's SQLite device registry and
command queue, never opens the serial port, and requires no Node.js, JavaScript
build, CDN, or additional Python dependency. Assets are included in the Python
package.

## Try it without hardware

From the repository root, with Python 3.9 or newer:

```sh
PYTHONPATH=host python3 -m livolod.web_demo
```

Open <http://127.0.0.1:8080>. This disposable demonstration uses simulated
switches and a temporary database. It never connects to MQTT or opens the UART;
all changes disappear when it exits. It includes a discovered candidate and
an offline device. Pairing is confirmed by simulated reports, so this is a UI
exercise, not a test of real device admission. Stop with Ctrl+C. Use `--port 8081`
if port 8080 is occupied.

## Enable on the daemon

Follow [daemon installation](README.md), then enable the web server in the JSON
configuration:

```json
"web": {"enabled": true, "host": "127.0.0.1", "port": 8080}
```

Alternatively pass `--web-port 8080` to `livolod --config /etc/livolod/config.json`.
The default is disabled, and the default bind address when enabled is loopback.
Output control separately requires an explicit daemon `output_mode`; absent or
`disabled` leaves observation and registry management available while disabling
the output buttons. The development Pi uses `experimental_phase` with the
compatible phase-capable firmware. See [daemon configuration](README.md) for the
capability checks and experimental legacy option.

To use a Pi's loopback server from another computer, forward its port:

```sh
ssh -L 8080:127.0.0.1:8080 sfires@10.1.1.21
```

Then open <http://127.0.0.1:8080> on that computer. Do not run a second daemon
against a UART already owned by the installed service.

For direct access on a trusted LAN, configure an access token through the
service's environment and enable a non-loopback bind:

```json
"web": {
  "enabled": true,
  "host": "0.0.0.0",
  "port": 8080,
  "token_env": "LIVOLO_WEB_TOKEN"
}
```

Set `LIVOLO_WEB_TOKEN` to a long random ASCII value in the daemon environment,
then open the Pi's IP address and enter that value in the UI. Non-loopback binding
without a token is rejected. The browser keeps the token in memory only; a page
reload requires signing in again. The built-in server uses HTTP; use the SSH
forwarding approach on untrusted networks. It is a small local management server,
not an Internet-facing service. Host validation accepts the configured bind name,
machine hostname, or actual local socket IP. Arbitrary reverse-proxy Host/Origin
rewriting is not supported.

## Device workflows

- **Add:** select an observed registration candidate or enter its 8-character
  wire-order device ID. Give it a name and select one or two outputs; the daemon
  allocates the peer slot. Advanced settings expose its 4-character suffix.
- **Pair:** “Save & start pairing” saves the device, waits for the daemon to
  reconcile its radio table, then opens a two-minute window for that one ID.
  Existing devices can be paired through **Manage → Pair device**. Follow the
  model's documented physical pairing procedure. Opening the host window does
  not put the switch into pairing mode. A received report confirms pairing;
  timeout, cancellation and unknown outcomes are shown separately in Activity.
- **Migrate:** remove only the target switch from its old gateway, keep the old
  gateway's pairing disabled, and use the documented switch pairing procedure.
  No unverified button hold duration or factory-reset sequence is assumed.
- **Name:** **Manage → Save name** persists the alias in the daemon registry.
  Renaming works while the radio is offline and does not change the device ID.
- **Control:** each output button sends a masked command. The UI waits for the
  daemon's confirmed result and displays received state; it does not optimistically
  label a transmitted command as successful. Offline controls are disabled.
- **Forget:** the confirmation explicitly removes the host record and saved
  state. It does **not** send a radio unpair or factory-reset command. The switch
  may retain its association and need its documented pairing/reset procedure
  before joining another gateway. The daemon rebuilds the volatile peer table.

Search matches name or ID. Availability filters, last received state, signal,
and activity update every two seconds. Disconnection leaves old reports visible
with a warning and disables commands. Network shows radio/MQTT connectivity and
diagnostics. The registry in SQLite is authoritative after the initial config
seed; editing the original JSON device list does not overwrite UI changes.

## HTTP contract

All service accesses execute on the daemon's asyncio loop; HTTP worker threads
never access SQLite or radio state directly.

| Request | Result |
| --- | --- |
| `GET /api/state` | `Service.api_snapshot()` plus a per-process `csrf_token` |
| `POST /api/command` | `Service.submit(action, payload)` result |
| `GET /api/commands/{request_id}` | Persisted result from `command_result()` |

POST bodies are JSON `{ "action": "rename", "payload": { "device": "46715621", "name": "Kitchen", "request_id": "unique-id" } }`.
Supported actions are `add`, `rename`, `remove`, `pair`, and `set`, following the
[daemon API](README.md). The browser sends a unique ID per user action and polls
pending commands without automatically resubmitting them.

Requests require `X-Livolo-Token: <csrf_token>`, `Content-Type: application/json`,
and a maximum 4096-byte body. When configured, every API route also requires
`Authorization: Bearer <web-token>`. Cross-origin writes and unrecognized Hosts
are rejected. Responses are not cached, no CORS access is granted, and a CSP
restricts scripts and connections to the same origin. Device text is escaped
before insertion into the page.

## Validation

```sh
PYTHONPATH=host python3 -m unittest discover -s host/tests -v
```

`test_web.py` uses a real local HTTP listener and the actual daemon with a
simulated radio. It covers assets and path allowlisting, auth and CSRF/Origin/Host
checks, malformed/oversized input, offline naming, invalid configuration,
request deduplication, output confirmation, add/pair/cancel/forget, and registry
reload. Tests need permission to bind loopback sockets. Optional MQTT/serial
integration tests require the daemon's normal dependencies.

For a bounded real-hardware check through the running HTTP server:

```sh
python3 host/tools/check_web_hardware.py --device 46715621 \
  --output /tmp/livolo-http-check.json
```

This requires an online device and an output-state report no more than 30 seconds
old. It reasserts the observed value of output 1, then requires a fresh report
matching the request token, mask and preserved state. It never opens the UART or
starts pairing. Evidence is saved even when confirmation fails; choose a new
output filename for each run. Device availability alone does not satisfy the
fresh-state requirement. An unconfirmed result does not prove that the physical
output stayed unchanged.

The development Pi installation and SSH access are recorded in
[DEPLOYMENT.md](DEPLOYMENT.md). Hardware trial evidence is retained under
`host/verification/`; simulated tests do not establish hardware acceptance.

## MQTT settings

Open **Network → MQTT settings** to set the broker hostname/IP, port, topic prefix,
username and password. Select **Replace password** to enter a new password, or
**Clear password** for anonymous access (also clear the username). **Keep existing
password** preserves either a saved password or the configured `password_env`.
Save reconnects only MQTT; the radio session and device pairing are unaffected.
The connection badge reports the actual connection; saving unreachable settings
shows a disconnected/error state and retries in the background. The HTTP UI stays
available so the settings can be corrected.

`POST /api/settings/mqtt` accepts `host`, `port`, `base_topic`, `username`, and
`password_action` (`keep`, `replace`, `clear`); only `replace` accepts `password`.
It uses the same authorization, Host/Origin checks, CSRF token and JSON size limit
as other writes. `/api/state` includes a redacted `mqtt` summary with `password_set`;
passwords never enter the command ledger, MQTT snapshots, or HTTP responses.

Changes are saved in the gateway's private SQLite state database (mode 0600),
including the password when supplied. They override the MQTT block in the startup
JSON after restart. Treat database backups as containing credentials. Existing
client ID and TLS certificate settings are preserved; TLS setup remains in the
startup configuration. To return to JSON configuration, stop the daemon and
remove only the `mqtt_settings` row from SQLite's `meta` table, then restart.
The GUI does not edit the root-owned startup file. Simulation disables MQTT
editing and adds its namespace suffix after loading saved settings.
