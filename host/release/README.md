# Standalone daemon and CLI distribution 0.5.1

Download `livolo-ec-0.5.1-standalone.tar.gz` and `SHA256SUMS` from the
[standalone 0.5.1 release](https://github.com/psires/livolo_ec/releases/tag/standalone-0.5.1).
This distribution contains daemon **0.14.1**, native Python client **0.5.1**, the
local web interface, service examples and firmware maintenance commands. The
bundle and daemon package numbers are independent of radio compatibility **0.13**; `livolod
--version` correctly prints `0.14.1`.

Use Linux with **Python 3.13 and venv support** for this bundle and its pinned
flasher dependencies. The release workflow tests wheel and source installs on
Linux x86-64 and ARM64. These are simulated service/packaging checks, not new
radio or RF qualification. The daemon metadata's broader Python minimum does
not certify the complete flasher dependency set on older interpreters.

## Verify and unpack

From the download directory, verify the standalone archive (the outer checksum
file also lists separately downloadable wheels, sdists and metadata):

```sh
grep '  livolo-ec-0.5.1-standalone.tar.gz$' SHA256SUMS | sha256sum --check -
tar -xzf livolo-ec-0.5.1-standalone.tar.gz
cd livolo-ec-0.5.1-standalone
sha256sum --check SHA256SUMS
```

The archive contains `packages/`, `config.example.json`, `systemd/`, `docs/`,
`constraints.txt`, `checks/smoke.py`, licence/notices and `release.json` with the
exact source commit. Dependencies are fetched separately from PyPI; this is not
an offline wheelhouse, Docker image or firmware bundle. Nothing installs,
starts a service or operates a radio merely by unpacking it.

## Try the installed commands without hardware

```sh
python3.13 -m venv .venv
.venv/bin/python -m pip install --constraint constraints.txt \
  './packages/livolo_ec_daemon-0.14.1-py3-none-any.whl[mqtt,flash]' \
  ./packages/livolo_ec_client-0.5.1-py3-none-any.whl
.venv/bin/python -m pip check
.venv/bin/livolod --version
.venv/bin/livolod --config config.example.json --check-config
PATH="$PWD/.venv/bin:$PATH" .venv/bin/python checks/smoke.py
```

Run the smoke check as your normal user. It creates a temporary configuration,
uses a simulated radio and loopback listeners, checks the authenticated native
API, web files, SQLite integrity and clean shutdown, then removes its state.
It does not use the example's serial port or a live MQTT broker.

The Python client is a library for applications, not an additional shell control
command. Day-to-day switch control is available through the web UI, native API
or optional MQTT. `livolod --help` and `livolod-firmware-manage --help` describe
the service and maintenance commands.

The unified firmware command provides `inspect`, `preflight` and `flash --check-only`. Default preflight does not open the serial device, start the daemon
or initialize its database. Explicit `--probe` is a separate read-only UART query
mode requiring the daemon to be stopped. The Web UI and native HA integration
use the same advisory preflight report; installation revalidates the target and
artifact under the durable job owner. See `docs/FIRMWARE_FLASHING.md` and
`docs/FIRMWARE_RECOVERY.md` for exact board commands and recovery limits.

## Install a new Linux service

These instructions are for a new installation. Existing installations must use
the upgrade procedure below to preserve their configuration and database.
Provide Python 3.13 with venv support through your operating system first.

```sh
sudo useradd --system --user-group --home-dir /var/lib/livolod --no-create-home livolod
sudo usermod -aG dialout livolod
sudo python3.13 -m venv /opt/livolod
sudo /opt/livolod/bin/python -m pip install --constraint constraints.txt \
  './packages/livolo_ec_daemon-0.14.1-py3-none-any.whl[mqtt,flash]' \
  ./packages/livolo_ec_client-0.5.1-py3-none-any.whl
sudo install -d -m 0750 -o root -g livolod /etc/livolod
sudo install -m 0640 -o root -g livolod config.example.json /etc/livolod/config.json
sudo install -m 0644 systemd/livolod.service /etc/systemd/system/livolod.service
```

Skip account creation if it already exists. `dialout` is the serial-device group
on Debian-family systems; use the actual device's group on other distributions.
Before starting, edit `/etc/livolod/config.json`: replace the radio path with its
stable `/dev/serial/by-id/` identity and choose your gateway identity/tag. The
example values are placeholders, not a new unique network. A migrated deployment
must retain its existing network identity and database, with its previous owner
stopped. Only one daemon may own that radio/network.

The example deliberately has an empty device registry, disables output control
and MQTT, and binds the web interface to `127.0.0.1:8080`. It requires matching
UART v3 firmware already on the radio. Register known switches through the UI.
Enable an experimental output mode only after reading its hardware limits in the
[daemon guide](https://github.com/psires/livolo_ec/blob/standalone-0.5.1/host/README.md).
Keep loopback access or configure authenticated access as documented there.
The native API is disabled initially; its bearer-token setup is described in
the included `docs/NATIVE_API.md`.

```sh
sudo /opt/livolod/bin/livolod --config /etc/livolod/config.json --check-config
sudo systemctl daemon-reload
sudo systemctl enable --now livolod
journalctl -u livolod -f
```

The service runs as `livolod`, keeps authoritative state under `/var/lib/livolod`
and reads optional secrets from `/etc/livolod/environment`. Protect that file
with mode `0600`, owned by root; systemd reads it before dropping privileges.
Never put tokens into download URLs or source control.

## Pair a new switch

Use **Pair new device** in Web and HA. Follow the
[pairing guide](../../docs/PAIRING.md) for its one-device session, supported
profiles and confirmation requirements. The bundle includes `docs/PAIRING.md`
and the [versioning migration guide](../../docs/VERSIONING.md) at
`docs/VERSIONING.md`. Use `livolod-commission --help` for the authenticated
terminal commissioning client.

## Upgrade, backup and removal

Finish any active firmware transfer or commissioning session before a planned
software upgrade, then stop the actual radio owner. Preserve configured database
and trusted catalog paths, including paths outside `/var/lib/livolod`: the
example backup below covers only the default directories. With Docker, retain
the same host-volume mounts and local-catalog setting. No database schema or
network-identity migration is required. Interrupted jobs keep their recorded
identity and target; restart may mark their outcome unknown/interrupted, never
replay them. For the systemd installation:

```sh
sudo systemctl stop livolod
sudo install -d -m 0700 /var/backups/livolod
sudo tar -czf /var/backups/livolod/pre-0.5.1.tar.gz -C / etc/livolod var/lib/livolod
sudo /opt/livolod/bin/python -m pip freeze > previous-python-packages.txt
sudo /opt/livolod/bin/python -m pip install --force-reinstall --constraint constraints.txt \
  './packages/livolo_ec_daemon-0.14.1-py3-none-any.whl[mqtt,flash]' \
  ./packages/livolo_ec_client-0.5.1-py3-none-any.whl
sudo /opt/livolod/bin/python -m pip check
sudo /opt/livolod/bin/livolod --config /etc/livolod/config.json --check-config
sudo systemctl start livolod
```

Use a unique backup filename on subsequent upgrades. Keep the previous wheel or
environment as well as its dependency inventory. `--force-reinstall` reinstalls the explicitly selected artifact; published
versions are immutable. Do not overwrite
configuration with the example: SQLite owns the device registry after initial
startup. Inspect service status/logs and verify the correct network and devices.

If an upgrade fails before restart, leave the service stopped while restoring
the previous environment and its compatible state. After radio activity has
resumed, an old database may no longer represent current pairing/command state;
do not blindly roll it back. Stop the service, preserve a fresh copy of current
state, and assess compatibility before choosing a restore point. Restore a
consistent stopped-service backup including SQLite sidecar files and ownership.

To retire this installation, run `sudo systemctl disable --now livolod` and
archive configuration and state. The environment and service unit can then be
removed; retain the backup until migration is verified. This release has no
automatic background package updater.

## Emergency firmware maintenance

```sh
sudo -u livolod /opt/livolod/bin/livolod-firmware-manage \
  --config /etc/livolod/config.json status
```

For `install` or `recover`, stop the daemon first and follow the included
`docs/FIRMWARE_RECOVERY.md`. Those commands reuse the persistent maintenance
journal and radio lock. Do not run the low-level `livolod-firmware` worker
directly or bypass its ownership locks.

The catalog is empty, so no firmware installation is offered out of the box.
To publish an administrator-reviewed catalog from a separate staging directory,
use the installed command without starting the daemon or opening its radio:

```sh
sudo /opt/livolod/bin/livolod-firmware-manage catalog \
  --source /path/to/reviewed-catalog \
  --destination /var/lib/livolod/firmware \
  --manifest-sha256 REVIEWED_MANIFEST_SHA256
```

Replace the paths and checksum with the reviewed values. Set
`firmware_bundle_dir` in the daemon configuration to the destination and ensure
the service user can read it. The command validates referenced artifacts before
publishing immutable objects and atomically replacing the manifest. Previous
manifests remain in `history/`; do not remove objects referenced by a retained
manifest or active job. Once configured, catalog changes refresh availability
without a radio reconnect. Invalid or missing files disable firmware eligibility
while normal switch control remains available. An active job retains its admitted
target; catalog publication never requests a flash.

Qualified, trusted payloads must match the exact board and host version. TI and
MG21 update backends exist. Nordic updates require an explicit physical USB
profile, narrow device-node publisher and isolated Python 3.10/nrfutil 6.1.7
helper; the default configuration leaves this backend unavailable. The unified
CLI also supports deliberate PCA10059 recovery with exact USB identities.
The legacy helper is bundled in the separate container image, but is not included
in this Python 3.13 native bundle. Factory/bootloader recovery retains
board-specific prerequisites. No firmware binaries, signing keys, SDKs or
third-party dependency wheels are shipped here.
Project code is BSD-2-Clause; dependencies, including the separately installed
GPL flasher, retain their own licences.
