# cod.m CC2652P passive receiver

First hardware bring-up firmware for the Livolo EC local gateway. It receives raw
IEEE 802.15.4 frames on channel 11 and streams them over UART at 115200 baud, 8N1,
without flow control. It has no pairing, switch control, or MQTT implementation.

The custom MAC passes bytes directly from the TI radio driver to UART. It does
not parse Zigbee headers, send packets, or acknowledge frames. Automatic ACK is
disabled at radio initialization and in the MAC configuration. BLE and sub-GHz
operation are disabled. Frames failing the radio FCS check are discarded by the
radio; the MAC FCS is removed from accepted frames before UART output.

## Reproducible dependencies

- Contiki-NG `7a4df8a941d552b10c730db70e934a9be10937af`
- TI Core SDK submodule `60e7325e122e95660fa9a4019e7868aedb4c317d`
- CMSIS submodule `2b7495b8535bdcb306dac29b9ded4cfb679d7e5c`
- Debian ARM GCC / newlib packages: `gcc-arm-none-eabi libnewlib-arm-none-eabi`

The repository does not vendor these dependencies. Their upstream licenses apply;
see [third-party notices](../THIRD_PARTY_NOTICES.md).
The generated board adapter copies the licensed CC1352P-2 BSP, retaining notices.
That BSP already uses module UART RX DIO12 / TX DIO13 and the P2 antenna-switch
mapping. The adapter removes LaunchPad external flash initialization/pins and
sub-GHz/BLE support. The upstream `DEVICE_CC1352P` compatibility define selects
the shared x2 PA radio implementation; the physical chip is the verified CC2652P.
Its linker map is **352 KiB flash / 80 KiB SRAM**, not the P7 map.
The generated `CC2652P_PIN.c` retains the TI driver's license and adds the missing
P-family GPIO range handling for the verified wafer/user identity. The bundled
SDK's chip-type detector predates CC2652P and otherwise reports it as CC2652.
Hardware validation confirmed 26 GPIOs and successful pin/platform initialization
with this fix. The old DIO0..25 calculation rejected the RF-switch pins.
The project CCFG overrides preserve this module's factory oscillator tuning and
power configuration; the complete generated CCFG block was compared byte for byte.
`toolchain-compat.h` supplies a type-checked missing PRIx64 macro for Debian's
newlib 4.5 headers, used by an upstream debug log that is disabled in this build.

In the initial bring-up, the normal build with the pin fix ran successfully.
Full-image ROM CRC32
`7d1c9fe2` matched, startup completed, and channel-11 EC frames are received over
UART. Periodic status confirms `rx_mode=0` (no address filtering or auto-ACK).
See the development bring-up notes for capture comparison results and limitations.
For early startup diagnostics, use `BOOT_DIAGNOSTICS=1 BUILD_DIR=build/diagnostic`
and target the binary under that directory. This enables temporary polled UART
instrumentation around device trimming and platform initialization. The corrected
optional diagnostic code compiles; the installed normal image omits it.

## Build on the Pi

From `/home/sfires/livolo-ec`, after cloning the pinned Contiki revision:

```sh
git -C vendor/contiki-ng submodule update --init --depth 1 \
  arch/cpu/arm/CMSIS \
  arch/cpu/simplelink-cc13xx-cc26xx/lib/coresdk_cc13xx_cc26xx
python3 firmware/receiver/prepare_board.py vendor/contiki-ng
cd firmware/receiver
make -j4 build/simplelink/launchpad/codm-cc2652p/ec-receiver.bin
```

The full binary includes CCFG at `0x57fa8`. DIO15 active-low bootloader entry and
JTAG remain enabled. `tools/flash_cc26xx.py` checks vectors, flash size, silicon,
and recovery settings before erasing. It verifies the entire flash CRC32 and
bootloader word after programming, and resets only after verification succeeds.
It uses the already synchronized bootloader, always at `/dev/ttyAMA0`; it never
autodetects another USB device. Enter/synchronize with `tools/probe_cc26xx.py`
after holding FLASH and pressing/releasing RESET when needed.

## UART protocol v1

```text
EC-RECEIVER 1 board=codm-cc2652p baud=115200 ticks=<ticks_per_second> tx=disabled
RX <sequence> <ticks> <rssi_dbm> <lqi> <length> <mac_hex_without_fcs>
STATUS channel=11 rx_mode=0 ticks=<ticks>
```

Upstream startup logs may precede these records. `sequence` counts frames passed
to the UART callback; it cannot detect RF frames lost before that callback.
`ticks` is the software callback time, not a precise RF timestamp. Periodic
STATUS records provide liveness, channel, and confirmation that filtering and
ACKs remain disabled (`rx_mode=0`). No UART command parser is included yet.

`tools/capture_cc26xx.py` writes a Wireshark-compatible TAP pcapng and preserves
all UART output in a sidecar log. Its capture timestamps are host UART receipt
times and **must not be used for protocol response timing measurements**. Use the
Nordic sniffer for that. At 115200 baud, sustained dense traffic can exceed this
diagnostic text stream's capacity; it is a bring-up interface, not a final gateway
transport. Existing `tools/decode_ec.py --skip-unknown` reads either capture source.
