Standalone daemon and CLI distribution **0.4.0**, containing daemon **0.13**
and Python API client **0.4.0**. UART protocol 3 and native API revision 1 remain
compatible; the software update does not flash radio firmware.

### Changes

- Trusted local firmware catalogs can be validated and promoted without radio
  access. Catalog changes refresh update eligibility without reconnecting the
  radio or clearing its observed version and session state.
- Installation admission revalidates the selected artifact. Existing durable
  jobs retain their original target when a catalog changes.
- Native clients receive measured firmware transfer progress separately from
  overall verification. Transfer completion alone is not success.
- The unified CLI retains offline dry-run preflight, explicit probing, exact-board
  checks and durable emergency recovery. Web and HA use the same daemon owner.

### Install

Download `livolo-ec-0.4.0-standalone.tar.gz` and `SHA256SUMS`, verify the archive,
and follow its README. No source checkout is required. Use Linux with Python
3.13 and venv support; dependencies are installed separately from PyPI.
See the [installation, upgrade and recovery guide](https://github.com/psires/livolo_ec/blob/standalone-0.4.0/host/release/README.md).

The bundle includes daemon/client wheels and source packages, web assets,
systemd/configuration examples, maintenance commands, checksums and licence
notices. The Python client is a library, not a remote-control shell.

### Validation and limits

Publication requires isolated wheel and source installs on Linux **x86-64 and
ARM64**, including installed CLI dry-run checks, simulated radio readiness,
web/native API, persistence and clean shutdown. These packaging checks use no
live radios. Nordic hardware qualification is recorded separately in the
[local trial record](https://github.com/psires/livolo_ec/blob/standalone-0.4.0/docs/FIRMWARE_013_TRIAL.md).

The default firmware catalog remains empty. The separately prepared 0.13 radio
images have offline build/comparison evidence for local installation trials;
they are not offered as public hardware-qualified updates. No firmware binaries,
private signing keys, SDKs or legacy Nordic helper are bundled. The exact-board
experimental limits and recovery prerequisites remain documented in the source.

Back up the stopped daemon's configuration/database before upgrading and preserve
its network identity and authoritative registry. HA integration releases and
container tags are separate distributions; prior published assets are unchanged.
