# Web deployment and live control acceptance — 2026-10-17

The browser connects through SSH forwarding to the persistent Pi service at
`127.0.0.1:8080`. It is not the disposable simulation. The service installation,
backups and release history are in [DEPLOYMENT.md](../DEPLOYMENT.md).

## Verified

- Live device registry and received state render in the browser.
- Naming through the browser persists across a page reload. The temporary test
  alias was restored to `Development switch`.
- The deployment restart preserved the registry and existing switch association.
- Activity distinguishes confirmed requests from unconfirmed requests. It does
  not treat an HTTP response or transmitted radio command as physical success.
- During the coordinated firmware-probe service stop, the live browser displayed
  the connection warning, marked reports as potentially stale, and disabled
  output controls and adding devices. It automatically reconnected after service
  restoration, preserving the old report timestamp rather than presenting cached
  state as a newly received report.
- The final 33-test host suite passed after the phase-only change, including
  HTTP, real MQTT TCP and serial PTY integration tests.

## Hardware trials

All requests used the running daemon's HTTP API. No pairing or unpairing commands
were sent. Device `46715621`, gateway `2a992b12`, tag `a7` remained configured.

| Trial | Result | Evidence |
| --- | --- | --- |
| Same-state output 1 OFF | Unconfirmed; no fresh state report | [JSON](web-control-same-state-2026-10-17.json) |
| Release 01 ON then OFF | Both unconfirmed; user saw no change and confirmed OFF | [JSON](web-control-toggle-2026-10-17.json) |
| Release 02 ON then OFF | ON confirmed with native report, token 04, counter 38; OFF token 05 unconfirmed | [JSON](web-control-phase-02-2026-10-17.json) |
| Release 03 ON then OFF | ON confirmed, token 06, counter 40; OFF token 07 unconfirmed despite normal heartbeat cadence | [JSON](web-control-cadence-03-2026-10-17.json) |
| Release 04 ON then OFF | ON token 09 unconfirmed; OFF token 0b confirmed, counter 44, both outputs OFF | [JSON](web-control-token-04-2026-10-17.json) |
| Release 05 phase mode | ON token 08 unconfirmed; OFF token 0d confirmed, counter 45, both outputs OFF | [JSON](web-control-phase-05-2026-10-17.json) |
| Release 06 phase mode | ON token 0f and OFF token 0c both confirmed; a user-confirmed physical press occurred between them; final counter 48, both OFF | [JSON](web-control-phase-retry-06-2026-10-17.json) |

For release 02, the user independently observed output 1 turn ON. This establishes
one successful live control transaction, **not reliable ON/OFF acceptance**.
The user subsequently restored output 1 to OFF; the daemon received bits 0,
counter 39, token 04. This physical report does not confirm failed command 05.

The separate Nordic sniffer verified that release 01 commands reached the air
several seconds after the preceding heartbeat. Earlier successful helper trials
sent commands within 17–31 ms of a heartbeat. Release 02 reproduces that timing
without changing tokens, counter epoch or confirmation rules. Its ON and OFF
commands followed their respective heartbeats by 14.826 and 18.968 ms, but the
heartbeats were only 1.312 seconds apart. Whether the switch accepts such closely
spaced heartbeats remains unresolved. No timing or confirmation claim should
exceed this evidence. A further passive capture contained no OFF report; it does
not establish whether a manual press occurred.

A subsequent local candidate reserves the next regular heartbeat for each set
transaction, preserving at least ten seconds between successful heartbeats. It
does not hold the radio lock during the wait, and still rejects stale queued
commands. Token allocation and strict confirmation occur after the wait. Browser
and acceptance-checker polling allow 20 seconds for the added delay. This
candidate was deployed as release 03. Its bounded trial again confirmed ON but
not OFF, so preserving cadence alone is insufficient. Nordic measured 10.046603
seconds between the two command heartbeats. Manual OFF restoration was requested
again. The user later confirmed both outputs OFF, and the daemon recorded bits 0,
counter 43, token 06 before the next trial.
The expanded suite ran 39 tests: 38 passed in the full run, and all five HTTP
tests passed in a targeted rerun after making startup-report timing deterministic.
The new cadence, cancellation, bounded-wait and reconnect cases passed, including
the real MQTT integration test with the ten-second heartbeat gate.

Further capture comparison found that each failed ON/OFF pair had an identical
application sum8 checksum: `c3`, `c5`, then `c7`. Incrementing sequential tokens
while decrementing the output value caused these collisions. Earlier successful
stock and helper transactions used tokens and checksums that differed more
widely. Checksum-based or partial-token duplicate suppression is a hypothesis,
not an established device behavior. A token-allocation candidate was prepared
without relaxing confirmation rules. The candidate atomically
reserves a nonadjacent token and a different preceding command signature while
retaining the token quarantine. All 44 tests passed, including five allocation
regressions and the MQTT, serial and HTTP integrations. The candidate was
installed as release 04 after physical OFF confirmation.
That trial confirmed OFF but not ON. Its sum8 checksums differed (`ca` versus
`cb`), so avoiding the observed collision is also insufficient for reliable
control. The switch's fresh OFF report contained counter 44 and token `0b`;
the independent Nordic capture verified that report and its gateway ACK. Both
outputs were reported OFF. The failed full-cycle result motivated the subsequent
phase-mode trials; it was not recorded as successful hardware acceptance.

A further local candidate extends same-token retries to six attempts within an
absolute four-second confirmation deadline. It is not deployed. The user offered
the original EC gateway for reference captures, so that retry expansion was
parked while the timing protocol was measured.

The original EC capture established a timing trailer closely matching elapsed
time since its heartbeat. A firmware extension now stamps that trailer at radio
entry. Independent Nordic validation captured all 14 self-addressed probes
exactly once, with zero output frames and at most 0.816 ms trailer/RF error.
The original host release 04 was restored afterward, then the reviewed phase
runtime was deployed as release 05 after all 67 host tests passed. Its output
trial confirmed OFF but not ON. Nordic independently verified every actual
41-byte command, with timing residuals of 132–593 microseconds. OFF reported
663 ms after its first attempt, about 193 ms after its third. No delayed ON report
appeared in the complete capture. The original EC had a distinct gateway and
network tag; no identity collision was found.

Timing accuracy is established for these bounded captures. Release 06 increased
only the phase-mode attempt limit to six, retaining the four-second deadline,
command token/body and strict confirmation. All 69 host tests passed before
deployment. See [phase verification](../../analysis/development/gateway-phase-01.md).

Release 06 produced matching native reports for both HTTP requests. ON reported
bits 1/counter 46/token `0f`. A physical button press, confirmed by the user,
then produced OFF/counter 47 with that retained token before the requested OFF
transmission. The OFF request subsequently confirmed bits 0/counter 48/token `0c`.
Both outputs are finally reported OFF. This verifies command/report correlation
and manual state reporting, but is not an uninterrupted automatic ON/OFF
transition test or proof of long-term reliability. ON responded after the second
send, so this trial does not establish that the increased retry limit was needed.
The capture tail contained further native ON/OFF reports without extra output
commands; its final report was OFF/counter 50/token `0c`. The final live API
snapshot agrees, with radio and MQTT ready. See the [final snapshot](web-final-state-2026-10-17.json)
and [independent air analysis](../../analysis/development/gui-toggle-restore-06.json).

New-device pairing remains untested on hardware because the user requested
preserving the currently paired switch. Its UI/API workflow is covered by the
simulated integration tests.
