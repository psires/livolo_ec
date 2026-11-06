# Livolo EC reverse engineering

Capture-based analysis of the EC gateway and a two-gang switch.

Daemon/firmware versions use **major.minor**, currently **0.13**. The root [VERSION](VERSION)
is the release source; `python3 tools/project_version.py` checks host and firmware
metadata, and `--write` propagates a new release number. UART protocol and data
schema numbers are separate compatibility identifiers.

The [standalone daemon/CLI distribution 0.4.0](host/release/README.md) packages
daemon 0.13 and client 0.4.0 for native Linux installation without a checkout.
Its bundle version is separate from the radio compatibility version above.

Published ARM64 containers are `ghcr.io/psires/livolod:0.4.0` and
`ghcr.io/psires/livolo-ec-ha-app:0.4.0`; see the
[verified image digests and release process](docs/CONTAINER_PUBLICATION.md).
The [Home Assistant installation guide](homeassistant/README.md) provides the
app repository and [HACS/Marketplace integration channel](https://github.com/psires/ha-livolo-ec),
with integration 0.4.0 and automatic update discovery. Installing integration
updates requires an HA restart; radio firmware updates are separate.

The CC2652P gateway supports signed firmware updates over UART after a one-time
bootloader installation. The daemon reports running and target versions and
confirms a trial image only after restoring the radio session. Unconfirmed trial
images roll back. See [firmware build and update instructions](firmware/gateway/README.md)
and [host update operations](host/README.md#firmware-updates).

The daemon also owns a shared firmware installation job for the web UI and
Home Assistant's standard firmware update entity. It validates an exact-board
local bundle, pauses radio operations, records progress and verifies the restored
session before reporting success. Initial backends cover TI signed UART and the
two MG21 profiles; Nordic USB DFU requires an explicit standalone profile and
the [documented deployment prerequisites](host/docker/NORDIC.md). It remains off
in default configurations; see the [0.13 trial scope](docs/FIRMWARE_013_TRIAL.md). No qualified release firmware is bundled yet, so the
source-only installation reports that no candidate is available. See the
[emergency recovery procedure](docs/FIRMWARE_RECOVERY.md) before qualifying updates
on hardware.

The gateway supports three MCU families through four board profiles: cod.m
CC2652P, Sonoff ZBDongle-E, ZB-GW04 v1.2 and official Nordic PCA10059. They share
the packet, peer, scheduler and protocol-v3 core, with MCU queues, heartbeats,
bounded state queries and heartbeat-aligned output control. The host retains
authoritative network state. The current development installation uses the
ZB-GW04 through the [Home Assistant app and native integration](homeassistant/README.md).
The former standalone Pi is retired; Sonoff runs the
[passive reference sniffer](firmware/sniffer-mg21/README.md). See the
[radio support matrix](analysis/development/ha-radio-support.md),
[Nordic qualification](analysis/development/nrf52840-012-20261101.md) and
[current deployment](host/DEPLOYMENT.md).

The original custom CC2652P UART gateway firmware established the baseline.
It receives raw EC frames on channel 11 and adds guarded transmit commands and
MCU-side ACK generation. The spare switch has successfully paired to the local
identity, and both outputs have been controlled with the stock EC unplugged.
Nordic independently verified the commands, state reports and ~1.87 ms ACKs.
The [Raspberry Pi daemon](host/README.md) now provides MQTT control, persistent
device names/state, targeted pairing, radio-session recovery and a shared GUI API.
Its bounded hardware check verified configuration restoration, lease renewal,
state reception and clean disarm. Multi-device RF operation and route recovery
still need further validation. The daemon supports
[standalone Docker deployment](host/docker/README.md), optional MQTT and a
native authenticated API. See the [deployment history](host/DEPLOYMENT.md) for
the migration from the standalone Pi to Home Assistant.

- [Protocol reconstruction and packet evidence](analysis/protocol.md)
- [Decoded packet index](analysis/decoded_packets.csv)
- [Capture summaries and SHA-256 hashes](analysis/summary.json)
- [Offline decoder](tools/decode_ec.py)
- [Wireshark EC plugin: installation, filters and tests](wireshark/README.md)
- [Development Pi setup and live capture results](analysis/development/bringup.md)
- [Bounded Nordic sniffer capture tool](tools/capture_nrf.py)
- [CC2652P passive receiver firmware](firmware/receiver/README.md)
- [CC2652P UART capture tool](tools/capture_cc26xx.py)
- [UART gateway firmware and protocol](firmware/gateway/README.md)
- [Host UART client](tools/ec_gateway.py)
- [Raspberry Pi daemon: setup and MQTT interface](host/README.md)
- [Daemon hardware verification](host/verification/README.md)
- [Bounded local pairing trial](tools/ec_join_trial.py)
- [Bounded local output control](tools/ec_set_trial.py)
- [Local pairing/control experiment and limitations](analysis/development/local-gateway-trial.md)

The observed protocol is proprietary over IEEE 802.15.4. Output control, state reports, application acknowledgments, and both checksum formats are decoded; complete pairing and routing semantics remain open.

```sh
python3 tools/decode_ec.py --output-dir analysis
python3 -m unittest discover -s tools -p 'test_*.py' -v
```

The offline decoder uses only the Python 3 standard library. MCU protocol tests
also require a host C compiler. Hardware tools require pySerial and the
dependencies described in the development notes.
Original captures are preserved.

## Development

See [CONTRIBUTING.md](CONTRIBUTING.md) for function-documentation conventions,
Linux kernel C style, pinned formatters and the complete offline test commands.
Python functions use purpose and parameter docstrings, C APIs use Doxygen blocks,
and browser helpers use JSDoc.
The [source validation record](docs/SOURCE_VALIDATION.md) lists the latest clean
export checks, target builds and remaining release limitations.

Historical development notes describe experiments on one installation. Additional
local capture and deployment logs referenced by those notes are not part of the
source distribution; the two development captures required by regression tests
are included. Signing keys and generated firmware remain local.

Project-owned code is released under the [BSD 2-Clause License](LICENSE).
Third-party components retain their own licences and attribution; see
[third-party notices](THIRD_PARTY_NOTICES.md). The
[release plan](docs/RELEASE_PLAN.md) describes packaging and remaining
qualification work. Public packages and automated firmware delivery are not yet
available.

Release 0.12 adds MCU-owned output transactions aligned to heartbeat-relative
200 ms slots. The Pi still allocates tokens and owns device state. Each command
has at most six radio attempts and a four-second deadline; queries and immediate
automatic ACK dispatch retain their previous policies. See the
[UART contract](firmware/gateway/UART_PROTOCOL.md) for confirmation and cancellation.
