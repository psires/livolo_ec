# Native Livolo EC integration

The native integration runs without MQTT. It connects to `livolod` over an
authenticated local API, creates one HA device per configured physical switch,
and one switch entity per output. HA Core never opens the radio serial port.
The independent daemon continues running during HA reloads/restarts.

The integration provides native control and commissioning, broker-free daemon
startup, push updates, ingress support, and local HA app packaging. Radio
firmware updates use a standard HA firmware entity backed by the daemon's durable
maintenance jobs. Initial conversion from stock adapter firmware remains a
separate board-specific operation. Network backup/import UI, automatic app
installation, socket profiles and curtain profiles are not implemented here.
Radio and commissioning support remains experimental within the tested board profiles.

The [radio readiness matrix](../analysis/development/ha-radio-support.md) records
the exact supported/experimental profiles. Commercial Zigbee adapters require
our **dedicated IEEE 802.15.4 firmware**, not stock coordinator firmware. The
official Nordic PCA10059 now also has a qualified gateway build; support remains
specific to the boards and tests recorded in the matrix. A radio running Livolo
firmware cannot simultaneously serve ZHA, Zigbee2MQTT or Thread.

## Install through HACS or Marketplace

Add [psires/ha-livolo-ec](https://github.com/psires/ha-livolo-ec) as a custom
**Integration** repository in HACS (HA 2026.10.4) or Marketplace (HA 2026.11).
Install **Livolo EC**, then restart HA. The current
[0.5.1 release](https://github.com/psires/ha-livolo-ec/releases/tag/0.5.1) contains
the assembled client and component. Shared default-catalog inclusion is separate;
this installation uses a custom repository. Marketplace custom repositories
require the user's GitHub authorization.

HACS/Marketplace discover future integration updates automatically; installing
one still requires an HA restart. Supervisor discovers app updates separately.
Automatic app updates are optional and restart the daemon when installed.

[Open the integration repository in HA](https://my.home-assistant.io/redirect/hacs_repository/?owner=psires&repository=ha-livolo-ec&category=integration)

Existing installations should update the integration files while retaining the
existing HA configuration entry. Do not delete and re-add it. Detection requires
the matching current daemon, not just the new integration. Older daemon builds
also report runtime version **0.12**, so use the app/distribution release and
source revision to distinguish them.

## Install the daemon app on HA OS

Add `https://github.com/psires/livolo_ec` under the app store's **Repositories**,
then install **Livolo EC**. The repository supplies the public **aarch64** wrapper
image `ghcr.io/psires/livolo-ec-ha-app:0.5.1`; it does not build on the HA host.
Configure the options documented below before starting the app. Install the
native integration separately and use the app's actual internal hostname and
the same API secret.

[Add the app repository to HA](https://my.home-assistant.io/redirect/supervisor_add_addon_repository/?repository_url=https%3A%2F%2Fgithub.com%2Fpsires%2Flivolo_ec)

**An existing local development app is a separate installation.** Adding this
repository does not update `local_livolo_ec` or copy its database. Keep that
working app until a planned migration preserves its network identity, token and
database and reconfigures the existing HA entry. Never start a second daemon on
the same radio. HA Container users instead use the
[standalone daemon container](../host/docker/README.md).

Public **0.5.0 to 0.5.1** upgrades passed through HACS on HA 2026.10.4 and
Marketplace on HA 2026.11.0. Separate-process archive checks preserved actual
disposable HA storage across stock/patched 0.5.0, 0.5.1 and rollback. Both ARM64
images passed installed upgrade/persistence checks from public 0.5.0. The release
incorporates the private pairing fixes, with daemon 0.14.1 and radio compatibility
0.13; no live HA deployment or firmware change was performed. See the
[0.5.1 verification](../docs/verification/software-release-0.5.1-20261107.json)
for exact public hashes and test limits.

Historical public integration upgrades from **0.4.0 to 0.5.0** passed with HACS on HA
2026.10.4 and Marketplace on HA 2026.11.0, preserving configuration and entity
identity through real ZIP replacement and entry reload. These isolated checks
verify restart-required handling, not a complete HA process restart. Both ARM64
0.5.0 images passed replacement from the public 0.4.0 images with persistent state.
See the [0.5.0 release verification](../docs/verification/software-release-0.5.0-20261107.json)
for exact source, public hashes and test limits.

Historical public integration upgrades from **0.3.0 to 0.4.0** passed with HACS on HA
2026.10.4 and Marketplace on HA 2026.11. The 0.4.0 app passed an isolated container
upgrade from 0.3.0, preserving configuration and state without radio access.
Native API access with Web disabled and a local catalog persisted across two
container recreations also passed. See the
[integration evidence](verification/integration-channel-0.4.0-20261106.json)
and [container evidence](../host/docker/publication-0.4.0-20261106.json).

The following Supervisor qualification is historical: The public app was installed and updated from 0.2.0 to 0.2.1 through
Supervisor on HA OS 18.4 using an isolated app without a radio. Its registry,
command history, options and secret persisted; the original live installation
was unchanged. This does not qualify a local-app migration, new physical
pairing, USB hotplug or automatic upgrades. See the
[publication record](../docs/HA_PUBLICATION.md) and
[Supervisor verification](verification/channel-publication-20261105.json).

## Manual integration installation

Build an archive from the repository root:

```sh
python3 homeassistant/build_release.py
```

Extract `dist/livolo-ec-ha-0.5.1.zip` into the HA configuration directory so
`custom_components/livolo_ec/manifest.json` is beneath it, then restart HA.
Use this assembled archive: the source component alone does not include its
independent client. The builder bundles `client/livolo_ec_client` as `_client`
without relying on an unpublished package or installing the daemon in HA Core.
The standalone client can also be installed with `pip install ./client`.
The client package, assembled integration and app build context include the
project's BSD-2-Clause license. The app image also retains it under
`/usr/share/licenses/livolo-ec/LICENSE`.

This is a custom integration, not an HA Core integration. Do not copy just
`custom_components/livolo_ec` from source.

## Integration channel packaging

The source includes generated-repository tooling for the separate
`psires/ha-livolo-ec` HACS/Marketplace channel. Its public installation and upgrade
checks passed on the versions documented above. The main repository's
historical `ha-0.1.1` and `ha-0.2.0` manual releases remain unchanged.

Maintainers export a reviewed, committed source revision with:

```sh
python3 homeassistant/export_channel.py --commit <full-source-commit> \
  --version 0.5.1 --output dist/ha-channel-0.5.1
python3 dist/ha-channel-0.5.1/scripts/build_release.py --tag 0.5.1
```

The generated repository contains the complete integration, client, original
project branding, pinned source/packaging provenance, and repository-local
validation and tag-triggered release workflows. A dirty packaging recipe or
explicit `--preview` export cannot be released. Push the generated repository
and ordinary matching SemVer tag only after review. Its `livolo_ec.zip` contains
component files at its root, unlike the manual archive above.

The app configuration names `ghcr.io/psires/livolo-ec-ha-app:0.5.1`. The production
catalog is advanced only after that exact image passes container upgrade and
anonymous registry checks. The historical Supervisor qualification above remains
separate.
For isolated Supervisor qualification,
`stage_supervisor_probe.py` prepares a separate app with no USB mapping or host
ports; it never modifies the existing installation. See the
[publication record](../docs/HA_PUBLICATION.md) for channel and migration details.

## Connect to a daemon on an existing Pi/container

Use the **new source build** of the daemon: the previously delivered 0.12 Docker
archive predates the native API and will not gain it merely by changing config.
Reuse its Dockerfile, persistence and device mapping, rebuilding under a distinct
development image tag so the known working release remains available.

For a standalone installation, install the current host package. MQTT is an
optional dependency: `pip install './host[mqtt]'` retains broker support;
`pip install ./host` provides the native-only service.

Keep existing network identity, baud, registry and output mode. Add:

```json
"mqtt": {"enabled": false},
"api": {
  "enabled": true,
  "host": "0.0.0.0",
  "port": 8090,
  "token_env": "LIVOLO_API_TOKEN"
}
```

Set `LIVOLO_API_TOKEN` to a randomly generated secret in the daemon environment.
For example, `python3 -c 'import secrets; print(secrets.token_urlsafe(32))'`
generates one. The standalone listener requires a token even on loopback.
Restrict access to the intended host/private network. Use TLS termination if
crossing a network where the bearer token must not travel in cleartext.

In HA, choose **Settings → Devices & services → Add integration → Livolo EC**.
Enter the daemon base URL (for example `http://<pi-address>:8090`) and token.
Leave simulation disabled for hardware. The flow verifies API version and
network identity, rejects duplicate entries and supports reconfiguration and
credential repair without silently replacing the logical network.

## Run the service as a local HA OS app

The local app wrapper reuses the daemon Dockerfile and packages the current source
with the ingress/bootstrap additions. Only `aarch64` is advertised. HA Container
users should use the companion daemon container above; it has no managed app store.

Stage a self-contained local app build context:

```sh
python3 homeassistant/build_release.py --app --output dist/native-preview
```

Copy `dist/native-preview/ha-addons/livolo_ec` into your HA local app/add-on
directory, refresh the local app store, and install **Livolo EC**. Re-running
the command with the same app output directory intentionally fails rather than
overwriting a previously staged source bundle; use a fresh output directory.
The integration ZIP is generated in the same output directory.

Configure these app options before starting:

| Option | Meaning |
| --- | --- |
| `serial_port` | Exact supported radio, preferably `/dev/serial/by-id/...`. |
| `serial_baudrate` | Installed application baud, e.g. 460800 for the current Sonoff build. |
| `gateway`, `tag` | Existing EC network identity when migrating; otherwise an explicitly chosen unique network identity/tag. Never copy the development network. |
| `api_token` | At least 32 non-whitespace ASCII characters; use a generated secret. Use the same token in the integration. |
| `ha_origin` | Exact browser origin including scheme and nondefault port, e.g. `http://homeassistant.local:8123` or `https://ha.example`. No trailing slash/path. |
| `output_mode` | `experimental_phase` for supported current firmware, or `disabled` for observation. |

The app stores SQLite in `/data/state.sqlite3` and runs the daemon as UID 10001
after preparing directory and serial permissions. Browser ingress listens on
8099 and only trusts the Supervisor proxy. The authenticated machine API listens
on 8090 separately. Leave its host-port mapping unpublished: HA Core can use the
app's internal hostname, normally `http://local-livolo-ec:8090` for this local app.
Confirm the actual hostname in Supervisor/app information instead of guessing
it for repository-installed apps. `localhost` inside HA Core is not the app.

Use **Open Web UI** for the ingress management interface, and add the native
integration using that internal API URL. Manual configuration is the supported
preview path; the config flow can accept Supervisor discovery hints, but this
app version does not publish them automatically or install the integration.

The app exposes serial devices through Supervisor's `uart` mapping; only the
selected port is opened. Only one process may own it. If a USB device is absent
at boot or returns with different permissions/device mapping, a restart may be
needed; actual HA OS hotplug behavior still requires hardware qualification.
There is no radio-readiness watchdog that repeatedly restarts an otherwise
healthy daemon when the adapter is unplugged.

## Radio firmware updates

Integration/client **0.3.0** add **Configure → Check firmware update (read-only)**.
The report shows the identified board, installed and candidate versions,
compatibility checks and blocking reason. Refresh repeats only read requests;
it does not open the radio, send UART commands, create an update job or write the
daemon database. Unknown identity is not a successful compatibility check.

The gateway's standard **Firmware** update entity remains the installation
control. HA requests fresh preflight for that exact candidate before submitting
one durable job, and rejects a changed candidate or blocked report. The daemon
independently repeats its admission checks; a prior report cannot bypass them.
Older daemons without `preflight_supported` retain switch control and firmware
status, but must be updated before the HA install action is enabled. A failed
preflight means no installation was requested; a lost response after submission
retains the existing uncertain-job handling and never automatically flashes again.
Manual local-artifact and recovery operations remain in the operator CLI; the
HA API does not accept firmware paths, helper executables or serial overrides.

Integration and client 0.2.0 add a **Firmware** update entity to the existing
Livolo EC gateway device. It reports the running version and the daemon's selected
compatible candidate. Use HA's standard update dialog or the `update.install`
action. The daemon also exposes the same job through its web interface; neither
HA Core nor the browser opens the serial device or selects a filesystem path.

The daemon must have a validated firmware bundle for the exact board and baud,
with the required installation backend available. Without a candidate, the entity
retains the installed version and blocking reason but has an unknown update state
and no install capability. Older daemons without firmware-update metadata keep
their switches working and expose the firmware entity as unavailable.

Integration 0.5.0 adds the following diagnostics.
Open the **Livolo EC gateway** device and find **Radio firmware status** under
**Diagnostic** for an explanation that remains visible when no update is offered.
It distinguishes a missing or unusable catalog from a catalog with no compatible
firmware, and retains the reported installed version and separate last-job details.
A disappearing offer does not confirm installation. Only a confirmed daemon job
establishes that its requested update completed; 100% transfer alone does not.
An old successful job can remain in the history while the current catalog is
unavailable, so check the reported running version as well.

When a previously available offer disappears because its catalog is unavailable,
a notice in [Settings → System → Repairs](https://www.home-assistant.io/integrations/repairs/)
explains the withdrawal. An empty catalog on first setup does not raise this
notice. Restore a reviewed compatible catalog using the
[catalog guide](../docs/FIRMWARE_CATALOG.md), then use **Configure → Check firmware
update (read-only)**. Restoring the catalog does not install firmware.

The initial backend scope is signed TI UART updates and the exact supported
Sonoff/ZB-GW04 MG21 profiles. Nordic installation remains disabled with a recovery
reason. Firmware delivery and backend prerequisites are described in the
[daemon documentation](../host/README.md#firmware-updates); installing the
integration does not download a radio image. No public firmware release or live
flash qualification is implied by these
source changes.

An install remains pending until the daemon verifies the resulting board,
application baud and firmware version, including the expected TI image hash.
During maintenance, switch controls are unavailable while the API and firmware
job remain readable. Job phase, request ID, failure and recovery guidance come
from the daemon. Percentage progress is shown only when a genuine overall
measurement is supplied; transfer progress alone does not imply completion.
The separate `transfer_percent` state attribute displays a validated measured
upload percentage, if supplied. A value of 100 keeps the job pending until
restoration is verified; it never supplies the overall update percentage.

The HA app can run this entire native workflow with `web_enabled: false`.
Web remains enabled by default for existing installations. `firmware_catalog:
local` selects the persistent `/data/firmware` catalog; the default `bundled`
uses the image's catalog. Invalid local catalogs disable firmware eligibility
without interrupting ordinary device control or falling back to bundled images.
See [app options](app/DOCS.md) and [catalog staging](../docs/FIRMWARE_CATALOG.md).

The client submits once and uses the same durable request ID to resolve lost
responses. A local observation timeout or HA reload does not cancel or restart
the firmware job. Check its reported status and recovery instructions before
retrying an uncertain outcome. Jobs started through the web interface remain
visible after HA reload. Arbitrary version selection and HA's optional backup
flag are not advertised; firmware rollback is board-specific.

The ARM64 app was built and installed on a Raspberry Pi 5 running HA OS 18.4
and Core 2026.10.4 on 2026-11-01. The ZB-GW04 gateway became ready, both switches
and all three native outputs loaded, and integration reload preserved the running
daemon. Initial distant-placement tests included unconfirmed commands. After
moving both switches close to HA, all **12 control commands confirmed** (six
through HA and six through the web API), both read-only refreshes confirmed,
and all outputs returned to their starting states. Firmware and settings were
unchanged; this is bounded evidence for these two devices, not a long-term
reliability guarantee. See the
[public verification summary](verification/native-preview-20261101.json).
Ingress HTTP and
access checks passed, and the user confirmed the page opens inside HA. USB hotplug
still needs qualification. Integration 0.1.1 fixes duplicate entity registration
during unload; its regression test and subsequent live reload passed.
The public verification summary omits private host addresses, device identities,
command history and backup locations from the local deployment records.
Preserve a working image and consistent database backup before trying it on hardware.

## Device management and state behavior

The gateway diagnostics distinguish **Daemon software version** from
**Firmware compatibility target**. The radio's **Firmware update** entity
continues to show the installed firmware and the validated available candidate.
Upgrading daemon software alone does not offer a radio firmware update.
Older daemons that omit the explicit version fields show **Unknown** for these
diagnostics; switch control remains available. The legacy API `host_version`
field is retained in diagnostic downloads as a compatibility alias, never used
to infer software identity or feature support. Downloads also include the
explicit native API and UART revisions when reported.

On the **Livolo EC gateway** device page, press **Pair new device**, then put
only the intended new switch into physical pairing mode. The daemon listens
for up to two minutes, selects a fresh supported announcement, saves its
profile and pairs it. **Pairing status** follows the session through detection,
registration and physical confirmation. **Cancel pairing** closes the window;
it cannot undo messages already sent or remove a registered device.

The switch and its outputs appear automatically under the existing gateway.
HA sends a notification linking to the device only after fresh physical
confirmation. Sessions started in the web interface use the same status.
An interrupted request is never automatically resubmitted, including after
an HA reload. If its outcome remains uncertain, use **Cancel pairing** before
starting again. Older daemons keep manual setup available but disable these
automatic pairing controls. See [pairing and recovery](../docs/PAIRING.md).

The integration's **Configure** dialog supports:

- Pair a new device using the same bounded session and inspect or cancel it.
- Register a device using its wire ID, name, output count and verified suffix.
- Detect a switch experimentally from its observed pairing signature, review
  its output count, and register it with a friendly name.
- Open a targeted pairing window, or close it with a zero-second duration.
- Request a bounded read-only state refresh.
- Forget a host record after confirmation. This is not a physical factory reset.

For detection, open **Settings → Devices & services → Livolo EC → Configure →
Detect a switch (experimental)** and put
only the intended new switch into physical pairing mode, with pairing on the
original EC gateway disabled. Submit the form without selecting a device to
refresh the list, then select its observed ID. HA displays the daemon's detected
output count and suffix for review; it does not infer the count from state bits.
Submit a name to register that reviewed profile. The daemon rechecks its token
against fresh evidence from the current radio session. Changed, expired or
conflicting evidence requires reselection. Unknown devices and older daemons
without profile metadata retain **Use manual setup instead** and the original
manual registration menu. This advanced detection flow requires explicit review;
the **Pair new device** action above performs automatic selection and registration.

Detection currently recognizes only the tested one- and two-gang pairing
signatures; see [experimental detection limits](../docs/DEVICE_PROFILES.md).
It does not qualify sockets, curtains or other switch variants. Once registered,
wait for radio readiness and reopen **Configure → Targeted pairing** with
the selected device ID. Registration alone does not complete physical pairing.

For advanced targeted pairing, register first. Manual profile fields remain explicit; suffix alone is
not an inferred gang count. Pairing acceptance opens the window, and the management UI shows
later confirmation/expiry. No generic automatic admission is enabled.

New registry devices appear without reloading HA. **Delete** on a switch's HA
device page forgets its daemon record, verifies that it is absent, and removes
the HA device and its entities. The daemon must be reachable; a rejected or
uncertain result keeps the HA record so you can check the daemon and retry.
The gateway device is protected. Deleting a switch is not a physical factory reset.

Forgetting through the web interface or **Configure** makes its HA channels
unavailable. Use the HA device's **Delete** action to clean up those entries;
it verifies absence without submitting another remove command.
HA-local friendly-name and area overrides remain under HA control. Existing HA
names are not automatically synchronized back to daemon names.

Only validated device reports change displayed switch state. A successful API
submission is not physical confirmation. Commands address only their output's
bit, preserving other channels. Unconfirmed/unknown outcomes appear as service
errors and retain the request ID for investigation. Lost replies are resolved
through the same durable ID, never an automatic new command. State is unknown
until observed; stale transport/radio/device availability makes controls unavailable.
No per-entity RF polling or invented button-press events are used.

The newest output-grid results improve control reliability, but query timeouts,
fresh commissioning and route recovery remain subject to the firmware evidence.
HA software tests cannot establish RF reliability.

## Move an existing installation

Stop/disarm the old owner before starting the new one. Preserve gateway/tag,
devices, suffixes, tokens and the command ledger by copying a consistent SQLite
backup plus configuration. Use SQLite backup facilities or stop/checkpoint before
copying; copying only a live WAL database file is not a complete backup. The HA
app expects `/data/state.sqlite3`; its startup seed is empty and does not replace
an imported database. Keep a private backup because existing SQLite can contain
MQTT credentials and history. A changed gateway/tag is rejected by the store.

Existing MQTT entities do not automatically merge with native ones. Record entity
IDs and automation references, stop old discovery publications, clear the old
retained discovery configurations deliberately, then reuse entity IDs where
appropriate. Device-based automation references may need manual repair. Do not
run a second daemon against the same radio/network during migration.

## Development validation

Tests target Home Assistant 2026.10.4 with Python 3.14.2 or newer:

```sh
python3.14 -m venv .local/ha-tests
.local/ha-tests/bin/pip install -r homeassistant/requirements-test.txt
.local/ha-tests/bin/python -m pytest -c homeassistant/pytest.ini homeassistant/tests -q
```

These tests assemble the actual distribution and use real HA classes. They cover
configuration, identities, per-output commands, dynamic devices, unavailable and
unknown states, unload, HTTP/WebSocket auth, lost command replies, and the full
simulator → native API → HA path. The suite opens loopback sockets only.
The earlier 0.1.1 source validation on 2026-11-01 passed **28 tests**, including
the reload regression, firmware diagnostic allowlist and license/client packaging checks.
The isolated native API and ingress suites also passed **14** and **10** tests,
respectively. The official HA 2026.10.4 **hassfest** validator passed with zero
invalid integrations. See the
[public verification summary](verification/native-preview-20261101.json).
That record predates the 0.2.0 firmware update implementation. Firmware tests
add standard update-service behavior, job deduplication after lost responses,
terminal failures, maintenance visibility and restoration of externally started
jobs after reload. All update tests use mocked jobs; no live HA actions or radio
flashes are part of the source checks. CI runs
hassfest and packages the integration/local app artifacts; no remote CI run is
claimed by these local results.

The 0.2.0 source checks pass **44 HA/client/packaging tests**, **16 native API
tests**, Black 24.8.0, function documentation checks and HA 2026.10.4 hassfest.
The [firmware-update verification](verification/firmware-update-20261101.json)
records the scope: the source bundle has no firmware artifacts, and this change
has not been deployed or used to flash a physical radio.

The [native API contract](../host/NATIVE_API.md) and
[architecture plan](../analysis/development/home-assistant-native-integration.md)
record the API boundaries and plans for backup/import and additional device profiles.
