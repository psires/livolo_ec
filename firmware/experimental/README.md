# Experimental transport and raw-radio bring-up

This is a shared diagnostic parser for new MCU targets, not a functioning
Livolo gateway. It uses UART v3 line framing but implements only PING, VERSION,
CAPS and STATUS. Without a radio hook, every other command returns
`ERR UNSUPPORTED`. RF-enabled builds additionally expose strictly bounded
`RFSTART`, `RFSTATUS`, `RFTX` and `RFSTOP` diagnostics described in the
[bench contract](../../docs/XIAO_RF_BENCH.md). No production raw-send, network,
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

Run the real C parser with address/undefined-behavior sanitizers:

```sh
make -C firmware/experimental BUILD=/tmp/ec-experimental-test test
```

The tests cover command rejection, capability limits, ID overflow, damaged-line
recovery and output/metadata bounds, plus RF ownership, deadlines, ring bounds and
fault fencing. The host's `test_xiao_transport` exercises
actual daemon restore against these capabilities. This is separate from radio
timing, hardware transport and firmware recovery qualification.
