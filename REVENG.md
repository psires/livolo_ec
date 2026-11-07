# Livolo EC reverse engineering

This document collects the protocol investigation and development evidence behind
[Livolo EC's local-control solution](README.md). Start with the README for user
setup; the material here explains how the original EC gateway and switches were
studied and how the replacement radio firmware was developed.

## What has been established

Capture-based analysis began with the EC gateway and a two-gang switch, later
extended to one-gang switches. The observed protocol is proprietary over
IEEE 802.15.4. Output control, state reports, application acknowledgments and
both checksum formats are decoded. Complete pairing and routing semantics
remain open; matching a tested switch profile is not a universal device interview.

The original custom CC2652P UART gateway established the baseline. It receives
raw EC frames on channel 11 and adds guarded transmit commands and MCU-side ACK
generation. A spare switch successfully paired to a local identity, and both
outputs were controlled with the stock EC unplugged. An independent Nordic
sniffer verified commands, state reports and approximately 1.87 ms ACKs.

The host daemon then added persistent device names/state, targeted pairing,
radio-session recovery, MQTT and a shared Web API. Bounded hardware checks
verified configuration restoration, lease renewal, state reception and clean
disarm. Multi-device RF operation and route recovery still need further validation.
See the [local gateway experiment](analysis/development/local-gateway-trial.md)
for the original evidence and limits.

## Protocol evidence and analysis tools

- [Protocol reconstruction and packet evidence](analysis/protocol.md)
- [Decoded packet index](analysis/decoded_packets.csv)
- [Capture summaries and SHA-256 hashes](analysis/summary.json)
- [Switch profile evidence and detection limits](docs/DEVICE_PROFILES.md)
- [Offline decoder](tools/decode_ec.py)
- [Wireshark EC plugin: installation, filters and tests](wireshark/README.md)
- [Development Pi setup and live capture results](analysis/development/bringup.md)
- [Bounded Nordic sniffer capture tool](tools/capture_nrf.py)
- [CC2652P passive receiver firmware](firmware/receiver/README.md)
- [CC2652P UART capture tool](tools/capture_cc26xx.py)
- [MG21 passive reference sniffer](firmware/sniffer-mg21/README.md)

From the repository root:

```sh
python3 tools/decode_ec.py --output-dir analysis
python3 -m unittest discover -s tools -p 'test_*.py' -v
```

The offline decoder uses only the Python 3 standard library. MCU protocol tests
also require a host C compiler. Hardware tools require pySerial and the
dependencies described in the development notes. Original captures are preserved.

## Gateway firmware and control experiments

The gateway supports three MCU families through four exact board profiles:
cod.m CC2652P, Sonoff ZBDongle-E, ZB-GW04 v1.2 and official Nordic PCA10059.
They share the packet, peer, scheduler and UART protocol-v3 core, with MCU queues,
heartbeats, bounded state queries and heartbeat-aligned output control. The host
retains authoritative network state.

Firmware 0.12 introduced MCU-owned output transactions aligned to
heartbeat-relative 200 ms slots. The host still allocates tokens and owns device
state. Each command has at most six radio attempts and a four-second deadline;
queries and immediate automatic ACK dispatch retain their previous policies.
The [UART contract](firmware/gateway/UART_PROTOCOL.md) defines confirmation and
cancellation. This is protocol history, not the current software release number;
see [software versions and radio compatibility](docs/VERSIONING.md).

- [UART gateway firmware and protocol](firmware/gateway/README.md)
- [MG21 firmware and exact board profiles](firmware/mg21/README.md)
- [Nordic firmware and recovery](firmware/nrf52840/README.md)
- [Host UART client](tools/ec_gateway.py)
- [Bounded local pairing trial](tools/ec_join_trial.py)
- [Bounded local output control](tools/ec_set_trial.py)
- [Local pairing/control experiment and limitations](analysis/development/local-gateway-trial.md)
- [Daemon hardware verification](host/verification/README.md)
- [Radio support and qualification matrix](analysis/development/ha-radio-support.md)
- [Nordic qualification](analysis/development/nrf52840-012-20261101.md)

The CC2652P gateway supports signed firmware updates over UART after a one-time
bootloader installation. The daemon confirms a trial image only after restoring
and verifying the radio session; unconfirmed trials roll back. MG21 and Nordic
use their own board-specific update and recovery procedures. See
[host update operations](host/README.md#firmware-updates),
[the 0.13 trial scope](docs/FIRMWARE_013_TRIAL.md) and
[emergency recovery](docs/FIRMWARE_RECOVERY.md). Software tests and helper
availability do not establish physical flashing qualification.

## Development installations and reproducibility

Development has used a ZB-GW04 with the HA app/native integration, a separate
standalone Radio + Web + MQTT testbed with its own network identity and device
registry, and a Sonoff passive reference sniffer. The
[deployment history](host/DEPLOYMENT.md) records changes in radio ownership and
configuration over time. Dated reports describe those experiments, not a
configuration to copy into a new installation.

Historical development notes describe bounded experiments on a small number of
devices. Additional local capture and deployment logs referenced by those notes
are not all included in the source distribution; the two development captures
required by regression tests are included. Signing keys and generated firmware
remain local.

The [source validation record](docs/SOURCE_VALIDATION.md) records dated clean
export checks, target builds and release limitations.
[CONTRIBUTING.md](CONTRIBUTING.md) documents function comments, Linux kernel C
style, pinned formatters and offline tests. Published software packages now exist;
firmware redistribution and broader hardware qualification are separate work
tracked through the [release process](docs/RELEASE_PROCESS.md).

Project-owned code is under the [BSD 2-Clause License](LICENSE). Third-party
components retain their own licences; see [third-party notices](THIRD_PARTY_NOTICES.md).
