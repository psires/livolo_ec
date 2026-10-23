# Volatile multi-peer firmware v3 — verification and handover

Implemented on 2026-10-17. The Pi owns the authoritative network state; TI owns
bounded radio operations and immediate responses. The host integration contract
is [UART_PROTOCOL.md](../../firmware/gateway/UART_PROTOCOL.md).

## Changes

- One volatile network identity/tag and 256 independently configurable peer slots.
- Per-peer ACK masks: state, property, capability, direct keepalive, broadcast state.
- CAPS and indexed table readback, configuration revision, optional callback timing.
- Host-supplied session on modern ARM/ACK/SEND; reset/session replacement rejects
  old sends. Request retry cache remains one entry and is not persistent.
- Disarmed table updates, renewable 1–60-second lease, no flash/network persistence.
- Legacy single-peer configuration retained explicitly; RX/ACKTX layout preserved.

The packet rules remain grounded in the captured EC protocol. This is a guarded
EC adapter, with channel 11 and 0 dBm fixed in this build, not an arbitrary MAC
radio service. No device-state database, heartbeats, routing policy or MQTT logic
was added. Host software can discover these limits via CAPS.

## Offline and hardware tests

12 new tests compile the actual `ec-gateway.c` parser/callback and peer table with
a fake clock/radio. They cover full capacity, every ACK profile, replacement and
duplicate peers, frame rejection, lease renewal/expiry, session changes, simulated
reset, reply-cache behavior, malformed UART input and queue overflow. All pass.
The 28 existing capture/parser/flash tests also passed after the firmware changes.

The first hardware run hit a corrupt first UART line (`ff` before a PING reply).
It transmitted nothing and disarmed in cleanup. The test harness was corrected
to retry the identical request ID/body on a missing reply. The failure and original
log are preserved; no firmware change was needed for that transport condition.

The second hardware run passed 13 checks, including:

- Configure 256 distinct peers and CHECK ACK generation for every slot.
- Reject out-of-range slots, duplicate identities and unsupported ACK-mask bits.
- Verify per-peer ACK masks, live-lease edit rejection, lease expiry and metadata.
- Reject legacy transmit bypass in modern mode and stale-session requests.
- Confirm duplicate requests are cached and ID reuse with different bodies fails.
- Confirm overlong lines are discarded and no RF sends occur during dry checks.
- Place the real spare `46715621` in slot 255 with 255 synthetic peers, then verify
  automatic ACKs for its responses to read-only queries.

The intentional overlong-line test increments input_errors once. RX, UART and
metadata loss counters remain zero. A fake-radio test is not evidence of RF
timing, and 256 configured rules are not a 256-physical-device traffic-load test.

## Independent Nordic verification

Capture: `captured/development/gateway-v3-air-nrf52840.pcapng`.

Three self-addressed diagnostics (`EC_V3_DUP_TEST`, MAC sequences 51/52/53) each
appear exactly once, although each successful UART SEND request was duplicated.
The local gateway then sent queries `10`, `ac`, `ac` to the spare. No output
command (`8c/02`) appears. State reports remained `1`: output 1 on, output 2 off.

Nordic saw all 12 MCU ACKs. Request/ACK frame pairs are 760/761, 765/766, 772/773,
777/778, 787/788, 811/812, 814/815, 819/820, 825/826, 830/831, 837/838, 843/844.
SFD-to-SFD intervals range 2.152–2.156 ms, median 2.153 ms. With the real peer in
the last slot of a full table, the bounded linear lookup adds roughly 0.28 ms
over the previous single-peer result. The measured timing is recorded, not
extrapolated to an untested larger table. Native state/property/capability
responses all exercised the ACK path.

## Reset, image and ownership handover

A GPIO reset cleared network configuration, all peers, session, revision, ACK
enable, metadata enable and lease. A SEND carrying the previous session was
rejected as NOT_CONFIGURED; an old SEND after a new NETWORK had already been
rejected as SESSION. Final test status was unconfigured and disarmed.

- SHA-256: `a21fb791f2e73b7e7e83d4e99b4361f63368d9be4958dce1f61116597488802c`
- Full flash CRC32 verified by ROM after programming: `a2597eb0`
- Factory CCFG unchanged; bootloader configuration `c5fe0fc5`
- Image: 360448 bytes; ELF text/data/BSS: 46876 / 1120 / 14200 bytes
- Rollback: remote `backups/ec-gateway-v2-broadcast-acks.bin`

The user authorized communication with **Implement Raspberry Pi daemon**. That
chat received the contract, session/reset semantics, test results, physical switch
state and explicit release of UART/reset/flash/Nordic ownership. Firmware tests
and captures stopped before release. Any subsequent live state belongs to the
host-side work; the firmware task will not reopen/reset the radio unannounced.

Evidence is stored as `gateway-v3-image.json`, `gateway-v3-flash.log`,
`gateway-v3-boot.json`, `gateway-v3-hardware.json` (initial interrupted check),
`gateway-v3-hardware-02.json`, `gateway-v3-air-verification.json`,
`gateway-v3-after-reset.json` and accompanying UART/reset logs in this directory.
