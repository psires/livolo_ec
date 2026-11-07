# Experimental XIAO ESP32-C6

Status: transport-only bring-up, not a supported gateway or updater target.
The app shares the [experimental parser](../experimental/README.md) and never
initializes Wi-Fi, Bluetooth or IEEE 802.15.4. A raw-radio adapter, ACK/CCA/FCS
proof and scheduler integration remain subsequent work. USB writes are bounded
and retain a pending reply offset across partial writes; a stalled reader cannot
splice two replies together. Failed reads are retried with a bounded yield.

Test the actual app loop with SDK-free native driver stubs and sanitizers:

```sh
make -C firmware/esp32c6/tests BUILD=/tmp/ec-c6-usb-test test
```

This covers driver-install failure, negative/zero reads, fragmented input,
negative/zero/partial writes, response ordering and rejected control commands.

The inspected device identifies through ROM as ESP32-C6FH4, revision 0.2,
40 MHz crystal and 4 MiB flash. It uses native USB Serial/JTAG, not Nordic-style
programmable USB descriptors. The protocol advertises nominal baud 115200;
tested serial opens leave DTR/RTS deasserted. Stock ROM/loader text can precede
the first numbered response. Match full RSP lines and never parse inside noise.

Build with a separate ESP-IDF v5.5.1 checkout at
`fcae32885b0296b32044cb99ecbdc50d98dddb83` and its pinned submodules/toolchain:

```sh
# Use an isolated IDF_TOOLS_PATH during SDK installation and export.
. /absolute/path/to/esp-idf/export.sh
cd firmware/esp32c6
idf.py -DIDF_TARGET=esp32c6 -B /tmp/ec-c6-build build
```

The app image version derives from `firmware/version.h`. Use a new build directory
and sdkconfig when adopting defaults. ESP-IDF generates bootstrap/partition
artifacts and prints a generic flash command; **those are not the installation
procedure for this bring-up**. Only the application BIN is written at `0x10000`.
The committed partition CSV matches this unit's backed-up stock layout; it is not
a universal factory layout or permission to overwrite another unit's partitions.

The stock loader, partition table, otadata, NVS, SPIFFS and coredump regions are
preserved. ROM recovery uses the exact USB serial identity with esptool 5.6.0's
USB reset, then normal reset. No eFuses, security settings, flash encryption,
calibration or whole-chip erase operations are used. Physical BOOT/GPIO9 plus
RESET remains a documented fallback, not a physically exercised result here.
The stock backup contains the whole 4 MiB flash. Recovery testing restores only
the original sectors touched by the diagnostic app, verifies them, then
reinstalls diagnostics. See the verification record for the actual outcomes.

ESP-IDF and its components retain their own terms. Component selection is
restricted to this app and its dependencies; no claim is made that all SDK files
share one licence. No release artifact, catalog eligibility, automatic update,
power-interruption resilience or radio qualification is established by this port.
