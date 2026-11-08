# Experimental XIAO C6 USB lifecycle comparison

This diagnostic compares two Linux host open sequences on the new
XIAO ESP32-C6. The qualified combined operation is now used by the experimental
RF and passive RX benches; the production daemon remains unchanged.
The host candidate does not qualify a board for pairing, control or firmware
publication. The original intermittent USB stall is a separate investigation.

## Why compare these sequences

The [ESP32-C6 Technical Reference Manual](https://documentation.espressif.com/esp32-c6_technical_reference_manual_en.pdf),
version 1.2, chapter 32, page 1035, table 32.4-1 identifies a transition that
clears DTR while RTS remains set as a SoC reset. Table 32.4-2 describes resetting
into flash execution using RTS set with DTR clear. This is the C6 native USB
Serial/JTAG peripheral; external USB-to-UART bridge wiring is not the basis of
this analysis. Page 1031 describes hardware recognition of these control
requests. No firmware reset-disable register changes are proposed here.

The inspected PDF SHA256 is
`e08d5b61ebcfa2a78f9735e3f721817cb1dc2df9cf05096d2f0399eaba9f1734`.
The official URL can change; retain the downloaded version with private evidence.

[pyserial 3.5 POSIX source](https://github.com/pyserial/pyserial/blob/v3.5/serial/serialposix.py)
opens the descriptor, takes its advisory exclusive lock, configures termios,
updates DTR and RTS separately, then flushes input. With both desired states
false, the two operations are `TIOCMBIC(DTR)` then `TIOCMBIC(RTS)`.
Its source SHA256 on the bench Pi and the offline test environment matches:
`5d56f98513391e1766a1847a04e2e1202ced4172a152803ba1c823a149a9b6f9`.

The [pinned Raspberry Pi reference CDC driver](https://github.com/raspberrypi/linux/blob/31a34885388835dce2460744d7961f2b9e7dc416/drivers/usb/class/cdc-acm.c)
sets both control bits in `acm_port_dtr_rts(active=true)`. Its
`acm_tty_tiocmset` computes a combined new state and issues a CDC control request
when that state changes. This supplies a mechanism for the intermediate state
above. This reference revision has **not** been established as the exact source
of the installed `6.18.50+rpt-rpi-2712` kernel, package
`1:6.18.50-1+rpt1`; kernel-internal initial state is an inference, not a userspace
trace observation.

The same revision's [TTY core](https://github.com/raspberrypi/linux/blob/31a34885388835dce2460744d7961f2b9e7dc416/drivers/tty/tty_port.c)
calls `tty_port_raise_dtr_rts` for a nonblocking open at nonzero baud in
`tty_port_block_til_ready` (lines 521–526). Its shutdown path lowers both lines
when HUPCL is set (lines 334–341). These reference paths explain why clearing
HUPCL alone would not remove the open-time sequence; this experiment leaves
HUPCL unchanged.

The November 8 stock trace directly shows C6 open at `1794096163.394335`,
termios preserving HUPCL, DTR clear at `.396401`, and RTS clear at `.398209`.
Close at `1794096173.202595` has no preceding explicit modem-control ioctl.
Kernel actions inside open/close and actual USB control transfers are not
visible in this syscall trace. Earlier close/reopen measurements showed uptime
restarting near 31–41 ms with reset reason 11, including a five-second closed
gap. These observations do not alone identify a unique reset edge.

## Candidate and boundaries

[C6Serial](../tools/xiao_c6_serial.py) requires an explicit `stock` or `combined`
mode, Linux, pyserial 3.5, the exact operator-supplied USB serial, matching C6
VID/PID, and an explicit `/dev/serial/by-id` path. It verifies metadata before
every open. Both modes use the same fixed 115200 8N1 configuration, no flow
control, false DTR/RTS, nonblocking reads, 0.25-second write timeout and exclusive
advisory lock. It rejects changes to those settings before open.

Combined mode replaces the two line operations with one
`TIOCMBIC(DTR|RTS)`, keeping pyserial's desired states false. It does not change
HUPCL, close, input flushing or framing. A rejected combined ioctl aborts and
closes, including EINVAL/ENOTTY that stock pyserial normally ignores. There is no
fallback, retry, reset pulse or extra newline. Later DTR/RTS changes are rejected
before the internal state changes. This helper serves the fixed probe and
qualified XIAO benches, not a general-purpose mutable serial API.

One host ioctl removes the intermediate userspace-requested state. It does not
guarantee electrical simultaneity, a USB transfer if state is unchanged, or a
reset-free kernel open/close. Advisory locking happens **after** OS open, so
exclusive operator ownership and known RF-off state must be established before
the process starts. Metadata checks do not eliminate hotplug races. Do not run
against an active device, other C6 boards, existing radios or HA services.

## Bounded operator procedure

ZigBee Specialist is the sole hardware operator. Source and experiment review
must precede execution. Keep the installed `xiao-rx-1` C6 firmware unchanged
through this comparison; MG24 remains closed. Run each arm once, stock first,
then combined. Inspect a failed arm before proceeding; never automatically
repeat a failed run or reopen stock after the combined arm.

The [probe](../tools/xiao_c6_serial_probe.py) takes `--port`, `--serial`,
`--mode stock|combined` and a new `--output` JSON path. It performs exactly three
explicit opens, seven seconds of same-open polling per cycle and five seconds
closed between cycles. Only PING, VERSION, CAPS, RFSTATUS and RFHEALTH are
allowed. It checks the exact diagnostic build and requires RF inactive, idle,
unfaulted and with no queued packets. Each request has a maximum two-second
budget; the overall diagnostic schedule has a 75-second deadline. OS open/close
calls themselves remain subject to kernel behavior rather than a hard watchdog.

Example shape, using the separately verified private path and serial:

```sh
strace -ff -ttt -T -yy -s 256 \
  -e trace=openat,close,ioctl,flock,read,write,poll,ppoll,select,pselect6 \
  -o /private/evidence/c6-stock.strace \
  python tools/xiao_c6_serial_probe.py \
  --port "$C6_BENCH_PATH" --serial "$C6_BENCH_SERIAL" \
  --mode stock --output /private/evidence/c6-stock.json
```

Use a distinct trace prefix and receipt for the separately reviewed combined
arm. Strace introduces timing overhead; label both arms as traced. Do not change
firmware, termios, USB topology or other parameters between them. No RF starts,
transmissions, switch actions, flash operations, bus resets or usbmon setup are
part of this procedure.

Receipts preserve lifecycle timestamps, numbered request/reply text, raw
received lines with delivery timestamps, incomplete trailing bytes, and health
before/after each hold. Unknown/ROM lines observed before a matching response
remain in the transcript. Bytes discarded by pyserial's normal initial input
flush cannot be recovered by the probe; absence of boot text is not continuity
evidence. Command failure retains partial evidence, closes once and stops the
run without another open. Exit success means collection completed, not that the
chip avoided reset or that USB stability is qualified.

Compare raw `uptime_ms` modulo 2^32 against host elapsed time, both within each
hold and across each closed gap. Retain `reset_reason`, watchdog/error/pending
counters and every failed receipt. A five-second closed gap followed by near-zero
uptime is evidence of a reset near reopen; continuous uptime with a matching
elapsed delta supports continuity across the tested interval. A persistent
reset-reason value describes the last boot, not a new reset by itself. Review
the actual ioctl sequence for every open. Report these results separately from
the original stall and from RF reception outcomes.

## November 8 comparison outcome

Both arms completed on the same Pi and unchanged C6 `xiao-rx-1` firmware.
Stock opens each started at 41 ms uptime and ended at 7051 ms. Combined opens
recorded these before/after uptime pairs:

| Open | Before hold (ms) | After hold (ms) |
| --- | ---: | ---: |
| 1 | 75011 | 82021 |
| 2 | 87081 | 94091 |
| 3 | 99151 | 106161 |

Across the two closed gaps, MCU uptime advanced 5060 ms each; host elapsed time
was 5060.459 and 5060.585 ms. All watchdog, read-error, write-error and pending
counters were zero. Each combined open had exactly one combined-mask ioctl in
the trace. Reset reason remained 11, consistent with the earlier stock reset;
it did not imply a fresh reset during the combined arm.

These results support the combined operation for the tested reopen-reset issue.
They do not resolve the original intermittent stall or establish universal USB
stability. `configured_port` now routes only C6 through combined mode in both
bench CLIs, with identity revalidation and no stock fallback; MG24 settings and
the production daemon are unchanged. Stock remains explicitly selectable only
in the comparison probe.

## Offline checks

```sh
PYTHONPATH=tools python -m unittest \
  test_xiao_c6_serial test_xiao_c6_serial_probe -q
```

Lifecycle tests execute real pyserial 3.5 open, termios and close paths with OS
boundaries mocked. They cover ordering, HUPCL preservation, identity and policy
rejection, advisory-lock failure, ioctl failures, descriptor cleanup and explicit
reopen. Probe fixtures cover fixed counts/waits, read-only commands, early
identity/busy failures, partial transcripts and no recovery after timeout.
