# Livolo EC native API client

Async Python client for the authenticated Livolo EC daemon API. It provides
snapshot reads, a WebSocket event stream and commands with durable request IDs.
It does not open the radio or require MQTT. Python 3.9 or newer is required.

Install from the source checkout with `pip install ./client`. No public package
registry release is implied by this installation command.

The caller supplies an `aiohttp.ClientSession`, the daemon base URL and its bearer
token when creating `livolo_ec_client.Client`. Keep that session open while
using the client and close it during application shutdown. Store tokens outside
source control; the API token must not be placed in a URL.

An accepted submission is not proof of physical execution. The client resolves
uncertain submissions through the same request ID and reports unconfirmed
outcomes as errors. See the repository's `host/NATIVE_API.md` for the API contract
and `homeassistant/README.md` for configuration and supported hardware limits.

Version 0.2.0 adds `Client.install_firmware(candidate_id, request_id=None,
timeout=600)`. Pass the opaque candidate ID advertised in `firmware_update`,
optionally retaining your own durable request ID. The method submits once, then
waits for a confirmed outcome through the existing command-result endpoint.
It raises `CommandFailed` for rejection or uncertainty. Cancellation and local
timeouts stop observation only; they do not stop or replay the daemon's job.
Firmware paths, download URLs and arbitrary target versions are not accepted.

Version 0.3.0 adds `Client.firmware_preflight(candidate_id=None)`. This sends an
authenticated GET and returns a validated schema-1 advisory report. It does not
submit a job, open the radio, restart the daemon or write its database. Pass an
advertised opaque candidate ID to check that exact selection; omit it to inspect
the daemon's currently selected candidate. `compatible` may be `None` when
identity evidence is missing; only `executable is True` reports readiness.
Blocked results are returned for display, while malformed reports, a different
candidate, or an older daemon without the endpoint raise `IncompatibleError`.
Unknown response fields are discarded.

A successful preflight is not permission to skip checks during installation.
`install_firmware` remains a separate explicit operation, and the daemon repeats
its admission and hardware checks. HA 0.3.0 requires the additive
`firmware_update.preflight_supported` capability before enabling its install
action. Older daemons still support ordinary native switch control.

The HA release builder bundles this module as `livolo_ec._client`; the source
custom component alone is not an installable distribution.

Licensed under BSD-2-Clause; see [LICENSE](LICENSE).
