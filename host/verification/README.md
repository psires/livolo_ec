# Daemon validation — 2026-10-17

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
