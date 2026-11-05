# Livolo EC daemon

Native Linux service and maintenance commands for Livolo EC UART v3 radios.
Provides a local web interface, authenticated API, optional MQTT and persistent
device registration. Project-owned code is BSD-2-Clause.

The daemon version follows the radio compatibility version, currently 0.13.
The standalone distribution has its own version, 0.3.0. See the
[standalone installation guide](https://github.com/psires/livolo_ec/blob/main/host/release/README.md)
for configuration, systemd setup, upgrades and emergency maintenance.

Commands: `livolod`, `livolod-firmware-manage`, and the low-level
`livolod-firmware` tool. Firmware maintenance requires the matching recovery
procedure and an explicitly qualified board-specific payload. The distributed
firmware catalog is empty. No radio firmware or third-party tools are embedded
in this Python package.
