# Livolo EC

A dedicated Livolo EC radio daemon for native Home Assistant switches. Install
the native integration separately through the Livolo EC HACS/Marketplace
repository; this app owns the USB radio and its persistent network database.

Read [installation and configuration](DOCS.md) before starting the app.
[Release changes](CHANGELOG.md) describe update requirements.

The app image supports ARM64 (`aarch64`). It requires a supported adapter already
running Livolo EC firmware. It cannot share that adapter with ZHA, Zigbee2MQTT,
or Thread. Installing or updating this app does not flash the radio.
