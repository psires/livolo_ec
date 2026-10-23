# Local retry-window candidate — 2026-10-17

Status: parked after the phase investigation. The current local runtime retains
three attempts in both phase and legacy modes; it does not enable the six-attempt
experiment described below. The Pi remains on release 20261017-04. No deployment
or control commands were sent while preparing this candidate.

## Motivation and limits

Trial 04 sent three valid ON attempts spanning about 431 ms without any matching
ON report in the complete 119.370-second capture. OFF subsequently returned a
matching state report (both OFF, counter 44, token 0b). Earlier successful helper
transactions sometimes reported around 501 ms after the first attempt and used
a second attempt around 288 ms. Their third attempt around 528 ms followed the
report, so those observations do not demonstrate a need for a third or later
attempt. This candidate expands the transmit window to investigate phase coverage;
it is not a proven fix or a new claim about the protocol.

## Behavior

Allow at most six application attempts, retaining the existing 200 ms wait after
each completed SEND. The first-to-sixth span is about one second plus UART time.
One token and application body are reused; only the MAC sequence advances.
Check for an already-completed confirmation before each application attempt and
immediately after SEND returns. Keep release 04's token allocator, heartbeat
cadence, epoch, mask/value, zero trailer and strict matching requirements.

Use one absolute four-second confirmation deadline, set immediately before the
first SEND, covering UART latency, busy backoff, application retries and the final
report wait. Heartbeat scheduling/prelude remains outside that budget. A matching
report received at or after the deadline still updates published state but cannot
confirm the transaction. No extra application attempts start after the deadline.

If the deadline interrupts an unfinished transmission, raise a link failure with
an unknown RF outcome. The existing command worker records `unknown` and the run
loop tears down that session; it never blindly replays the interrupted output.
Ordinary unanswered sends finish `unconfirmed`; explicit all-busy results remain
`busy`. RF lock/reservation and pending future are released on every exit.

## Validation

New regression tests verify a matching fourth-attempt report stops further
attempts with exactly one reserved token and advancing MAC sequence; a six-attempt
cap under one deadline that includes UART latency; rejection of late reports for
confirmation while still updating state; and a stalled transmission ending in
link failure without another attempt or leaked lock/reservation. Existing tests
cover immediate confirmation, disconnect/session recovery and no blind RF replay.
All 40 core tests pass. The full suite ran 47 tests in 36.755 seconds: 46 passed,
including MQTT/PTY integrations. The sole failure was the HTTP rejection test's
empty-state assertion: a normal simulator startup OFF report (counter 1, token 0)
arrived before that assertion. Request rejection assertions passed. The GUI owner
is updating that test to check for output commands rather than absence of native
state reports. After that test correction, all five HTTP tests passed in 15.160
seconds. All 47 tests are covered by the initial 46 passes and the affected-suite
rerun; the complete suite was not redundantly rerun after the test-only correction.

Deployment and local output trials are on hold while the firmware chat collects
reference traffic from the original EC gateway. The Pi remains on release 04
with its daemon running; release 05 installation has not started.

Review and any subsequent captured trial remain coordinated with the GUI and
firmware chats. The daemon retains exclusive ownership of the Pi UART.
