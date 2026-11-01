# Sonoff ZBDongle-E migration and UART stability — 2026-10-24

The development gateway now uses an original Sonoff ZBDongle-E, USB CP210x
`10c4:ea60`, serial `9006be7861f3ef118a30c41b6d9880ab`. Firmware and host remain
0.4; application UART is 460800, Gecko bootloader 2.4.2 remains at 115200.
The ZB-GW04 was unplugged before migration. Only the daemon's serial port changed;
the gateway ID, tag, device registry and MQTT settings were preserved.

## Board and recovery

`BOARD=sonoff-zbdongle-e` selects the verified USART0 PB1/PB0, HFXO CTUNE 128
profile and avoids driving the ZB-GW04 v1.2 RTS pin PD4. Both use
EFR32MG21A020F768IM32. Changing board or baud forces an object rebuild; unknown
boards fail the build. A compiled marker lets the GBL packager identify the
board instead of labelling every image ZB-GW04. Legacy images remain accepted
but are labelled `mg21-unspecified`.

RTS/DTR bootloader entry worked without buttons. Software `BOOTLOADER` entry
from our 460800 application and return from the 115200 Gecko menu also worked.
The menu prints `v2.04.02`, normalized to 2.4.2. The application-only GBL contains
no bootloader, Secure Engine or token writes, and programs only the bounded
application region starting at 0x4000. Full-flash readback is not exposed by the
serial bootloader. The original application was not identified by the Zigbee or
Thread probes used; this does not prove the flash was blank. No exact original
application/configuration backup was obtained. An official downloadable Sonoff
coordinator image was retained as a recovery option, explicitly not a readback.

Sources: [pinned board profile](https://github.com/darkxst/silabs-firmware-builder/blob/49fda29e1bc068b0a153bf5fd5899a58d04c01f8/manifests/sonoff/zbdonglee_zigbee_ncp.yaml),
[manufacturer recovery image](https://github.com/itead/Sonoff_Zigbee_Dongle_Firmware/blob/bc80aa33e82dbac9669d318e8922e01998618b43/Dongle-E/NCP/ncp-uart-sw_EZNet6.10.3_V1.0.1.gbl).

## Reproduced UART fault and correction

With the original UART implementation on Sonoff, all 1000 CAPS replies matched,
but four of 1000 long CHECK commands returned `ERR HEX`. The MCU recorded four
UART receive errors. A diagnostic build separated overrun/framing/parity flags.
Its configured repeat produced four `ERR HEX` replies, one response timeout and
six receive-overrun observations; framing and parity counters remained zero.
A first diagnostic attempt omitted volatile network setup after reset; its
`NOT_CONFIGURED` replies are a test-setup error and are not counted as corruption.

The MG21 UART writer disabled all interrupts while copying a complete outgoing
message into its software queue. This can prevent timely service of incoming
bytes at 460800. The correction keeps interrupts enabled while the main-loop
producer stages bytes, then publishes the queue head once, after a memory
barrier. The TX interrupt consumes only published bytes and is re-enabled after
publication, including when it drained the previous queue during the copy.
Compiled-code inspection confirms that the long `PRIMASK`/`cpsid` section was
removed. No radio/retry scheduling code changed; TI's UART implementation was
not changed.

The corrected build passed **6000 requests**: 3000 exact CAPS comparisons and
3000 long CHECK commands containing 125-byte non-EC payloads, without retries.
There were zero mismatches, timeouts, new UART errors, input errors, UART/RX/radio
drops or RF transmissions. Nordic recorded on the separate Pi during these
checks. The reusable `hardware_uart_integrity.py` tool supports this class of
non-transmitting check on a configured, disarmed adapter.

This isolates a firmware receive-overrun problem also reproducible on the
Sonoff. It does not explain or clear the earlier ZB-GW04 bulk-IN reply truncation;
the corrected firmware has not been hardware-tested on that removed adapter.

## Live radio and ten-minute observation

The corrected Sonoff firmware completed a **600.022-second** observation in one
UART session. UART RX errors, overruns, framing/parity errors, input errors,
UART/RX/radio drops, RX sequence gaps and ACK failures all had zero deltas.
All 48 automatic ACK transmissions succeeded. The service log contains no radio
reconnect. Final checks found the daemon active/enabled, MQTT connected and the
switch online; its last fresh state was OFF/OFF. A ten-minute run is a bounded
check, not long-term or multi-device qualification.

Twelve read-only state refreshes used the existing three rounds, 200 ms query
wait, four-second response wait and 14-second deadline. **Two confirmed and ten
were unconfirmed**; none had an unknown/session-failure outcome. Requests 4 and
10 confirmed in their first round with counters 21 and 22. No later query was
captured after either fresh state report. No output-setting commands were sent.

Nordic captured **63 of 64** reported query transmissions. Request 1's final
`ac` was not captured, so its final TX metadata cannot be independently matched;
the other eleven requests' latest TX metadata match the captured bytes. Median
query-pair spacing was **213.397 ms**, range 211.617–250.112 ms. All captured
query trailers matched heartbeat-relative RF age within **0.404 ms**. This run
did not attempt to match the previous 115200 spacing. The earlier 224 ms host-delay
experiment on ZB-GW04 was stopped by the user and remains incomplete.

Failed requests 1, 3, 5, 7, 8 and 11 had a native `fa/10` but no `fa/ac` or state
report; the other four failures had no captured query replies. This repeats the
RF acceptance problem despite accurate phase stamps and a clean UART session.
The adapter recorded 15 unsuccessful radio-send attempts over the whole soak;
aggregate counters do not identify each cause. The host `invalid_rx` count rose
5195; that counter combines malformed envelopes and unsupported/non-EC frames
and is not itself a UART-corruption count. Do not describe this as every
possible diagnostic remaining zero.

For the two counter-identified confirmations, Nordic timestamps were 14–22 ms
later than daemon reception timestamps. Request matching uses a 250 ms margin,
smaller than the three-second request gap; RF timing calculations use only
Nordic timestamps. The earlier ZB-GW04 runs differ in adapter and firmware, so
success-rate differences are not a controlled comparison of baud alone.

Evidence: [deployment and UART before/after results](../../host/verification/deployment-sonoff-20261024.json),
[per-request RF and soak records](sonoff-query-stability-20261024.json),
[Nordic capture](../../captured/development/sonoff-query-stability-20261024-nrf52840.pcapng).

## Build and preservation

The shared firmware suite passed 38 tests and the tools suite passed 42 tests,
including three packager tests and four integrity-harness tests.
The corrected code builds for Sonoff at 115200/460800 and ZB-GW04 v1.2 at 115200.
The initial Sonoff board build also passed 12 dry hardware checks, including all
256 peer slots, ACK lookup, leases, duplicate IDs, replay cache and session
replacement. The live test uses unchanged host sources and query limits.

Installed GBL SHA-256:
`b999bf4e3e9c128c20ec13cc058e1e889a3a5dc480d8487dc91c8cf02f73930e`.
Application binary SHA-256:
`b3d49a2120a1955d6a6e18c2cd070af749d0e34ceaadc9bdf9aa12c7495f9673`.

Pi sources, images, private configuration/database backups and test files:
`/home/sfires/livolo-ec/sonoff-20261024`. Installed image:
`firmware/mg21/build-fixed/ec-mg21.gbl` below that directory. Nordic capture files:
`/home/sfires/livolo-sniffer/sonoff-20261024` on `10.1.1.120`. Local private copies:
`.local/sonoff-20261024`. Firmware variants, source hashes and pre-fix failure
records are retained separately. Private configuration and databases stay
outside source control.
