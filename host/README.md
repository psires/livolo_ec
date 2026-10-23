# Livolo EC daemon

`livolod` is the Raspberry Pi host service for the [UART v3 radio adapter](../firmware/gateway/UART_PROTOCOL.md).
It bridges the verified Livolo light-switch protocol to MQTT. The TI module owns
fast radio acknowledgements; the Pi owns network identity, the device registry,
pairing, command tracking and reported output state.

The development Pi is now running the service; see [deployment paths and rollback](DEPLOYMENT.md).
The deployed release includes a [cadence experiment](verification/cadence-candidate.md).
It can delay a set until the next regular heartbeat, adding up to about ten seconds
before transmission; reliable output-control acceptance is still being verified.

Implemented:

- Up to 256 configured peers; explicit one/two-output profiles and wire-order IDs.
- SQLite device registry, names, reported state and durable request deduplication.
- MQTT set commands, state, availability, discovery candidates and diagnostics.
- Home Assistant MQTT discovery: one switch entity per output, grouped by device.
- Bounded pairing windows for one named device at a time.
- Complete peer-table restoration with a fresh session after UART failure/reset.
- Renewable 30-second radio lease, 10-second heartbeats, clean shutdown/disarm.
- Simulator, systemd unit and a service API shared with the separate web UI.

This is a first implementation for the directly reachable, tested EC switch
family. Full route discovery/recovery, four-gang profiles, generic Zigbee support,
and automatic capability inference are not implemented.
One-gang configuration is supported but has not been validated against a real
one-gang device. The `0002` suffix is **not** used to infer output count. Pairing
reproduces the successful spare-switch trial; it is not a claim that every Livolo
model or factory-reset sequence is supported.

## Install on the Pi

Requires Python 3.9+, a running MQTT broker, and UART v3 firmware. From the checkout:

```sh
sudo apt-get install python3-venv
sudo useradd --system --user-group --home-dir /var/lib/livolod --no-create-home livolod
sudo usermod -aG dialout livolod
sudo python3 -m venv /opt/livolod
sudo /opt/livolod/bin/pip install ./host
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
operations are serialized. Deployed release 06 increases phase mode to six attempts
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
Pi uses experimental phase mode explicitly selected; current deployment and
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

Host and application releases use `major.minor` (currently `0.2`). The daemon
exposes `diagnostics.host_version` and `diagnostics.firmware` in `/api/state`,
including `running_version`, `target_version`, `update_available`,
`update_supported`, `boot_version` and `trial`. MQTT `bridge/info` includes
`host_version` and `firmware_version`; its existing schema version stays `1`.
Version comparisons are numeric, so `0.10` is newer than `0.9`. Old firmware
without version discovery remains usable but reports an unknown running version.

Install the matching host release with its `update` extra before updating the
MCU. After the one-time bootloader installation, routine updates use UART only:

```sh
sudo /opt/livolod/bin/livolod-firmware \
  --config /etc/livolod/config.json \
  --package /path/to/ec-gateway-0.2.ecupd \
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
