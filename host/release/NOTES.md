Standalone daemon and CLI distribution **0.2.0**, containing daemon **0.12**
and Python API client **0.2.0**. Component versions are preserved because daemon
and radio firmware share an explicit compatibility contract.

### Install

Download `livolo-ec-0.2.0-standalone.tar.gz` and `SHA256SUMS`, verify the archive,
and follow its README. No source checkout is required. Use Linux with Python
3.13 and venv support; dependencies are installed separately from PyPI.
See the [installation, upgrade and recovery guide](https://github.com/psires/livolo_ec/blob/standalone-0.2.0/host/release/README.md).

### Included

- Installable daemon and client wheels, plus source distributions.
- `livolod`, `livolod-firmware-manage` and the low-level maintenance worker.
- Local web interface, native API and optional MQTT support.
- systemd unit, configuration example, pinned dependency constraints and a
  non-root simulated-service smoke check.
- Emergency firmware recovery instructions, BSD-2-Clause licence and dependency
  notices, exact source/component metadata and SHA-256 checksums.

The standalone archive contains everything authored by this project needed for
native installation. The same wheels and source packages are separately attached
for packaging tools. The Python client is a library, not a remote-control shell.

### Validation and limits

Publication requires fresh wheel and source installs on Linux **x86-64 and
ARM64**, Python 3.13, including dependency consistency, all CLI help commands,
configuration checks, the external flasher's help entry point, simulated radio
readiness, web files, authenticated native API, SQLite integrity and clean exit.
These checks do not flash hardware or qualify RF behavior.

No radio firmware, signing keys, prebuilt container, third-party dependency
wheelhouse or automatic software updater is included. The firmware catalog is
empty. TI/MG21 update backends require a separately qualified compatible bundle;
integrated Nordic DFU remains disabled. Output control and detection retain the
board- and switch-specific experimental limits documented in the source.

Back up the stopped daemon's configuration/database before upgrading. Preserve
network identity and the authoritative device registry. This distribution keeps
the runtime version at 0.12, so replacing an older 0.12 build requires the
documented `--force-reinstall` step. HA integration releases remain separate;
this release does not replace or modify their existing assets.
