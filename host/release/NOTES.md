Standalone daemon and CLI distribution **0.5.0**, containing daemon **0.14.0**
and Python API client **0.5.0**. Radio firmware compatibility remains **0.13**,
UART protocol 3 and native API revision 1. This is a software-only release.

### Changes

- Pair a new supported switch from Web or HA with one action. A bounded session
  detects its profile, registers it and reports confirmation; unknown or
  conflicting profiles remain on the advanced/manual path.
- Use `livolod-commission` for authenticated terminal commissioning.
- Report daemon software and radio compatibility versions independently in CLI,
  API and HA diagnostics. API revision 1 retains its legacy compatibility alias.
- Explain missing or unusable firmware catalogs without disrupting switch control.
- Preserve existing local catalogs, network identity and authoritative device
  registry. Restart does not replay interrupted firmware installations.

### Install and upgrade

Download `livolo-ec-0.5.0-standalone.tar.gz` and `SHA256SUMS`, verify the archive,
and follow its README. Linux with Python 3.13 and venv support is required;
install dependencies separately from PyPI. The bundle includes daemon/client
wheels and source packages, web assets, configuration/service examples, pairing
and versioning guides, checksums and licence notices.

See the [installation and recovery guide](https://github.com/psires/livolo_ec/blob/standalone-0.5.0/host/release/README.md).
Finish active firmware or pairing operations, stop the daemon, and back up its
configuration, database and trusted catalog (including custom paths). Retain the
same Docker mounts and local-catalog setting. No database-schema or network-ID
migration is required. Preserve the previous environment for rollback.

### Validation and limits

Publication requires isolated wheel and source installs on Linux **x86-64 and
ARM64**, with installed CLI, simulated radio, Web/API and persistence checks.
The coordinated ARM64 containers also require upgrade and anonymous-pull checks.
These software tests do not establish new physical-radio qualification.

The public firmware catalog remains empty. No firmware payloads, private keys,
vendor SDKs or bootloaders are included. The four source board profiles retain
radio compatibility 0.13 and their existing experimental limits; updating this
software never flashes a radio. Existing trusted local catalogs remain usable.
HA integration releases and container tags are separate distributions. Published
versions and prior release assets remain immutable.
