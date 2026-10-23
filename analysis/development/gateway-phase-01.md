# Volatile timing extension and independent probe validation

The original EC reference established a timing trailer that changes with elapsed
time since the gateway heartbeat. An opt-in firmware extension now computes that
trailer immediately before the radio call. Network identity, peers and output
state remain host-owned; the module retains only a volatile timing reference.
The normal raw SEND path and ACK rules remain compatible with UART v3.

## Implementation and limits

`SEND_PHASE` accepts only guarded direct peer `8c/02` frames. `PHASE_PROBE`
constructs a self-addressed 38-byte non-EC diagnostic. Both expose the exact
stamped bytes and entry/reference/return ticks. The capability is explicitly
`tx_entry_v1`: timestamp sampling precedes actual CCA and RF scheduling and is
not a hardware SFD timestamp. No fitted offset or oscillator correction is used.

Only a successfully sent own heartbeat establishes a reference. Reference expiry,
session/configuration replacement, DISARM and lease expiry prevent stale use.
An exact repeated UART request returns its cached result without another RF send.
The conversion uses integer scaling by 65500, independently of the timer's
65536 Hz frequency. The complete contract is in `firmware/gateway/UART_PROTOCOL.md`.

Eighteen tests compile and execute the actual firmware command parser with a
deterministic radio, including phase vectors, timer wrap, CCA/error behavior,
reference lifecycle, frame restrictions and UART replay. The existing 28
protocol/capture tests also pass. The host owner separately verified 14
phase-client/helper tests before hardware execution.

## Build and controlled handoff

- Image SHA-256: `c15d9b87cdec11761526cbac674eff3732b26b755251b23318a9ed315fe2a69f`
- Full-image ROM CRC32: `f7c0613b`, verified after programming
- Size: 360448 bytes; ELF text/data/BSS: 48198 / 1120 / 14216 bytes
- Factory CCFG byte-for-byte unchanged; DIO15 recovery word `c5fe0fc5`
- Rollback: `/home/sfires/livolo-ec/backups/ec-gateway-v3-pre-phase.bin`, verified
  SHA-256 `a21fb791f2e73b7e7e83d4e99b4361f63368d9be4958dce1f61116597488802c`

The host owner backed up SQLite to
`/var/lib/livolod/backups/before-phase-probe01-20261017.sqlite3`, stopped the
daemon and verified no UART owners before release. The firmware owner flashed,
verified, recorded Nordic traffic and ran the isolated host diagnostic helper.
The helper read the registry without modifying it, restored the same gateway,
tag and peer, and sent only two own heartbeats and fourteen self probes.
Neither device output commands nor device queries were issued.

The helper completed in 22.519 seconds. Missing-reference and expired-reference
requests were rejected; one successful probe request was replayed with its exact
UART ID and bytes. Cleanup verified `armed=0`, `ack=0`, `lease=0`, and
`phase_valid=0`, then closed UART. No RX/UART drops or cleanup errors occurred.

The host owner then restarted unchanged release 04 with the same database and
verified radio/MQTT readiness, peer/name preservation and a fresh session. Its
cached state remained both OFF, counter 44, token `0b`; that retained timestamp
is not a fresh physical-state observation after restart. The daemon owns UART
again. Nordic has stopped. Phase-based output control remains disabled.

## Independent radio measurements

Capture: `captured/development/gateway-phase-probe-01-nrf52840.pcapng`.
All fourteen unique probe nonces appear exactly once, including the replayed
UART request. Both helper heartbeat frames match exactly. No local `8c/02`
output frame appears anywhere in the capture.

| Comparison with Nordic heartbeat-to-probe SFD interval | Minimum | Median | Maximum |
| --- | --- | --- | --- |
| Firmware entry-age interval | 3.338 µs | 259.399 µs | 809.431 µs |
| Encoded trailer phase | 18.245 µs | 261.840 µs | 815.544 µs |

Actual probe phases span approximately 0.195–9.795 seconds. Radio-call durations
are 1.801–1.831 ms. A descriptive fit gives relative clock drift 82.16 ppm,
offset 1.36 µs and remaining RMS residual 9.86 µs. No correction was applied to
the measurements or firmware. Absolute SFD-clock calibration is not established.

The sub-millisecond objective is met in this bounded run. There were no CCA
failures, so contention-induced scheduling tails are not characterized. The
38-byte diagnostic is not a 41-byte device-command acceptance test. A future
explicitly enabled, captured output experiment must retain these distinctions.

Evidence: `gateway-phase-candidate-01-image.json`, `gateway-phase-01-flash.log`,
`gateway-phase-probe-01.json` (complete UART/helper record), and
`gateway-phase-probe-01-air.json` (per-packet independent comparison).
