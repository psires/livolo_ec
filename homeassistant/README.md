# Native Livolo EC integration (development preview)

The native integration runs without MQTT. It connects to `livolod` over an
authenticated local API, creates one HA device per configured physical switch,
and one switch entity per output. HA Core never opens the radio serial port.
The independent daemon continues running during HA reloads/restarts.

This preview implements native control and commissioning, broker-free daemon
startup, push updates, ingress support, and local HA app packaging. Radio
firmware updates use a standard HA firmware entity backed by the daemon's durable
maintenance jobs. Initial conversion from stock adapter firmware remains a
separate board-specific operation. Network backup/import UI, automatic app
installation, socket profiles and curtain profiles are not implemented here.
Do not publish this preview as a fully qualified plug-and-play release.

The [radio readiness matrix](../analysis/development/ha-radio-support.md) records
the exact supported/experimental profiles. Commercial Zigbee adapters require
our **dedicated IEEE 802.15.4 firmware**, not stock coordinator firmware. The
official Nordic PCA10059 now also has a qualified gateway build; support remains
specific to the boards and tests recorded in the matrix. A radio running Livolo
firmware cannot simultaneously serve ZHA, Zigbee2MQTT or Thread.

## Install the native integration

Build an archive from the repository root:

```sh
python3 homeassistant/build_release.py
```

Extract `dist/livolo-ec-ha-0.2.0.zip` into the HA configuration directory so
`custom_components/livolo_ec/manifest.json` is beneath it, then restart HA.
Use this assembled archive: the source component alone does not include its
independent client. The builder bundles `client/livolo_ec_client` as `_client`
without relying on an unpublished package or installing the daemon in HA Core.
The standalone client can also be installed with `pip install ./client`.
The client package, assembled integration and app build context include the
project's BSD-2-Clause license. The app image also retains it under
`/usr/share/licenses/livolo-ec/LICENSE`.

This is a manual custom integration, not yet a HACS repository or an HA Core
integration. Do not copy just `custom_components/livolo_ec` from source.

## Connect to a daemon on an existing Pi/container

Use the **new source build** of the daemon: the previously delivered 0.12 Docker
archive predates the native API and will not gain it merely by changing config.
Reuse its Dockerfile, persistence and device mapping, rebuilding under a distinct
development image tag so the known working release remains available.

For a standalone installation, install the current host package. MQTT is an
optional dependency: `pip install './host[mqtt]'` retains broker support;
`pip install ./host` provides the native-only service.

Keep existing network identity, baud, registry and output mode. Add:

```json
"mqtt": {"enabled": false},
"api": {
  "enabled": true,
  "host": "0.0.0.0",
  "port": 8090,
  "token_env": "LIVOLO_API_TOKEN"
}
```

Set `LIVOLO_API_TOKEN` to a randomly generated secret in the daemon environment.
For example, `python3 -c 'import secrets; print(secrets.token_urlsafe(32))'`
generates one. The standalone listener requires a token even on loopback.
Restrict access to the intended host/private network. Use TLS termination if
crossing a network where the bearer token must not travel in cleartext.

In HA, choose **Settings → Devices & services → Add integration → Livolo EC**.
Enter the daemon base URL (for example `http://<pi-address>:8090`) and token.
Leave simulation disabled for hardware. The flow verifies API version and
network identity, rejects duplicate entries and supports reconfiguration and
credential repair without silently replacing the logical network.

## Run the service as a local HA OS app

The local app wrapper reuses the daemon Dockerfile and packages the current source
with the ingress/bootstrap additions. Only `aarch64` is advertised. HA Container
users should use the companion daemon container above; it has no managed app store.

Stage a self-contained local app build context:

```sh
python3 homeassistant/build_release.py --app --output dist/native-preview
```

Copy `dist/native-preview/ha-addons/livolo_ec` into your HA local app/add-on
directory, refresh the local app store, and install **Livolo EC**. Re-running
the command with the same app output directory intentionally fails rather than
overwriting a previously staged source bundle; use a fresh output directory.
The integration ZIP is generated in the same output directory.

Configure these app options before starting:

| Option | Meaning |
| --- | --- |
| `serial_port` | Exact supported radio, preferably `/dev/serial/by-id/...`. |
| `serial_baudrate` | Installed application baud, e.g. 460800 for the current Sonoff build. |
| `gateway`, `tag` | Existing EC network identity when migrating; otherwise an explicitly chosen unique network identity/tag. Never copy the development network. |
| `api_token` | At least 32 non-whitespace ASCII characters; use a generated secret. Use the same token in the integration. |
| `ha_origin` | Exact browser origin including scheme and nondefault port, e.g. `http://homeassistant.local:8123` or `https://ha.example`. No trailing slash/path. |
| `output_mode` | `experimental_phase` for supported current firmware, or `disabled` for observation. |

The app stores SQLite in `/data/state.sqlite3` and runs the daemon as UID 10001
after preparing directory and serial permissions. Browser ingress listens on
8099 and only trusts the Supervisor proxy. The authenticated machine API listens
on 8090 separately. Leave its host-port mapping unpublished: HA Core can use the
app's internal hostname, normally `http://local-livolo-ec:8090` for this local app.
Confirm the actual hostname in Supervisor/app information instead of guessing
it for repository-installed apps. `localhost` inside HA Core is not the app.

Use **Open Web UI** for the ingress management interface, and add the native
integration using that internal API URL. Manual configuration is the supported
preview path; the config flow can accept Supervisor discovery hints, but this
app version does not publish them automatically or install the integration.

The app exposes serial devices through Supervisor's `uart` mapping; only the
selected port is opened. Only one process may own it. If a USB device is absent
at boot or returns with different permissions/device mapping, a restart may be
needed; actual HA OS hotplug behavior still requires hardware qualification.
There is no radio-readiness watchdog that repeatedly restarts an otherwise
healthy daemon when the adapter is unplugged.

## Radio firmware updates

Integration and client 0.2.0 add a **Firmware** update entity to the existing
Livolo EC gateway device. It reports the running version and the daemon's selected
compatible candidate. Use HA's standard update dialog or the `update.install`
action. The daemon also exposes the same job through its web interface; neither
HA Core nor the browser opens the serial device or selects a filesystem path.

The daemon must have a validated firmware bundle for the exact board and baud,
with the required installation backend available. Without a candidate, the entity
retains the installed version and blocking reason but has an unknown update state
and no install capability. Older daemons without firmware-update metadata keep
their switches working and expose the firmware entity as unavailable.

The initial backend scope is signed TI UART updates and the exact supported
Sonoff/ZB-GW04 MG21 profiles. Nordic installation remains disabled with a recovery
reason. Firmware delivery and backend prerequisites are described in the
[daemon documentation](../host/README.md#firmware-updates); installing the
integration does not download a radio image. No public firmware release or live
flash qualification is implied by these
source changes.

An install remains pending until the daemon verifies the resulting board,
application baud and firmware version, including the expected TI image hash.
During maintenance, switch controls are unavailable while the API and firmware
job remain readable. Job phase, request ID, failure and recovery guidance come
from the daemon. Percentage progress is shown only when a genuine overall
measurement is supplied; transfer progress alone does not imply completion.

The client submits once and uses the same durable request ID to resolve lost
responses. A local observation timeout or HA reload does not cancel or restart
the firmware job. Check its reported status and recovery instructions before
retrying an uncertain outcome. Jobs started through the web interface remain
visible after HA reload. Arbitrary version selection and HA's optional backup
flag are not advertised; firmware rollback is board-specific.

The ARM64 app was built and installed on a Raspberry Pi 5 running HA OS 18.4
and Core 2026.10.4 on 2026-11-01. The ZB-GW04 gateway became ready, both switches
and all three native outputs loaded, and integration reload preserved the running
daemon. Initial distant-placement tests included unconfirmed commands. After
moving both switches close to HA, all **12 control commands confirmed** (six
through HA and six through the web API), both read-only refreshes confirmed,
and all outputs returned to their starting states. Firmware and settings were
unchanged; this is bounded evidence for these two devices, not a long-term
reliability guarantee. See the
[public verification summary](verification/native-preview-20261101.json).
Ingress HTTP and
access checks passed, and the user confirmed the page opens inside HA. USB hotplug
still needs qualification. Integration 0.1.1 fixes duplicate entity registration
during unload; its regression test and subsequent live reload passed.
The public verification summary omits private host addresses, device identities,
command history and backup locations from the local deployment records.
Preserve a working image and consistent database backup before trying it on hardware.

## Device management and state behavior

The integration's **Configure** dialog supports:

- Register a device using its wire ID, name, output count and verified suffix.
- Detect a switch experimentally from its observed pairing signature, review
  its output count, and register it with a friendly name.
- Open a targeted pairing window, or close it with a zero-second duration.
- Request a bounded read-only state refresh.
- Forget a host record after confirmation. This is not a physical factory reset.

For detection, choose **Configure → Detect a switch (experimental)** and put
only the intended new switch into physical pairing mode, with pairing on the
original EC gateway disabled. Submit the form without selecting a device to
refresh the list, then select its observed ID. HA displays the daemon's detected
output count and suffix for review; it does not infer the count from state bits.
Submit a name to register that reviewed profile. The daemon rechecks its token
against fresh evidence from the current radio session. Changed, expired or
conflicting evidence requires reselection. Unknown devices and older daemons
without profile metadata retain **Use manual setup instead** and the original
manual registration menu. No switch is selected or registered automatically.

Detection currently recognizes only the tested one- and two-gang pairing
signatures; see [experimental detection limits](../docs/DEVICE_PROFILES.md).
It does not qualify sockets, curtains or other switch variants. Once registered,
wait for radio readiness and reopen **Configure → Open or close pairing** with
the selected device ID. Registration alone does not complete physical pairing.

Register before pairing. Manual profile fields remain explicit; suffix alone is
not an inferred gang count. Pairing acceptance opens the window, and the management UI shows
later confirmation/expiry. No generic automatic admission is enabled.

New registry devices appear without reloading HA. Forgotten channels become
unavailable; remove their HA devices after forgetting them in the daemon.
HA-local friendly-name and area overrides remain under HA control. Existing HA
names are not automatically synchronized back to daemon names.

Only validated device reports change displayed switch state. A successful API
submission is not physical confirmation. Commands address only their output's
bit, preserving other channels. Unconfirmed/unknown outcomes appear as service
errors and retain the request ID for investigation. Lost replies are resolved
through the same durable ID, never an automatic new command. State is unknown
until observed; stale transport/radio/device availability makes controls unavailable.
No per-entity RF polling or invented button-press events are used.

The newest output-grid results improve control reliability, but query timeouts,
fresh commissioning and route recovery remain subject to the firmware evidence.
HA software tests cannot establish RF reliability.

## Move an existing installation

Stop/disarm the old owner before starting the new one. Preserve gateway/tag,
devices, suffixes, tokens and the command ledger by copying a consistent SQLite
backup plus configuration. Use SQLite backup facilities or stop/checkpoint before
copying; copying only a live WAL database file is not a complete backup. The HA
app expects `/data/state.sqlite3`; its startup seed is empty and does not replace
an imported database. Keep a private backup because existing SQLite can contain
MQTT credentials and history. A changed gateway/tag is rejected by the store.

Existing MQTT entities do not automatically merge with native ones. Record entity
IDs and automation references, stop old discovery publications, clear the old
retained discovery configurations deliberately, then reuse entity IDs where
appropriate. Device-based automation references may need manual repair. Do not
run a second daemon against the same radio/network during migration.

## Development validation

Tests target Home Assistant 2026.10.4 with Python 3.14.2 or newer:

```sh
python3.14 -m venv .local/ha-tests
.local/ha-tests/bin/pip install -r homeassistant/requirements-test.txt
.local/ha-tests/bin/python -m pytest -c homeassistant/pytest.ini homeassistant/tests -q
```

These tests assemble the actual distribution and use real HA classes. They cover
configuration, identities, per-output commands, dynamic devices, unavailable and
unknown states, unload, HTTP/WebSocket auth, lost command replies, and the full
simulator → native API → HA path. The suite opens loopback sockets only.
The earlier 0.1.1 source validation on 2026-11-01 passed **28 tests**, including
the reload regression, firmware diagnostic allowlist and license/client packaging checks.
The isolated native API and ingress suites also passed **14** and **10** tests,
respectively. The official HA 2026.10.4 **hassfest** validator passed with zero
invalid integrations. See the
[public verification summary](verification/native-preview-20261101.json).
That record predates the 0.2.0 firmware update implementation. Firmware tests
add standard update-service behavior, job deduplication after lost responses,
terminal failures, maintenance visibility and restoration of externally started
jobs after reload. All update tests use mocked jobs; no live HA actions or radio
flashes are part of the source checks. CI runs
hassfest and packages the integration/local app artifacts; no remote CI run is
claimed by these local results.

The 0.2.0 source checks pass **44 HA/client/packaging tests**, **16 native API
tests**, Black 24.8.0, function documentation checks and HA 2026.10.4 hassfest.
The [firmware-update verification](verification/firmware-update-20261101.json)
records the scope: the source bundle has no firmware artifacts, and this change
has not been deployed or used to flash a physical radio.

The [native API contract](../host/NATIVE_API.md) and
[architecture plan](../analysis/development/home-assistant-native-integration.md)
record the API boundaries and plans for backup/import and additional device profiles.
