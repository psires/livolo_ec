# Livolo EC for Home Assistant

Native Home Assistant control of supported Livolo EC switches, without MQTT.
This repository is generated from reviewed commits in
[psires/livolo_ec](https://github.com/psires/livolo_ec); `source.json` identifies
its exact source and packaged files. Report issues in the source repository.

[![Install with HACS or Marketplace](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=psires&repository=ha-livolo-ec&category=integration)

Use the badge, or add `https://github.com/psires/ha-livolo-ec` as an Integration
custom repository. HA 2026.11 uses Marketplace; older installations use HACS.
This is a custom repository, not a claim of default catalog inclusion. A GitHub
connection may be required. Install the integration, restart HA, then add
**Livolo EC** under **Settings → Devices & services**.

A running Livolo EC daemon and dedicated, already-flashed supported adapter are
required. HA OS users install the companion app; HA Container users run the
standalone daemon container. See the [setup instructions](https://github.com/psires/livolo_ec/blob/main/homeassistant/README.md)
for app availability, USB selection and native API URL/token configuration.
Installing this integration does not install the daemon or flash a radio.

The minimum qualified HA version is 2026.10.4. The tested one- and two-gang
switches are supported; profile detection is experimental. Sockets, curtains
and other variants remain unqualified.

New releases appear as an integration update item. Install an update and restart
HA to apply it. App updates and radio firmware updates are separate items;
unattended software updates are optional. Preserve the existing HA integration
entry when adopting this channel so its entity IDs, names and areas survive.
Back up first. Do not delete/re-add a working entry merely to install its files
through HACS/Marketplace.

The release asset `livolo_ec.zip` has the component files directly at its root,
including its bundled client. It is intended for HACS/Marketplace. For manual
installation, extract it into `config/custom_components/livolo_ec/`.
The differently structured manual ZIP in the source repository must instead be
extracted into the HA configuration directory.

Project code and original icon: BSD-2-Clause; see [LICENSE](LICENSE).
