# Firmware installation and recovery

The daemon owns firmware installation, whether requested through the web UI,
Home Assistant's update entity, or the offline maintenance CLI. Clients select
an opaque candidate ID from the daemon. They cannot provide a firmware path,
URL, flash command, or arbitrary version. Installation is explicit, never
automatic on discovery of a newer version.

The installer implementation does not constitute a qualified binary release.
No official deployable firmware bundle accompanies this change. A missing
bundle must appear with no advertised latest version or install action. A
validated candidate whose helper is unavailable may still show its version,
but installation remains disabled with a reason. Test fixtures, dummy signing keys and build
checks must never be packaged as installable firmware.

## Supported installation paths

| Exact board identity | Integrated backend | Recovery |
| --- | --- | --- |
| `codm-cc2652p` | Signed UART application update; bootloader 0.2 required | Trial rollback and independent ROM/GPIO recovery |
| `sonoff-zbdongle-e` | Application-only Gecko GBL through pinned external helper | Preserved Gecko bootloader; no trial rollback |
| `zb-gw04-1v2` | Application-only Gecko GBL through pinned external helper | Preserved Gecko bootloader; no trial rollback |
| `nordic-pca10059` | Disabled pending integrated DFU qualification | Manual application-only factory USB DFU |

The initial integrated scope preserves **460800 application baud**. A deployment
configured at another rate is not eligible for these bundles. MG21 bootloader
uploads remain at **115200**, independently of application baud. Nordic uses
native USB CDC; its nominal baud is not a physical UART rate.

The cod.m development module is CC2652P, with 352 KiB flash and 80 KiB RAM. It
is not CC2652P7. USB bridge names do not prove an MG21 board's identity: the
application's exact board identifier and the image's compiled markers must
agree. A generic CP210x or CH340 device is insufficient evidence to select an
image.

## Normal maintenance lifecycle

Before downtime the daemon validates the selected local artifact, checks live
board/version/update capabilities and disarms the radio. Requests are rejected
while commands, queued radio work or pairing are active; accepted maintenance
stops the normal radio tasks before handing over the port. Web and native APIs remain available while conflicting
radio controls are blocked. The daemon journals each destructive phase before
acknowledging it to the worker. Missing acknowledgements stop the worker before
the next operation.

A stable lock at `<database>.firmware.lock` protects radio ownership. The daemon,
worker and external helper share the inherited lock across serial-port closure.
An orphaned helper retains it until that helper exits. Never delete or replace
this lock file to bypass an active owner. The SQLite database and its journal
remain authoritative; the worker does not edit them.

An upload reaching 100% or a worker reporting `transferred` does **not** confirm
success. The daemon must reconnect, verify the expected board and version,
restore network configuration and peers, and complete its readiness checks.
For TI, the selected image digest must match before trial confirmation. A state
query response is not required to prove update completion. Transfer progress is
reported only when measured; overall progress may remain unspecified.

After interruption, the recorded job is reconciled with the running device.
Unknown outcomes remain unknown/recovery-required. The worker never silently
replays a failed flash, confirms a trial itself, or invokes a generic recovery
routine. MG21 and Nordic do not offer TI's signed-update rollback semantics.

## Trusted local bundle contract

`firmware_bundle_dir` is an absolute local directory configured by the operator;
the default is the installed `livolod/firmware` package directory. A release
packager supplies `manifest.json`, qualified payloads, qualification records and
all required notices. The directory must be writable only by trusted operators.
SHA-256 binds the bytes to this trusted manifest; it does not independently
authenticate the publisher. TI additionally verifies the package's Ed25519
signature against its included public key and the key installed on the radio.

The schema-1 manifest has exactly three top-level fields: `schema` (integer 1),
`host_version` (the installed daemon's `major.minor` release), and `artifacts`
(at most 32 entries). There is at most one entry for each board. Each entry has:

| Field | Requirement |
| --- | --- |
| `id` | 1–96 characters: initial ASCII letter/digit, then letters/digits/period/underscore/hyphen; unique |
| `board`, `backend`, `layout` | Exact combination from the table below |
| `role`, `qualified` | `gateway` and boolean `true` |
| `version`, `protocol` | Exact host release and integer 3 |
| `path`, `size`, `sha256` | Relative payload path, exact integer byte count (256–1048576), lowercase 64-digit digest |
| `app_baudrate` | Integer 460800 |
| `source_commit` | Lowercase 40-digit source commit hash |
| `qualification` | Relative path to a nonempty qualification record |
| `notices` | Nonempty list of at most 32 relative paths to nonempty notice files |

| Board | Backend | Layout |
| --- | --- | --- |
| `codm-cc2652p` | `ti_uart_signed_v1` | `ti-app-8000-20000` |
| `sonoff-zbdongle-e`, `zb-gw04-1v2` | `mg21_gecko_gbl_v1` | `mg21-app-4000-30000` |
| `nordic-pca10059` | `nrf52840_usb_dfu_v1` | `nrf-app-1000-e0000` (reserved; backend disabled) |

TI additionally requires `bootloader_min: "0.2"`, `public_key` (relative path
to exactly 32 raw public-key bytes), and `public_key_sha256`. MG21 requires
`bootloader_baudrate: 115200`. Optional `release_notes` and HTTPS `release_url`
are limited to 4096 characters each. Other fields are rejected. Paths cannot
be absolute, traverse parents, use backslashes or symlinks, or reference
nonregular files. The manifest is limited to 64 KiB; other files to 1 MiB.

The public candidate ID is `id:sha256`, including the entire digest. API clients
must accept candidate IDs up to 256 characters, although this format currently
uses at most 161. Request IDs use `[A-Za-z0-9_.:-]{1,128}`. Selecting an old ID
after the bundle changes is rejected. The candidate must be newer than the
running firmware and match the installed host release; integrated installation
does not downgrade or reinstall the same version.

Validation checks the payload's compiled gateway, board and version markers,
not just its filename. MG21 accepts only the supported application-only GBL
tag sequence, CRC, vectors and properties, programming `[0x4000, 0x30000)`.
Bootloader, security-engine, token and extra-tag writes are rejected. Sniffer
images are not gateway candidates. TI packages must satisfy the signed package
format and application layout checks. The worker revalidates and uses an
immutable byte snapshot rather than trusting an earlier filesystem read.

Release qualification and complete binary redistribution notices remain a
packager responsibility. See [firmware third-party notices](../firmware/THIRD_PARTY_NOTICES.md).
Successful compilation alone does not qualify an artifact for distribution.

## Offline emergency CLI

Use the same configuration and database as the deployment:

```sh
livolod-firmware-manage --config /absolute/path/config.json status
livolod-firmware-manage --config /absolute/path/config.json install --candidate-id 'ID_FROM_DAEMON'
livolod-firmware-manage --config /absolute/path/config.json recover
```

`status` reads the saved journal and can run while the daemon is active.
Before `install` or `recover`, stop the actual owner using the deployment's
systemd, Docker or Home Assistant controls. The CLI does not stop services,
manage containers, or toggle GPIOs. It takes the normal ownership locks and
runs the same update/restore lifecycle without web or MQTT services. Restart
the deployment after maintenance completes. Do not run the low-level worker
directly or invent an inherited lock descriptor.

`recover` is limited to a journaled TI operation. It verifies board and signing
identity before considering any mutation. An incomplete receive may be aborted
and rebooted only when the original version and image digest still match. A
committed candidate is activated only when its digest matches the journal; it
is never aborted. A matching trial resumes normal restoration and confirmation.
A stable selected or previous image is reconciled through the normal lifecycle.
A different candidate, unknown image or mismatched key requires investigation;
the CLI refuses to erase or reset it automatically.

## TI physical recovery and first installation

Initial installation of signed-update bootloader 0.2 is separate from routine
application updates. Follow the [TI gateway build and recovery instructions](../firmware/gateway/README.md#build-and-recovery)
and preserve the module's factory CCFG and ROM recovery access. Once installed,
routine signed UART updates do not require GPIOs or button presses.

RESET and FLASH assignments remain configurable through
`tools/cc26xx_gpio.py --config` or its explicit pin options. The development
setup uses BCM GPIO23 for RESET and BCM GPIO24 for FLASH; these are deployment
settings, not universal hardware requirements. Use the matching hardware
configuration and exact module identity before ROM recovery. Keep the signing
private key out of runtime images and bundles; only the public key belongs there.

## MG21 physical recovery

The worker requests the running application's `BOOTLOADER` command, closes its
UART, and invokes **universal-silabs-flasher 0.0.31** with fixed arguments. For
manual recovery, with the daemon stopped and the exact board's validated GBL:

```sh
python -m universal_silabs_flasher \
  --device /dev/serial/by-id/EXACT_DEVICE_ID \
  --bootloader-baudrate 115200 --probe-method bootloader \
  flash --firmware /absolute/path/EXACT_BOARD_APPLICATION.gbl
```

If the application cannot answer, enter the preserved Gecko bootloader using
the board's BOOT/RESET procedure. The tested Sonoff also supports the helper's
`--bootloader-reset rts_dtr` option; do not assume this wiring applies to ZB-GW04.
See [MG21 installation and recovery](../firmware/mg21/README.md#installation-and-recovery).
Do not replace the bootloader or use a mass-erase/full-flash image for routine
recovery. A stock download is not a backup of this unit's former configuration.

The helper is an external pinned GPL dependency. Its implementation is not
copied into the project's BSD-licensed modules. Distribution of a container or
package containing it must retain its licence/notices and satisfy applicable
source-distribution requirements; invoking a subprocess does not remove those
obligations. See the repository and firmware third-party notices.

## Nordic manual recovery

Integrated Nordic installation is intentionally unavailable. The validated
legacy tooling uses `nrfutil` 6.1.7 in an isolated Python 3.10 environment; it
does not run on the container's Python 3.13. Application-to-bootloader USB
re-enumeration and device access must also be qualified for each deployment.
Do not enable the backend merely because a DFU ZIP is present.

On the official PCA10059, use the small sideways SW2 RESET switch, not SW1.
If pressing it while powered does not enter DFU, hold SW2 while reconnecting
USB and then release it. The red LED pulses in factory DFU mode. That mode
enumerates as `1915:521f`; the gateway application currently uses development
VID/PID `cafe:4001`. Identify each mode explicitly; their serial strings may
differ, and the first `/dev/ttyACM*` is not a reliable selection rule.

Use an application-only USB DFU package and the exact DFU device:

```sh
nrfutil dfu usb-serial --package /absolute/path/PCA10059_APPLICATION.zip \
  --port /dev/serial/by-id/EXACT_DFU_ID
```

Preserve MBR, UICR, bootloader and bootloader settings. Application HEX ranges
must remain within `[0x1000, 0xe0000)`. Retain a known official sniffer recovery
package and follow the [Nordic build/recovery instructions](../firmware/nrf52840/README.md#installation-and-recovery)
for packaging and required legacy helper dependencies. There is no qualified
autonomous stall watchdog or TI-style trial rollback on this port. An
unresponsive application can still require physical USB/button recovery.
