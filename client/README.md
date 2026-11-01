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

The HA release builder bundles this module as `livolo_ec._client`; the source
custom component alone is not an installable distribution.

Licensed under BSD-2-Clause; see [LICENSE](LICENSE).
