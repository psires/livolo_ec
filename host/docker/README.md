# Run livolod 0.12 in Docker on ARM64

This image packages daemon **0.12** for **Linux ARM64**, including its web UI and
MQTT/Home Assistant integration. Use a 64-bit Raspberry Pi OS (`uname -m` should
print `aarch64`). It requires a radio already running compatible Livolo EC UART
firmware. The image does not flash firmware, change GPIOs or contain a broker.

The prepared archive is approximately **44 MB**. Its checksum accompanies it in
`dist/`; [build verification](../verification/docker-arm64-0.12-20261101.json)
records the image ID and exact daemon source hashes. Native ARM smoke checks
passed both during the build and under an OCI runtime with the Compose security
settings. Compose configuration validation also passed. The image was subsequently
loaded into Docker Engine on the development Pi and took over the physical radio:
MQTT connected, both registered switches came online, and the web assets returned
HTTP 200. Clean shutdown, serial release and container recreation were verified.
See [deployment verification](../verification/deployment-docker-0.12-20261101.json).

These records describe the original local 0.12 artifact. It predates native API,
ingress and package-license changes now present in the source. Its recorded
hashes remain unchanged; a new source build is a different artifact even while
the development version still reads 0.12. Archives and broad deployment logs
are local evidence and are not included in the source checkout.

The Compose configuration runs as UID/GID **10001**, grants access to one serial
device, and keeps the root filesystem read-only. SQLite and saved MQTT settings
live in a host directory. Linux host networking lets it reach a broker on the Pi
at `127.0.0.1`; the example web UI binds only to the Pi's loopback interface.

## Existing development Pi

**Retired after the HA handover:** the old Pi container is stopped with restart
policy `no` in both Docker and its saved Compose file. Native `livolod.service`
remains inactive/disabled; no automatic boot hook was found. Do not run the start
commands below while HA owns the network. See the [rollback procedure](../DEPLOYMENT.md)
and `/opt/livolod-docker/HA-HANDOVER.txt` on the Pi. The repository Compose template
still uses `unless-stopped` for new, active installations.

The installation at **10.1.1.21** is already deployed under
`/opt/livolod-docker`, with its database at `data/state.sqlite3` and the native
systemd daemon disabled. Host nginx was configured to expose its web UI at
[http://10.1.1.21/](http://10.1.1.21/), forwarding to the container's loopback
listener. With the daemon retired, that proxy has no running upstream. This
development endpoint uses HTTP without an additional login.
The [nginx site configuration](nginx-development-pi.conf) validates the external
Host and Origin before translating them to the upstream address, preserving the
daemon's CSRF checks. See the [nginx proxy module documentation](https://nginx.org/en/docs/http/ngx_http_proxy_module.html)
for the forwarding directives. On that Pi, use the installed helper from any directory:

```sh
sudo livolodctl stop       # Release the radio before development/flashing.
sudo livolodctl start      # Recreate and resolve USB mapping after development.
sudo livolodctl status
sudo livolodctl logs -f
```

`restart` also stops and recreates the container. The helper's
[source](livolodctl) fixes the deployment path to `/opt/livolod-docker`; install
it root-owned at `/usr/local/sbin/livolodctl` only for that layout. The Pi last used
the official Nordic PCA10059 over native USB CDC; the host retains baud 460800
as compatibility metadata. These are historical deployment details; active radio
ownership is recorded in the HA handover. Sonoff was used as the passive
[reference sniffer](../../analysis/development/sonoff-sniffer-20261101.md) on
10.1.1.120. See the [Nordic qualification](../../analysis/development/nrf52840-012-20261101.md).
Do not start `livolod.service` alongside
Docker. The old native database is no longer authoritative. Full paths, backup
location and host-side firmware maintenance notes are in
[the deployment record](../DEPLOYMENT.md).

The following steps describe installation on another ARM64 Pi.

## 1. Install Docker and obtain the image

Install [Docker Engine and the Compose plugin](https://docs.docker.com/engine/install/debian/)
on the ARM64 Pi. The commands below assume your account can run `docker`; use
`sudo docker` if needed.

If you have the original local artifact, copy `livolod-0.12-linux-arm64.tar.gz` and its `.sha256` file from
this project's `dist/` directory to the Pi. In their destination directory:

```sh
sha256sum -c livolod-0.12-linux-arm64.tar.gz.sha256
docker load -i livolod-0.12-linux-arm64.tar.gz
docker image inspect livolod:0.12 --format '{{.Os}}/{{.Architecture}}'
docker run --rm livolod:0.12 --version
```

Expected results: `linux/arm64` and `0.12`. The image is a local release artifact;
it has not been pushed to Docker Hub or another registry.

Alternatively, build directly from the repository root on an ARM64 machine:

```sh
docker build --platform linux/arm64 \
  --build-arg RELEASE_VERSION="$(cat VERSION)" \
  -f host/docker/Dockerfile -t "livolod:$(cat VERSION)" .
```

Docker Desktop/Buildx with ARM emulation can use the same Dockerfile from another
architecture. The build checks the three daemon version declarations and runs a
simulated startup/shutdown test inside the ARM image. Runtime dependencies are
fixed at pySerial 3.5 and Paho MQTT 2.1.0, with aiohttp 3.14.5 and its resolved
dependencies added by the current native API development source. The Python 3.13 Bookworm base tag can
receive upstream patches; the delivered image digest identifies the exact build.

## 2. Prepare a persistent deployment directory

From the repository root, copy the supplied Compose file and example:

```sh
mkdir -p "$HOME/livolod"
cp host/docker/compose.yaml "$HOME/livolod/compose.yaml"
cp host/docker/config.example.json "$HOME/livolod/config.json"
cd "$HOME/livolod"
sudo install -d -m 0700 -o 10001 -g 10001 data
touch daemon.env
chmod 0600 daemon.env

serial_device=/dev/ttyAMA0
printf 'LIVOLO_SERIAL_DEVICE=%s\nLIVOLO_SERIAL_GID=%s\n' \
  "$serial_device" "$(stat -Lc '%g' "$serial_device")" > .env
```

For USB adapters, set `serial_device` to their stable `/dev/serial/by-id/...`
path before generating `.env`. Docker maps the selected host device to
`/dev/livolo` inside the container. The numeric supplemental group must match the
device on the host; do not assume the `dialout` group has the same ID everywhere.
Recreate the container after reconnecting a USB adapter if its device mapping
changes. ARMv7/32-bit OS images are not provided in this release.

## 3. Configure the daemon

Edit `config.json` before starting:

- Replace the example gateway `10203040` and tag `42` with your network identity.
  For an existing installation, preserve its exact identity and registry; use the
  migration procedure below. For a new network, choose a unique nonzero identity.
- Keep `serial_port` as `/dev/livolo` and `database` as
  `/var/lib/livolod/state.sqlite3`; these are container paths.
- Match `serial_baudrate` to the installed radio firmware. The example uses
  **460800**; older builds may use **115200**.
- Set `mqtt.host` and `mqtt.port`. Use the broker's LAN IP/name for a broker on
  another machine. If it runs on this Pi, `127.0.0.1` works with host networking.
- For authentication, add `username` and `password_env: "LIVOLO_MQTT_PASSWORD"`
  inside `mqtt`, then put `LIVOLO_MQTT_PASSWORD=your-password` in `daemon.env`.
  Use Compose env-file quoting for passwords containing `$`, `#` or quotes.
- The empty `devices` array starts a fresh registry. Register/pair devices through
  the web UI, or seed their exact IDs, output counts and suffixes as described in
  the [daemon guide](../README.md). Changing this seed later does not replace the
  existing SQLite registry.

The example enables `experimental_phase` output control. Use `disabled` for
observation only. Keep web host `127.0.0.1` for local/SSH access. To expose the web
UI on a LAN address, configure `web.token_env` and supply that environment variable
in `daemon.env`, as described in [web authentication](../WEB_UI.md).

Make the config readable by the container without making secrets public:

```sh
sudo chown 10001:10001 config.json
sudo chmod 0600 config.json
docker compose config --quiet
docker compose run --rm --no-deps livolod \
  --config /etc/livolod/config.json --check-config
```

`--check-config` validates settings without opening UART, MQTT or the database.
For MQTT TLS, bind-mount the CA/client certificate files read-only and use their
container paths in the existing `tls_ca`, `tls_cert` and `tls_key` settings.

## 4. Give the container exclusive ownership and start it

If the systemd installation is currently using this radio, stop and disable it
before starting the container:

```sh
sudo systemctl disable --now livolod
docker compose up -d
docker compose logs --tail=100 -f livolod
```

For a fresh installation with no `livolod.service`, skip that systemctl command.
No other daemon or flashing tool may own the same serial port. Logs should report
the radio becoming ready. `restart: unless-stopped` restores the container after
a Docker/host restart; `docker compose stop` deliberately leaves it stopped.

Inspect status on the Pi:

```sh
docker compose ps
curl --fail http://127.0.0.1:8080/api/state
```

Check `ready`, `bridge.mqtt_connected`, firmware diagnostics and device
availability in that response. A running container alone does not prove that
the radio or broker is reachable. There is deliberately no HTTP-dependent Docker
health check, so MQTT-only configurations remain supported.

To use the UI from your workstation:

```sh
ssh -L 8080:127.0.0.1:8080 your-user@your-pi
```

Open `http://127.0.0.1:8080/` locally. Home Assistant discovery is automatic on the
configured MQTT broker, under the standard `homeassistant` discovery prefix.

## Migrate an existing systemd installation

Preserve SQLite: it contains device names, network identity, tokens, command
history and MQTT settings saved through the UI. Copying only `config.json` loses
that authoritative state. After preparing the deployment directory, stop the
old service and create a consistent database copy:

```sh
cd "$HOME/livolod"
sudo systemctl disable --now livolod
sudo cp /etc/livolod/config.json ./config.json
sudo python3 - <<'PY'
import sqlite3
from pathlib import Path
destination = Path('data/state.sqlite3')
if destination.exists():
    raise SystemExit('Destination exists; move it aside deliberately before migrating')
with sqlite3.connect('file:/var/lib/livolod/state.sqlite3?mode=ro', uri=True) as source:
    with sqlite3.connect(destination) as target:
        source.backup(target)
PY
sudo chown -R 10001:10001 data
sudo chmod 0700 data
sudo chmod 0600 data/state.sqlite3
```

Edit the copied config with `sudoedit config.json`: change `serial_port` to
`/dev/livolo`, retain the baud/network settings and use the database path shown
above. Copy any required variables from `/etc/livolod/environment` into
`daemon.env`; Docker does not inherit the systemd environment file. Use the
permission and configuration checks in step 3, then start the container.
SQLite MQTT settings take precedence over the startup JSON, as before.

The old systemd database remains untouched. If reverting after using Docker,
stop the container and back up its current database into the systemd installation
with the correct `livolod` ownership before re-enabling that service; otherwise
you return to stale registry/token/settings state. Never run both against the
same network/radio concurrently.

## Backups, upgrades and shutdown

For a simple consistent backup, stop the container, copy the entire deployment
directory (including `.env`, `daemon.env`, `config.json` and `data/`), and start it
again. Protect the backup because saved MQTT credentials are in SQLite.

```sh
docker compose stop
# Back up this directory now.
docker compose start
```

For a later daemon release, load/build its image, change `LIVOLO_VERSION` in
`.env`, and run `docker compose up -d`. The bind-mounted data survives replacement.
Keep the old image and a pre-upgrade data backup for rollback. Do not delete the
data directory during an image upgrade.

Updating the daemon image **does not update radio firmware**. This minimal image
omits the optional signing/updater dependencies and has no systemd or GPIO access.
For a signed radio update, stop the container, use the matching host updater
outside Docker without its `--service` option, then start the matching container
promptly to validate/confirm the trial. Follow the board-specific
[firmware update procedure](../../firmware/gateway/README.md); this task did not
perform or change any firmware update.

Docker details: [device mappings, supplemental groups and host networking](https://docs.docker.com/reference/compose-file/services/),
[ARM builds](https://docs.docker.com/build/building/multi-platform/), and the
[official Python base image](https://hub.docker.com/_/python).

## Packaging boundaries and Home Assistant support

Host networking is a choice in this Compose example, not a requirement baked
into the image. An HA app/add-on wrapper can supply its own configuration,
use a writable `/data` directory owned by the container UID, and select bridge
networking with an authenticated non-loopback API listener. The image runs the
daemon directly and has no host systemd dependency. Only ARM64 has been built and
smoke-tested here; an amd64 artifact needs its own build and validation.

The current source now supports [optional MQTT and native API v1](../NATIVE_API.md),
but the delivered archive and retired development container predate that work.
Rebuild from the updated source before using these capabilities. The Dockerfile
includes the MQTT extra for backwards compatibility and downloads aiohttp with
its transitive wheels for offline runtime installation. Its updated smoke check
exercises authenticated native API startup with MQTT disabled as well as the web
UI. To enable the native API, add the `api` configuration described in the
[daemon guide](../README.md) and supply its token in `daemon.env`; it is disabled
by default. No native API development changes have been deployed to the Pi.

The current source is BSD-2-Clause. `host/LICENSE` is included in the daemon
wheel and the Docker build context, with an OCI license label on newly built
images. Independent source assembly must retain that package-local license.
The separate Home Assistant packaging lives under `homeassistant/`.
