# Experimental XIAO queue diagnostics

These diagnostics apply only to the experimental XIAO MG24 `xiao-queue-1` and
ESP32-C6 `xiao-usb-2` builds and their `xiao-uart-1` / `xiao-rxdiag-1`
successors. They do not enable gateway operation, pairing,
automatic acknowledgements or firmware updates. Existing radio platforms and
Home Assistant are outside the bench procedure.

## Reading queue evidence

`CAPS queue_diag=ring_v1` identifies the new read-only `RFQUEUE` command. Send it
through the normal numbered request protocol after RFSTOP, draining all event
lines first. An active, unquiesced or undrained core returns
`ERR RF_QUEUE_NOT_IDLE`. A latched fault does not prevent observation when the
adapter has positively confirmed a stop and all core events have drained.

| Field | Meaning |
| --- | --- |
| `rx_queue_full` | Accepted receive records rejected because the portable ring was full. |
| `rx_enqueued` | Records published into the portable ring. |
| `rx_dequeued` | Records completely formatted into the application's output buffer. |
| `rx_high_water` | Largest admitted occupancy observed by the producer, including its new record. |
| `rx_max_age_us` | Maximum adapter-timestamp-to-successful-format interval, modulo 2³² microseconds. |
| `queue_capacity` | Seven usable records; unchanged from the preceding builds. |

Occurrence counters use 32-bit unsigned wrapping arithmetic. Setup or a
successful RFSTART/RFLISTEN resets the queue evidence; STOP, failed starts,
undersized output buffers and repeated queries preserve it. Starting a new
session with undrained receive records is refused instead of discarding those
records. The response declares `counter_bits=32`, `counter_mode=wrapping` and
`scope=last_successful_start`.

High-water is a producer-observed snapshot, not a synchronized measurement of
the hardware FIFO or concurrent queue occupancy. Age declares
`age_basis=adapter_timestamp age_clock=mod32`; C6 timestamps originate in its
receive callback, whereas MG24 timestamps originate in adapter polling. Age
excludes pending serial output and requires fewer than 2³² microseconds between
the adapter timestamp and formatting. Neither metric is on-air timing.

`rx_dequeued` does not prove host delivery. After a clean stop, independently
count complete delivered receive lines and reconcile, modulo 2³²:

```text
RFSTATUS.rx = RFQUEUE.rx_enqueued + RFQUEUE.rx_queue_full
RFQUEUE.rx_enqueued = RFQUEUE.rx_dequeued = delivered receive lines
```

The existing `dropped` total retains its meaning. Queue-full counts are one
component; adapter rejection occurrences are separate evidence and must not be
silently discarded. In particular, MG24 can report overlapping hardware event
reasons, so their sum is not universally a lost-packet count. Zero reported C6
drops does not establish zero hardware acquisition or CRC rejections.

## Output scheduling

C6 retains one output buffer and preserves its unwritten suffix. A pending RF
event suppresses the otherwise ten-millisecond input wait. A callback can race
this hint, so it does not guarantee zero input-wait latency. Command replies
and ready RF records alternate under competing load; input advances in bounded
batches between complete output records.

Successful output work yields to the RTOS after at most four complete records,
1,024 accepted bytes or sixteen write attempts. Each write is clamped to the
remaining byte budget. Zero/error writes yield immediately, and the existing
256-byte input-work limit remains. RF expiry is serviced throughout, including
when output is blocked. USB buffer sizes, watchdog policy, stack size and radio
queue capacity are unchanged.

MG24 continues transmitting diagnostic text through its 115200-baud UART.
The `xiao-uart-1` candidate moves byte service to a bounded TX interrupt while
the main loop continues radio polling; its ownership and fairness contract is
described in the [board notes](../firmware/mg24/README.md).
Queue telemetry makes burst pressure visible; it does not remove that physical
output limit. Neither board is qualified for pairing or control by a host-only
test or by a transmission-completion report alone.

## Offline regression checks

Run these checks without opening serial ports or accessing radio hardware:

```sh
make -C firmware/experimental test
make -C firmware/mg24/tests test
make -C firmware/esp32c6/tests test
PYTHONPATH=tools python -m unittest test_xiao_rf_bench test_xiao_rx_bench test_xiao_queue_bench
```

The native tests exercise queue saturation, wraparound, stop/drain behavior,
failed starts and undersized output buffers. The C6 tests execute the actual
application loop with simulated USB and radio drivers, checking partial and
blocked writes, command/event fairness, bounded yields and lease expiry during
blocked output. Host tests cover capability negotiation, malformed evidence,
legacy images and independent delivered-event accounting. These checks protect
the software contracts; target USB behavior and RF capture still require bench
measurements.
