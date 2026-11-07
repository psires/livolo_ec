# Livolo EC — local control, on your premises

Livolo EC is an open-source solution for controlling supported Livolo devices
entirely on your own premises. A dedicated radio adapter and the `livolod`
service replace the original EC gateway's control path. Switch commands, device
state and your network configuration stay on your local equipment: everyday
control needs no vendor cloud, cloud account or internet connection.

Use the built-in **Web UI** on a standalone Linux host, or connect the same
service to **Home Assistant** for dashboards and local automations. MQTT is
optional; neither the Web UI nor the native HA integration requires a broker.
Internet access is needed to download software and updates, not to operate your
installed switches.

## Supported devices

Currently supported devices are **one-gang and two-gang Livolo EC light
switches**: one controllable output per gang, with reported state shown in the
Web UI and Home Assistant. Support is based on the tested EC switch profiles;
it is not a claim of compatibility with every Livolo model or hardware revision.
Sockets, curtains, dimmers and other device types are not yet qualified.
Pairing and radio support remain experimental; see the
[device profile details](docs/DEVICE_PROFILES.md).

The EC devices studied here use a proprietary protocol over IEEE 802.15.4.
They are not ordinary Zigbee devices that can simply be added to ZHA or
Zigbee2MQTT.

## Choose your setup

| Solution | What you run | How you control devices |
| --- | --- | --- |
| Standalone | `livolod` on Linux, using Docker or the native daemon/CLI package | Built-in Web UI; optional MQTT or authenticated local API |
| Home Assistant | Livolo EC daemon app on HA OS, plus the native integration | HA switches, dashboards and automations; optional app Web UI |
| HA Container with an external daemon | Standalone `livolod` plus the native integration | The same HA experience, connecting to the daemon over your LAN |

The currently published software distribution and HA integration are **0.4.0**.
The source on `main` also includes improvements for the next release: one-button
automatic pairing, clearer firmware-catalog status, and separate daemon software
versioning (`0.14.0`, targeting firmware `0.13`). Those additions are **not in the
published 0.4.0 downloads**. See [releases](https://github.com/psires/livolo_ec/releases)
and [versioning](docs/VERSIONING.md).

## Before you start

You need a supported switch and a **dedicated adapter already flashed with this
project's matching gateway firmware**. Stock Zigbee coordinator firmware will
not work, and installing the daemon or HA integration does not convert it.
The adapter cannot simultaneously serve another radio network.

Three MCU families currently have four exact board profiles:

| Adapter | MCU family | Firmware setup |
| --- | --- | --- |
| cod.m CC2652P / RF-BM-2652P2 | Texas Instruments CC2652P | [TI guide](firmware/gateway/README.md) |
| Sonoff ZBDongle-E | Silicon Labs EFR32MG21 | [MG21 guide](firmware/mg21/README.md) |
| ZB-GW04 v1.2 | Silicon Labs EFR32MG21 | [MG21 guide](firmware/mg21/README.md) |
| Official Nordic PCA10059 dongle | Nordic nRF52840 | [Nordic guide](firmware/nrf52840/README.md) |

Support and recovery procedures are board-specific; sharing a chip does not
make another dongle interchangeable. The public firmware catalog is currently
empty, so initial firmware setup is still a developer-oriented step. Read the
[board qualification notes](analysis/development/ha-radio-support.md) and
[firmware flashing guide](docs/FIRMWARE_FLASHING.md) before choosing hardware.
The quick starts below assume this prerequisite is complete.

## Standalone quick start

For a new installation, the public Docker image runs on **64-bit ARM Linux**,
including a Raspberry Pi with a 64-bit OS. Install Docker Engine first. For
Linux x86-64 or an installation without Docker, use the
[native daemon/CLI package guide](host/release/README.md), including its Python
3.13, systemd and upgrade instructions.

1. Create a new directory and download the published configuration example:

   ```sh
   mkdir livolo-ec
   cd livolo-ec
   curl --fail --location \
     https://raw.githubusercontent.com/psires/livolo_ec/standalone-0.4.0/host/docker/config.example.json \
     --output config.json
   ```

2. Edit `config.json`. Set `network.gateway` and `network.tag` to your own
   network identity, match `serial_baudrate` to the flashed radio, and replace
   the `mqtt` object with `{"enabled": false}` unless you want MQTT. Keep
   `/dev/livolo`, `/var/lib/livolod/state.sqlite3` and the loopback Web address
   as supplied. The example enables experimental switch control.

   For a **new network**, this command generates an identity you can copy into
   the configuration. When migrating, preserve your existing identity and database.

   ```sh
   python3 -c 'import secrets; print("gateway:", f"{secrets.randbelow(0xfffffffe)+1:08x}"); print("tag:", f"{secrets.randbelow(254)+1:02x}")'
   ```

3. Select the adapter's actual Linux device path and start the daemon. Replace
   the placeholder below; use a stable `/dev/serial/by-id/` path where available.
   Only one daemon may own the radio.

   ```sh
   serial_device=/dev/serial/by-id/REPLACE_WITH_YOUR_RADIO
   sudo install -d -m 0700 -o 10001 -g 10001 data
   chmod 0644 config.json
   docker run -d --name livolod --restart unless-stopped \
     --network host --user 10001:10001 \
     --group-add "$(stat -Lc '%g' "$serial_device")" \
     --device "$serial_device:/dev/livolo" \
     --mount "type=bind,src=$PWD/config.json,dst=/etc/livolod/config.json,readonly" \
     --mount "type=bind,src=$PWD/data,dst=/var/lib/livolod" \
     --read-only --tmpfs /tmp:rw,noexec,nosuid,size=16m \
     --cap-drop ALL --security-opt no-new-privileges:true \
     ghcr.io/psires/livolod:0.4.0
   docker logs --tail=50 livolod
   ```

4. Open [the local Web UI](http://127.0.0.1:8080/) on that Linux host. From another
   computer, run `ssh -L 8080:127.0.0.1:8080 your-user@your-linux-host` and open
   the same address there. Wait for the gateway to be ready, then follow
   **Adding a switch** below. For direct LAN access, use the
   [Web authentication setup](host/WEB_UI.md#enable-on-the-daemon).

Keep `config.json` and `data/`: they hold your network configuration and device
registry. Back them up with the daemon stopped before replacing an image.
See the [Docker operations guide](host/docker/README.md) for Compose, optional
MQTT and maintenance. Do not replace an existing installation's state with the
example configuration.

## Home Assistant quick start

Use **HA OS on aarch64/ARM64** for the published daemon app. The qualified
integration baseline is **HA 2026.10.4**. HA Container users run the standalone
daemon above and follow the [native API setup](homeassistant/README.md#connect-to-a-daemon-on-an-existing-picontainer)
instead of installing an HA OS app.

1. Add the [Livolo EC app repository](https://my.home-assistant.io/redirect/supervisor_add_addon_repository/?repository_url=https%3A%2F%2Fgithub.com%2Fpsires%2Flivolo_ec)
   to HA's app store, then install **Livolo EC**. The repository URL is
   `https://github.com/psires/livolo_ec`.
2. In the app configuration, select the flashed radio's `serial_port`, match
   `serial_baudrate`, and set your `gateway` and `tag` as described above.
   Generate an `api_token` with
   `python3 -c 'import secrets; print(secrets.token_urlsafe(32))'`.
   Set `ha_origin` to your HA browser address, including the scheme and port,
   with no trailing slash. Keep `web_enabled: true` for the setup UI, then start
   the app. [App option reference](homeassistant/app/DOCS.md).
3. Add [psires/ha-livolo-ec](https://github.com/psires/ha-livolo-ec) as a custom
   **Integration** repository in HACS or Marketplace, install **Livolo EC**,
   then restart HA. You can use the
   [integration installation link](https://my.home-assistant.io/redirect/hacs_repository/?owner=psires&repository=ha-livolo-ec&category=integration).
   This is a custom repository, not a built-in HA integration.
4. Open **Settings → Devices & services → Add integration → Livolo EC**.
   Enter `http://<app-hostname>:8090` and the same API token. Copy the actual
   hostname from the app's information page; `localhost` is not the app.
   Leave the API host-port mapping unpublished on HA OS.
5. Use the app's **Open Web UI** to add your switches as described below.
   Each registered physical switch appears as an HA device, with one switch
   entity per output. Add those entities to dashboards and automations.

HACS/Marketplace discover integration updates automatically; install the update
and restart HA to apply it. HA app updates are managed separately by Supervisor.
Radio firmware updates are a third, separate operation. For migrations, retain
both the daemon's state and the existing HA integration entry so names and entity
IDs survive. See the [full HA guide](homeassistant/README.md).

## Adding a switch

With the **published 0.4.0** software, open **Add device** in the Web UI, put the
intended switch into its documented physical pairing mode, select its detected
entry and check the suggested output count. Give it a name, choose
**Save & start pairing**, and follow the displayed pairing instructions. Keep
pairing disabled on the original gateway when migrating. Wait for a fresh device
report to confirm pairing; saving a device alone does not establish a connection.
If automatic profile detection cannot identify it, use the documented manual
setup rather than guessing its profile.

In the **development source**, press **Pair new device** in either Web or HA,
then put one new switch into pairing mode. The daemon detects a supported profile,
registers it and reports the pairing outcome. The [pairing guide](docs/PAIRING.md)
describes this next-release workflow and its limits.

## Development and protocol research

The project grew out of reverse engineering the original EC gateway and switches.
[REVENG.md](REVENG.md) collects that work: protocol reconstruction, packet captures,
decoders, sniffers, hardware experiments and their limitations.

For contributing code, see [CONTRIBUTING.md](CONTRIBUTING.md): function comments
must explain intent and parameters, with Linux kernel style for C and the pinned
formatters for other languages. See [release ownership and quality gates](docs/RELEASE_PROCESS.md)
for how changes become releases.

Project-owned code is under the [BSD 2-Clause License](LICENSE).
[Third-party notices](THIRD_PARTY_NOTICES.md) describe separately licensed components.
