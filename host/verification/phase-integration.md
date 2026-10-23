# Host phase integration review

Status: deployed as release 20261017-05 after joint review and 67 passing tests.
The Pi explicitly selects experimental phase mode on the probe-validated MCU
candidate. Initial reference, radio/MQTT readiness and exact installed hashes were
verified. No output commands were sent during deployment; captured output
acceptance remains separate. See [deployment verification](deployment-phase05-2026-10-17.json).

First captured trial result from the GUI owner: ON token 08 unconfirmed, then
OFF token 0d confirmed with bits 0, counter 45 at Unix timestamp
1792268345.980656. The full cycle failed; phase-enabled host operation alone has
not established reliable control. Both outputs are freshly reported OFF. Detailed
air analysis is pending and no further local controls or changes are underway.

## Explicit configuration and compatibility

New top-level `output_mode` accepts:

| Value | Behavior |
| --- | --- |
| `disabled` (default if absent) | Reject output commands; registry and observation remain available |
| `experimental_legacy` | Explicit zero-trailer, normal-heartbeat prelude experiment |
| `experimental_phase` | Require `tx_entry_v1`, use validated SEND_PHASE metadata; no silent fallback |

The example configuration explicitly selects legacy mode. An upgrade of the Pi
for phase testing must set `output_mode` to `experimental_phase` deliberately and
back up the old configuration alongside the database. Release 04 rejects unknown
configuration fields: rollback must restore that saved configuration or remove
`output_mode` before restarting the release-04 wheel. Do not erase the database.
GUI output buttons follow `diagnostics.output_mode=disabled`, remaining compatible
with release 04 snapshots that lack this field.

## Runtime behavior

Phase mode fails capability negotiation before configuring/sending if support is
absent or incompatible. Restore installs a fresh session and the authoritative
peer table, then sends an ordinary own heartbeat and verifies STATUS reference
validity before marking radio ready. The firmware supplies reference timestamps;
host completion time only controls conservative normal heartbeat cadence.

Output requests do not wait for or force a new heartbeat while the current
reference is usable. A short RF-lock section orders each SEND_PHASE relative to
any due heartbeat. The lock is released between attempts, so periodic heartbeat
work can replace the reference normally; firmware stamps subsequent attempts
against that new reference. UART lease/ACK work stays independent.

Each mode has at most three application attempts with approximately 200 ms waits,
one persisted token/body, advancing MAC sequence and an absolute four-second
deadline. Early confirmation stops further attempts. The six-attempt experiment
is parked entirely. Existing token quarantine/collision avoidance and strict
device/tag/source/subject/mask/value/token/freshness checks remain in effect.

`ERR PHASE_REFERENCE` is a definite non-send. Mark the reference missing, avoid
further phase sends while missing, and recover at the next normal heartbeat.
The active request remains bounded and can return busy; it does not wait ten
seconds or silently replay later. Enriched `ERR TX 2` is a definite busy result;
other radio errors, UART ambiguity or invalid metadata fail the session. Existing
restart recovery records interrupted commands as unknown and never replays them.

Latest verified phase-reference and stamped-transmission metadata are retained in
bridge diagnostics, explicitly labeled TX entry rather than SFD. Historical
reference fields with `phase_valid=0` are never treated as a live reference.

## Validation and limits

New service tests cover disabled default, missing capability before any radio
mutation, reference establishment before readiness, immediate control without a
forced prelude, three-attempt/token behavior across a heartbeat boundary, missing
reference recovery at a normal boundary, and uncertain transmission followed by
session recovery without output replay. Existing tests cover deadline, early stop,
late reports, UART cache handling, MQTT and HTTP. Final suite results follow below.

All 67 tests passed in 33.608 seconds on the final three-attempt candidate, including
real MQTT/TCP, serial PTY, HTTP and all phase/helper/runtime regressions. The earlier
run's only failure was a test teardown attempting to close a gateway already
cleared by normal service shutdown; that test cleanup was corrected before this
complete passing run. No runtime behavior was relaxed to make the tests pass.

The prior non-actuating Nordic run validated 14 unique probes with trailer/RF
residual below 0.82 ms, but no CCA-failure samples. That demonstrates bounded-run
timestamp accuracy for probes; switch acceptance and reliable controls are unproven.
No timing compensation, route discovery, ACK policy changes or retry expansion are
included in this host candidate.
