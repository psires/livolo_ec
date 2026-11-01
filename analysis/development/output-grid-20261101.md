# Sonoff heartbeat-aligned output transactions, 0.12

The heartbeat-grid hypothesis is strongly supported for output control. In one
48-command ABBA comparison on the same Sonoff firmware/session, host-timed
control confirmed 12/24 requests, and MCU-aligned control confirmed 24/24.
This tests alignment and MCU ownership together; it does not independently
isolate phase, retry interval, or RX-to-cancellation latency.

| Channel | Host-timed confirmed | MCU-aligned confirmed |
| --- | ---: | ---: |
| 66780945, one-gang output 1 | 4/8 | 8/8 |
| 46715621, two-gang output 1 | 4/8 | 8/8 |
| 46715621, two-gang output 2 | 4/8 | 8/8 |
| All ON requests | 2/12 | 12/12 |
| All OFF requests | 10/12 | 12/12 |

Block outcomes were 7/12, 12/12, 12/12, 5/12. Both variants used the same
0.12 image, UART at 460800, token allocator, frame contents and six-attempt /
four-second bounds. The temporary host wrapper selected the old host-managed
path only for labelled unaligned requests. The normal service was restored
with MCU alignment enabled; its configuration bytes were unchanged.

## Nordic evidence

- Aligned: 48/48 reported successful transmissions captured; all 24 commands
  confirmed after two transmissions. No further output frame followed a valid
  matching report. One separately labelled cleanup request also confirmed in two
  transmissions and is excluded from the comparison counts.
- Aligned retry interval: median 200.013 ms, range 199.979–200.976 ms.
  Host-timed adjacent retry cluster: median 213.661 ms; one doubled captured gap
  is retained separately rather than assumed to be a delayed transmission.
- Absolute heartbeat-grid error: aligned median 0.057 ms, maximum 1.041 ms;
  47/48 within 1 ms. Host-timed median 47.858 ms; 0/119 within 1 ms.
  Grid period is estimated from the Nordic-observed local heartbeat period to
  account for independent oscillator rates.
- Original EC reinspection found 108/116 output frames within 1 ms and median
  absolute error 0.204 ms. The new timing now resembles that observed pattern.
- Aligned state reports arrived 11.460–16.284 ms after the last output frame,
  median 15.315 ms, consistent with the previously observed original network's
  native reports shortly after grid edges. This is timing evidence, not proof
  of the switch's internal receive-window implementation.
- Automatic ACK median delay was 1.857 ms versus 1.858 ms in the preceding
  0.11 experiment. One 4.679 ms outlier occurred when an old host-managed retry
  overlapped an incoming report (frames 1992–1994). ACK dispatch code was not
  changed or deliberately delayed.
- 737 UART requests, no missing replies/error replies; RX/TX logging drops,
  radio drops, UART errors, overruns, DMA errors and resynchronizations all zero.
- No original EC gateway heartbeats were captured. Its silence does not rule
  out other RF interference. Nordic was on the separate Pi at 10.1.1.120.

## Implementation and validation

`OUTPUT_GRID` carries a Pi-selected output template and previous state signature
into the shared volatile transaction slot. The MCU sends on live heartbeat
edges, skips slots missed by more than 1 ms, and stops on an exact matching fresh
native report. Busy-channel attempts consume the same six-attempt budget.
The retained result lets the daemon independently validate confirmation and
recover a state report even if bulk logging is lost. Network records and tokens
remain authoritative on the Pi. Session reset/cancellation stop execution.

Host regression suite: 116 passed. Scheduler suites: 62 passed, plus 26 output
suite tests (including inherited common scheduler cases), covering exhaustion,
busy-channel attempts, missed slots, stale/wrong reports, deadline boundaries,
cancellation, disarm and timer wrap. A firmware version-marker mismatch was
caught after the first flash, corrected, rebuilt and reflashed before the RF
comparison. A new regression check ties firmware, host and project versions.
Final firmware and host both report 0.12; Gecko 2.4.2 and its 115200 baud rate
were preserved. Only the Sonoff hardware was flashed in this experiment.

## Remaining limits and final state

This is a small sequential test, not a long-duration reliability guarantee.
Restoration requests can be same-state commands after a failed preceding ON.
Confirmation means a matching state report; physical lights were not separately
observed by the user during every command.

Both switches were restored to all OFF, as at the start, and the normal daemon
is active/enabled with no temporary systemd override. Pairing remains disabled.
The final unchanged paired state query to 66780945 timed out after three rounds /
six query frames; 46715621 confirmed OFF in one round / two frames. Output control
improved; read-only query reliability remains unresolved. No query timing,
query retry budget or automatic ACK policy was changed in this release.

Evidence: [machine-readable comparison](output-grid-20261101.json),
[raw Nordic capture](../../captured/development/output-grid-20261101-nrf52840.pcapng),
[reproducible analyzer](../../tools/analyze_output_grid.py), and
[UART contract](../../firmware/gateway/UART_PROTOCOL.md).
The supporting trial/controller/UART records are in `output-grid-20261101/`.
