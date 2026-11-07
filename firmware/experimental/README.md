# Experimental transport bring-up

This is a shared diagnostic parser for new MCU targets, not a functioning
Livolo gateway. It uses UART v3 line framing but implements only PING, VERSION,
CAPS and STATUS. Every other command returns `ERR UNSUPPORTED`; no transmit,
network, pairing, persistent state, update or request-cache implementation exists.
It advertises zero peers, ACK mask, lease, session and raw-frame capacity so the
real daemon refuses readiness before sending NETWORK or ARM. Unknown experimental
fields alone would not prevent the daemon from becoming ready.

Commands are `<nonzero decimal uint32> COMMAND\n`, with responses
`RSP <id> OK ...\n` or `RSP <id> ERR ...\n`. Input over 319 characters, control
bytes and malformed IDs are discarded; damaged lines resynchronize at newline.
Board/build tokens are bounded and validated, and undersized output storage
never emits a partial response. Replies are side-effect-free and are not cached.

Run the real C parser with address/undefined-behavior sanitizers:

```sh
make -C firmware/experimental BUILD=/tmp/ec-experimental-test test
```

The test covers command rejection, capability limits, ID overflow, damaged-line
recovery and output/metadata bounds. The host's `test_xiao_transport` exercises
actual daemon restore against these capabilities. This is separate from radio
timing, hardware transport and firmware recovery qualification.
