# Local admission and output control — 2026-10-17

The spare switch `46715621` accepted our CC2652P gateway identity `2a992b12`
and network tag `a7`. Both outputs subsequently reported the commanded changes
with the original EC gateway unplugged. The user confirmed that only EC was
unplugged; the Pi and switch remained powered. No installed home switch was
admitted or controlled by our tools.

This is a single-device prototype with bounded host processes. It is not yet an
always-running replacement gateway or MQTT/Home Assistant integration.

## Admission

The user removed the spare from EC before putting it into pairing mode, with EC
pairing disabled. The first local controller sent five heartbeats, then exited
and disarmed after an explicit CCA collision on the next heartbeat. The saved
`local-gateway-join-01` evidence is retained. The host now retries only explicit
CCA collisions with bounded backoff; unknown transmit outcomes still abort.

The second controller registered the switch successfully. In
`local-gateway-handover-01-nrf52840.pcapng`:

| Frames | Observation |
| --- | --- |
| 1078–1107 | Native `9e/01` registrations, tag `ff` |
| 1108, 1109 | Local `8c/fa` registration responses, tag `a7` |
| 1111 → 1112 | Query `10`; native switch reply addressed to `2a992b12`, tag `a7` |
| 1115, 1117 → 1118 | Queries `ac` and native response |
| 1120–1146 | State and property reports, with local MCU ACKs |
| 1249 onward | Repeated device-specific keepalive exchanges |

Both identity and tag changed in native packets, not merely in relayed frames.
The property values remain `24→2`, `25→4`, `26→0`; their meanings and any older
four-gang software compatibility remain unproven. This was deregistration and
re-registration, not a confirmed factory-reset experiment. No `0b` exchange was
needed for this observed admission.

Within the 129.053-second common interior window, TI and Nordic recorded **28/28
identical native spare-switch frames**, including multiplicity. Whole-network
reception differs; Nordic also receives our own transmissions, which TI does not
log as RX. The UART-derived TI pcap uses host receipt timestamps. Only Nordic
timestamps are used for RF timing.

Fourteen independently matched direct request/ACK pairs have SFD-to-SFD
intervals of 1.868–1.892 ms, median 1.870 ms. Nordic missed one state/property ACK
which the MCU logged as transmitted; radio success counters alone therefore do
not substitute for over-air verification. The three-minute controller exited
cleanly and disarmed; its final log counters show no RX/UART drops.

## Output control with EC unplugged

The latest state before control was `2` (bit 0 off, bit 1 on). The tests restored
that state. Physical left/right button mapping and visual output observations
were not supplied, so the evidence here is the switch's own state reporting.

Frame references below use `local-gateway-control-01-nrf52840.pcapng`.

| Test | Mask/value | Token | Command frames | Reported state | ACK evidence |
| --- | --- | --- | --- | --- | --- |
| Output 1 on | `01/01` | `17` | 1248, 1262, 1265 | `3`, first report 1249 | Initial direct-only filter excluded broadcast reports |
| Output 1 off | `01/00` | `fd` | 1932 | `2`, reports 1939/1950 | Experimental host ACKs 1940/1951, about 24 ms |
| Output 2 off | `02/00` | `6e` | 3651, 3657 | `0`, report 3668 | Updated MCU ACK 3669, 1.871 ms |
| Output 2 on | `02/02` | `b3` | 4148, 4153, 4158 | `2`, report 4156 | Updated MCU ACK 4157, 1.870 ms |

The first command really changed the reported state, but the original host
confirmation predicate rejected its broadcast destination. Its JSON therefore
correctly records `confirmed: false` for that version of the host logic. Nordic
captured 47 copies of the unacknowledged state over about 15 seconds. Host replies
in the second test reduced this to two reports. With the updated MCU rule, the
third and fourth tests each produced one captured report. All command-related
reports have the corresponding transaction token and valid CRC.

The observed broadcast reports use tag `a7`, destination `ffffffff`, flags `13`,
and native source/subject `46715621`. MCU handling was extended only for opcode
`05` from the configured peer and tag. Broadcast announcements, keepalives and
registration requests still do not receive automatic replies. The change was
tested against wrong peers, tags, checksums, opcodes and destination forms before
flashing. A transient Linux-side broadcast ACK experiment is preserved in test 2;
the final control tool relies entirely on the MCU for ACKs.

Single-output commands preserved the other output's state. Command-to-first-
report intervals ranged from 22 to 502 ms. Retries keep the application token
and body while changing MAC sequence; UART buffering can cause a final retry
after a radio report has already appeared (test 4). These are idempotent set
commands, not toggle commands.

## Firmware, verification, and remaining work

UART transmit commands use a renewable lease and one-request response cache.
The MCU responds before logging, and UART output uses a nonblocking ring. Earlier
hardware checks verified lease expiry, parser bounds, wrong-peer rejection,
duplicate request handling and three independently captured diagnostic packets.
The current image includes the subsequently tested broadcast-state ACK rule:

- SHA-256: `02ff197a916e238cd175dfdbe4958cb97bb6e9e0ef489ad630e1d1322a184cd7`
- Full ROM CRC32 verified after flashing: `bb3a50cb`
- Factory CCFG unchanged, including bootloader/JTAG recovery configuration
- 28 offline tests pass, including the compiled MCU protocol implementation
- Final control status: disarmed, ACK disabled, RX/UART drops zero

The switch remains associated with the local identity/tag. No host daemon was
left running. The output-control tool can be invoked for bounded transactions.
Keep the stock-gateway absence distinct from full RF isolation: other installed
switches continued transmitting and relaying their own `b1` traffic. The spare's
native state replies and the Pi's ACKs were independently observed directly.

Remaining work includes sustained heartbeat/route recovery, broadcast keepalive
semantics, physical-button reports, pairing persistence through switch power
cycling, multi-device state, and a persistent MQTT bridge. The broadcast fallback
occurred during the outage/trial transition; these data do not isolate whether
missing local maintenance, the stock outage, or another discovery condition
triggered it. Direct control works in that state, but it is not proof of complete
mesh maintenance.

Evidence: `local-gateway-join-01/02.json` and UART logs,
`local-gateway-air-verification.json`, `local-gateway-join-02-comparison.json`,
`local-gateway-control-verification.json`, `local-set-01` through `local-set-04`
JSON/UART logs, and `gateway-v2-broadcast-ack-image.json`/flash log. Original
captures are preserved under `captured/`; new captures are in
`captured/development/`.
