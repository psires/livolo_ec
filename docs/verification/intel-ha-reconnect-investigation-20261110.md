# Intel HA reconnect investigation — 2026-11-10

The published AMD64 daemon replacement on the isolated Intel HA Container
failed its 120-second natural reconnect criterion. The daemon was ready with
controls disabled; HA recovery was observed approximately 600 seconds after
container start without an HA restart or reload. The observation is not the
exact socket reconnection time. The daemon log recorded radio ready at
14:31:29.300296 UTC with two peers; HA reported the version sensor available at
14:41:27.581563 UTC, approximately 598.281 seconds later. That interval is an
observation, not socket latency. The retained logs did not capture the historical
WebSocket stage or an exception, so the cause of the delay is **unknown**. The
production HA installation, radio firmware and physical devices were unchanged
in this investigation.

Linux verified HA Core 2026.10.4 and aiohttp 3.14.5 on the testbed. Source review
of that version and the integration shows:

- HA supplies a shared aiohttp session and connector. In the inspected version,
  the default HTTP request total timeout is 300 seconds and socket-connect timeout
  is 30 seconds. The WebSocket upgrade uses that session; its 20-second heartbeat
  starts after a successful handshake. The default WebSocket receive timeout is
  unlimited. A wait for DNS, a connector slot or a handshake is not necessarily
  limited by the heartbeat.
- The integration's reconnect backoff runs only after the event iterator returns
  or raises. A generic client error uses a 1-to-30-second bounded backoff;
  authentication starts reauthentication and incompatible data stops the stream.
  An accepted event snapshot is needed for the coordinator to become connected.
- A live WebSocket that responds to heartbeats but sends no first application
  snapshot can remain open without making the coordinator connected.

Two disposable loopback experiments used the same local HA/aiohttp versions;
no testbed service or radio was contacted:

1. A server accepted the HTTP request but did not complete the WebSocket upgrade.
   With the session total artificially reduced to 1.5 seconds and heartbeat set
   to 0.1 seconds, the request timed out at 1.5 seconds, not at the heartbeat
   interval. This demonstrates that the heartbeat does not bound the handshake.
2. A server completed the upgrade and responded to heartbeats but sent no
   application snapshot. The client was still open after an externally imposed
   0.8-second observation deadline with a 0.1-second heartbeat. This demonstrates
   first-snapshot starvation independently of handshake delay.

These are reproducible boundedness gaps, not evidence that either caused the
historical delay. In particular, the approximately 600-second observation does
not prove two 300-second waits, DNS caching, or any other mechanism.

## Recommended next step

Prepare and review instrumentation before any separately authorized controlled
replacement. Record monotonic elapsed times and sanitized outcome/error class
for handshake, first validated snapshot and established-stream termination;
never log tokens, authorization headers, secret-bearing URLs or payloads. Capture
whether the existing retry task is alive and correlate those phase events with
native readiness and HA sensor observations. Restore any temporary logging level
after capture. Keep the original failed 120-second result even if a future trial
passes; do not infer changed-address behavior from a same-address test.

Consider a narrowly scoped hardening change with explicit, independently bounded
handshake and first-snapshot deadlines (for example 10 seconds each), using the
existing retry behavior on timeout. The deadlines must not cap the lifetime of a
healthy established stream or turn a quiet established stream into a failure.
Tests should cover a stalled handshake, a heartbeat-responsive connection with
no first snapshot, a healthy stream lasting beyond both deadlines, malformed
snapshot/incompatibility, authentication, cancellation and resource cleanup.
Deadline values and recovery behavior require review against actual environments.
No code change or live deployment is made by this investigation.
