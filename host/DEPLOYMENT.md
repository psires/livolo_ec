# Development Pi deployment — 2026-10-18

**Current release: `0.2`**, with host, application firmware and resident
bootloader all reporting `0.2`. The daemon detects running/target firmware versions,
reports whether an update is available, and confirms a matching trial after
restoring the radio session. UART protocol remains version 3. Release numbering
now follows `major.minor`; dated identifiers below are historical deployments.

The signed UART updater completed a managed service stop, transfer, activation,
restart and confirmation. A subsequent UART software reset retained the confirmed
image. An intentionally unconfirmed trial rolled back from `0.2` to `0.1` in
about 62 seconds after trial startup. Resetting an incomplete transfer after
288 bytes also preserved the working image. These routine update/recovery tests
used no GPIO operations and sent no switch output commands. Initial bootloader
installation used the existing GPIO/ROM recovery route.

Radio and MQTT are ready, with zero systemd automatic restarts. Device registry,
saved MQTT settings and startup configuration were verified unchanged against
the pre-deployment backups. The daemon now reports `update_available=false`.
All 87 host tests, 35 tool tests, 18 gateway protocol tests and 7 update-core tests
passed. Hardware validation caught and corrected missing TI VIMS cache/line-buffer
invalidation around flash operations before the final successful tests.

Host source: `/opt/livolod-releases/0.2/host`.
Wheel SHA-256: `90fd8e60734fca6aa1af402defcf3ff32bb4d18a60021d59830366bed2a4202c`.
Application package SHA-256: `4faee78653439b80e81b6e26c183f62f2e7b467b1aa569dd1e21234c45426203`.
Database backup: `/var/lib/livolod/backups/before-update-0.2-20261018.sqlite3`.
Configuration backup: `/etc/livolod/config.before-update-0.2-20261018.json`.
Original MCU backup: `/home/sfires/livolo-ec/backups/before-uart-update-0.2-20261018.bin`.

[Deployment evidence](verification/deployment-uart-update-0.2.json),
[timeout rollback](../analysis/development/uart-update-rollback-0.2.json),
[interrupted transfer](../analysis/development/uart-update-interruption-0.2.json),
[confirmed reboot](../analysis/development/uart-update-confirmed-reset-0.2.json), and
[build identity](../analysis/development/uart-update-0.2-build.json).
See [update operations](README.md#firmware-updates) for future releases.

Host rollback: stop `livolod`, reinstall the retained `20261017-08` wheel, then
restart, keeping the database/configuration. This does not downgrade the MCU.
Normal application updates only accept a newer signed version. To restore the
pre-updater MCU image, stop the daemon, use the configurable GPIO/ROM recovery
tools and the full backup, verify flash CRC, then restart the daemon. Bootloader
replacement is deliberately outside routine application updates.

These checks qualify the update path on this development module. Physical
power-cut testing, other board variants and long-term RF reliability remain
separate validation work.

## Release 08 history

**Previous release: `20261017-08`** adds Home Assistant MQTT discovery. Both
outputs of **Development switch** are advertised as separate MQTT switch
entities under one device. Retained configuration and state were independently
received from the configured broker at `10.1.1.40:1883`, prefix `livoloec`.
Radio and MQTT are ready, with no service restarts or startup errors.

All 78 host tests passed, including real TCP discovery, exact HA command payloads
against the simulator, channel isolation, retained-command rejection, reconnect,
HA birth, renaming and removal. No physical output commands were sent during
deployment or verification; the HA frontend itself was not inspected.

Source: `/opt/livolod-releases/20261017-08/host`.
Wheel SHA-256: `12f49b3ac45d353a8f9afed2ee6fea94acce8fd1fc0b64dfcc5bede630273960`.
Source archive SHA-256: `2639f864bdbed5d5e6314baff0475b1750915e4bff09dcb7b8b68a87923f9698`.
Installed runtime/static source hashes were verified. Startup configuration,
SQLite registry and saved MQTT settings were preserved; firmware was not changed.
Database backup: `/var/lib/livolod/backups/before-ha08-20261017.sqlite3`.
Configuration backup: `/etc/livolod/config.before-ha08-20261017.json`.
[Deployment evidence](verification/deployment-ha08-2026-10-17.json).

Rollback: stop `livolod`, install the retained release-07 wheel with
`/opt/livolod/bin/pip install --no-deps --force-reinstall`, then start the service.
Keep the existing database/configuration. Retained HA discovery entries remain
on the broker after rollback, and their existing command/state topics still work
with release 07; remove those retained config topics explicitly if withdrawing
HA integration. See [HA behavior and permissions](README.md#home-assistant).

## Release 07 history

**Previous release: `20261017-07`** adds MQTT configuration under **Network →
MQTT settings** (server, port, topic prefix, username, keep/replace/clear password).
Settings persist in private SQLite metadata and reconnect MQTT without resetting
the radio. Startup configuration, registry and firmware were preserved during
deployment. All installed source hashes were verified; radio and MQTT are ready.
The live form successfully saved the existing settings unchanged. All 74 host
tests passed, including real TCP credentials/topic replacement, HTTP security,
password redaction, persistence and simulation namespace isolation.

Source: `/opt/livolod-releases/20261017-07/host`.
Wheel SHA-256: `7f380bd58684b2a2ada25800803b1562154008ef57b565ca930ffba907173ef6`.
Source archive SHA-256: `a5d9a2ec88e094d3779ac6bdafda133c9565c69c51f889bf84080bfb5a426a17`.
Database backup: `/var/lib/livolod/backups/before-mqtt07-20261017.sqlite3`.
Configuration backup: `/etc/livolod/config.before-mqtt07-20261017.json`.
[Deployment evidence](verification/deployment-mqtt07-2026-10-17.json).

Rollback: stop livolod, install the retained release-06 wheel using
`/opt/livolod/bin/pip install --no-deps --force-reinstall`, then start livolod.
Preserve the database; release 06 ignores the new MQTT metadata and uses the
startup JSON instead. After any UI settings change, account for this difference
before rollback. Password-bearing databases and backups must remain private.
See [MQTT settings documentation](WEB_UI.md#mqtt-settings) for storage precedence
and returning to startup JSON configuration.

## Release 06 history

Latest control verification on release 06: both HTTP requests received matching
native ON/OFF reports. A user-confirmed physical press occurred between them;
the final native report and API snapshot show both outputs OFF, counter 50,
token `0c`. Radio and MQTT are ready. This is not an uninterrupted automatic
transition or long-term reliability test. See [web acceptance](verification/WEB_ACCEPTANCE.md)
for the complete evidence and remaining limits.

**Previous release: `20261017-06`**, six attempts in experimental phase mode.
The sole runtime change from release 05 is the phase-attempt limit from three to
six. Legacy mode stays at three; the four-second deadline, token/body, MCU timing
and confirmation rules are unchanged. All 69 tests and joint reviews passed.

One controlled host restart preserved configuration bytes, database/registry and
MCU firmware. Every installed source/UI hash was verified, including service hash
`45c386a2d19c234877e248d55963cddf72ea029f3ac5f73ecf9d7e59c57a7e27`.
Radio/MQTT and a fresh phase reference are ready. No output or pairing commands
were sent by deployment. Saved state remains bits 0, counter 45, token 0d at
1792268345.980656; immediate post-restart availability was offline awaiting native
RX, so this was not a new physical confirmation. Captured acceptance is separate.

Source: `/opt/livolod-releases/20261017-06/host`.
Wheel: `/opt/livolod-releases/20261017-06/wheels/livolo_ec_daemon-0.1.0-py3-none-any.whl`.
Wheel SHA-256: `0e2424def78acabf1a5c4ec49818b4bbe408ef4463125a910b2c1ec8f247a6f3`.
Source archive SHA-256: `84b4f612bf6474b1c6a1b35c0258de6de57bf5c6a4e1ec49caf78c46fdbb6d07`.
Database backup: `/var/lib/livolod/backups/before-phase06-20261017.sqlite3`.
Configuration backup: `/etc/livolod/config.before-phase06-20261017.json`.
[Live verification](verification/deployment-phase06-2026-10-17.json) and
[candidate review](verification/phase-retry06-candidate.md).

Rollback to release 05: stop livolod, install the retained release-05 wheel with
`/opt/livolod/bin/pip install --no-deps --force-reinstall`, then start livolod.
Keep the same configuration and database; neither schema nor configuration changed.

## Release 05 history

Release `20261017-05` introduced explicit experimental phase mode.
All 67 tests and joint GUI/firmware reviews passed before deployment. One
controlled stop/install/start installed the exact reviewed code and UI, preserved
the database/registry, and set `output_mode=experimental_phase`. Capability
negotiation and an initial heartbeat reference passed before readiness; radio and
MQTT are ready. No output or pairing commands were sent by the deployment.

The saved baseline remains bits 0, counter 44, token 0b at timestamp
1792266728.6699536. The immediate post-restart availability was offline while
awaiting native traffic; the saved state is not a new physical confirmation.
The GUI/firmware chats coordinate captured output acceptance separately.

The first release-05 phase-enabled trial was **partially successful**: ON token
`08` (request `web-live-ff53451272304608871050133e4f706d`) was unconfirmed.
OFF token `0d` (request `web-live-a040e5980cd04f16b6758266b0604094`) received a
matching native report at Unix timestamp 1792268345.980656: bits 0, counter 45.
Both outputs are freshly reported OFF, but a successful ON/OFF cycle has not been
established. Completed air analysis validates all six command trailers within
132–593 microseconds of RF heartbeat-relative timing and finds no delayed ON
report. OFF confirmed 663 ms after the first attempt. A local six-phase-attempt
[candidate](verification/phase-retry06-candidate.md) is under review; release 05
remains installed and no further cycles have been sent.

Source: `/opt/livolod-releases/20261017-05/host`.
Wheel: `/opt/livolod-releases/20261017-05/wheels/livolo_ec_daemon-0.1.0-py3-none-any.whl`.
Wheel SHA-256: `2e002e53d71924e3019a2fa0e613a0ceb0fb504de135548e1bc66618a60dc461`.
Source archive SHA-256: `7ca95f17d0449c33850735be1eb49d5169b88a7b26052180643b05e47718eb9f`.
Database backup: `/var/lib/livolod/backups/before-phase05-20261017.sqlite3`.
Configuration backup: `/etc/livolod/config.before-phase05-20261017.json`.
[Live verification](verification/deployment-phase05-2026-10-17.json) and
[configuration/runtime review](verification/phase-integration.md).

The MCU remains on the probe-validated candidate
`c15d9b87cdec11761526cbac674eff3732b26b755251b23318a9ed315fe2a69f`; it was not
reflashed during this host update. UART remains exclusively owned by livolod.

To roll back to release 04:

```sh
sudo systemctl stop livolod
sudo cp -p /etc/livolod/config.before-phase05-20261017.json /etc/livolod/config.json
sudo /opt/livolod/bin/pip install --no-deps --force-reinstall /opt/livolod-releases/20261017-04/wheels/livolo_ec_daemon-0.1.0-py3-none-any.whl
sudo systemctl start livolod
```

Keep the current database. Restoring the configuration is required because release
04 rejects the new `output_mode` field. The older host remains compatible with the
installed MCU's unchanged raw SEND interface.

## Release 04 history

Release `20261017-04` was the reviewed token compatibility candidate.
It was restored unchanged after the non-actuating phase-firmware measurement.
The MCU now runs candidate image SHA-256
`c15d9b87cdec11761526cbac674eff3732b26b755251b23318a9ed315fe2a69f`, ROM-verified
by the firmware owner. Host phase controls remain disabled; radio and MQTT
readiness and the exact release-04 source hashes were reverified. See
[probe/handoff record](verification/phase-probe-plan.md) and
[restoration snapshot](verification/restored-after-phase-probe01-2026-10-17.json).

All 44 tests passed before deployment. After the user's physical confirmation
that both outputs were OFF, the native baseline was bits 0, counter 43, token 06.
One controlled stop/install/start preserved the database and association. Exact
installed source hashes, radio readiness, MQTT connection and the unchanged
baseline timestamp were verified. No output or pairing commands were sent by the
deployment procedure. Captured control acceptance is coordinated separately.

The first release-04 HTTP trial was **partially successful**: ON token `09`
(request `web-live-a54a63ca9ced454887b467efa871a1c8`) was unconfirmed. OFF token
`0b` (request `web-live-c336efc6c2a14d6e9595be22882b3916`) received a matching
native report at Unix timestamp 1792266728.6699536: bits 0, counter 44. Both
outputs are freshly reported OFF; this does not establish that the preceding ON
was applied or that control is reliable. Live Nordic analysis confirms all three
ON transmissions (checksum `ca`) without a matching ON report, and OFF checksum
`cb` followed by the matching report and a 1.879 ms gateway ACK. The first ON/OFF
attempts followed their heartbeats by 24.450/73.430 ms; heartbeat spacing was
10.030236 seconds. The allocation guard alone is insufficient for reliable ON.

Source: `/opt/livolod-releases/20261017-04/host`.
Wheel: `/opt/livolod-releases/20261017-04/wheels/livolo_ec_daemon-0.1.0-py3-none-any.whl`.
Wheel SHA-256: `7e23253c8c4355d489e86526c874463d705206ed2137a93392825e3a3cb358e0`.
Source archive SHA-256: `78b894f8775d9074751f30eaf14471db87429864ffaaae52c881a43ee409cc8a`.
Database backup: `/var/lib/livolod/backups/before-token04-20261017.sqlite3`.
[Live verification](verification/deployment-token04-2026-10-17.json) and
[candidate design/tests](verification/token-candidate.md).

Release 04 preserves release 03's cadence and strict confirmation, but avoids
adjacent tokens and the preceding reserved output checksum. This is a compatibility
experiment, not proof of the switch's duplicate-detection algorithm.

Rollback: stop `livolod`, install the retained release-03 wheel with
`/opt/livolod/bin/pip install --no-deps --force-reinstall`, then start `livolod`.
Keep the current database. The added metadata uses the existing schema and is
ignored by release 03; after any rollback, re-evaluate that metadata before a
later re-upgrade because older code does not maintain the new reservations.

## Release 03 history

Release `20261017-03` was the cadence-isolation experiment.
The user confirmed physical OFF restoration before deployment. The existing
database, peer association, device name and command ledger were preserved through
one controlled stop/install/start. Radio and MQTT readiness and the exact tested
source hash were verified. The restored report is bits 0, counter 39, token `04`.
No output or pairing commands were sent during deployment; controlled acceptance
remains coordinated by the GUI and firmware chats.

Captured release-03 acceptance remains **partially successful**: ON token `06`
was confirmed (bits 1, counter 40), but OFF token `07` was unconfirmed. Nordic
observed 10.046603 seconds between their preceding heartbeats, and first-command
phases of 18.814 and 34.879 ms. Both commands shared checksum `c7`; adjacent token
and checksum hypotheses remain confounded. No matching OFF report appeared in
the 119.542-second capture. Release 03 is not a reliable control fix.

Release 03 reserves the next regular heartbeat for an active set, enforcing at
least ten seconds since the previous successful heartbeat completion. Lease/status
work continues while waiting outside the RF lock. Epoch, tokens, wire bodies and
strict confirmation remain unchanged. Normal added latency can be ten seconds;
this is an experimental scheduling limitation, not the final intended UX. UI
result polling allows 20 seconds. See the [design and test record](verification/cadence-candidate.md).

Source: `/opt/livolod-releases/20261017-03/host`.
Wheel: `/opt/livolod-releases/20261017-03/wheels/livolo_ec_daemon-0.1.0-py3-none-any.whl`.
Wheel SHA-256: `e012dc28b1adba6f3fb1833398d5dde699dfd5dae231a139325fbd00d7916911`.
Source archive SHA-256: `4ca2daa2e9703699c7a880e3d24a05be2ed6e7829a11cb4080d42e9e6c0f3eb8`.
Database backup: `/var/lib/livolod/backups/before-cadence03-20261017.sqlite3`.
[Live source/readiness verification](verification/deployment-cadence03-2026-10-17.json).

To roll back to release 02, stop `livolod`, run
`sudo /opt/livolod/bin/pip install --no-deps --force-reinstall /opt/livolod-releases/20261017-02/wheels/livolo_ec_daemon-0.1.0-py3-none-any.whl`,
then start `livolod`. Keep the current database; no schema change was made.

## Release 02 history

Release `20261017-02` was a heartbeat-phase compatibility candidate.
The initial deployment details below remain as history. Release 02 preserves
the same configuration/database and unchanged process-relative heartbeat counter
epoch, token allocation, lease/session handling and report confirmation rules.
It emits a heartbeat immediately before each set transaction, serializing host
radio sends through its initial attempts. Maintenance rechecks its deadline after
acquiring that lock so an already-waiting periodic heartbeat cannot interrupt
the prelude. MCU automatic ACKs and UART lease renewal remain independent.

The prior successful trial's first commands followed heartbeats by 17–31 ms.
The failed GUI commands followed them by 8.525 and 3.604 seconds while using the
same zero timing trailer. This change reproduces the observed successful phase;
it is not yet a claim that phase is the sole cause. Controlled radio-captured
acceptance is owned by the GUI/firmware chats.

The first captured release-02 trial was **partially successful**: ON token `04`
received a matching native report (mask 1, bits 1, counter 38); the subsequent
OFF token `05` remained unconfirmed. Nordic observed the first commands 14.826 ms
and 18.968 ms after their respective heartbeats, so the intended prelude ordering
worked for both. Those heartbeats were only 1.312 seconds apart. Whether that
cadence affects switch acceptance remains unproven; release 02 must not be
described as a reliable control fix. No matching rules were relaxed.

Release 02 source: `/opt/livolod-releases/20261017-02/host`.
Wheel: `/opt/livolod-releases/20261017-02/wheels/livolo_ec_daemon-0.1.0-py3-none-any.whl`.
Wheel SHA-256: `ef19a26ae118c931a8d4f44cbe9d418db9b6fb6ae96eb594fcd1409961c56b50`.
Source archive SHA-256: `c0bd88e1a70841907ba2c25632c635a479492ebac8fa14c4551f306b5642924f`.
Database backup before upgrade:
`/var/lib/livolod/backups/before-phase02-20261017.sqlite3`.
[Live source/readiness verification](verification/deployment-phase02-2026-10-17.json).
The final 33-test suite passed after narrowing the candidate to phase-only,
including real Paho TCP, pySerial PTY, HTTP integration, prelude ordering,
unchanged counter epoch and rejection of prelude-time report confirmation.

Rollback to initial daemon code: stop `livolod`, run
`sudo /opt/livolod/bin/pip install --no-deps --force-reinstall /opt/livolod-releases/20261017-01/host`,
then start `livolod`. Preserve the current database; no schema change was made.

## Initial deployment

Installed on `10.1.1.21` (`livolodev.home.sfires.net`, system hostname `livolo`).
The user authorized persistent deployment in the GUI chat. There was no existing
Livolo service/configuration/database or MQTT broker to migrate.

| Component | Installed location / setting |
| --- | --- |
| Service | `livolod.service`, enabled, runs as `livolod`, supplementary group `dialout` |
| Python environment | `/opt/livolod`, Python 3.13.5, Paho 2.1.0, pySerial 3.5 |
| Source release | `/opt/livolod-releases/20261017-01/host`, root-owned |
| Configuration | `/etc/livolod/config.json`, root:livolod, mode 0640 |
| Database | `/var/lib/livolod/state.sqlite3`, livolod:livolod, mode 0600 |
| State directory | `/var/lib/livolod`, mode 0700 |
| Initial backup | `/var/lib/livolod/backups/initial-deployment-20261017.sqlite3`, SQLite online backup |
| Unit file | `/etc/systemd/system/livolod.service` |
| MQTT | Mosquitto 2.0.21, enabled, `127.0.0.1:1883` only |
| Broker listener config | `/etc/mosquitto/conf.d/livolod-local.conf` |
| Web UI | `127.0.0.1:8080` on Pi; access through SSH forwarding |
| Radio | `/dev/ttyAMA0`, UART v3; daemon is its exclusive owner |
| Network | Gateway `2a992b12`, tag `a7`, existing peer `46715621`, slot 0, two outputs |

Deployment archive SHA-256:
`16af2ac7ed01628cc36aceb91ec35fa159f6fb3ee96207d99c99570921958e76`.
Installed wheel SHA-256:
`a85435b07d468aec97be01f8ce91c758d7a249a7366984296edd2733166a3dbe`.

The daemon reached radio/MQTT readiness and received a native state report on
startup: bits 1 (output 1 on, output 2 off), counter 36, token `b3`. A later
explicit `refresh` using the observed `10`/`ac` queries did not elicit another
state report within its four-second window, and correctly returned `unconfirmed`.
Those queries must not be advertised as guaranteed state reads. The service
continues to receive/acknowledge native traffic; the UI distinguishes device
availability from the age of its last output-state report.

A single coordinated service restart verified clean shutdown, persistent
registry/name/state recovery, and re-establishment of radio/MQTT readiness. It
did not immediately produce a fresh output-state report; the saved state retained
its original timestamp. See the [deployment snapshot](verification/deployment-2026-10-17.json).
An API control checker in the GUI chat retains its fresh-baseline gate, so service
readiness must not be confused with completed output-control acceptance.

No firmware changes, pairing commands or output-control commands were made by
the deployment procedure. Real HTTP control acceptance is coordinated separately
by the GUI chat. The current switch association is preserved.

## Access and operations

```sh
ssh -N -L 127.0.0.1:8080:127.0.0.1:8080 sfires@10.1.1.21
```

Then open `http://127.0.0.1:8080/`. The GUI chat already manages a tunnel on this
Mac; do not start a second listener on the same port. Broker and HTTP ports are
not exposed to the LAN.

On the Pi:

```sh
sudo systemctl status livolod mosquitto
sudo journalctl -u livolod -f
sudo systemctl restart livolod
```

Restart preserves registry/names/state and installs a fresh volatile radio
session. It does not replay pending output commands. Stop the service before
running any standalone UART, capture or firmware tool.

## Rollback / disable

This was the first persistent host deployment, so rollback means disabling it:

```sh
sudo systemctl disable --now livolod
```

Graceful stop sends DISARM; the MCU lease independently expires if the serial
link is lost. Keep `/etc/livolod` and `/var/lib/livolod` to preserve device names,
state and command history. Do not erase the database as part of a routine
rollback. Mosquitto can remain running or be disabled with
`sudo systemctl disable --now mosquitto` if no other consumer uses it.

For a future code rollback, stop `livolod`, reinstall a retained source release
into `/opt/livolod` using its pip, and restart with the same configuration and
database. Check schema compatibility before reverting to a different future
version. The retained source/archive is the recovery source for this deployment;
no earlier daemon release existed.
