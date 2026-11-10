# Experimental AMD64: Home Assistant Container and Livolo EC

This is an experimental Linux AMD64 setup for a dedicated radio already running
compatible Livolo EC firmware. It does not qualify HA OS, Supervisor, other
architectures or radio boards. The tested candidate used firmware 0.14 on one
Sonoff ZBDongle-E; candidate HA-origin output control has not been qualified.

This example describes a separate installation; following it is not required for
an existing working deployment and must not alter that deployment. The daemon
owns the serial adapter and exposes its native API to the integration;
HA does not access the adapter. No MQTT broker is needed. Use a private Docker
network shared by HA and the daemon. The API uses a bearer token over HTTP; do
not publish its port to an untrusted network. TLS termination is needed if traffic
crosses an untrusted network. HA Container has no Supervisor app or ingress.

## Prepare

1. Back up HA's `/config` and take a consistent backup of any existing Livolo
   database and journal. Preserve the current network identity, devices, token,
   serial baud and output settings. Stop any previous daemon before moving its
   data or assigning the adapter; never run two owners on the radio or network.
2. Choose a dedicated adapter and identify its stable host path under
   `/dev/serial/by-id/`. It cannot be shared with ZHA, Zigbee2MQTT, Thread or a
   flashing tool. Updating this container does not flash the radio.
3. Obtain the verified experimental AMD64 image reference **`ghcr.io/psires/livolo-ec-ha-app@sha256:7b84b3ab6fa55fcbf7b05bce36efa5d010b1cf8aa5da999f2ec997615c3b829b`**
   with [supplementary third-party notices](https://github.com/psires/livolo_ec/releases/download/container-preview-0.6.0-amd64-81cf6fb/experimental-amd64-third-party-notices.txt)
   (SHA-256 `86ad9a5c4ffb4812c0019d2bf22cd394a929d641298a25ad8c8aa5a15d5a71c3`),
   and matching [integration ZIP](https://github.com/psires/livolo_ec/releases/download/container-preview-0.6.0-amd64-81cf6fb/livolo-ec-ha-0.6.0-experimental-amd64.zip), SHA-256
   **`8fbfb3b98179d821af017baeaac7acf141ce07e359981c28148312c0ff2466b8`**.
   The matching [flasher source archive](https://github.com/psires/livolo_ec/releases/download/container-preview-0.6.0-amd64-81cf6fb/universal_silabs_flasher-0.0.31.tar.gz) has SHA-256
   `7f7a4a744273277430cbff19a1c7522372b434ab147adcbc821395566c3a6a14`.
   Verify the ZIP hash before installing. The
   published image was rebuilt from the tested source and differs from the image
   tested with the physical radio; physical results do not establish image
   equivalence. The manifest digest is a registry
   identifier, distinct from a local Docker image ID. Use the immutable digest rather than relying on a mutable tag.
4. Create a private persistent directory for `/data` and a private Docker
   network, for example `docker network create livolo-private`. The wrapper
   reads `/data/options.json`; protect this file because it contains the token.
   Use the existing network identity and token for a migration. For a new network,
   choose an appropriate unique gateway and tag (01 through fe). Do not copy
   development IDs. The wrapper requires write access to `/data`, changes the
   directory ownership to UID/GID 10001, and prepares its state files with
   private permissions; use a dedicated directory rather than a shared root.

Example `/data/options.json` (replace every angle-bracket value):

```json
{
  "serial_port": "/dev/livolo",
  "serial_baudrate": 460800,
  "gateway": "<8-hex-digit-gateway>",
  "tag": "<2-hex-digit-tag>",
  "api_token": "<at-least-32-non-whitespace-ASCII-characters>",
  "output_mode": "disabled",
  "web_enabled": false,
  "firmware_catalog": "bundled",
  "radio_setup_profile": "disabled",
  "radio_mode": "gateway"
}
```

Set the baud to match the installed firmware. `disabled` is the observation-only
default in this example: switch entities are expected to be unavailable while
controls are disabled. For the tested Sonoff ZBDongle-E running Livolo EC
firmware 0.14, opt in to experimental switch control by changing `output_mode`
to `experimental_phase` and restarting the **companion daemon container** so it
reads the new options. This mode is experimental; candidate HA-origin control
has not been qualified. For an existing installation, preserve its original
mode and other settings rather than applying this example over it. With Web disabled,
the native integration remains available; the Supervisor ingress web interface
is not provided by this plain-container example.

## Start and connect

Use a Docker Compose service such as the following, replacing the data directory and host serial path. The stable device path is mapped to the fixed
container path used in the options file. The wrapper starts as root to prepare
permissions and drops privileges for the daemon.

```yaml
services:
  livolo:
    image: ghcr.io/psires/livolo-ec-ha-app@sha256:7b84b3ab6fa55fcbf7b05bce36efa5d010b1cf8aa5da999f2ec997615c3b829b
    restart: unless-stopped
    devices:
      - /dev/serial/by-id/<YOUR-ADAPTER>:/dev/livolo
    volumes:
      - /absolute/path/to/private-livolo-data:/data
    networks:
      - livolo-private
networks:
  livolo-private:
    external: true
```

Start with `docker compose up -d` and inspect its logs for a ready daemon. Attach
HA Container to `livolo-private` using its existing deployment configuration;
keep HA's existing `/config` mount and networking intact. Do not expose port
8090 on the host. The daemon hostname on this network is `livolo`; `localhost`
inside HA refers to HA itself. For host-networked HA, this bridge hostname may
not be reachable: arrange a private authenticated route appropriate to that
installation before proceeding.

Install the verified ZIP's `custom_components/livolo_ec` into HA's persistent
`/config/custom_components/livolo_ec`, then restart HA to load the Python
component when installing or updating the integration; an existing working
integration does not need to be reinstalled or restarted for this example. In **Settings → Devices & services → Add integration → Livolo EC**,
enter `http://livolo:8090` and the same token. Disable simulation for hardware.
Confirm the expected network and existing devices appear. Only when adding a new supported switch, use **Pair new device** on the gateway page and put only that switch in
pairing mode. Existing switches and identities should be retained.

The bundled public firmware catalog is empty. A private board-specific first-flash
trial used separately provisioned artifacts; this image does not provide a
general factory-firmware conversion or universal flashing capability. Do not
start initialization or firmware setup for an already configured radio.

## Update and recovery

Record the working image digest and integration artifact before updating. Stop
the current daemon, replace the image with the intended verified digest, and
reuse the same `/data`, options, serial mapping, token and network. Install a
matching integration package and restart HA if its Python files change. A
rollback to a known compatible image must retain the **current** database and
journal; do not overwrite newer command or identity history with an old backup.
Backups are for deliberate recovery, not routine image rollback. Never run both
versions against the same radio.

On the tested Intel setup, daemon replacement reconnected automatically at the
same address while HA stayed running. Recovery after an address change and the
cause of an earlier HA DNS resolver failure remain unverified. A controlled HA
restart recovered that particular incident; it does not establish a general DNS
fix. If control is unconfirmed, inspect the recorded outcome before retrying.
