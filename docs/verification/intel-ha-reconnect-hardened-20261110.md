# Intel Home Assistant reconnect repeat — 2026-11-10

The isolated Intel testbed passed one 120-second natural reconnect check after
installing a reviewed engineering build of the Home Assistant integration. The
daemon was recreated from the same published AMD64 image, retained its Docker
IP address, and ran with physical output disabled. This result does not change
the previous installation's failed 120-second check or establish the cause of
its approximately ten-minute recovery; that cause remains unknown. This repeat
does not test changed-address recovery.

## Build and activation

- Integration source commit: `3ea07aaa2e9a8fd5cc151ebe12469e4cc5714fdb`.
- Engineering integration ZIP SHA-256:
  `b1afca0054c70a3d8444faa75a38efd42836e1d67413927376b3dc3af8b62afe`.
  All 23 installed files matched the ZIP; this is a test build of integration
  version 0.6.0, not a published integration release.
- The Home Assistant suite reported 507 passing tests; release review also
  reported six independently run focused tests passing.
- Published daemon OCI index: `sha256:7b84b3ab6fa55fcbf7b05bce36efa5d010b1cf8aa5da999f2ec997615c3b829b`;
  local AMD64 image ID before and after:
  `sha256:c92b0eaefc0f56999cb7aacdde1d04d3627d5b18b39738cd4a5b0bb88908deae`.
- Test Home Assistant was restarted once to load the integration. After that
  activation both physical and simulator entries were loaded and connected;
  the integration registry entries and options, 20 entities, six devices and
  15 daemon command ledger records matched the pre-activation baseline. The
  daemon process was unchanged during HA activation.

## Controlled repeat

The operator stopped the single physical daemon, confirmed the serial device
and exclusive locks were free, took stopped-owner SQLite backups, and observed
the HA physical coordinator disconnected. The stopped container was retained;
the replacement used the same image, configuration, mounts, network and
security profile. Both old and new Docker addresses were `172.18.0.4`. HA was
not restarted or reloaded during the daemon replacement and reconnect check.

Times below are UTC Docker log timestamps. Client phase elapsed values come
from the integration's monotonic timers and are rounded to milliseconds.

| Event | Time or elapsed |
| --- | --- |
| Previous established stream closed | 16:29:11.393127 |
| Daemon container started | 16:29:12.198442 |
| Handshake failed with `ClientConnectorError` while the daemon was down | 16:29:12.395170; 0.001 s elapsed |
| Coordinator scheduled retry | 16:29:12.395286; 2 s delay |
| Daemon logged radio ready, two peers | 16:29:14.172936 |
| Retry handshake started | 16:29:14.397346 |
| Handshake completed | 16:29:14.398538; 0.002 s elapsed |
| First validated snapshot completed | 16:29:14.400365; 0.001 s elapsed |
| Physical coordinator logged connected | 16:29:14.401526 |

The logged coordinator connection was approximately 0.229 seconds after the
daemon's logged radio-ready event. Separately, the polling script observed HA
connected, ready, with the sensor available approximately 0.00455 seconds
after its own native-ready API observation. This sampled interval is not the
exact socket reconnection time. The monitor's 120-second deadline began at its
native-ready API observation; it observed recovery within that deadline. Neither
startup deadline triggered in the successful live attempt; deadline behavior was
exercised in offline tests. The
native-ready API observation occurred approximately 2.102 seconds after the
script's post-container-launch monotonic start point; that is a polling
measurement, not the Docker container start-to-ready log interval.

After the repeat, both HA entries were connected, both physical peers remained
registered, physical controls remained disabled, and firmware was 0.14. Entry
data and options, 20 entities, six devices, and all 15 ledger records matched
the baseline. The component files still matched the engineering ZIP. The
replacement daemon process was intentionally different from the pre-activation
daemon process. Scoped integration DEBUG logging was restored to the prior
inherited `NOTSET` level after capture. No pairing, switch control or firmware
operations were performed, and production HA at `10.1.1.40` was not changed.

## Protected evidence

The following artifacts remain on the Intel host, under root-only testbed
directories. Their contents can include testbed details and should not be
published without review.

| Artifact | SHA-256 |
| --- | --- |
| `ha-reconnect-20261110/phase-logs.txt` | `868518e73b52f17310d9026047521fa91b3d0bac5a209da59429eecae2874d66` |
| `ha-reconnect-20261110/native-ready.txt` | `01b3d9b5863f8250a7bff77aee00e3eae3a0340d31e924a814f4ede85a3a3e0a` |
| `ha-reconnect-20261110/ha-activation-after.json` | `a0098f4d31d1e1b23085a3db6e2d010f8452a97fad3ae5be8c99da3eaa272268` |
| `ha-reconnect-20261110/ha-reconnect-after.json` | `4abe23a1f935b023ea6ca8b249ceabb3595b9ac3adc617f902ccd9c43d15a2b8` |
| `candidate-81cf6fb-20261110/transition-reconnect-hardened/outage.json` | `a7a0344e219800177e267684c3a88234cd1f8755dcdc0bb4725cbbf585d28e32` |
| `candidate-81cf6fb-20261110/transition-reconnect-hardened/after.json` | `764268ea6fdbe021456f7bcaa45c50255e4ba9bc6143a411658c482bc3d29d1a` |

All paths above are relative to `/storage/livolo-testbed`. The prior failed
installation evidence remains under `candidate-81cf6fb-20261110/transition-public-install`.
