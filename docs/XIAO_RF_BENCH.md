# XIAO synthetic RF cross-capture

`tools/xiao_rf_bench.py` runs the bounded `synthetic_v1` diagnostic contract on
the two new XIAOs. It is an experimental bench tool, not daemon deployment,
pairing, switch control, firmware installation or gateway qualification.
ZigBee Specialist owns live operation. Existing radios, sniffers, Pi services
and Home Assistant are excluded.

The owner confirmed the nearby detached switches as `46715621` (two gang) and
`66780945` (one gang). This baseline does not address either switch. It uses only
synthetic channel-26 cross-capture between the new radios. Passive switch
reception and any later pairing/control require their own procedure and evidence.

## Preconditions and invocation

Use the diagnostic firmware reviewed for this stage, with exact board identity,
`experimental=1`, `rf_diag=synthetic_v1`, `radio_tx=synthetic_only`, `gateway=0`,
`peers=0`, `ack_mask=00` and `update=none`. Both radios must already be idle.
The operator supplies the two exact `/dev/serial/by-id/` paths and USB serials
from the private inventory. No endpoint is selected automatically. Before opening
either port, the tool checks path resolution, distinct endpoints, VID/PID and
serial against enumerated USB metadata. VERSION and CAPS then verify the live
board before either RF window starts.

| Board | USB VID:PID | Baud | DTR | RTS |
| --- | --- | --- | --- | --- |
| `seeed-xiao-mg24` | `2886:0062` | 115200 | true | false |
| `seeed-xiao-esp32c6` | `303a:1001` | 115200, nominal USB rate | false | false |

The MG24 SAMD11 bridge requires DTR for forwarding. The C6 may emit ROM boot
chatter; only a complete matching numbered response satisfies a command.
These settings are specific to the observed bench devices and do not alter any
daemon serial policy. Ports are opened exclusively, with control-line state set
before opening; no reset, bootloader, flush or buffer-reset operation is issued.

Run only after the sole device operator has released other owners of these two
new endpoints. Use Python 3.9 or newer and pyserial 3.5, as in the host environment:

```sh
python3 tools/xiao_rf_bench.py \
  --mg24-port /dev/serial/by-id/EXACT_MG24_PATH \
  --mg24-serial EXACT_MG24_SERIAL \
  --c6-port /dev/serial/by-id/EXACT_C6_PATH \
  --c6-serial EXACT_C6_SERIAL \
  --output /private/bench/new-cross-capture.json
```

Replace all placeholders from the private inventory; this is not an executable
device-selection example. The output file must not exist. It contains private
endpoint identity and raw diagnostic transcripts, so retain it as private bench
evidence rather than publishing it without review. Exit zero means the finite
baseline checks passed; a JSON error, missing evidence or nonzero exit does not.
The default mode is `baseline`; it never starts a follow-up experiment itself.

## Frozen baseline

1. Read PING, VERSION, CAPS and RFSTATUS from both exact endpoints. Refuse an
   already active window without sending START, STOP or TX to either radio.
2. Start both windows with the same fresh nonzero 32-bit nonce. Verify fixed
   channel 26, SDK-configured power at most 0 dBm, automatic ACK off, retries off,
   callback timestamps at 1 MHz and no latched fault.
3. Send 20 single frames from MG24: ten 39-byte and ten 41-byte MAC frames,
   excluding FCS, using sequences 0–19. Then send the same length distribution
   from C6 using sequences 32–51. Requests are spaced by at least 200 ms.
   CCA and ACK-request flags are both zero. There is no automatic CCA/ACK test
   expansion, transmit retry or switch command.
4. Drain observations, stop both owned windows, drain again and verify final
   inactive, not-busy status. Preserve counters and exact transcripts.

The fixed bytes start `01 88 <sequence> ef be 22 22 ef be 11 11`, then ASCII
`XRF1`, the four nonce bytes in hex order, sequence, length, CCA and ACK-request
bits. Remaining byte at offset `i` is `(i * 17 + sequence) & 255`. There is no
Livolo control header or action. Entire payload equality, including nonce and
sequence, is required; matching only a length or marker is insufficient.

Ordinary work has a 30-second monotonic budget and each request at most two
seconds. Cleanup adds at most two one-second commands per started endpoint and
a 200 ms drain, plus bounded serial-write overhead. The firmware independently
expires its RF window after 60 seconds. Partial successful writes continue from
the unsent suffix; a missing response never causes command replay. A write
exception or truncated write poisons that link: the tool cannot safely append a
STOP to an unknown command prefix. It reports cleanup uncertainty, continues
cleanup of the other endpoint and relies on the firmware lease for the affected
radio. Verify hardware expiry/recovery separately before another live run.

## Explicit CCA and ACK-request follow-up

After review of a successful baseline, the sole device operator may select
`--mode cca-ack` in a **separate invocation** with a new output file. The same
exact endpoint checks, live identity checks, idle preflight, 30-second budget,
200 ms request spacing, one-shot commands, firmware lease and verified shutdown
apply. This mode does not run the baseline first or expand its frame count:

| Sequence offset per transmitter | MAC length | CCA | ACK-request bit |
| --- | --- | --- | --- |
| 0 | 39 | 1 | 0 |
| 1 | 41 | 1 | 0 |
| 2 | 39 | 0 | 1 |
| 3 | 41 | 0 | 1 |

MG24 uses sequences 0–3 and C6 uses 32–35, for exactly **four frames per
direction, eight total**. The fixed channel, source/destination fields, nonce,
non-Livolo payload pattern, SDK-configured power limit, automatic ACK off and
retries off remain unchanged. Setting the ACK-request bit changes the frame
control byte from `01` to `21`; it is not proof that a peer acknowledged it.

`case_outcomes` separates `cca` and `ackreq` capture/completion results for each
direction. `ack_observations` counts captured classic three-byte IEEE ACKs per
endpoint, with their sequences. Both `02 00` and frame-pending `12 00` control
fields are recognized. Invalid software CRC, other frame types and enhanced
ACK formats do not count as classic ACK observations. Matching a requested
sequence is a hint only, not causal or timing evidence.

Acceptance requires four starts, four completions and four exact independent
captures per direction, all the same final counter/delivery/idle checks as the
baseline, and **zero observed classic ACKs on both endpoints**. Any such ACK,
including unrelated traffic, conservatively prevents this run from passing:
`ack_check.status` becomes `inconclusive`, requiring review. This is not a finding
that either board generated an ACK. Matched and unmatched sequence counts are
reported separately. No observed ACK
does not prove that no hidden ACK was transmitted. Successful CCA on idle air
does not establish busy-channel behavior, and the fixed synthetic destination
does not qualify addressed-unicast ACK behavior on either board. There is no
retry, automatic mode chaining, interference generation or third receiver.

## Evidence and limits

`RF TX ... status=done` is hardware transmit completion, **not air reception**.
Each direction is checked against events from the other endpoint. Reports keep
completion outcomes, missing sequences, duplicate sequences, reordered first
observations, unrelated frames and CRC evidence separately. Missing observations
are not automatically classified as RF loss: receiver queue overflow and USB
delivery failures are separate possibilities.

MG24 RX events must have `fcs=included`: `len` MAC bytes plus two actual FCS bytes.
The tool independently calculates the reflected IEEE 802.15.4 CRC and compares
the little-endian FCS. C6 events must have `fcs=stripped`: exactly `len` MAC bytes.
The C6 API replaces the original FCS bytes with RSSI/LQI; firmware must remove
those metadata bytes. Its `crc=1` is hardware-valid metadata only, never a claim
of independent software CRC verification. Unexpected per-board FCS mode fails
the run. Unrelated CRC-valid frames of MAC length 1–125 are retained; this includes
three-byte IEEE ACKs when present. `bad_crc=unavailable` means the tool cannot
calculate a bad-CRC frame rate.

Passing the default baseline requires 20 independent captures and exactly one successful TX completion
for every expected sequence in each direction, no duplicate captures or invalid
software CRC, 20 starts/completions per transmitter, zero failed TX/drop/queue/busy/
fault/active counters at the end, and equality between final RX counters and
delivered RX event counts. Callback ticks are not SFD timestamps and cannot prove
turnaround or ACK timing. Bounded ASCII lines, event counts and request deadlines
prevent a noisy USB endpoint from causing unbounded memory use or waiting.

The default baseline does not test CCA or ACK-request flags. Neither mode proves interference
performance, production timing, lease/watchdog behavior under a physically stalled
host, long-duration load, pairing, outputs or daemon readiness. Both boards retain
the zero gateway capabilities until separately accepted.

## Offline regression command

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=tools \
  python3 -m unittest test_xiao_rf_bench -v
```

The tests exercise the actual runner with virtual time, fragmented and stalled
writes, split replies/events, independent captures, wrong CRC/nonce/length,
missing and duplicate sequences, lost responses without replay, identity and
active-window rejection, failed shutdown, inconsistent counters and bounded
stream parsing. Additional cases verify explicit mode selection, the four-frame
flag/length plan and spacing, per-case outcomes, observed ACK classification,
uncertainty without expansion and unchanged default baseline behavior. They
import no serial module and open no hardware endpoints.
