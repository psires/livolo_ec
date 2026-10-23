# Non-actuating phase measurement handoff

Local helper: `host/tools/check_phase_hardware.py`. Its 14 phase/helper tests pass
(0.067 seconds). The helper is separate from daemon installation; it does not use
the pending six-attempt service candidate or enable runtime SEND_PHASE.

## Approved bounded sequence

1. GUI coordinates the handoff. Host owner makes a SQLite backup, stops release 04
   and explicitly releases UART ownership. Firmware owner preserves/verifies the
   installed v3 rollback image before flashing the reviewed candidate image
   SHA-256 `c15d9b87cdec11761526cbac674eff3732b26b755251b23318a9ed315fe2a69f`.
2. Firmware owner starts Nordic capture before running this helper. Helper requires
   `livolod.service` inactive and opens UART exclusively. It reads the live registry
   through SQLite read-only mode, restores a fresh session and the same peers,
   verifies peer records, then enables a 60-second lease and native ACKs.
3. Confirm a probe with no heartbeat reference returns `ERR PHASE_REFERENCE`.
4. Run two normal heartbeat cycles, with seven self-addressed probes per cycle
   requested at 0.2, 0.9, 1.1, 3.25, 5.812, 8.5 and 9.8 seconds from the host's
   heartbeat request start. These are scheduling targets, not claimed RF phases.
   The next heartbeat starts at least ten seconds after successful completion of
   the preceding heartbeat command, conservatively preserving entry spacing.
5. Immediately replay the first successful probe's exact UART ID and bytes, with
   no intervening command. Require an identical cached reply; independently verify
   only one RF packet with that nonce in Nordic. Do not infer no retransmission
   from identical UART output alone.
6. After the final heartbeat command completion plus 12.3 seconds, verify STATUS
   reports no valid reference and another probe rejects without RF transmission.
7. DISARM, verify armed/ACK state is zero, close UART and save all records. Firmware
   owner explicitly releases UART. Host owner restarts the unchanged release-04
   daemon with the same database/config and verifies radio/MQTT/session recovery.

Only raw own heartbeats and self-addressed 38-byte non-EC probes may be transmitted
by the helper. An explicit guard rejects SEND_PHASE, device output frames and
device queries. No pairing commands, database writes, service stop/start or flash
operations are performed by the helper itself.

Heartbeat CCA busy permits at most three attempts with short bounded waits; unknown
outcomes abort. Busy probe results are retained without extra RF retries. The
measurement sequence has a 45-second cap, normally about 22.3 seconds plus UART
latency; cleanup follows before the 60-second lease expires under normal transport
timeouts. A failed cleanup is recorded; the MCU lease remains the independent
fallback. The evidence file is written even if the measurement or cleanup fails.

## Invocation after staging and hardware-owner release

Stage the helper archive in `/home/sfires/livolo-phase-probe-helper-01`, preserving
its `host/` layout. Do not install it into `/opt/livolod`.

```sh
sudo env PYTHONPATH=/home/sfires/livolo-phase-probe-helper-01/host \
  /opt/livolod/bin/python \
  /home/sfires/livolo-phase-probe-helper-01/host/tools/check_phase_hardware.py \
  --config /etc/livolod/config.json \
  --output /tmp/livolo-phase-probe-01.json
```

The output path must be new. Evidence includes all command IDs, host timestamps,
raw UART replies/errors, RX/ACK/status events, requested phases, exact stamped
bytes, entry/return/anchor ticks and final DISARM status. Compare actual captured
heartbeat/probe intervals against `age_ticks / rtimer_hz` and trailer phase; report
residual distribution, radio-call durations and busy attempts. This measures
TX-entry accuracy, not SFD timestamps, and grants no output-control enablement.

## Handoff record

The reviewed helper archive was staged on the Pi at
`/tmp/livolo-phase-probe-helper-01.tar.gz`, SHA-256
`20aabf36c132fd029ee529239d10e6ef40b6b79453d98374fb3a951d0e001975`.
Helper source SHA-256:
`a536400e16bdf55859a99fdac6c628af2c04b9ed6546d486627e9207fe7d26fd`.

SQLite backup: `/var/lib/livolod/backups/before-phase-probe01-20261017.sqlite3`,
mode 0600, owned by livolod. Before stop, radio/MQTT were ready and the saved
native state was bits 0, counter 44, token 0b. The service then reported inactive
with MainPID 0; a privileged `/proc` scan found no remaining UART file descriptors.
Ownership was explicitly handed to the firmware chat. The run completed in
22.519 seconds: 14 probes and two heartbeats transmitted successfully, missing and
expired references rejected, and the immediate cached replay returned identically.
There were no cleanup errors or reported RX/UART drops; final lease/armed/ACK/phase
reference were disabled. See [probe evidence](phase-probe01-2026-10-17.json).

After explicit UART release, the unchanged release-04 daemon was restarted.
Installed source hashes, radio readiness and MQTT connectivity were verified,
with a fresh session and the same registry/database. The saved OFF state retained
counter 44, token 0b and its original timestamp; the immediate snapshot had not
received a fresh state packet. The firmware owner reports the reviewed candidate
image hash above, verified after flashing. Phase controls remain disabled and no
pending host candidate was installed. See
[restoration verification](restored-after-phase-probe01-2026-10-17.json).

UART ownership is back with livolod. Independent Nordic analysis captured all
14 probes exactly once, including the nonce whose UART request was replayed,
matched both helper heartbeats, and found zero local 8c02 output frames.
Entry-age versus RF interval residual was 3.338–809.431 microseconds (median
259.399); encoded-trailer versus RF residual was 18.245–815.544 microseconds
(median 261.840), across roughly 0.195–9.795-second phases. Residual increased
with phase, consistent with relative clock drift; no compensation was applied.
Radio-call duration was 1.8005–1.8311 ms.

This bounded run met a sub-millisecond timing target. No CCA failures occurred,
so contention-tail behavior is unmeasured. Self-addressed 38-byte probe accuracy
does not demonstrate 41-byte device command acceptance or reliable output control.
Phase controls remain disabled pending review of a separate bounded experiment.
Evidence: `analysis/development/gateway-phase-probe-01-air.json` and
`captured/development/gateway-phase-probe-01-nrf52840.pcapng`.
