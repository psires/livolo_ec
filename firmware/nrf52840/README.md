# Nordic PCA10059 gateway

Application-only nRF52840 port of the shared gateway core, release 0.12.
Native USB CDC carries UART protocol v3; the advertised 460800 value preserves
host configuration compatibility, but does not set a physical UART bit rate.
IEEE automatic ACKs, BLE and Zigbee stacks are disabled. Channel 11, 0 dBm,
phase-stamped Livolo replies, timed queues, heartbeats, complete query/output
transactions, retry limits and the 200 ms grid come from the existing core.
All peers, leases and jobs are volatile. Authoritative state stays on the Pi.

This target is specifically the **official Nordic PCA10059**. It is not a generic
nRF52840 image. Hardware qualification is recorded separately; a successful build
alone does not establish reliable RF operation.

See [third-party notices](../THIRD_PARTY_NOTICES.md) for the pinned SDK inputs,
file-specific licence exceptions and binary-distribution requirements.

## Dependencies and build

Clone these dependencies into one directory, keeping their licenses with them:

| Directory | Upstream | Pinned revision |
| --- | --- | --- |
| `tinyusb` | https://github.com/hathach/tinyusb | 0.18.0, `86ad6e56c1700e85f1c5678607a762cfe3aa2f47` |
| `nrfx-1.8.6` | https://github.com/NordicSemiconductor/nrfx | v1.8.6, `d68126c1acc9e2a7575a2c5de4a06f279d780f4f` |
| `radio-driver` | https://github.com/NordicSemiconductor/nRF-IEEE-802.15.4-radio-driver | v1.8.0, `0b972851eed5e1fddc6b9a6ac022fe46fe445ff4` |

Populate TinyUSB's pinned `lib/CMSIS_5` dependency using its dependency script
(commit `2b7495b8535bdcb306dac29b9ded4cfb679d7e5c`).
The standalone radio-driver tag contains the complete bare-metal driver; newer
SDK integration layouts are not drop-in replacements. This port uses the nrfx
1.x HAL explicitly. No dependency source is downloaded by `make`.

```sh
make DEPS=/absolute/path/to/nrf-port
```

GNU Arm Embedded GCC builds ELF, application HEX and binary files in `build/`.
The linker reserves MBR flash 0..0x1000, factory bootloader/settings from 0xe0000,
and the first eight bytes of RAM for the MBR. Application vectors start at
0x1000; RAM ends at 0x20040000. The build does not define SDK options that rewrite
UICR/reset/NFC settings. Do not mass-erase the dongle or flash a merged image.

The radio driver uses TIMER0/1/2, RTC2, EGU and PPI resources. TIMER3 is dedicated
to the 1 MHz gateway clock; SysTick provides milliseconds for leases. The main
loop continuously services RF, USB and the shared scheduler. The LF clock-ready
callback is synchronous because the driver waits for it inside initialization;
HF readiness is deferred to avoid reentrancy. Radio callbacks copy into a bounded
16-slot queue; USB output has an 8 KiB ring. Native USB
hardware provides transfer CRC/retry and backpressure. Radio queue losses,
receive errors, TX timeouts and rejected output bytes are reported separately.
A full software queue is not proof of RF loss; packets never received cannot be
counted. The USB controller runs below the radio IRQ priority.

## Installation and recovery

Preserve the PCA10059 factory **Open DFU Bootloader**. Enter it initially with
the small sideways RESET switch (SW2); LD2 pulses red. SW1 is the user button.
The bootloader enumerates as Nordic `1915:521f`, independently of the application.
Use Nordic's USB serial DFU tool and an **application-only** package:

```sh
nrfutil pkg generate --hw-version 52 --sd-req 0x00 \
  --application-version 12 --application build/ec-nrf52840.hex gateway-0.12.zip
nrfutil dfu usb-serial --package gateway-0.12.zip --port /dev/serial/by-id/EXACT_DFU_ID
```

These commands use legacy Nordic `nrfutil` 6.1.7. On the development Pi it runs
in an isolated Python 3.10 environment because system Python 3.13 is unsupported.
The USB path needs the Python exception/helper files from official
`pc-ble-driver-py` v0.17.0, not its x86-only native BLE library. The modern Nordic
nrfutil command suite has different subcommands; do not assume this syntax fits.

Before flashing, verify every HEX data range lies in `[0x1000, 0xe0000)`, the
initial stack is aligned within RAM and the reset vector is Thumb/application
code. Retain an official PCA10059 sniffer application recovery package. The
upstream sniffer image used in this development is from
`nordicsemi/nRF-Sniffer-for-802.15.4` revision
`b69293680ac92aeddc10807314d564e4c929dcd3`, filename
`nrf802154_sniffer_nrf52840dongle.hex`. This is a stock recovery image, not a
readback of the originally installed firmware.

The gateway's protocol-v3 `BOOTLOADER` command disarms, flushes USB boundedly,
and pulls PCA10059 P0.19 low to assert its wired reset signal. The preserved
bootloader enters DFU on that pin reset. This follows Nordic's
[USB DFU trigger design](https://docs.nordicsemi.com/r/bundle/nrf5_sdk_v15.0.0/page/lib_dfu_trigger_usb.html).
The self-reset pin is left untouched during normal operation. A separate
experimental GPIO/timer stall-recovery mechanism failed qualification and was
removed. No autonomous watchdog is currently qualified; an unresponsive image
may require holding SW2 while reconnecting USB, as observed during bring-up.

Normal commands report `fw_version=0.12`. The TI signed updater/rollback protocol
is not implemented here: `update=none` remains accurate. Factory open DFU accepts
unsigned application packages, so authenticating update artifacts is a host or
deployment responsibility.

USB uses TinyUSB's development VID/PID `cafe:4001`, a project label and a stable
FICR-derived serial. Obtain an appropriate VID/PID allocation before distributing
a product. The bootloader's serial string may differ; select each mode explicitly.
The port boots unconfigured/disarmed. Never run this adapter and another gateway
with the same network identity armed together during qualification.


## Verification

Run `python3 firmware/nrf52840/tests/test_clock.py` from the repository root to
exercise the startup callback contract against the actual C adapter. The shared
core tests are `python3 -m unittest discover -s firmware/gateway/tests`.
The existing `hardware_v3.py`, `hardware_scheduler_dry.py` and
`hardware_uart_integrity.py` accept the native-USB by-id path with `--baud 460800`.
Stop the daemon before exclusive hardware checks; they alter volatile
configuration and must not share its USB connection.

See the [board qualification record](../../analysis/development/nrf52840-012-20261101.md)
for the exact application hashes, tests and independent Sonoff capture. Use
`tools/analyze_nrf52840_012.py` from the repository root to reproduce that RF
comparison. That evidence covers one PCA10059 and two registered switches.
