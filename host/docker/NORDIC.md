# Opt-in Nordic USB updates on standalone Linux

The normal installation stays disabled without an explicit `firmware_nordic`
profile. This setup is for an official PCA10059 with its preserved factory
bootloader, application-only DFU ZIPs and a verified physical USB connection.
It does not enable Home Assistant app updates or qualify another board/USB host.
The public firmware catalog remains empty; install only an independently reviewed
catalog matching the daemon release. Routine firmware must be strictly newer
than the running application. Do not weaken that check for repeated testing.

The ARM64 container includes an isolated Python 3.10.22/nrfutil 6.1.7 runtime at
`/opt/nrfutil/bin/python3.10`. Its fixed-port wrapper suppresses vendor USB
discovery and automatic reset/port substitution. Matching source archives,
checksums and notices are under `/usr/share/livolo-ec/sources/nordic`. Native
Python packages do not install this extra runtime automatically.

## Protected host publisher

Record the exact application (`cafe:4001`) and factory DFU (`1915:521f`) serials
and physical USB sysfs parent. These serials may differ. Use `udevadm info
--attribute-walk --name=/dev/serial/by-id/EXACT_ID` to identify the USB ancestor
containing `idVendor`, `idProduct` and `serial`, not its interface or tty child.
An unplugged, duplicated or moved board must fail closed. Do not guess the first
`ttyACM` device.

Stop the daemon and back up its configuration and consistent SQLite state.
Install this release's `host/livolod/nordic_usb.py` as root-owned, mode 0755,
`/usr/local/libexec/livolod-nordic-usb.py`. It is a standalone stdlib script and
does not require a host daemon installation. Create root-owned mode 0600
`/etc/livolod-usb.json`, replacing all identity placeholders:

```json
{
  "usb_path": "/sys/devices/EXACT_PHYSICAL_USB_PARENT",
  "application_serial": "EXACT_APPLICATION_SERIAL",
  "dfu_serial": "EXACT_FACTORY_DFU_SERIAL",
  "device_directory": "/run/livolod-usb",
  "helper_python": "/opt/nrfutil/bin/python3.10"
}
```

Create `/etc/tmpfiles.d/livolod-usb.conf`:

```text
d /run/livolod-usb 0750 root dialout -
```

Create `/etc/udev/rules.d/99-livolod-nordic.rules`:

```text
SUBSYSTEM=="tty", KERNEL=="ttyACM[0-9]*", RUN+="/usr/bin/python3 /usr/local/libexec/livolod-nordic-usb.py --config /etc/livolod-usb.json --group dialout"
```

Load the rule and publish the currently present mode without resetting USB:

```sh
sudo systemd-tmpfiles --create /etc/tmpfiles.d/livolod-usb.conf
sudo udevadm control --reload-rules
sudo python3 /usr/local/libexec/livolod-nordic-usb.py --config /etc/livolod-usb.json
```

The publisher exposes only `application` and `dfu` character nodes, with group
`dialout` and mode 0660. It removes stale/ambiguous selections on subsequent tty
events. It never opens the serial port or sends radio commands. Identity checks
also run inside the worker and daemon, independently of the publisher.

## Container configuration

Retain existing config/data mounts and security settings. Remove the static
`devices:` mapping; it cannot follow USB re-enumeration. Add read-only bind mounts
from `/run/livolod-usb` to the same container path, and from your protected catalog
directory to `/etc/livolod/firmware`, with `create_host_path: false` for both.
Grant the numeric host `dialout` group through `group_add` and only:

```yaml
device_cgroup_rules:
  - "c 166:* rw"
```

Keep the non-root user, read-only root filesystem and `cap_drop: [ALL]`; the
container needs neither privileged mode, host `/dev`, nor mknod permission.
In the existing JSON configuration set `serial_port` to
`/run/livolod-usb/application`, `firmware_bundle_dir` to
`/etc/livolod/firmware`, and `firmware_nordic` to the same complete profile above.
Keep the configured database and network identity unchanged.

Recreate the container, verify normal radio/MQTT readiness, then use **Network →
Gateway firmware → Run preflight**. Only a matching successful preflight can
enable an update review. The update must verify USB return and normal network
restoration before reporting confirmation. An image/software upgrade alone
does not flash the radio.

## Interrupted transfer

Stop the daemon before direct recovery. Check the durable CLI `status`; do not
erase its journal. Enter factory DFU using the board-specific SW2 procedure if
necessary. From the deployment directory, run the one-off Compose service with
`--entrypoint livolod-firmware-manage`; it uses the same config/data/catalog/node
mounts and ownership locks. Explicit recovery requires all of:

```text
--config /etc/livolod/config.json flash --operation recovery
--board nordic-pca10059 --artifact /etc/livolod/firmware/EXACT_APPLICATION.zip
--version MATCHING_HOST_VERSION --sha256 REVIEWED_PACKAGE_SHA256
--dfu-port /run/livolod-usb/dfu --dfu-serial EXACT_FACTORY_DFU_SERIAL
--helper-python /opt/nrfutil/bin/python3.10 --retry-interrupted
```

Use `--check-only` first. A successful manual transfer returns exit 2 and
`manual_verification_required`. Separately run `recover --application-serial
EXACT_APPLICATION_SERIAL` with the same config to verify identity and restore
the network; only then restart the normal container. No automatic retry or
same-version banner can clear an incomplete transfer. The earlier command ledger
is retained when an explicit retry supersedes an interrupted Nordic job.
