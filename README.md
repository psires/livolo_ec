# Livolo EC reverse engineering

Capture-based analysis of the EC gateway and a two-gang switch.

Release versions use **major.minor**, currently **0.2**. The root [VERSION](VERSION)
is the release source; `python3 tools/project_version.py` checks host and firmware
metadata, and `--write` propagates a new release number. UART protocol and data
schema numbers are separate compatibility identifiers.

The gateway supports signed firmware updates over UART after a one-time
bootloader installation. The daemon reports running and target versions and
confirms a trial image only after restoring the radio session. Unconfirmed trial
images roll back. See [firmware build and update instructions](firmware/gateway/README.md)
and [host update operations](host/README.md#firmware-updates).

A custom CC2652P UART gateway firmware is now flashed on the development Pi.
It receives raw EC frames on channel 11 and adds guarded transmit commands and
MCU-side ACK generation. The spare switch has successfully paired to the local
identity, and both outputs have been controlled with the stock EC unplugged.
Nordic independently verified the commands, state reports and ~1.87 ms ACKs.
The [Raspberry Pi daemon](host/README.md) now provides MQTT control, persistent
device names/state, targeted pairing, radio-session recovery and a shared GUI API.
Its bounded hardware check verified configuration restoration, lease renewal,
state reception and clean disarm. Multi-device RF operation and route recovery
still need further validation. The daemon is now
[deployed as a persistent Pi service](host/DEPLOYMENT.md), with a configured MQTT
broker and web UI reached through SSH forwarding.

- [Protocol reconstruction and packet evidence](analysis/protocol.md)
- [Decoded packet index](analysis/decoded_packets.csv)
- [Capture summaries and SHA-256 hashes](analysis/summary.json)
- [Offline decoder](tools/decode_ec.py)
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

Historical development notes describe experiments on one installation. Additional
local capture and deployment logs referenced by those notes are not part of the
source distribution; the two development captures required by regression tests
are included. Signing keys and generated firmware remain local.

A project license still needs to be selected before the public open-source
release. Vendored Monocypher retains its upstream license and attribution.
