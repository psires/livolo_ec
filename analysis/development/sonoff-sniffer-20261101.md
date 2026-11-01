# Sonoff replacement sniffer, 2026-11-01

The genuine Sonoff ZBDongle-E on `10.1.1.120` now runs the dedicated passive
sniffer application, release 0.12, at 921600 baud. Its Gecko 2.4.2 bootloader
remains at 115200. Application-only flash completed successfully; the previous
0.12 gateway GBL is saved on the sniffer Pi. It can be restored after sending
the sniffer maintenance line `BOOTLOADER`.

The sniffer contains no application radio transmission path, disables IEEE
automatic ACKs, and reports hardware-derived sync-word-end timestamps over a
CRC-protected binary stream. See [implementation and wire contract](../../firmware/sniffer-mg21/README.md).

A 180-second Sonoff recording overlapped a 210-second recording from the Nordic
on the main Pi. Sonoff received 1,636 frames; Nordic received 1,353 across its
longer, differently placed capture. These total counts are not an RF ranking.
Sonoff recorded zero stream CRC failures, sequence gaps, resynchronizations,
FIFO overflows, rejected UART bytes, timestamp errors, calibration errors or
UART RX/DMA errors. Four host tests exercise the actual C wire encoder and its
Python decoder, including fragmentation, corruption and counter rollover.

In a conservative common 175.396-second window, Sonoff heard 200 valid EC frames,
Nordic 182, with a byte-identical multiset intersection of 162. Coverage differs
by switch, so neither capture is a lossless reference. Cross-host wall-clock
anchors include USB latency; fit offset/drift using shared frames before
comparing radio timing.

The initial matcher found 1,094 byte-identical pairs. Fitting 146 shared 39-byte
EC frames gives Sonoff-minus-Nordic drift of -39.37 ppm, median residual -0.128 us
and a range of -4.755 to +54.148 us. Twenty shared 41-byte frames have a median
residual of -1.524 us and a range of -4.279 to +1.731 us. Other frame lengths
mostly agree to similar precision. Two non-EC candidate matches differ by
approximately 4–4.8 ms; repeated identical packets make those associations
ambiguous. They remain in the raw comparison instead of being silently removed.
This establishes useful timestamp agreement, not universal sub-microsecond
accuracy or complete reception.

Evidence: [comparison](sonoff-sniffer-20261101/comparison.json),
[transport diagnostics](sonoff-sniffer-20261101/transport.json),
[image metadata](sonoff-sniffer-20261101/image.json),
[flash log](sonoff-sniffer-20261101/flash.log),
[Sonoff capture](../../captured/development/sonoff-sniffer-20261101-sonoff.pcapng),
[Nordic capture](../../captured/development/sonoff-sniffer-20261101-nordic.pcapng).

The Nordic is now being repurposed as a PCA10059 gateway. TI remains the active
host-managed gateway during initial Nordic USB/passive-reception qualification.

Reproduce the timestamp/coverage comparison with:

```sh
python tools/compare_sniffer_timestamps.py \
  captured/development/sonoff-sniffer-20261101-sonoff.pcapng \
  captured/development/sonoff-sniffer-20261101-nordic.pcapng \
  --output comparison.json
```

The promoted tool reproduces the original counts and fitted clock values in
[the reproducible result](sonoff-sniffer-20261101/reproducible-comparison.json).
