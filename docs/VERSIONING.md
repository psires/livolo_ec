# Software versions and radio compatibility

This separation is in development after the published 0.4.0 distribution.
It does not change the installed radio or automatically start a firmware update.

| Version | Meaning in the development tree |
| --- | --- |
| Daemon software `0.14.0` | Version of the installable `livolo-ec-daemon` package |
| Firmware compatibility `0.13` | Exact firmware target used by catalog admission and update validation |
| Native API `1` | Contract between the daemon, HA integration and other native clients |
| UART protocol `3` | Contract between the daemon and radio adapter |

The standalone/container distribution and HA integration have their own release
numbers. The Release Engineer assigns those when preparing a release. A larger
daemon software version does not mean the radio needs firmware with the same
number.

## Reading versions

`livolod --version` reports the daemon software version. It matches the installed
Python package metadata. For explicit machine-readable fields, run:

```sh
livolod --version-info
livolod-commission --version-info
livolod-firmware-manage --version-info
livolod-firmware --version-info
```

These commands report versions without loading a configuration, opening a
database, contacting the daemon or accessing a radio. Firmware artifact
inspection remains a separate operation.

The native API adds `daemon_version` and `firmware_compat_version`. API 1's
existing `host_version` field retains its historical firmware-compatibility
value for older clients. New scripts must use the explicit fields rather than
interpret `host_version` as the software package version.

Web and HA distinguish daemon software, installed radio firmware and the
compatibility target. When connected to an older daemon that does not report a
software version separately, clients show that limitation instead of guessing.
Firmware update availability continues to depend on the installed radio and a
compatible catalog candidate.

## Upgrading existing installations

The first independently versioned daemon package is `0.14.0`. Python package
ordering considers it newer than `0.13`; an overall distribution version such
as `0.5.0` would not be a valid newer daemon package version.

Use the normal installation or container-upgrade procedure for the selected
release. Preserve the configuration, database and local firmware catalog. This
version separation requires no database conversion, device re-pairing or change
to HA entity identities. Interrupted operations are not automatically replayed.

Scripts that expected two components from `livolod --version` must switch to
`--version-info` and read `firmware_compat_version` when they need the firmware
target. Software checks should read `daemon_version`.

Existing catalog schema 1 is unchanged: its legacy `host_version` field still
means firmware compatibility. Do not rewrite catalog versions or relabel old
firmware to match a software package. Exact board, image, signature, baud,
protocol and trial-confirmation checks still apply.

For rollback, select the exact previous software artifact and follow its
documented backup/restore procedure. Keep published tags and artifacts intact;
rollback does not mean overwriting an existing release.

See the [approved design](DAEMON_VERSIONING_PROPOSAL.md) and
[release quality and acceptance process](RELEASE_PROCESS.md) for engineering
requirements and validation gates.
