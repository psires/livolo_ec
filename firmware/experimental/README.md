# Experimental transport and raw-radio bring-up

This is a shared diagnostic parser for new MCU targets, not a functioning
Livolo gateway. It uses UART v3 line framing but implements only PING, VERSION,
CAPS and STATUS. Without a radio hook, every other command returns
`ERR UNSUPPORTED`. RF-enabled builds additionally expose strictly bounded
`RFSTART`, `RFSTATUS`, `RFTX` and `RFSTOP` diagnostics described in the
[bench contract](../../docs/XIAO_RF_BENCH.md), plus fixed-channel passive listening
and optional read-only board health. No production raw-send, network,
pairing, persistent state, update or request-cache implementation exists.
It advertises zero peers, ACK mask, lease, session and raw-frame capacity so the
real daemon refuses readiness before sending NETWORK or ARM. Unknown experimental
fields alone would not prevent the daemon from becoming ready.

Commands are `<nonzero decimal uint32> COMMAND\n`, with responses
`RSP <id> OK ...\n` or `RSP <id> ERR ...\n`. Input over 319 characters, control
bytes and malformed IDs are discarded; damaged lines resynchronize at newline.
Board/build tokens are bounded and validated, and undersized output storage
never emits a partial response. Ordinary diagnostic replies are side-effect-free.
RF commands can start/stop a temporary radio window or admit a single synthetic
transmission. Requests are not cached: never replay RFTX after a lost response.

`RFLISTEN <nonce>` accepts the same nonzero eight-character lowercase hexadecimal
nonce as RFSTART, but opens a **60-second channel-11 receive-only window**.
Automatic ACKs remain disabled; every RFTX is rejected in passive mode, with an
additional channel-26 guard in each board's send function. RFSTART still opens
only the channel-26 synthetic window. Stop the current window before changing
modes. RFSTATUS reports `mode=passive|synthetic` and the last selected channel,
retaining both after STOP or expiry; startup is inactive with channel 26 selected.
CAPS identifies the receive-only contract with `rx_diag=passive_v1` and the build
tokens are `xiao-usb-2` on C6 and `xiao-queue-1` on MG24. Production gateway
capabilities remain zero.

`queue_diag=ring_v1` additionally advertises read-only `RFQUEUE` after verified
stop and complete core event drain. It reports explicit portable-ring overflow,
admitted/formatted counts, producer-observed occupancy and adapter-timestamp
age without changing the seven usable receive slots. STOP and failed starts
preserve this evidence; a new start refuses undrained records. See the
[queue contract](../../docs/XIAO_QUEUE_DIAGNOSTICS.md) for counter scopes,
wrapping and host-delivery requirements.

`RFHEALTH` never starts RF. C6 reports USB, reset and watchdog diagnostics in its
[board notes](../esp32c6/README.md); MG24 reports receive-discard reason occurrences
as described in its [board notes](../mg24/README.md). Unsupported adapters return
`ERR RF_HEALTH_UNSUPPORTED`.

Run the real C parser with address/undefined-behavior sanitizers:

```sh
make -C firmware/experimental BUILD=/tmp/ec-experimental-test test
```

The tests cover command rejection, capability limits, ID overflow, damaged-line
recovery and output/metadata bounds, plus RF ownership, deadlines, ring bounds and
fault fencing. The host's `test_xiao_transport` exercises
actual daemon restore against these capabilities. This is separate from radio
timing, hardware transport and firmware recovery qualification.
