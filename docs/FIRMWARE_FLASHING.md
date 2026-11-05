# Shared firmware flashing tools

Use `livolod-firmware-manage` for application inspection, preflight, normal
updates and explicit Nordic recovery. The CLI and daemon use the same Python
validators and transfer backends. Web and Home Assistant requests use the
daemon's durable update job; they do not execute shell commands or accept
arbitrary paths from the browser.

From a source checkout, install the host package with its flashing dependencies:

```sh
python -m pip install './host[flash]'
livolod-firmware-manage --help
```

For the native standalone archive, use its included `README.md` and install the
daemon wheel with `[mqtt,flash]` from `packages/`, using the included constraints.
The native wheel does not install a second Python interpreter.

Nordic uses an isolated Python 3.10 environment with `nrfutil==6.1.7`, including
the USB dependencies described in the
[Nordic README](../firmware/nrf52840/README.md#installation-and-recovery).
The updated container build includes that helper at
`/opt/nrfutil/bin/python3.10`, separately from the Python 3.13 daemon. Having the
helper installed does not enable automatic Nordic updates: the exact USB profile,
device access and a trusted matching catalog must also be configured.

## Board and artifact selection

| Board argument | Application artifact | Routine daemon update | First installation/recovery |
| --- | --- | --- | --- |
| `codm-cc2652p` | Signed `.ecupd` package and raw Ed25519 public key | Signed UART updater, bootloader 0.2 | One-time full image via TI ROM script below |
| `sonoff-zbdongle-e` | Exact Sonoff application `.gbl` | Preserved Gecko bootloader, helper 0.0.31 | Enter its existing bootloader, upload the validated exact-board GBL |
| `zb-gw04-1v2` | Exact ZB-GW04 application `.gbl` | Preserved Gecko bootloader, helper 0.0.31 | Enter its existing bootloader, upload the validated exact-board GBL |
| `nordic-pca10059` | Application-only legacy DFU `.zip` | Default off; explicit USB profile and isolated helper required for the standalone trial | Explicit factory-DFU recovery, followed by deliberate application verification |

A USB-to-UART bridge identifies the transport, not the radio board. Never select
a firmware solely because the OS says CP210x, CH340 or `/dev/ttyACM0`. The cod.m
module tested here is CC2652P, not CC2652P7. Select each MG21 board explicitly;
their pin mappings differ despite sharing a chip family.

The repository's default public firmware catalog is currently empty. The
[0.13 standalone trial catalog](FIRMWARE_013_TRIAL.md) is separately prepared and
explicitly selected on the test Pi; it is not a public production update channel.
Local image inspection alone does not qualify a catalog release. A deployment
without matching artifacts keeps normal Web/HA installation unavailable.

## Inspect without touching a radio

These examples use placeholder paths and a `0.13` image; use the actual release
printed by the build and contained in the image:

```sh
livolod-firmware-manage inspect \
  --board sonoff-zbdongle-e --artifact /absolute/path/sonoff.gbl --version 0.13

livolod-firmware-manage inspect \
  --board codm-cc2652p --artifact /absolute/path/gateway.ecupd --version 0.13 \
  --public-key /absolute/path/update-public-key.bin

livolod-firmware-manage inspect \
  --board nordic-pca10059 --artifact /absolute/path/gateway.zip --version 0.13
```

Inspection is local and does not open UART, reset a board or initialize a daemon
session. It bounds file reads, rejects symlinks/nonregular files, verifies the
container and returns JSON metadata. The private library result also retains
immutable bytes for the worker; callers must not serialize that entire object.

All application checks include compiled gateway role, exact board, expected
version, vectors and permitted flash range. TI additionally verifies the signed
manifest, Ed25519 signature and SHA-512. MG21 validates the application-only
GBL tag sequence, CRC and application properties, excluding bootloader, security
engine and token writes. Nordic checks exactly three ZIP members, rejecting
duplicates, links and traversal; it verifies the application-only init packet,
hardware 52, no SoftDevice requirement other than zero, nondebug mode, image
length and SHA-256. Its application range is `[0x1000, 0xe0000)`.

Nordic firmware version integers use `(major << 16) | minor`. Its init packet
accepts the observed unsigned legacy package format and generated-CRC boot
validation; alternate signed/init variants are deliberately rejected pending
qualification. The implementation follows the published
[Nordic DFU schema](https://github.com/NordicSemiconductor/pc-nrfutil/blob/v6.1.7/nordicsemi/dfu/dfu-cc.proto).

These checks establish structural compatibility. An unsigned GBL or DFU ZIP
still needs a trusted source; a matching hash is not independent publisher
authentication. No static image validator can guarantee the behavior of
arbitrary machine code that happens to contain valid vectors and markers.

## Preflight and normal installation

```sh
livolod-firmware-manage --config /absolute/path/config.json preflight \
  --candidate-id 'CATALOG_ID' --board sonoff-zbdongle-e

# Stop the deployment owner before explicitly probing its serial device.
livolod-firmware-manage --config /absolute/path/config.json preflight \
  --candidate-id 'CATALOG_ID' --probe

livolod-firmware-manage --config /absolute/path/config.json install \
  --candidate-id 'CATALOG_ID'
```

Default preflight does not send UART commands. Without live identity evidence,
it reports missing checks rather than claiming the attached board is compatible.
Explicit `--probe` takes ownership guards and issues read-only identity/status
commands; it does not reset, disarm, configure or arm the adapter. API preflight
uses the daemon's already-owned session. Preflight is advisory: installation
revalidates the image and target before writing.

Normal installation closes pairing, drains bounded work, disarms and hands the
radio to the worker. The daemon remains responsible for durable phase
acknowledgements and restoring its authoritative registry afterward. TI trials
are confirmed only after successful verification and restoration. MG21 uploads
use 115200 bootloader baud while the application profile remains 460800.

For a trusted local TI/MG21 application, the CLI can run the same update
lifecycle without placing that file in the Web/HA catalog. Bind the selection
to a separately verified SHA-256 and use the exact supported board:

```sh
livolod-firmware-manage --config /absolute/path/config.json flash \
  --operation update --board sonoff-zbdongle-e \
  --artifact /absolute/path/sonoff.gbl --version 0.13 \
  --sha256 EXPECTED_PACKAGE_SHA256 --check-only
```

Remove `--check-only` to request the actual update after stopping the normal
owner. TI additionally requires `--public-key`. Normal update rules, including
a newer application version and supported host/firmware compatibility, still
apply. The explicit local file is not exposed as an API upload mechanism.

See [recovery and lifecycle details](FIRMWARE_RECOVERY.md) and the
[native API](../host/NATIVE_API.md). The same preflight/install services are
available to Web/HA; Nordic arbitrary-file recovery is local-CLI-only.

## Opt-in Nordic Web update path

The standalone trial uses an explicit `firmware_nordic` configuration containing
the physical sysfs USB path, application serial, factory-DFU serial, published
device directory and isolated helper interpreter. None of those identities is
guessed from the first matching tty. The application and factory bootloader use
different VID/PID pairs and may use different serial strings.

For containers, a root-owned host publisher exposes only the selected board's
current `application` and `dfu` character-device nodes through a mounted
directory. A fixed Docker mapping of the original application tty is insufficient
across USB re-enumeration. Follow the
[container deployment instructions](../host/docker/README.md) and the
[trial verification record](FIRMWARE_013_TRIAL.md); do not enable this profile
merely because a dongle belongs to the same MCU family.

Before a transfer, the worker rechecks the exact USB target and live firmware
identity. It enters the factory bootloader only after the durable job
acknowledgement, waits for the configured DFU identity, and invokes a fixed-port
transport that disables the vendor helper's automatic discovery. After transfer,
it checks the returning application and firmware version. The daemon separately
checks identity before restoring its network session and confirming the job.
An incomplete transfer remains blocked across restarts and requires recovery.

The profile defaults to empty, including in ordinary HA deployments. A configured
profile enables only that deployment; it does not turn the local trial result
into general USB hotplug or all-board qualification. Actual observed results and
their limits are recorded separately from these implementation checks.

## Explicit Nordic recovery

Stop the deployment owner and enter the PCA10059 factory DFU mode using SW2.
If needed, hold SW2 while reconnecting USB. The red LED pulses; the factory
bootloader enumerates as `1915:521f`. Supply its exact device path and serial
number. The script refuses a missing target, a duplicate identity or the
application's different USB mode.

The unified `flash --operation recovery` command requires an exact board,
artifact, version, expected digest, DFU path, DFU serial and isolated helper
interpreter:

```sh
livolod-firmware-manage --config /absolute/path/config.json flash \
  --operation recovery --board nordic-pca10059 \
  --artifact /absolute/path/gateway.zip --version 0.13 \
  --sha256 EXPECTED_PACKAGE_SHA256 \
  --dfu-port /dev/serial/by-id/EXACT_DFU_ID --dfu-serial EXACT_DFU_SERIAL \
  --helper-python /absolute/path/nrfutil-venv/bin/python --check-only
```

Remove `--check-only` for the actual transfer. It never
chooses the first serial device or automatically enters DFU. It verifies the
pinned runtime and that its USB command imports before the write boundary.

When the configuration includes the exact Nordic USB profile, manual recovery
also verifies that profile and can use `/run/livolod-usb/dfu` with the isolated
`/opt/nrfutil/bin/python3.10` helper. The explicit `--dfu-serial` must still match.
Without that profile, the host's serial-device metadata must identify the
operator-selected port. Both routes invoke the fixed-port transport; neither
permits the vendor helper to substitute another dongle.

The worker revalidates immutable bytes, obtains the durable flash acknowledgement,
rechecks the USB identity, and runs the external helper with a bounded lifetime.
The helper inherits the ownership lock; a surviving child prevents a restarted
daemon from taking the same deployment's radio. Do not swap dongles or remove
the USB connection during a transfer.

Successful transfer is recorded as requiring verification, not as an online
gateway. The application uses development VID/PID `cafe:4001` and can have a
different USB serial from the bootloader. Explicit `recover --application-serial`
checks the selected application identity, live board/version/capabilities and
successful prior transfer before normal restoration. A daemon restart alone
does not approve that transition. Failed or interrupted transfers cannot be
cleared merely because an application reports the same version. The transfer
command exits with code 2 while verification is required:

```sh
livolod-firmware-manage --config /absolute/path/config.json recover \
  --application-serial EXACT_APPLICATION_SERIAL
```

An interrupted manual transfer is never automatically retried. If physical
inspection confirms the intended dongle is again in DFU, `flash` supports an
explicit `--retry-interrupted` request to replace that interrupted recovery job.
It still performs the same package, identity and helper checks.
The same deliberate recovery route can supersede an interrupted automatic
Nordic update after ownership locks are acquired; the previous command record
is retained.

The manual recovery procedure requires deliberate selection of both USB modes;
it does not use the opt-in automatic profile to infer their correspondence.
Nordic has no TI-style trial rollback or qualified autonomous stall watchdog.
Physical recovery can still be required. The helper remains a separately
licensed Nordic dependency; no vendor flashing implementation is copied into
these validators.

## TI first installation

The full-image TI ROM installer is intentionally separate from routine signed
application updates. It overwrites main flash and requires a board already in
ROM download mode. Stop the daemon and follow the exact module's build/recovery
instructions before using it. GPIO pin assignment remains configurable through
`tools/cc26xx_gpio.py`; the flash script does not toggle GPIOs implicitly.

```sh
python tools/flash_cc26xx.py /absolute/path/full-image.bin --check-only \
  --sha256 EXPECTED_FULL_IMAGE_SHA256

python tools/flash_cc26xx.py /absolute/path/full-image.bin \
  --port /dev/ttyAMA0 --bsl-source /absolute/path/cc2538-bsl \
  --sha256 EXPECTED_FULL_IMAGE_SHA256 --reset
```

The check-only path needs no serial port or bootloader helper. It validates
352-KiB image size, vectors, active-low DIO15 ROM recovery and JTAG/erase access.
The live script additionally reads silicon/flash/factory identity before erase
and verifies full-flash CRC and recovery configuration afterward. The supplied
hash must come from the trusted image build, not be copied from an untrusted
download merely to satisfy the check. The full-image checker does not provide
the application package's signature or release-role assurance.

## Validation scope

Offline checks cover synthetic containers, exact local image inspection, mocked
UART/USB endpoints and subprocess ownership. The separately authorized
[standalone trial](FIRMWARE_013_TRIAL.md) records live qualification when it is
completed. Neither those tests nor a firmware catalog alone establish broader
RF reliability or qualify a production release channel.
