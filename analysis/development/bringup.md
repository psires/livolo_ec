# Development machine bring-up — 2026-10-17

**Current status:** the normal receiver with the CC2652P pin fix is flashed and
running on channel 11. UART startup and EC reception are verified against the
Nordic sniffer. See the final section for results and the remaining reception gap.
The added Pi reset/flash wiring now works after the user corrected a loose
connector: GPIO reset, ROM bootloader entry/identification, and return to the
receiver are verified. Firmware is unchanged. See the GPIO retest below.

## Access and hardware

- SSH: `sfires@livolodev.home.sfires.net` (`10.1.1.21`). Existing SSH identity works.
- The existing known-hosts entry for `10.1.1.21` verifies the connection using `ssh -o HostKeyAlias=10.1.1.21 livolodev.home.sfires.net`.
- Raspberry Pi 5 Model B Rev 1.0, Debian 13 (trixie), aarch64.
- Clock synchronized by NTP; timezone Europe/Warsaw.
- Remote project directory: `/home/sfires/livolo-ec`.
- User is in `dialout`; passwordless sudo is available.
- Nordic USB ID `1915:154b`, exposed as `/dev/ttyACM0`.
- Stable sniffer path: `/dev/serial/by-id/usb-Nordic_Semiconductor_ASA_nRF_802154_Sniffer_F1BF2323F452D37E-if00`.

The user identified the board as the [cod.m CC2652 Raspberry Pi module](https://github.com/codm/cc2652-raspberry-pi-module), attached to the Pi header UART. The bootloader and factory registers now confirm **CC2652P with 352 KiB flash**, consistent with the board's RF-BM-2652P2 module. It is not the previously assumed P7.

Initially only `/dev/ttyAMA10` (`/dev/serial0`) was exposed, with the Linux console attached. After the user identified the board and authorized reboot, `dtoverlay=uart0-pi5` was added under a `[pi5]` section in `/boot/firmware/config.txt`. The `console=serial0,115200` token was removed from `/boot/firmware/cmdline.txt`, preserving `console=tty1` and the other boot arguments.

Both files were backed up with suffix `.before-livolo-uart-20261017-182907`. The reboot completed successfully: boot ID changed from `5decfe11-3b51-4823-8c4e-f9024e884b42` to `dbf07fe9-d224-47f9-8c06-e0f80b488986`.

Verified after reboot:

- `/dev/ttyAMA0` exists, with group `dialout`.
- GPIO14 is TXD0; GPIO15 is RXD0.
- `serial-getty@ttyAMA0.service` is inactive and no process owns the port.
- `/dev/serial0` still points to `/dev/ttyAMA10`; **use `/dev/ttyAMA0` explicitly for the module**.

From the manufacturer's Eagle schematic: module RX is DIO12, TX is DIO13, FLASH is DIO15 pulled to ground by the button, and the board LED is on DIO7. Reset and FLASH do not connect to Pi GPIOs in the original design. The user subsequently added RESET to BCM GPIO23 (physical header pin 16) and FLASH to BCM GPIO24 (physical pin 18), without external pull-ups. Automatic reset/bootloader entry now works after correcting a loose connector; the initial failure and successful retest are recorded below.

## Installed tools

Installed Git, python3-serial, tshark/wireshark-common and dependencies using Debian packages; python3-venv was already present. No existing packages were upgraded. Verified Python 3.13.5 and TShark 4.4.19.

The official Nordic sniffer script and its license are installed in `/home/sfires/livolo-ec/vendor/nrf-sniffer`, pinned to revision `b69293680ac92aeddc10807314d564e4c929dcd3` of [Nordic's repository](https://github.com/nordicsemi/nRF-Sniffer-for-802.15.4). The commit metadata is retained in `vendor/nrf-sniffer-commit.json`.

The [cc2538-bsl source](https://github.com/JelmerT/cc2538-bsl) is checked out in `/home/sfires/livolo-ec/vendor/cc2538-bsl` at revision `28cce9749bfb73c50363ded01bd8001b1639d9fb`. Its help command runs with system Python and pySerial:

```sh
cd /home/sfires/livolo-ec/vendor/cc2538-bsl
python3 -m cc2538_bsl.cc2538_bsl --help
```

The read-only wrapper `tools/probe_cc26xx.py` uses this source to synchronize with the ROM bootloader and read chip/register identity. It does not invoke reset control, erase, flash programming, or clock switching. The first attempt timed out; after the user entered the bootloader with FLASH/RESET, identification succeeded at **115200 baud on `/dev/ttyAMA0`**.

When requested, hold FLASH, briefly press and release RESET while keeping FLASH held, then release FLASH. The ready-to-run probe is:

```sh
python3 /home/sfires/livolo-ec/tools/probe_cc26xx.py \
  --port /dev/ttyAMA0 \
  --bsl-source /home/sfires/livolo-ec/vendor/cc2538-bsl
```

Always specify the actual UART port explicitly; the upstream tool's default USB-port selection could otherwise target the sniffer. Firmware must preserve the DIO15 active-low bootloader-entry configuration. The initial read-only bring-up below was followed by the authorized receiver flash described at the end of this document.

### Confirmed chip and preserved original image

Raw identity/checksum evidence is in [module-identity.json](module-identity.json).

| Field | Observed value |
|---|---|
| ROM chip ID | `0xf000` |
| ICEPICK device ID / wafer | `0x3bb4102f` / `0xbb41` |
| Factory user ID | `0x3202f000` |
| Flash sectors | 44 × 8192 bytes = 360448 bytes (352 KiB) |
| Primary IEEE address | `00:12:4b:00:2a:99:2b:12` |
| Existing initial stack pointer | `0x20013000` |
| Existing reset vector | `0x00025201` |
| Existing CCFG bootloader configuration | `0xc5fe0fc5` |

TI's [chip identification implementation](https://github.com/TexasInstruments/simplelink-lowpower-f2-sdk/blob/3615ad5d3f4f271258d18a2f5ce1785064ff9a58/source/ti/devices/cc13x2_cc26x2/driverlib/chipinfo.c) and its factory-register bit definitions map this wafer/user ID to CC2652P: protocol field `0xf`, sub-1-GHz flag clear, PA flag set, HPOSC flag clear. This avoids the misleading older chip name printed by some bootloader utilities. TI specifies [352 KiB flash for CC2652P](https://www.ti.com/product/CC2652P).

The module is **not blank**: it contains an existing image and populated CCFG. The complete flash was read using `tools/backup_cc26xx.py` and verified against the ROM bootloader's CRC32:

- Remote original-image backup: `/home/sfires/livolo-ec/backups/codm-original-20261017.bin`.
- Remote backup metadata: same path with `.json` suffix.
- Second copy, outside the repository: `/private/tmp/livolo-ec-backups/codm-original-20261017.bin` on the development client. The Pi copy is the persistent primary backup.
- Host and device CRC32 both equal `eadf8146`.
- SHA-256: `64469d15e8d257eb00fd4ab3c7534520734d9b24dab866a87203c21cd045e921`.

The raw image is kept outside version control. The backup reader issues only ping, identity, memory-read, status and CRC32 commands. It neither resets nor writes the device. A repeat identity read in the same powered bootloader session should use `probe_cc26xx.py --already-synced`; a fresh FLASH/RESET entry uses the default autobaud synchronization. The module was left in the bootloader after backup; the later receiver flash changed that state.

## Successful passive capture

The receive-only capture ran on channel 11 on 2026-10-17, approximately **18:22:23–18:22:43 Europe/Warsaw**. The local copy is `captured/development/2026-10-17-bringup-channel11.pcapng`.

Results:

- 121 captured packets total.
- 11 recognized EC packets, all with valid application checksums.
- 110 other/unsupported frames retained in the original capture and counted as skipped in EC decoding.
- Two gateway `8c/08` heartbeats from `9bf4ea38`.
- Nine `9e/07` announcements collectively from all six previously referenced IDs: `47778828`, `56830549`, `81422527`, `67181844`, `72715996`, `75710834`.

This confirms that the six IDs in the old gateway polling traffic are real transmitters in the current home installation. Combined with the user's explanation that the original captures were made away from home, it strongly supports retained home-device records as the explanation for the otherwise unexplained polling. It does not establish whether those records were active registrations during the old experiment.

The heartbeat parameter is 6 in this capture, consistent with the six observed switch identities, but its exact semantics remain a hypothesis. No switch controls or pairing commands were transmitted during this bring-up.

## Repeat a capture on the Pi

Choose a fresh output filename; the wrapper refuses to overwrite existing files.

```sh
cd /home/sfires/livolo-ec
python3 tools/capture_nrf.py \
  --device /dev/serial/by-id/usb-Nordic_Semiconductor_ASA_nRF_802154_Sniffer_F1BF2323F452D37E-if00 \
  --sniffer vendor/nrf-sniffer/nrf802154_sniffer.py \
  --channel 11 --duration 30 \
  --output captures/next-experiment.pcapng

python3 tools/decode_ec.py --skip-unknown \
  captures/next-experiment.pcapng --output-dir analysis/next-experiment
```

The wrapper asks the official script to receive packets, stops it after the requested duration, then converts its TAP pcap to pcapng using editcap. It retains the pcap and diagnostic log alongside the pcapng. The successful bring-up capture left no diagnostic errors.

`--skip-unknown` skips unrecognized frame layouts, not bad checksums in recognized EC frames. Recognized checksum failures still cause a nonzero exit. Packet numbers and relative times remain tied to the full capture, including skipped packets. The relay matcher still assumes the observed gateway identity `9bf4ea38`; a future independently addressed gateway will require configurable identity handling.

## Authorized passive receiver flash

The user explicitly authorized freely flashing this new, unused module. Installed
Debian `gcc-arm-none-eabi` and `libnewlib-arm-none-eabi` and built the custom
[passive receiver](../../firmware/receiver/README.md) against pinned Contiki-NG
and TI Core SDK sources. The custom MAC forwards raw frames to UART with automatic
ACK, BLE, and sub-GHz disabled. No application transmit path is implemented.

The complete 88-byte CCFG block matches the preserved factory image byte for byte,
including oscillator tuning, power settings, DIO15 bootloader entry, and JTAG.
The linker uses 352 KiB flash and 80 KiB SRAM. The image uses approximately 40 KiB
code/constant data and 7 KiB initialized/zero-initialized RAM, excluding stack.

- Image metadata: [receiver-image.json](receiver-image.json).
- Programming/startup log: [receiver-flash.log](receiver-flash.log).
- Image SHA-256: `d968bb129ec6cccda322029f85c26b7f1cc9996230c51df6a499450789e2d4cf`.
- Host and ROM full-image CRC32: `1d04364e` (verified).
- Bootloader configuration readback: `0xc5fe0fc5` (verified).
- ROM reset command: acknowledged.

**Startup is not yet verified.** No UART output appeared during the first eight
seconds after the software reset. Subsequent existing-session ping and fresh
autobaud probes both timed out. This does not distinguish an early startup hang
from other reset/UART issues. A physical RESET without FLASH has been requested.
Do not assume the module is still in a synchronized bootloader session.

Next: resolve startup, capture simultaneously with the Nordic receiver, and compare
exact EC frames using `tools/compare_receivers.py`. Pairing/control and MQTT remain
unimplemented. The Nordic capture and offline decoder remain operational.

### Physical reset follow-up

After the user confirmed a physical RESET, a further 12-second UART capture
received no bytes and no packets. Remote evidence is in
`captures/receiver-after-physical-reset.pcapng` and its `.uart.log` sidecar.
The physical reset did not establish a working receiver.

An instrumented build is prepared with `make BOOT_DIAGNOSTICS=1` (clean when
switching build modes). It adds polled UART messages before/after device trim
and around platform initialization, independent of the normal UART driver and
scheduler. It has compiled and passed the same image-layout checks, and its
complete CCFG still matches the factory image. It has **not been flashed**.
Metadata: [receiver-diagnostic-image.json](receiver-diagnostic-image.json).

Re-entering the ROM bootloader requires the local FLASH/RESET buttons because
this board does not wire those signals to Pi GPIOs, and the running application
has no working command interface. Next physical action: hold FLASH, press and
release RESET, then release FLASH. After confirmation, synchronize the ROM and
flash the prepared diagnostic image to identify the early startup failure.

### Diagnostic flash result and prepared pin fix

The diagnostic image was flashed after the user's next bootloader entry. Its
full-image ROM CRC32 matched `f7ddb893`, and software reset produced:

```text
BOOT before trim
BOOT after trim
BOOT platform one
```

This establishes working application entry, device trimming, and polled UART.
Startup does not reach the completion of platform stage one. Evidence is in
[receiver-diagnostic-flash.log](receiver-diagnostic-flash.log).

Source review found a likely compatibility problem in the pinned 2019 TI SDK:
`ChipInfo_GetChipType()` reports CC2652 for this CC2652P because its CC26x2 branch
does not inspect the PA flag. `PIN_init()` only reserves DIO0..4 for CC1352P, then
computes the upper GPIO bound from the factory GPIO count. This can reject the
P-family RF-switch pins and enter the platform's deliberate `PIN_NO_ACCESS`
infinite loop. This explanation still requires hardware confirmation.

The board preparation script now generates a licensed copy of the vendor pin
driver with the DIO5..30 correction restricted to the module's verified wafer and
factory user ID. Additional diagnostic wrappers report power, pin and interrupt
initialization. The rebuilt image passed layout/recovery checks and still has a
byte-identical factory CCFG. Metadata: [receiver-pin-fix-image.json](receiver-pin-fix-image.json).
It has **not been flashed**; the first diagnostic image is currently installed.
Next: enter bootloader with FLASH/RESET, read FCFG1 IOCONF to confirm the GPIO
count, then flash and inspect the prepared image. No radio reception is verified.

### Pin fix verified; diagnostic UART interference identified

The next ROM session read FCFG1 IOCONF at `0x50001344` as `0xffffff1a`:
**26 GPIOs**. With the old SDK's incorrect lower bound of 0, its calculated upper
bound was 25 and rejected DIO28..30 in the board table. The corrected range is
DIO5..30. Evidence: [module-gpio-count.json](module-gpio-count.json).

The pin-fix diagnostic image was flashed and its full ROM CRC32 matched
`bf1bc171`. It reported successful pin initialization, interrupt enable, both
platform initialization stages, and normal UART initialization. See
[receiver-pin-fix-flash.log](receiver-pin-fix-flash.log).

The last diagnostic output was a further `Power_init()` call. Source inspection
found that the temporary wrapper reconfigured UART after the regular UART driver
had opened it. In particular, its `UARTEnable()` leaves TXE set; the TI driver's
`UARTCC26XX_write()` interprets TXE as an active write and rejects subsequent
writes. Therefore silence after that message does **not** establish a radio hang.
An additional eight-second UART capture still yielded no frames.

The normal receiver has now been clean-built with the confirmed pin fix and
**without diagnostic wrappers**. It passed image checks and the complete CCFG
still matches the factory image. It awaits another FLASH/RESET entry and flash:
[receiver-normal-pin-fix-image.json](receiver-normal-pin-fix-image.json), CRC32
`7d1c9fe2`. The diagnostic wrapper source was also corrected to leave an opened
normal UART alone and to preserve the `Power_init()` return type; that optional
diagnostic change is not part of the prepared normal binary.

Current installed image: pin-fix diagnostic (`bf1bc171`). Prepared next image:
normal receiver with pin fix (`7d1c9fe2`). Reception remains unverified.

### Normal receiver running; independent reception verification

The normal image with the pin fix was flashed after the user's next bootloader
entry. Full-image ROM CRC32 matched `7d1c9fe2`, bootloader readback matched, and
software reset started the receiver successfully. It printed its board/version,
channel 11, `tx=disabled`, and received raw frames. The hardware GPIO issue and
the later diagnostic interference are resolved in this normal build.

- Installed image metadata: [receiver-normal-pin-fix-image.json](receiver-normal-pin-fix-image.json).
- Flash/startup evidence: [receiver-normal-flash.log](receiver-normal-flash.log).
- Runtime evidence: [receiver-verified-cc2652p.uart.log](receiver-verified-cc2652p.uart.log).
- Capture comparison: [receiver-comparison.json](receiver-comparison.json).
- Full capture summaries: [receiver-verified/summary.json](receiver-verified/summary.json).
- Signal observations: [receiver-rssi.json](receiver-rssi.json).

Both receivers captured simultaneously for 40 seconds. The pcapng files are
saved under `captured/development/receiver-verified-{cc2652p,nrf52840}.pcapng`.

| Measurement | CC2652P | Nordic nRF52840 |
| --- | ---: | ---: |
| Frames in entire capture | 132 | 230 |
| EC frames in entire capture | 15 | 19 |
| Valid EC checksums | 15/15 | 19/19 |
| EC frames in 35.596-second interior overlap | 13 | 18 |

All 13 CC2652P EC frames in that overlap match the Nordic capture **byte for
byte**, including multiplicity. Nordic captured five additional EC frames.
The CC2652P UART capture contained no malformed RX records or sequence gaps.
Eight periodic STATUS records all reported `channel=11 rx_mode=0`, confirming
the address filter and automatic ACK remain disabled. The application has no
radio transmit path; no pairing or switch commands were sent.

This validates reception and the raw-MAC transport, not lossless coverage or
gateway control. The Nordic receiver saw all six installed switch identities;
the CC2652P saw four, plus the gateway. Its reported median RSSI was roughly
16–24 dB lower for common sources. Different radio RSSI calibration, antenna
configuration, placement, and RF settings require investigation; this is not a
calibrated antenna-gain comparison. Whether an external antenna is attached to
the CC2652P module has been asked of the user. Sequence continuity only checks
frames emitted by the firmware, not frames lost earlier in the radio/queues.

The CC2652P remains powered and running the normal passive receiver. Capture
clients have stopped. The Nordic sniffer was returned to sleep by its capture
script. The next development stages are reception/range checks, a reliable UART
command interface, controlled protocol transmission to a spare switch, pairing,
and MQTT/Home Assistant integration.

### Pi GPIO reset/flash wiring test

The user confirmed BCM numbering: RESET = GPIO23/header pin 16, FLASH =
GPIO24/header pin 18. Both lines initially had no active function and internal
pull-downs. They are unclaimed on `/dev/gpiochip0` (`pinctrl-rp1`). Python
libgpiod 2.2.0 is installed.

Added `tools/cc26xx_gpio.py`, using exclusive GPIO requests and an exclusive UART
lock. It uses open-drain outputs and internal pull-ups: low asserts a signal;
high releases it to input. It never drives high against a physical button.
The helper checks levels, releases both lines in its cleanup path, and requires
the receiver startup banner before reporting a verified reset. Bootloader entry
still requires a successful subsequent ROM probe.

Observed results:

- Both released lines read high. `pinctrl` confirms input mode with pull-up.
- A 50 ms GPIO23 reset pulse did not restart the receiver: packet sequence and
  uptime continued, and no startup banner appeared.
- FLASH low, RESET low, RESET released, 300 ms delay, FLASH released produced
  the expected Pi GPIO levels, but the read-only ROM autobaud probe timed out.
- Separate longer tests held each pin low for about 1.6 seconds. `pinctrl`
  confirmed `op dl pu | lo` on the asserted pin. UART RX records continued
  during both assertions. For GPIO23, sequence 1272–1276 and ticks 48839–48963
  were received while low; for GPIO24, sequence 1280–1284 and a STATUS record
  at tick 49338 were received while low. Neither signal reset the module.

These observations indicate that the tested Pi signals are not reaching the
module's effective reset input; they also rule out a simple swap between these
two wires as the sole explanation. Continuity and voltage at the actual module
RESET signal need checking. FLASH continuity to DIO15 must also be checked;
bootloader operation cannot be verified until RESET works. The exact wiring
fault has not been established remotely.

No flash erase/programming was attempted. The existing passive receiver remains
running. Both GPIOs were left released as inputs with internal pull-ups. This
pull-up configuration is for the current boot only; boot configuration was not
changed while the wiring remains unverified. A repeat reset test is saved in
[gpio-reset-test.log](gpio-reset-test.log).

Once the wiring is corrected, from `/home/sfires/livolo-ec`:

```sh
python3 tools/cc26xx_gpio.py reset
python3 tools/cc26xx_gpio.py bootloader
python3 tools/probe_cc26xx.py --port /dev/ttyAMA0 --bsl-source vendor/cc2538-bsl
```

Only proceed to flashing after successful ROM identification. To leave ROM
without flashing, `python3 tools/cc26xx_gpio.py reset` should restart the existing
receiver once GPIO control works.

### GPIO retest after connector correction — successful

After the user reported a loose header-pin-18 connector and requested a retest:

1. `cc26xx_gpio.py reset` produced a fresh Contiki startup banner and
   `EC-RECEIVER 1 board=codm-cc2652p ... tx=disabled`. RX sequence restarted at 1,
   and EC traffic was received.
2. `cc26xx_gpio.py bootloader` followed by the read-only ROM probe succeeded:
   chip ID `f000`, wafer `bb41`, FCFG user ID `3202f000`, and the same IEEE
   address `00:12:4b:00:2a:99:2b:12` as before.
3. A second GPIO reset returned the module from ROM to the passive receiver.
   Startup evidence is saved in [gpio-reset-retest.log](gpio-reset-retest.log).

Both RESET and FLASH control are now verified without physical button presses.
No firmware erase or programming was needed for this retest. Both GPIOs were
left released with internal pull-ups, and the receiver is running on channel 11.
Boot-time GPIO configuration remains unchanged; the helper applies the pull-ups
when invoked. The earlier failed wiring observations are retained as history.

### Antenna fitted: simultaneous capture comparison

After the user fitted a TI antenna and powered the original test switch, both
receivers captured channel 11 simultaneously for 90 seconds on 2026-10-17.
Capture files are `captured/development/antenna-check-20261017-{cc2652p,nrf52840}.pcapng`.
Both clients exited successfully. The TI UART had zero malformed RX records and
zero sequence discontinuities.

| Measurement | TI CC2652P | Nordic nRF52840 |
| --- | ---: | ---: |
| All captured frames | 499 | 553 |
| Recognized EC frames | 53 | 53 |
| Valid EC application checksums | 53/53 | 53/53 |
| EC sources | 8 | 8 |
| EC frames in 86.730-second interior overlap | 50 | 50 |

Across the full files, 52 EC frames match byte for byte, counting multiplicity.
Inside the conservative comparison window, 49 match. Each receiver captured one
different announcement from `47778828` that the other missed; the discrepancies
are inside the overlap, not capture-boundary artifacts. That switch is the
weakest received EC source (median TI -97 dBm, Nordic -83 dBm). Both receivers
see the gateway, all six installed switch identities, and the original test
switch. The evidence supports much closer EC capture agreement than the earlier
13-common/5-Nordic-only overlap, but does not establish lossless reception.

TI median RSSI improved by 4.5–8 dB for the five EC sources visible in its earlier
capture. For example, the gateway changed from -78 to -71 dBm and `81422527`
from -77 to -69 dBm. Placement, propagation and receiver calibration prevent
attributing these changes solely to antenna gain. Nordic still reports stronger
signals; the two RSSI scales are not calibrated against each other.

The original switch **`46715621` is confirmed on both radios**: six identical,
checksum-valid `9e/07` announcements, about 14.8 seconds apart. All have tag `ff`
and subject suffix `0002`. Median RSSI is -46.5 dBm on TI and -40 dBm on Nordic.
The `ff` tag is consistent with pre-association traffic in the original pairing
capture; this baseline alone does not prove the switch's complete pairing state.

Evidence:

- [Comparison](antenna-check-20261017-comparison.json)
- [Per-source RSSI, tags, and opcodes](antenna-check-20261017-rssi.json)
- [Exact differences and target-switch packet records](antenna-check-20261017-details.json)
- [Decoded summaries](antenna-check-20261017/summary.json)
- [Capture execution results](antenna-check-20261017-run.json)

Both capture clients stopped cleanly; the TI passive firmware remains running
and the Nordic radio was returned to sleep. No pairing/control transmissions
were made by our tools. For the next experiment, start both captures before the
user enables pairing, record the order/times of switch and gateway actions, and
continue through the exchange and subsequent idle announcements. Use Nordic
timestamps for protocol response timing; TI timestamps are host UART receipt
times.

### Switch-only pairing capture

The [five-minute pairing observation](pairing-observation-20261017T181307Z.md)
was completed with the gateway unchanged. Both radios captured the same 293
native packets from `46715621`, including 269 registrations spanning 72.4 seconds.
No registration ACK or native tag change was observed. The switch returned to
ordinary announcements, retaining `ff`. Five installed switch identities were
observed forwarding its registration traffic toward the gateway. Device-specific
opcode-08 request/reply exchanges required a small decoder correction; all 15
tests pass. Capture clients stopped cleanly and the TI remains receive-only.
The next controlled experiment is pairing with the EC gateway explicitly in
pairing mode, with capture running before both pairing actions.

### Gateway-enabled pairing and app control — successful

The [next five-minute experiment](gateway-pairing-20261017T182031Z.md) enabled
pairing on both the gateway and switch. The user confirmed the switch was added
in the EC app. Both receivers captured identical bytes for all 65 packets from
or about the switch, including registration ACKs, adoption of tag `b1`, queries,
properties, and five app-initiated control transactions across both outputs.
The joined tag persisted for the remaining four minutes. Whole-network reception
still differs (193 TI versus 222 Nordic EC packets), but all EC checksums pass
and the TI UART has no malformed records or sequence gaps.

This is a repeat-registration experiment, not a confirmed factory-reset test.
No `0b` exchange appears in either capture. The registration byte continued from
the previous attempt's `3c` to `3d`, arguing against a reset-on-entry timer.
The report contains packet references, the candidate registration envelope, and
retry-aware control details. Both capture clients exited cleanly; the TI remains
receive-only. UART command/transmit support and MCU-side fast ACK handling are
the next implementation work; a replacement gateway has not transmitted yet.

### Local gateway firmware, admission and control — successful prototype

The next implementation is complete: UART transmission with bounded leases,
duplicate-command handling, nonblocking UART logging and MCU-side application
ACKs. The spare `46715621` was removed from EC by the user and paired to local
identity `2a992b12`, tag `a7`. With EC unplugged, both outputs reported the
commanded changes, and final state `2` was restored. Nordic independently
verified the exchange and ~1.87 ms MCU ACKs. TI/Nordic agreed on all 28 native
spare frames in the common pairing window.

The [experiment report](local-gateway-trial.md) documents a broadcast-state
fallback discovered during the outage and its MCU fix, exact packet references,
tests, image hashes and limitations. The installed image SHA-256 is
`02ff197a916e238cd175dfdbe4958cb97bb6e9e0ef489ad630e1d1322a184cd7`, full flash CRC32
`bb3a50cb`, with factory CCFG unchanged. All 28 offline tests pass. Capture and
trial processes exited; the radio is disarmed. No persistent gateway service or
MQTT bridge has been installed yet.

### Physical-button reports — verified

The [manual-button test](physical-buttons-local-01.md) confirmed state changes
from `2` to `3` to `1`, with EC unplugged and no output commands transmitted.
The user confirmed the final physical state: output 1 on, output 2 off. The MCU
acknowledged all four state packets it received at about 1.87 ms; Nordic exposed
one additional RF packet missed by TI and subsequently retried by the switch.
Manual reports retained the previous command token `b3`, correcting any assumption
that nonzero tokens imply newly commanded changes. The radio is disarmed after
the test; final output state `1` was left as observed.
