# Pair a new switch

Automatic pairing is included in software distribution **0.5.0** with daemon
**0.14.0** and HA integration **0.5.0**. Earlier 0.4.0 packages retain advanced
manual commissioning. This feature does not change radio firmware.

## Add a switch

1. Open the daemon's Web UI or the Livolo gateway device in Home Assistant and
   press **Pair new device**.
2. Put the intended switch into its physical pairing mode. Activate only one
   new switch at a time and keep other EC gateways out of pairing mode.
3. Watch the pairing status. The daemon detects the supported switch profile,
   registers it and attempts pairing within a two-minute window. You do not
   need to enter an ID, output count or protocol suffix.
4. Wait for **confirmed**. Detection and registration are intermediate steps;
   they do not establish that the switch joined the network.

Each session handles one new switch. Start another session to add the next
one. You can rename a registered switch afterwards. Existing configured
devices are not silently replaced or reconfigured.

If multiple eligible new switches announce themselves during initial
detection, the session stops with a conflict. Put only the intended switch
into pairing mode before starting again. Adding its radio peer requires a
brief reconfiguration pause; a registering state during that pause is normal.

Automatic detection currently recognizes the experimentally verified one-gang
and two-gang EC pairing layouts. The displayed gang count is a supported
profile match, not a universal model identification. Unknown or conflicting
profiles need the advanced setup path; the daemon does not guess their output
count. See [profile evidence and limitations](DEVICE_PROFILES.md).

## Home Assistant

The gateway exposes a **Pair new device** button that can also be added to a
dashboard. Pairing state and cancellation are available alongside it. The
integration's options provide another entry point to the same daemon session.
Sessions started in the Web UI are visible in HA too.

New switches and their output entities appear automatically under the existing
Livolo integration. A confirmed pairing produces a notification linking to the
device. A device may appear before confirmation because its configuration has
already been saved; its presence alone is not proof of successful pairing.

Child switches do not create cards in **Settings → Devices & services →
Discovered**. HA uses that area for integration setup flows, and its subentry
flows do not support discovery. Creating a second integration for each switch
would duplicate the existing network configuration. The automatic device
registration and confirmation notification provide the supported workflow.
See HA's [config-flow discovery documentation](https://contributors.home-assistant.io/core/integrations/config-flow/#discovery-steps)
and [subentry documentation](https://contributors.home-assistant.io/core/integrations/subentries/).

## Cancellation and unsuccessful attempts

The session expires automatically; **Cancel pairing** closes it sooner.
Cancellation stops further admission work but cannot undo radio messages
already sent. A switch registered during an unsuccessful attempt remains in
the device list, with an explicit unconfirmed outcome. Use its advanced
targeted pairing action to retry, or remove it if it should not be retained.

If the daemon or radio disconnects, an interrupted attempt is not reported as
successful. Reconnect and inspect the recorded result before trying again.
Restarting the daemon does not automatically resume admission. Firmware
maintenance and other pairing operations cannot run concurrently with an
automatic pairing session.

Only fresh eligible announcements received after the session starts can select
a device. Previously displayed discoveries do not trigger automatic admission.
Pairing success requires a fresh, valid state report addressed to this gateway
after admission transmission; merely detecting a device or accepting a
command is insufficient.

Development validation uses recorded packet fixtures and simulated radio
transports. It does not by itself establish physical pairing reliability on
every supported radio board.

For a terminal client, `livolod-commission` exposes the same start, status and
cancel operations through the native API. See the
[command-line procedure](../host/README.md#pair-a-new-device-from-the-command-line).
