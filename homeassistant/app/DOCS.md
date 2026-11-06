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
Registration and physical pairing are separate actions. Sockets and curtains are
not yet qualified.
