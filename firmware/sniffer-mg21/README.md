# Passive MG21 sniffer

Dedicated Sonoff ZBDongle-E application, sharing the project version (currently
0.12). It does not link the gateway core and has no application RF transmission
operation. IEEE 802.15.4 automatic ACKs are disabled. It captures CRC-valid
standard frame types promiscuously on channel 11, including ordinary IEEE ACKs;
MAC FCS bytes are stripped by the radio. It does not capture CRC-invalid frames.

Build against the same pinned Gecko SDK as `../mg21`:

```sh
make GSDK=/path/to/gecko_sdk BOARD=sonoff-zbdongle-e UART_BAUD=921600
python ../../tools/mg21_gbl.py build/ec-sniffer.bin build/ec-sniffer.gbl
```

The application stays within 0x4000..0x30000. Flash only its application GBL
through the existing Gecko bootloader at **115200**. The captured data stream
uses **921600, 8N1, no flow control**. Keep a known-good gateway GBL for recovery.
`BOOTLOADER\n` (preceded by a newline to clear partial input) enters the preserved
bootloader from the capture application. All other input lines are ignored.
The inherited alternate MG21 board profile is not independently sniffer-qualified.

See [third-party notices](../THIRD_PARTY_NOTICES.md) for the pinned SDK inputs,
file-specific licence exceptions and binary-distribution requirements.

## Binary ESN1 transport

Every record has a 20-byte little-endian header:

| Offset | Type | Meaning |
| --- | --- | --- |
| 0 | 4 bytes | ASCII `ESN1`, wire format version 1 |
| 4 | uint16 | Body length, at most 129 |
| 6 | uint8 | 1 = received frame, 2 = status |
| 7 | uint8 | 1 = RX sync-word-end timestamp, MAC FCS omitted |
| 8 | uint32 | Record sequence, wrapping; increments even when UART rejects a record |
| 12 | uint64 | Extended radio microseconds since boot |

The body is followed by a little-endian IEEE reflected CRC32 over header/body,
compatible with Python `binascii.crc32`. An RX body is signed RSSI dBm (int8),
LQI (uint8), channel (uint8, 11), FCS format (uint8, 0), then 1..125 MAC bytes.
A status body is major/minor (two uint16), then eight uint32 values: baud,
RF FIFO overflows, rejected UART bytes, malformed/rejected radio packets,
timestamp errors, calibration errors, UART RX errors and DMA errors. The
remaining bytes are a NUL-terminated `EC-MG21-BOARD=sonoff-zbdongle-e` marker.
Status is emitted once a second. There is no persistent network configuration.

RAIL packet metadata is converted to sync-word end with the complete air length
(PHR + MAC + FCS). Its wrapping 32-bit microsecond clock is extended by frequent
main-loop reads. Packet timestamps are taken before releasing held RX buffers.
RX FIFO and atomic-record UART TX ring are both 8 KiB. Buffer overflows are
reported rather than silently concealing loss; RF packets never received cannot
be counted. A radio reset terminates a host capture when its sequence regresses.

```sh
python tools/capture_mg21_sniffer.py --port /dev/serial/by-id/EXACT_SONOFF_ID \
  --duration 180 --output capture.pcapng
```

The host writes TAP pcapng, original `.uart.bin`, and `.json` diagnostics. Packet
spacing comes entirely from the radio clock. The wall-clock epoch is a constant
estimated from the first record's USB receipt; it has USB/host latency and is
not suitable for absolute cross-host timing. Compare shared packets to estimate
clock offset/drift. Initial stream-fragment discards and a partial final record
are recorded separately from CRC errors and sequence gaps.

`tools/test_mg21_sniffer.py` compiles the real C wire encoder and checks fragmented
reads, corruption recovery, sequence wrap/reset, large timestamps and size bounds.
The [independent Nordic comparison](../../analysis/development/sonoff-sniffer-20261101.md)
records timestamp agreement and imperfect RF coverage. Neither receiver is an
absolute lossless reference.
