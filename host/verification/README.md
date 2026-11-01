# Daemon validation

## Source finalization — 2026-11-01

The host suite passed all 140 tests on Python 3.9.6. The simulated non-root
startup check passed for the web UI, authenticated native API, SQLite and clean
SIGTERM shutdown, with MQTT disabled and no radio or broker. This smoke check
ran as a local process, not in a newly built container.

The daemon wheel built successfully and contains the web assets, native API,
ingress module and a BSD-2-Clause license identical to the repository license.
Owned Python sources passed the function documentation and Black 24.8.0 checks.
An AST comparison excluding docstrings confirmed that finalization's Python
documentation and formatting edits preserved behavior.

The [original ARM64 Docker build record](docker-arm64-0.12-20261101.json) and
[native API development record](native-api-20261101.json) retain their original
source hashes and scope. They describe earlier snapshots, not the finalized
source. The original 0.12 Docker archive was not rebuilt or replaced. Historical
reports below and in deployment notes may reference additional local logs that
are intentionally not part of the source distribution. No hardware, firmware or
live deployment was changed during source finalization.

## Initial hardware validation — 2026-10-17

The development Pi ran a bounded 25-second daemon session against the installed
UART v3 firmware, using the known local gateway `2a992b12`, tag `a7`, and switch
`46715621`. Source was copied to a temporary directory; no system service was
installed or enabled.

The [hardware report](hardware-2026-10-17.json) records every host command/reply
and published event. The daemon verified capabilities and the restored peer table,
enabled its lease and MCU ACKs, renewed the lease twice, sent three heartbeats
and the observed read-only `10`/`ac` queries. The module reported 13 successful
transmissions, including eight successful automatic ACKs. RX/UART/meta drop and
input-error counters remained zero.

The daemon received a valid switch state report: bits `1`, output 1 on, output 2
off, counter 35, token `b3`, RSSI -42, LQI 62. That agrees with the state reported
by the firmware chat before the test. **No output-control or pairing commands
were sent.** The final independent STATUS read confirmed `armed=0`, `ack=0`,
`lease=0`. UART ownership was then released.

This hardware check validates actual UART framing, restoration, heartbeat/lease
operation, report decoding and graceful disarm. It does not independently verify
over-air timing, new-device admission, multiple physical switches, MQTT against a
production broker, or output actuation by this daemon. Those paths are covered
only by prior protocol experiments and the daemon's automated tests as applicable.

Automated validation uses Python 3.9 locally, Paho 2.1.0 and pySerial 3.5. The Pi
uses Python 3.13.5 and pySerial 3.5. Tests include a real Paho client over loopback
TCP against a small test broker, real POSIX pySerial/PTTY ownership and fragmented
UART traffic, and a simulated v3 adapter for application behavior. The simulator
does not model radio timing, propagation, routing or arbitrary switch firmware.
