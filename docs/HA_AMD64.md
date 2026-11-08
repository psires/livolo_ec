# Experimental Intel/AMD 64-bit Home Assistant app

The published app image is ARM64-only. A non-publishing local build can be
staged for an x86-64 Home Assistant OS installation with a Supervisor and a
supported Livolo EC radio already running compatible firmware. The native
integration is installed separately as described in the main Home Assistant
guide. This candidate has not yet been qualified on Intel hardware, and does
not change the published image or enable automatic updates.

From a checkout on a build machine with Python and the release dependencies:

```sh
python3 -m pip install -r tools/requirements-firmware-release.txt
python3 homeassistant/build_release.py --app --candidate --experimental-amd64 --output /tmp/livolo-amd64
```

Copy the entire `/tmp/livolo-amd64/ha-addons/livolo_ec` directory to
`/addons/livolo_ec` on the Home Assistant host, refresh the app store, and
install the local Livolo EC app. The candidate deliberately omits the public
image reference so Supervisor builds the included Dockerfile for the host.
The build needs internet access for its base images and dependencies. Configure
and use the radio as described in [the app guide](../homeassistant/app/DOCS.md).
Candidate firmware catalogs may be incomplete; do not assume radio flashing is
available. Back up existing app data before replacing an installation.

Both image variants passed native x86-64 CI at source revision `8cbf4ef`,
including offline first-install and persistence checks. The ARM64 jobs also
passed at that revision. Those checks do not prove
USB radio or Supervisor behavior on a physical Intel system. A new multi-platform
published image and a corresponding app release are still needed before Intel
can be advertised in the public repository metadata.

For Home Assistant Container, run the daemon separately and connect using the
[native API setup](../homeassistant/README.md#connect-to-a-daemon-on-an-existing-picontainer).
Home Assistant Container has no Supervisor app store. Testing the companion
daemon container or its wrapper does not qualify the Supervisor app channel.
