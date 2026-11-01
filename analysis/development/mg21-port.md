# ZB-GW04 v1.2 MG21 port — 2026-10-24

The development Pi's existing `livolod` 0.2 service now uses the MG21 USB
adapter. Network identity `2a992b12`, tag `a7`, the existing device registry,
MQTT settings and host software are retained. Only `serial_port` changed to
`/dev/serial/by-id/usb-1a86_USB_Serial-if00-port0`. The TI adapter remains
installed on `/dev/ttyAMA0`, disarmed and available for rollback.

## Identification and build

The user identified the PCB as ZB-GW04 v1.2. USB reports CH340 `1a86:7523`.
The UART answered with Gecko Bootloader **1.9.2**, 115200 baud. Application
startup read `DEVINFO.PART=01150014`, `DEVINFO.MEMINFO=01000103` and a 38.4 MHz
core clock. PART identifies MG family 21, device number 20. The build profile
is EFR32MG21A020F768IM32, 768 KiB flash / 64 KiB RAM, USART0 PB1/PB0 and
HFXO CTUNE 128, matching the published ZB-GW04 v1.2 board definition.

Sources: [MG21 target](../../firmware/mg21/README.md), shared gateway core and
[bounded GBL packager](../../tools/mg21_gbl.py). SDK v4.4.6 commit
`59d2160fe448b13730a91c0f019a8b4c53c43eb2`; RAIL archive SHA-256
`630a5f8a07aaf034c3de83cd99b832ba4eefa658d8332bc12e537f1df6fee9f0`.
Debian arm-none-eabi GCC 14.2.1 produced the installed application:

- Application binary SHA-256:
  `2327e0e1cef6d1a3e9e1293367c9a3ff5f3e0c5ff8f7b10d0fefb8b723261040`.
- Application-only GBL SHA-256:
  `ecfc6036ab3dc4923ee30d1170ba643aeb8f9a7af5af61ba0d90b6412db9a18c`.
- Binary 53,168 bytes; ELF text/data/BSS 52,963 / 204 / 16,000 bytes.
  Stack reserves a further 8,192 bytes.
- Application starts at `0x4000`; generated program tags remain below `0x30000`.
  The existing bootloader, Secure Engine and token areas are not in the package.
- An independent `universal-silabs-flasher` 0.0.31 GBL parser accepted both
  packages. Gecko accepted the uploads and started the applications. Its menu
  does not offer full flash readback; these are build hashes, not readback hashes.

Release remains 0.2, with UART protocol 3. MG21 reports its own board identity,
1000 Hz lease/status clock, 1,000,000 Hz phase clock, and `update=none`.
The existing TI signed package format and automatic rollback are not implemented
on MG21. The preserved Gecko bootloader supports application GBL recovery.

## Hardware verification

The original TI service was stopped while MG21 used the same radio identity,
then restored after each bounded lab test. Those initial port checks issued no
pairing or output-setting commands. Test queries were limited to the already
paired spare `46715621`. The subsequent authorized output trial is recorded below.

The existing UART v3 harness passed all 12 dry checks and all 13 air checks:
256 peers, real C ACK lookup for every slot, mask/bounds enforcement, lease
expiry, stale-session rejection, retry-cache semantics, overlong-line rejection,
metadata and real-peer ACKs at slot 255. The dry test transmitted nothing.

The Nordic independently captured:

- Exactly one copy of each of three self-addressed diagnostic transmissions,
  despite replaying each UART request.
- Exactly one copy of each of three read-only query transmissions.
- All five ACKs reported by MG21, with exact matching bytes.
- Request-to-ACK SFD intervals: 2.165, 2.146, 2.145, 2.149 and 2.148 ms.

Those air checks used the first functional build. The final build adds hardware
loss diagnostics and RX recovery if a TX FIFO write fails; framing and ACK policy
are unchanged. The final build was independently tested with three self-addressed
phase probes. Each was seen exactly once despite duplicate UART requests. The
phase encoding matched the 1 MHz clock and 65500 trailer scale. Difference
between encoded phase and measured RF phase was 0.018, 0.056 and 0.112 ms.
These three samples are not a bound under congestion. `tx_entry_v1` is still
radio-call entry, not SFD scheduling.

`BOOTLOADER` software entry returned to the original 1.9.2 menu. A second upload
succeeded without buttons or Pi GPIO. Running the application again cleared
network/peers/session/leases, and the old session's SEND was rejected. Final
phase and reboot checks had zero radio, UART, receive-log or metadata drops.

## Deployment and recovery

Pi work directory: `/home/sfires/livolo-ec/mg21-port`.
Builds: `firmware/mg21/build`; installed package: `candidate-03.gbl`.
Logs, raw pcaps, pcapng, UART records and JSON results are under `evidence`.
Local copies are retained in `.local/mg21/evidence` and are not source artifacts.

Migration preserved all configuration fields except the serial port, backed up
SQLite, and verified radio/MQTT readiness, the existing gateway/tag/device,
armed ACK reception, and a valid phase reference. No host package replacement
or schema migration was required. An 82-second observation (beyond two 30-second
leases) retained readiness and MQTT, renewed the phase reference, confirmed five
automatic ACKs, and recorded zero radio/UART/logging losses. A subsequent daemon
restart established a fresh MG21 session, restored readiness/MQTT/phase tracking,
and preserved the device registry and saved MQTT settings byte-for-byte against
the database backup. Systemd reported zero automatic restarts. The build-source
manifest matched every checked workspace firmware source. Recovery backups:

- `/etc/livolod/config.before-mg21-20261024.json`
- `/var/lib/livolod/backups/before-mg21-20261024.sqlite3`

To return to TI, stop `livolod` and ensure MG21 is disarmed (or allow its
30-second lease to expire), restore the saved configuration with its original
owner/mode, then start `livolod`. Keep the current database so later names/state
are retained. Do not run both adapters armed with the same gateway identity.

On 2026-10-24, a [controlled output trial](mg21-toggle-20261024.md) verified one
OFF → ON → OFF cycle on each output through the deployed MG21. All four logical
commands received matching native state reports independently captured by the
Nordic sniffer. The fresh baseline and final state were both OFF/OFF; report
counters advanced from 113 to 117. Existing retries produced 11 command copies.
State refresh required retries before the baseline was obtained.

Signed MG21 updates with trial rollback, long-term reception under load, and
multi-device RF operation remain unqualified. The output trial verifies
device-reported states, without an external electrical measurement. The
previously documented output-control reliability limitations still apply; one
cycle per output does not establish long-term switching reliability.

## Regression checks

All 149 automated tests passed: 37 tool tests (including two MG21 package
validation tests), 18 shared gateway tests, 87 host tests and 7 TI update-core
tests. The shared source also builds successfully for CC2652P with the pinned
Contiki/TI toolchain dependencies. Python documentation, Black, changed C
formatting, release consistency and whitespace checks passed.
