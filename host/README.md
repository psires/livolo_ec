# Livolo EC daemon

For the ARM64 container image and deployment steps, see the
[Docker guide](docker/README.md).

`livolod` is the Raspberry Pi host service for the [UART v3 radio adapter](../firmware/gateway/UART_PROTOCOL.md).
It bridges the verified Livolo light-switch protocol to optional MQTT and the
authenticated native API. The radio module owns fast acknowledgements; the host owns network identity, the device registry,
pairing, command tracking and reported output state.

The former development Pi is stopped after the Home Assistant handover; see
[deployment history and rollback](DEPLOYMENT.md). Current source release is 0.12.
Output control remains experimental and depends on the selected mode and radio
capabilities described below; hardware results apply to the tested switch models.

Implemented:

- Up to 256 configured peers; explicit one/two-output profiles and wire-order IDs.
- SQLite device registry, names, reported state and durable request deduplication.
- MQTT set commands, state, availability, discovery candidates and diagnostics.
- Optional MQTT and a separate authenticated native HTTP/WebSocket API v1.
- Home Assistant MQTT discovery: one switch entity per output, grouped by device.
- Bounded pairing windows for one named device at a time.
- Complete peer-table restoration with a fresh session after UART failure/reset.
- Renewable 30-second radio lease, 10-second heartbeats, clean shutdown/disarm.
- Simulator, systemd unit and a service API shared with the separate web UI.

This is a first implementation for the directly reachable, tested EC switch
family. Full route discovery/recovery, four-gang profiles, generic Zigbee support,
and automatic capability inference are not implemented.
One-gang configuration was validated on switch `66780945` on 2026-11-01:
targeted pairing, a fresh state query, and a physical ON-to-OFF report succeeded
alongside two-gang switch `46715621`. Its suffix was `0001` and full-state mask
was `01`; output count was explicitly configured, not automatically inferred.
See `analysis/development/onegang-pairing-20261101.json` and the corresponding
Nordic capture. Subsequent output-control testing found intermittent failures:
only 3/8 recorded ON requests were confirmed, versus 7/7 OFF requests; failures
persisted with the original EC gateway off. MQTT delivery worked, but reliable
one-gang output control is not yet validated. See
`analysis/development/onegang-control-20261101.json`.
The `0002` suffix is **not** used to infer output count. Pairing
reproduces the successful spare-switch trial; it is not a claim that every Livolo
model or factory-reset sequence is supported.

## Install on the Pi

Requires Python 3.9+ and UART v3 firmware. MQTT installations additionally need
a running broker and the `mqtt` dependency extra. From the checkout:

```sh
sudo apt-get install python3-venv
sudo useradd --system --user-group --home-dir /var/lib/livolod --no-create-home livolod
sudo usermod -aG dialout livolod
sudo python3 -m venv /opt/livolod
sudo /opt/livolod/bin/pip install './host[mqtt]'
sudo install -d -m 0750 -o root -g livolod /etc/livolod
sudo install -m 0640 -o root -g livolod host/config.example.json /etc/livolod/config.json
sudo install -m 0644 host/systemd/livolod.service /etc/systemd/system/livolod.service
sudo /opt/livolod/bin/livolod --config /etc/livolod/config.json --check-config
sudo systemctl daemon-reload
sudo systemctl enable --now livolod
journalctl -u livolod -f
```

Skip account creation if the account already exists. Edit the configuration before
starting. The example identifies the already-paired development switch:
gateway `2a992b12`, tag `a7`, device `46715621`, slot 0, two outputs. Do not copy
that identity to another live gateway on the same radio network.

Only one program may use `/dev/ttyAMA0`. Stop existing capture/trial processes
before starting the daemon. Stop the daemon before flashing or using the older
tools. UART/reset recovery is automatic after firmware becomes available; the
daemon does not operate GPIO or flash firmware.

The JSON file supplies network identity, UART/MQTT settings and the **initial**
device registry. On subsequent starts, SQLite is authoritative for devices and
names; editing `devices` in JSON does not overwrite UI/MQTT changes. Use add,
rename and remove commands to change the registry. Network identity/tag changes
require a separate database so old observations and command IDs cannot be
mistaken for another network. Configuration changes require a service restart.

`--check-config` validates syntax without opening UART, MQTT or SQLite. It does
not verify connectivity or existing database contents. Unknown fields, duplicate
slots/IDs and invalid masks are rejected.

For broker authentication, set `mqtt.username` and `mqtt.password_env` (for example
`LIVOLO_MQTT_PASSWORD`), then put that environment variable in
`/etc/livolod/environment`, readable only by root/the service. Optional `tls_ca`
enables TLS with certificate/hostname verification; `tls_cert` and `tls_key`
enable client certificates. Use broker ACLs to restrict access to the chosen
topic prefix. MQTT controls can change lights and admit configured peers.

The service runs without root, writes to `/var/lib/livolod`, and logs to the
journal. It keeps radio ACK handling active while MQTT is disconnected and
republishes current snapshots when the broker reconnects. Broker disconnects do
not initiate pairing or output changes.

## MQTT interface, version 1

Default prefix: `livolo`. Device topics use stable wire-order IDs, not names.
Publish commands with QoS 1 and **without retain**. Include a unique `request_id`
and reuse it if checking/retrying the same request. Without an ID the daemon
assigns one, so applications cannot rely on deduplication of repeated submissions.
Request IDs accept 1–128 letters, digits, underscores, dots, colons or hyphens.

| Topic suffix | Direction | Payload / behavior |
| --- | --- | --- |
| `46715621/set` | command | `{"request_id":"kitchen-1","state":"ON"}` or `{"request_id":"kitchen-2","mask":2,"value":0}` |
| `46715621/state` | retained event | Full observed bits, output names `1`/`2`, report counter/token, RSSI/LQI, Unix `last_seen` |
| `46715621/availability` | retained event | `online` / `offline`; also check bridge state |
| `bridge/state` | retained event | `online` only when radio table/lease/ACKs are verified; last will `offline` |
| `bridge/controls` | retained event | `offline` when output controls are disabled; otherwise `online` |
| `bridge/info` | retained event | API version, gateway/tag, readiness and current pairing window |
| `bridge/devices` | retained event | Device registry with name, ID, slot, outputs and suffix |
| `bridge/diagnostics` | retained event | MCU counters/status, host RX gaps, invalid frames and ACK failures |
| `bridge/discovery` | event | Observed unpaired registration candidate; does not admit it |
| `bridge/request/add` | command | `{"request_id":"add-1","device":"12345678","name":"Hall","outputs":2,"suffix":"0002"}`; optional `slot`, otherwise allocated |
| `bridge/request/rename` | command | `{"request_id":"name-1","device":"46715621","name":"Kitchen"}` |
| `bridge/request/remove` | command | `{"request_id":"remove-1","device":"46715621"}`; forgets host record, does not factory-reset switch |
| `bridge/request/pair` | command | `{"request_id":"pair-1","device":"46715621","seconds":120}`; 0 closes this device's window, max 300 |
| `bridge/request/snapshot` | command | `{"request_id":"snapshot-1"}` republishes retained snapshots |
| `bridge/request/refresh` | command | `{"request_id":"refresh-1","device":"46715621"}` sends observed read-only queries and waits for a fresh state report |
| `bridge/result` | event | `request_id`, status and details; subscribe before issuing requests |

State refresh makes at most three rounds of the observed `10/ac` read-only
queries, with a 224 ms host delay between queries at 460800 baud (200 ms at
other rates) and up to four seconds for a report after
each pair. It stops on the first accepted native state report received since
the request began, even if the reported bits have not changed. Cached state,
keepalives and `fa` query replies do not confirm a refresh. New queries stop
after a 14-second scheduling deadline; an in-flight UART operation retains its
existing bounded timeout and same-ID recovery rules. No new query is scheduled
after fresh state, including while waiting for the radio lock or CCA backoff.

Results include `query_rounds` (rounds started) and `queries_sent` (successful
adapter TX submissions, not device acknowledgements). Exhaustion returns
`unconfirmed`; a lost radio session or uncertain UART outcome returns `unknown`
through the existing session recovery path. Refresh uses the serial command
worker, so commands queued behind a long refresh retain their five-second queue
expiry. Heartbeats and lease renewal continue independently. No output command
or speculative state update is used to obtain a refresh.

In `experimental_phase` mode, firmware advertising `phase_queries=10_ac_v1`
(release 0.3) also stamps `10/ac` query trailers at radio-call entry. Diagnostics
report `query_mode=phase` and the latest validated `query_phase_tx` metadata.
Older adapters or other output modes retain raw queries and report
`query_mode=raw`. Phase-query failure never triggers raw fallback. A lost timing
reference waits for the normal heartbeat to restore it; no extra heartbeat is
inserted by refresh. The three-round retry schedule and response waits above
remain unchanged. Larger UART timing replies can increase measured on-air
spacing even though the host's configured delays are unchanged.

Output 1 is bit 0; output 2 is bit 1. `mask=2,value=2` turns output 2 on and
preserves output 1. `state=ON/OFF` selects all configured outputs. Physical
left/right positioning remains a device-specific mapping. There is no toggle
command or optimistic state publication: state always comes from validated
switch reports. Repeated reports update `last_seen`; they are not separate
button events.

### Home Assistant

Discovery is automatic on the configured MQTT broker, using Home Assistant's
default `homeassistant` discovery prefix and `homeassistant/status` birth topic.
Each registered output becomes an MQTT **switch** entity named `Output 1` or
`Output 2`, grouped under the device's configured name. No YAML is required.
The broker account needs publish access to the generated discovery topics
(covered by the MQTT filter `homeassistant/switch/+/config`),
plus subscribe access to `homeassistant/status` and the
normal Livolo topic permissions.

Retained configuration topics have the form
`homeassistant/switch/livolo_ec_<gateway>_<device>_<output>/config`.
Entity identities are stable across renames and topic-prefix changes. Discovery
and reported state are republished on MQTT reconnect and Home Assistant birth.
Renaming updates the device name; forgetting a device clears its retained
discovery entries, including after an offline removal. Home Assistant name
overrides remain under Home Assistant's control. Simulations never publish HA
discovery and cannot overwrite production entities.

Commands target only their own output through the existing JSON set API. They
are not retained and the state is not optimistic: HA displays native reports.
Availability requires the daemon, device and output controls all to be online.
An unavailable device can still have a cached last-known ON/OFF state.

If moving to a different broker, old discovery entries remain on the old broker
and its entities remain unavailable. Remove those old MQTT devices/config topics
there if that broker is still used. A custom HA discovery prefix/birth topic is
not configurable in this version.

The payload follows the official [MQTT switch](https://www.home-assistant.io/integrations/switch.mqtt/)
and [discovery](https://www.home-assistant.io/integrations/mqtt/#mqtt-discovery) contracts.

Example using Mosquitto clients:

```sh
mosquitto_sub -h localhost -t 'livolo/#' -v
mosquitto_pub -h localhost -q 1 -t livolo/46715621/set \
  -m '{"request_id":"test-output2-on-1","mask":2,"value":2}'
```

Results:

- `confirmed`: a matching state report was observed, or a registry change was
  saved. A registry confirmation does not mean the radio table is already restored;
  wait for `bridge/state=online` before pairing/control.
- `pairing`: the targeted window is open; a later result reports confirmation,
  timeout or cancellation. Confirmation means a native report with the configured
  network tag was observed, not a complete capability interview.
- `unconfirmed`: a send succeeded but no matching report arrived. The output may
  have changed. Do not automatically issue a new request ID.
- `busy`: every transmit attempt got an explicit CCA-busy reply.
- `unknown`: a restart/link failure interrupted the command; it is not replayed.
- `rejected`, `timeout`, `cancelled`: details are in the result's `error` field
  when applicable.

The command queue is bounded at 64, with a five-second waiting lifetime. Set
operations are serialized. Phase mode allows six attempts
([review record](verification/phase-retry06-candidate.md))
while retaining three in legacy mode. Both use roughly 200 ms waits and one
four-second deadline from the first attempt, including UART and confirmation
waits. A matching report
stops further attempts. In `experimental_legacy` mode, each set reserves the next
regular heartbeat and begins with a serialized prelude. In `experimental_phase`
mode, firmware stamps each command against its current heartbeat reference; the
host uses ordinary heartbeats without a forced prelude or ten-second phase wait.
Legacy mode's intentional phase wait is separate from the five-second queue-backlog
limit. The process-relative heartbeat
counter epoch remains unchanged. Each uses one persisted token; only
explicit CCA busy gets bounded low-level retries. UART reply loss retries the
identical request ID/bytes; unresolved outcomes discard the session. Retained
commands are rejected, and commands from an old session are never replayed into
a new one. Pending requests found after process restart become `unknown`.

Physical presses may retain old transaction tokens. Every valid state change is
published regardless of token. Confirmation requires the outstanding device,
tag, source/subject, mask, desired bits, token and a fresh report. Tokens avoid
the last observed value and are quarantined for 60 seconds before reuse. The
release-04 compatibility candidate additionally avoids adjacent tokens, tokens sharing
their upper seven bits, and the previous output command's checksum. It reserves
the token/signature atomically before transmission. This is a response to captured
ON/OFF failures, not proof of the switch's duplicate-detection algorithm; see the
[candidate record](verification/token-candidate.md). The
8-bit protocol does not provide exactly-once delivery or proof of causality.

Local phase integration requires top-level `output_mode`: `disabled` (default
when absent), `experimental_legacy`, or `experimental_phase`. The example explicitly
selects legacy mode. Phase mode requires the advertised `tx_entry_v1` capability
and a verified heartbeat reference before readiness; it never silently falls back
to raw commands. The GUI disables output buttons in disabled mode. The development
Pi used experimental phase mode explicitly selected; deployment history and
captured output checks are recorded in [DEPLOYMENT.md](DEPLOYMENT.md). See the
[phase integration review](verification/phase-integration.md) before upgrading or
rolling back configuration.

Availability requires an accepted native packet during the current daemon/radio
session, with a configurable timeout (default 180 seconds). Cached state remains
visible with its original timestamp after restart; it is not proof that the
device is currently reachable. Require both bridge and device availability in a
consumer. Removing a device clears its retained state and availability.

## Pair another switch

1. Observe `bridge/discovery` while the intended switch is in its pairing mode.
   Discovery is passive and never acknowledges admission on its own.
2. Add its ID, name, explicit output count and observed suffix. Wait for radio
   readiness after the peer table is restored.
3. Open the targeted pairing window and put that switch in pairing mode again
   if necessary. Only the named peer can trigger registration responses.
4. Watch `bridge/result` and subsequent state/availability. Timeout is not success.

Admission uses the observed delayed registration acknowledgements and `10`/`ac`
queries. Ordinary operation does not admit devices. Adding/removing a peer briefly
disarms and restores the complete table with a fresh session. In-flight control
may become `unknown`; queued controls are rejected during this transition.
Removing a host record does not make the physical switch forget its network.

## Native API and optional MQTT

The current source implements [native API v1](NATIVE_API.md) for machine clients.
This is development code; the previously deployed 0.12 image has not been rebuilt
or replaced with these changes. The browser API remains separate.

Add this object to a configuration to enable the listener:

```json
"api": {
  "enabled": true,
  "host": "127.0.0.1",
  "port": 8090,
  "token_env": "LIVOLO_API_TOKEN"
}
```

Supply a nonempty ASCII secret without whitespace in `LIVOLO_API_TOKEN` through
the service environment (or `daemon.env` for Compose). Every request, including
WebSocket upgrades and `/health`, requires `Authorization: Bearer <secret>`.
URL credentials are rejected. The native API is disabled by default; choose an
explicit bind address when clients connect from another container or machine.

Endpoints are `/api/v1/info`, `/api/v1/snapshot`, `/api/v1/events` (WebSocket),
`POST /api/v1/commands`, and `/api/v1/commands/{request_id}`. HTTP snapshots and
events share a full-snapshot envelope with `api_version`, process `epoch`, and
increasing `sequence`. Clients replace state from every snapshot and reconnect
for a fresh one; a new epoch allows the sequence to reset. State includes output
bits, availability, control policy, pairing, registry, public diagnostics and
recent results, without broker settings, raw packets or browser tokens.

Commands use the same durable service policy as MQTT and the browser UI. After an
ambiguous response, look up the original request ID before retrying. A pending
command interrupted by a process restart becomes `unknown`; retrying its ID does
not execute it again. Request bodies are limited to 4096 bytes. Event subscriptions
are limited to 32 clients with four queued snapshots each and a five-second write
deadline; a slow client is closed with code 1013. Service mutations coalesce on
the owner loop, with a one-second check for time-driven state changes. `/health`
reports API liveness even while the radio is offline.

For broker-free operation, omit `mqtt`, set it to `null`, or use
`"mqtt": {"enabled": false}`. Saved MQTT settings remain in SQLite but are not
restored or connected while disabled; the browser MQTT editor is unavailable.
Install `./host` without extras for this mode. Existing MQTT configurations remain
enabled by default. Install `./host[mqtt]` to use them; the Dockerfile retains that
extra for backwards compatibility. Simulation also works without a broker and is
always marked `simulated` in native API responses.

## GUI integration and simulation

The service exposes `api_snapshot()`, `submit(action,payload)` and
`command_result(request_id)` on its asyncio event loop. The separate UI uses
these same registry and command paths; it never opens the serial device. See
[WEB_UI.md](WEB_UI.md) for the web interface developed in the GUI chat.

The optional `web` configuration accepts `enabled`, `host`, `port` and `token_env`.
The default is disabled/loopback. `--web-host 127.0.0.1 --web-port 8080` enables
it from the command line. Non-loopback binds require token authentication.

To exercise MQTT without hardware, copy the example to a local config, change
`database` to an absolute writable path and point MQTT at a test broker:

```sh
livolod --config /absolute/path/config.json --simulate
```

Simulation never opens UART. It appends `/simulated` to the MQTT prefix,
`-simulated` to the MQTT client ID and `.simulated` to the database path. Simulated
set commands generate reports; the simulator is not an RF, mesh or pairing model.

## Firmware updates

The web UI and native API share a daemon-owned firmware update manager. Installing
a new host package or image **never flashes the radio automatically**. An explicit
install selects one validated, exact-board candidate bundled with that host
release. The shipped `livolod/firmware/manifest.json` is currently empty: no
official payload is included, so installation is unavailable. Development images
built with dummy signing keys are not release candidates.

Integrated backends cover the codm CC2652P signed updater and the Sonoff
ZBDongle-E / ZB-GW04 v1.2 application-only Gecko upload. The first qualified
bundle contract uses 460800 application baud. TI retains signed-image checking
and trial rollback; MG21 has no last-good application rollback. Nordic integrated
DFU remains disabled pending a qualified helper and USB re-enumeration access.
See the [board recovery procedures](../docs/FIRMWARE_RECOVERY.md).

Install `./host[flash]` for both integrated backends, or `./host[update]` for TI
only. Container builds use the pinned helper dependency file in `host/docker`.
The default bundle directory is immutable package data. A local operator may
set an absolute `firmware_bundle_dir` in JSON for an independently supplied,
trusted release bundle; HTTP clients cannot supply files, URLs or paths. Bundle
hashes bind the selected bytes to that manifest and do not independently prove
who published it. The same validated payload files are intended to accompany
the matching GitHub Release.

Updates reject active commands, pairing and conflicting registry/configuration
changes. The daemon disarms and closes the radio before a bounded worker takes
over; web/API listeners remain available. Progress and destructive boundaries
are recorded in SQLite before execution. The worker and its helper inherit a
radio ownership lock, preventing restart from racing an orphaned flasher.
Success requires exact board/version/baud and normal peer/session/heartbeat
restoration; TI additionally checks the selected digest before confirming trial.
Switch-query success is not an upgrade readiness requirement.

After interruption, the daemon reconciles the saved job without replaying a
transfer. A verified TI rollback releases maintenance with a rejected result;
uncertain hardware remains in `recovery_required`. Retrying an existing request
ID returns its durable result rather than running the update again.

### Emergency local CLI

`livolod-firmware-manage --config /etc/livolod/config.json status` reads the saved
job without stopping the daemon. For `install` or `recover`, first stop the actual
owner (systemd service, Docker container or HA app). Run the tool as the daemon's
user with its current configuration, database and serial device accessible:

```sh
livolod-firmware-manage --config /etc/livolod/config.json install \
  --candidate-id 'candidate-id-from-the-firmware-status'
livolod-firmware-manage --config /etc/livolod/config.json recover
```

The CLI uses the same database/radio locks and manager. It starts a temporary
radio service without MQTT/web listeners, restores the network and confirms a
successful TI trial itself, then closes/disarms. Restart the normal owner after
completion. `recover` applies only to a journaled TI update: it checks the board,
key and digests, aborts/reboots an incomplete receive, activates only the matching
committed candidate, or resumes trial confirmation. It never aborts a committed
candidate. A failed application that cannot answer UART, and interrupted MG21 or
Nordic flashes, require the board-specific bootloader procedure linked above.

### Existing signed UART command

Host and application releases use `major.minor` (currently `0.12`). The daemon
exposes `diagnostics.host_version` and `diagnostics.firmware` in `/api/state`,
including `running_version`, `target_version`, `update_available`,
`update_supported`, `boot_version` and `trial`. MQTT `bridge/info` includes
`host_version` and `firmware_version`; its existing schema version stays `1`.
Version comparisons are numeric, so `0.10` is newer than `0.9`. Old firmware
without version discovery remains usable but reports an unknown running version.

The signed UART updater applies to the codm CC2652P board with this project's
dual-slot bootloader. Other boards use their board-specific flash procedures.
Install the matching host release with its `update` extra before updating the
MCU. After the one-time bootloader installation, routine updates use UART only:

```sh
sudo /opt/livolod/bin/livolod-firmware \
  --config /etc/livolod/config.json \
  --package /path/to/ec-gateway-0.12.ecupd \
  --public-key /path/to/public.key \
  --service livolod
```

The updater verifies the signature, board, version and installed verification
key, stops the service, transfers the image, observes trial boot, restarts the
service and waits for durable confirmation. The daemon restores and checks the
radio session before confirming a matching trial image. Failure to confirm
within 60 seconds, or a reset during trial, rolls back to the previous image.
The updater requires a strictly newer application version; it does not silently
reinstall or downgrade an image. An interrupted transfer can be retried with the
same package; a committed matching package resumes activation.

`--service livolod` controls the native systemd service only. For Docker or a
Home Assistant app, stop the actual owner first, omit `--service`, and arrange
for the matching daemon to restart within the trial confirmation window. The
original standalone 0.12 Docker archive does not include updater dependencies;
new source builds include the integrated TI/MG21 backends.

No RESET/FLASH GPIO wiring is required for routine updates. Configurable GPIO
recovery and the original buttons remain available for initial installation or
bootloader recovery. Keep the signing private key off the gateway; only the
public key and signed package are needed there. Bootloader updates still require
the recovery installer. See [firmware documentation](../firmware/gateway/README.md)
for key generation, packaging, memory layout and recovery limits.

## Verification

```sh
PYTHONPATH=host python3 -m unittest discover -s host/tests -v
```

The core tests need only the standard library. Installing the package dependencies
also enables real Paho TCP and pySerial PTY integration tests; they need permission
to bind a loopback TCP socket. Tests cover captured frame bodies, CRC rejection,
state/command correlation, durable deduplication, registry reconciliation, UART
fragmentation/lost replies/resets, broker reconnect and clean shutdown.

A bounded hardware check is provided separately. It restores the configured
peers, runs heartbeats/lease renewal and optionally sends only the observed
read-only queries, then verifies disarm. It does not change outputs or pair:

```sh
PYTHONPATH=host python3 host/tools/check_hardware.py \
  --config host/config.example.json --query-device 46715621 \
  --seconds 25 --output /tmp/livolod-hardware-check.json
```

Back up the JSON configuration and SQLite database. Stop the daemon before a
plain filesystem copy, or use SQLite's online backup API; do not copy only the
main file while WAL writes are active. Command IDs are retained indefinitely for
deduplication, so database size grows with commands. Keep the state directory
private. Reported state changes are persisted immediately; unchanged reports
are persisted at most once per minute to reduce SD-card writes.

The MQTT adapter uses [Paho's callback v2/network-loop API](https://eclipse.dev/paho/files/paho.mqtt.python/html/client.html),
and UART uses [pySerial's POSIX exclusive mode](https://pyserial.readthedocs.io/en/latest/pyserial_api.html).

## Application UART baud

At 460800 the host uses a measured 224 ms query-pair delay to compensate for
shorter UART serialization. Nordic measured about 237 ms between radio query
transmissions, matching the earlier 115200 run. Other baud rates retain the
200 ms delay; the correction is not extrapolated to unmeasured rates. This
affects read-only refreshes only. Three rounds, a four-second response wait,
the 14-second deadline and stopping on fresh state are unchanged. Effective
settings are exposed in `diagnostics.query_timing`.

`serial_baudrate` in the daemon JSON defaults to 115200 and accepts 115200,
230400, 460800 or 921600. It must match the firmware's `UART_BAUD` build option.
The daemon checks an advertised `CAPS baud=` before arming and includes the
configured rate in diagnostics. Hardware-check tools and the TI application
updater use the same setting. Ordinary firmware upgrades should retain the
application rate; a rate-changing upgrade requires coordinated config migration,
including rollback. No automatic baud probing or live switching is performed.

MG21 Gecko recovery and TI ROM flashing remain at 115200. To enter MG21 recovery,
send `BOOTLOADER` at the current application rate and then select 115200 in the
Gecko flashing tool. See [the hardware comparison](DEPLOYMENT.md): the current
Sonoff development gateway uses 460800. A reproduced MCU receive-overrun fault
was corrected in the MG21 UART writer, then passed 6000 integrity requests and
a ten-minute live run without UART faults. Query spacing at 460800 now includes
the measured compensation above; RF query reliability still has unresolved
timeouts. Defaults remain 115200; other higher rates are not
hardware-qualified. The earlier ZB-GW04 USB reply truncation was not retested.


## MCU scheduler capability

Release 0.6 negotiates `scheduler=timed_v1` on the phase-capable TI adapter.
The daemon installs an absolute ten-second heartbeat schedule during session
restoration and verifies its first phase anchor. It continues renewing the
existing lease and monitors `diagnostics.heartbeat_owner` and
`diagnostics.scheduler`. UART polling supplies diagnostics; the MCU timer owns
heartbeat deadlines. A missing schedule triggers session reconciliation.

The Pi retains all authoritative network/device records, pairing and routing
policy, persistence and transaction confirmation. Query policy remains three
rounds, four CCA attempts per send, a 14-second scheduling deadline, the existing
baud-specific pair delay and a 4.000-second response wait. Fresh state still stops
retries. Current supported adapters negotiate their clock rate (65536 or 1000000
ticks per second); adapters without this capability retain host heartbeats.

`SchedulerClient` exposes explicit enqueue, result, cancel and drain operations
for the first-stage RAM queue. In 0.6 the daemon does not submit whole query/output
transactions to it. Queue acceptance, radio transmission and native confirmation
are distinct outcomes. See the [firmware contract](../firmware/gateway/UART_PROTOCOL.md#ti-timed-queues-and-heartbeat-ownership--release-06).


### Complete MCU state queries (0.7)

TI capability `query=transaction_v1 query_slots=1` moves the whole read-only
query into one retained volatile transaction. The daemon supplies the exact peer,
suffix, output mask and timing profile, then polls/drains the result; polling
never drives RF retries. The MCU retains the confirming native frame even if
bulk UART logs are dropped. The Pi independently validates and persists it,
without allowing a delayed older RX event to replace newer state.

Three rounds, four CCA attempts per opcode and the 14-second deadline are
unchanged. The MCU uses measured TI wire targets (235.504 ms between 10/ac,
4.011993 s after ac), replacing the old host sleeps plus UART overhead. Diagnostics
expose `query_mode=mcu`, `query_execution` and `query_transaction`; adapters without
this capability retain the existing host query path. No persistent network state
moves onto the radio. See the [UART transaction contract](../firmware/gateway/UART_PROTOCOL.md#complete-ti-state-query-transactions--release-07).

### MCU output and query grid (0.12)

With `output_grid=transaction_200ms_v1`, phase-mode output commands use a bounded
MCU transaction on heartbeat-relative 200 ms boundaries: at most six attempts
within four seconds. The host persists the token before submission, validates
the retained peer report against the requested bits and prior state, and drains
the completed slot. Acceptance alone never publishes a confirmed output state.

The scheduler client also supports explicit `ac_only` and `grid` options for
read-only query experiments; grid queries require AC-only mode and the matching
adapter capabilities. These are client/harness options, not daemon JSON settings.
The daemon uses ordinary paired queries. The 14-second query deadline and
authoritative host registry remain unchanged.
