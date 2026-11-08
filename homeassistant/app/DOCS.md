# Installation and configuration

Install this app from the Livolo EC app repository, then install the **Livolo EC**
integration through its HACS/Marketplace repository. HA Container users run the
standalone daemon container instead; Supervisor apps require HA OS.

Select a supported dedicated radio under `serial_port`, preferably a persistent
`/dev/serial/by-id/` path. It must already run the project's board-specific
firmware. Choose its installed application baud rate; current qualified MG21
and TI configurations use 460800. Only this daemon may own the adapter.

Set `gateway` to the network's eight hexadecimal digits and `tag` to its two
hexadecimal digits. Preserve both when migrating an existing network. For a new
network, choose your own nonreserved identity; do not copy an example network.
Set `api_token` to a generated secret of at least 32 non-whitespace ASCII
characters. Save the same secret in the native integration. Set `ha_origin` to
the exact URL used to open HA, including scheme and nondefault port, without a
trailing slash or path. Use `experimental_phase` for supported output control,
or `disabled` for observation.

`web_enabled` defaults to `true`, including when upgrading an existing options
file. Set it to `false` to run only the native integration: the app does not
start its Web listener, and the daemon does not need `ha_origin`, a browser
session or ingress for control, commissioning or firmware updates. Supervisor
may still display **Open Web UI** or the sidebar shortcut because its ingress
metadata is static; those links are unavailable while Web is disabled.

Start the app and check its log for radio readiness. **Open Web UI** provides the
ingress management page. The native integration uses the app's internal hostname
on port 8090; find the actual hostname in app/Supervisor information. Do not use
`localhost`, and leave port 8090 unpublished to the host. This release still
requires entering the API URL and token in the integration setup dialog.

The daemon stores its registry and confirmed state in `/data/state.sqlite3`.
App updates retain `/data`. Back up before upgrades; the app and integration are
separate update items. HA integration updates require an HA restart. App updates
restart the daemon, briefly making switch control unavailable. Automatic app
updates are optional. Radio firmware installation is a separate explicit action.

`firmware_catalog` defaults to `bundled`, selecting the catalog shipped in the
image. Select `local` to use the fixed persistent directory `/data/firmware`.
The wrapper does not create a catalog, download firmware, or change catalog
file ownership. Provision a complete validated catalog using the
[operator staging procedure](../../docs/FIRMWARE_CATALOG.md), readable by
runtime UID/GID 10001. Back up the catalog with the app's private data. App
recreation must retain `/data`. Missing or invalid local files disable firmware
eligibility with a reason; normal device control continues, and the app never
silently falls back to the bundled catalog. The daemon notices catalog changes
without a Web visit or radio restart and revalidates the selected bytes before
installation.

Catalog qualification records are operator-supplied trust metadata, not a
publisher signature or proof of hardware qualification. Only use artifacts
reviewed for the exact adapter. HA's firmware update entity exposes measured
`transfer_percent` separately from overall update progress. Transfer reaching
100% does not mean the new application or network has been verified. Completion
requires the daemon's confirmed result after normal radio restoration. Failure
and recovery status remain visible through HA; use the documented emergency
CLI if manual intervention is necessary.

A local development app and this repository app have different Supervisor IDs
and separate databases. Do not delete/recreate a working HA integration to move
between them. Stop the old daemon, preserve a consistent database/options backup,
transfer it through the documented migration procedure, and reconfigure the
existing integration endpoint while keeping the network identity unchanged.
The publication work does not perform that migration automatically.

Only tested one- and two-gang switch profiles are supported. Automatic profile
detection is experimental; unknown or conflicting evidence requires manual setup.
In advanced/manual setup, including the published 0.4.0 workflow, registration
and physical pairing are separate actions. Sockets and curtains are not yet
qualified.

## Pair a new switch

With app/integration **0.5.1**, in the Web UI or on the Livolo gateway device
in HA, press **Pair new device**,
then put one intended new switch into physical pairing mode. The daemon detects
its supported profile and attempts registration and pairing within a 120-second
session. Wait for **confirmed**; a detected device or a saved entry is not proof
that pairing succeeded. **Cancel pairing** closes the session early.

HA adds child switch devices/entities beneath the existing network entry and
shows a notification with a device link after confirmation. It does not create
Discovered cards or a separate integration entry for each switch. An unconfirmed
registered device remains visible; use advanced targeted pairing to retry.

See the [0.5.1 pairing guide](https://github.com/psires/livolo_ec/blob/ha-0.5.1/docs/PAIRING.md)
for supported profiles, failure handling and limits, and the
[versioning guide](https://github.com/psires/livolo_ec/blob/ha-0.5.1/docs/VERSIONING.md)
for daemon software 0.14.1 versus firmware compatibility 0.13. The software
release contains no firmware payloads; updating the app does not flash a radio.

## Guided ZB-GW04 radio setup (opt-in engineering profile)

The default **Radio setup profile: disabled** keeps the existing app startup and
routine updater. For the reviewed dedicated ZB-GW04 1v2 test unit, select that
profile in the app Configuration and restart the app. Keep **Radio mode:
gateway** for an existing network. Use **bootstrap** only for a new unconfigured
network, with gateway/tag empty. This mode controls the first startup; subsequent
transitions and an explicitly initialized network are saved in the independent
physical setup journal. Blank initial Supervisor options do not replace an
accepted saved network on restart.

In the Livolo integration's **Configure → Set up radio**, explicitly enter setup,
confirm dedicated use and the exact board, follow the physical BOOT/RESET
instructions, then inspect the loader. Review the selected artifact before
installing. Firmware application verification and resuming the normal network
are separate actions: only a successful normal daemon restore reports the
network ready. Closing the dialog does not resume the daemon. Existing switch
registrations, gateway/tag, and HA entities remain associated with the same
network. The Web UI is optional and is stopped while the normal radio service is
quiesced; setup uses the authenticated native API.

Before any installation attempt, **Cancel setup** can return to the intact
application. Follow the physical BOOT/RESET instructions on the confirmation
screen first. Cancellation independently verifies the preserved loader, runs the
intact application once, checks its saved version, then attempts normal network
restore. It does not require a firmware catalog or transfer an image. If the
application runs but this app cannot restore the network, the result requests a
compatible app rollback; it does not claim readiness. A possible write, unknown
installation, changed attachment, or unresolved previous updater recovery
prevents cancellation from clearing the safety fence.

The engineering profile additionally requires a protected, server-provisioned
`/data/bootstrap-policy.json` containing the reviewed exact-unit target and
loader policy. This evidence is prepared with the candidate deployment; selecting
a board in the UI does not manufacture loader security facts. A missing or stale
policy blocks setup. The currently reviewed evidence applies to the existing
tested unit, not arbitrary stock radios. The policy binds the attachment, so
reattachment or a host restart requires renewed review; a stale plan cannot be
reused. This remains an engineering test path until the exact packaged workflow
has been qualified.

The app uses `/share/livolo-radio-locks` for cooperative, per-device ownership,
independent of its database path. All cooperating Livolo deployments must use
that same protected directory and canonical host USB metadata. The normal radio
owner, setup worker and fixed flashing helper retain the guard through handoff;
workers also inherit the actual deployment database and firmware guards. This
supports a radio explicitly dedicated to Livolo. It does not prove global absence
of noncooperating or privileged software, and does not stop ZHA or unrelated
integrations. Known conflicts must be removed before setup.

For the current 0.14 engineering candidate, the private catalog is fixed at
`/data/bootstrap-firmware` and explicitly records `qualified: false`. Its exact
board, version, artifact SHA-256, source commit and manual-trial evidence must
match the protected policy's `engineering` record and the reviewed source
allowlist. The loader policy binds the same evidence and exact unit. All existing
path, hash, GBL layout, application-only and notice checks still apply. Normal
firmware loaders and public release validation continue to reject this artifact;
the candidate's ordinary bundled catalog remains empty. Native review marks this
path **Engineering test only**, rather than claiming a qualified public release.

Successful network handoff ends the temporary attachment fence. Normal reconnect
and host restart may renew attachment metadata while retaining the configured USB
topology, interface and board/bridge profile. Only another setup operation needs
the engineering policy's exact attachment to be reviewed again.
