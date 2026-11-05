Standalone daemon and CLI distribution **0.3.0**, containing daemon **0.13**
and Python API client **0.3.0**. UART protocol 3 and native API revision 1 remain
compatible; the software update does not flash radio firmware.

### Changes

- Unified firmware `inspect`, `preflight`, and explicit local `flash` commands.
- Offline dry-run validates exact-board artifacts without serial access or daemon
  startup; optional probing is a separate explicit operation.
- Shared preflight for the Web UI and native Home Assistant update path.
- Durable installation ownership, interrupted-job recovery and post-flash
  identity/session verification. Transfer completion alone is not success.
- PCA10059 factory-DFU recovery and an opt-in standalone USB handoff through an
  isolated legacy helper, with exact physical and serial identity checks.

### Install

Download `livolo-ec-0.3.0-standalone.tar.gz` and `SHA256SUMS`, verify the archive,
and follow its README. No source checkout is required. Use Linux with Python
3.13 and venv support; dependencies are installed separately from PyPI.
See the [installation, upgrade and recovery guide](https://github.com/psires/livolo_ec/blob/standalone-0.3.0/host/release/README.md).

The bundle includes daemon/client wheels and source packages, web assets,
systemd/configuration examples, maintenance commands, checksums and licence
notices. The Python client is a library, not a remote-control shell.

### Validation and limits

Publication requires isolated wheel and source installs on Linux **x86-64 and
ARM64**, including installed CLI dry-run checks, simulated radio readiness,
web/native API, persistence and clean shutdown. These packaging checks use no
live radios. Nordic hardware qualification is recorded separately in the
[local trial record](https://github.com/psires/livolo_ec/blob/standalone-0.3.0/docs/FIRMWARE_013_TRIAL.md).

The default firmware catalog remains empty. The separately prepared 0.13 radio
images have offline build/comparison evidence for local installation trials;
they are not offered as public hardware-qualified updates. No firmware binaries,
private signing keys, SDKs or legacy Nordic helper are bundled. The exact-board
experimental limits and recovery prerequisites remain documented in the source.

Back up the stopped daemon's configuration/database before upgrading and preserve
its network identity and authoritative registry. HA integration releases and
container tags are separate distributions; prior published assets are unchanged.
