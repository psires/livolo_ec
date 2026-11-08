# Experimental XIAO receive handoff diagnostics

MG24 `xiao-uart-1` and ESP32-C6 `xiao-rxdiag-1` advertise
`adapter_diag=handoff_v1`. These experimental images provide receive-path
evidence; they do not enable pairing, control or automatic acknowledgements.
The production daemon continues to refuse them as gateways.

## Reading a snapshot

Send numbered `RFADAPTER` requests after `RFSTOP` has positively confirmed
quiescence and all core events have drained. Initial inactive setup also permits
a snapshot. An active, busy, unquiesced or undrained core returns
`ERR RF_ADAPTER_NOT_IDLE`; an adapter without the hook returns
`ERR RF_ADAPTER_UNSUPPORTED`. A latched fault permits observation only after
confirmed stop and event drain. The snapshot callback only reads counters;
the RF command dispatcher retains its ordinary service poll before dispatch.

Each reply declares `counter_bits=32 counter_mode=wrapping
scope=last_start_attempt`. Counts initialize to zero at boot and reset before each
adapter start attempt, after channel validation. STOP and reads preserve them.
A failed start can therefore reset these counts while
[RFQUEUE](XIAO_QUEUE_DIAGNOSTICS.md) still describes the preceding successful
start. Compare counters only when their epochs have been established.

## MG24 counters

| Field | Counted occurrence |
| --- | --- |
| `rx_event` | Existing RAIL received-packet event bit observed by the callback. |
| `hold_ok` | Existing packet-hold call returned a valid handle. |
| `copy_calls` | Existing packet-copy call completed; the SDK API has no success result. |
| `release_ok` | Existing packet-release call returned success. |
| `release_fail` | Existing packet-release call returned an error. |
| `core_calls` | Attempts to forward a validated packet to the shared receive core. |
| `poll_budget` | Poll passes that consumed their four-handle service budget. |

`poll_budget` does not peek for a fifth packet or prove a backlog. These counters
do not change holding, copying, releasing, validation or aggregate drop policy.
The private rejected-frame build does not expose this capability.

For the ordinary diagnostic build, source branch accounting gives these
identities, modulo 2³², using reason fields from `RFHEALTH`:

```text
rx_event = hold_ok + rx_hold_failed
copy_calls = core_calls + rx_packet_layout_invalid
release_ok + release_fail = copy_calls + rx_packet_status_invalid
                          + rx_packet_length_invalid + rx_packet_details_failed
```

There is no general `hold_ok = release_ok` rule. Preserve any difference for
investigation; it is not a measurement of FIFO occupancy or lost packets.

## ESP32-C6 counters

| Field | Counted occurrence |
| --- | --- |
| `sfd_event` | SDK receive-SFD callback invocation. |
| `rx_done` | Receive-done callback entry. |
| `rx_invalid` | Existing receive argument or PHR validation rejected the callback. |
| `rx_null` | Invalid callback whose frame pointer was null; a subset of `rx_invalid`. |
| `rx_release_ok`, `rx_release_fail` | Results of existing receive-buffer release calls. |
| `ack_release_ok`, `ack_release_fail` | Results of existing release calls in the unexpected-ACK callback. |
| `core_calls` | Attempts to forward a validated packet to the shared receive core. |

The SFD hook increments a lock-free counter without reading the clock. It uses
the SDK's existing event dispatch; event masks, PHY, CRC policy and receive
buffer management remain unchanged. No additional release or retry is added.
Callback branch accounting gives these identities, modulo 2³²:

```text
rx_done = core_calls + rx_invalid
rx_release_ok + rx_release_fail = rx_done - rx_null
```

SFD indications are not unique packets, CRC failures or an on-air transmission
denominator. Do not require `sfd_event = rx_done`. Original received FCS bytes
remain unavailable on C6 because its SDK substitutes RSSI/LQI metadata.

## Qualification and limits

Public bench runners negotiate the capability and retain explicit absence for
legacy images. They collect queue and adapter snapshots independently after
confirmed stop, so a malformed queue reply cannot hide adapter evidence.
Malformed fields, unknown advertised versions, release failures or inconsistent
callback accounting fail validation. For an aligned successful window,
`core_calls` must equal `RFSTATUS.rx`; a mismatch remains a failed observation,
including at a stop boundary.

Sustained qualification additionally requires a fresh zero baseline or a
verified prior receipt, and exact stopped-state continuity before the next
window. Unexpected changes cannot be erased by starting another window.
Counter consistency and zero portable queue loss do not establish complete RF
capture. Keep raw lines, independently reconcile delivered records and FCS where
available, and retain failed windows without retrying them into a passing result.

Offline regression checks open no radio ports:

```sh
make -C firmware/experimental test
make -C firmware/mg24/tests test
make -C firmware/esp32c6/tests test
PYTHONPATH=tools python -m unittest test_xiao_rf_bench test_xiao_rx_bench test_xiao_queue_bench test_xiao_adapter_bench
```
