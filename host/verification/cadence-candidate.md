# Cadence candidate (release 03 experiment)

This candidate is deployed as release `20261017-03` after the user confirmed
physical OFF restoration. The controlled captured trial is coordinated by the
GUI test owner and firmware chat; deployment alone does not establish reliable
output control. See [deployment and rollback](../DEPLOYMENT.md).

Evidence: original gateway heartbeat gaps and prior successful local-control
preludes were at least about 10 seconds apart. Release 02's first ON was confirmed,
but OFF was unconfirmed after an additional heartbeat just 1.312 seconds later.
That motivates an isolated cadence experiment, not a proven explanation.

The candidate reserves the next existing heartbeat deadline for an active set.
Maintenance does not send another heartbeat while that reservation is active,
but continues lease/status work. The worker waits without holding the radio lock,
then holds it across the due heartbeat and initial set/retry sequence. The next
heartbeat cannot occur until at least 10 seconds after the previous successful
heartbeat's UART completion. This is conservative relative to its actual RF send.

The confirmation window, token allocation and baseline snapshot begin only after
the successful prelude. Epoch, packet bodies, zero trailers, tokens across retries,
session protection and strict report matching are unchanged. Busy prelude means
no output command. `finally` releases reservations/locks on cancellation, session
change, failure or timeout. The process-wide last successful heartbeat time also
survives radio reconnects within that process; process restarts still establish a
new first heartbeat as before.

Normal scheduling delay is at most about 10 seconds, plus the existing roughly
four-second report budget. The internal window wait is capped at 12 seconds to
allow limited UART contention. The five-second queue TTL remains a backlog check
at dequeue; it does not incorrectly expire the active request's intentional phase
wait. Later stale queued requests are rejected. A reconnect cancels the active
request without replay. UI and acceptance-test result polling allow 20 seconds.

This is a low-throughput experimental scheduler, not the intended final responsive
light-control interface. No speculative trailer-time formula is introduced.

Validation: 39 tests are covered. The full run passed 38 and exposed an HTTP test
race between simulated startup reporting and pairing; after that test waited for
the startup report explicitly, all five affected HTTP tests passed (15.442 s).
The unchanged remaining tests passed in the full run, including real MQTT with
the normal ten-second wait, pySerial, deadline/lock contention, cancellation,
session changes, reconnect without replay, stale queued intents, prelude ordering
and lease commands during the wait. No RF timing shortcut is used in the HTTP or
MQTT integration tests.
