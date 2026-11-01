# Permanent 460800 query spacing and round-spacing investigation — 2026-10-24

The daemon now permanently uses a **224 ms host query-pair delay at 460800**.
Other baud rates retain 200 ms. The three-round limit, four-second response wait,
14-second scheduling deadline and cancellation on fresh state remain unchanged.
Effective settings are visible in `diagnostics.query_timing`. No firmware was
flashed, and host/firmware versions remain 0.4.

The installed service module matches repository SHA-256
`2dc8019bad6e27a6b8469604f20b08a43a2716854a204ebc5b09b454f91ef71c`.
The previous module and a SQLite backup are retained privately on the Pi under
`/home/sfires/livolo-ec/sonoff-rounds-20261024/`. Configuration, device registry
and MQTT settings were preserved. All **103 host tests** passed, including three
new timing tests covering baud selection, cancellation during the added delay,
and the existing scheduling deadline. Documentation and formatting checks passed.

## Round-spacing comparison

Two 24-request experiments used the same Sonoff firmware and permanent pair
policy. Each run interleaved twelve requests with the normal 4.000-second response
wait and twelve with a temporary 4.023-second wait. The first used ABBA ordering;
the repeat reversed it to BAAB. Each run retained one UART session. Requests
started three seconds after the previous result. No output commands were sent.

| Policy | Host response wait | Median RF round interval, run 1 / repeat | Confirmed, run 1 / repeat | Total |
| --- | ---: | ---: | ---: | ---: |
| Permanent pair correction only | 4.000 s | 4251.082 / 4250.882 ms | 6/12 / 10/12 | **16/24** |
| Pair correction plus matched rounds | 4.023 s | 4274.143 / 4274.217 ms | 10/12 / 9/12 | **19/24** |

Aggregate median pair gaps were 237.454 ms and 237.407 ms respectively, so both
policies retained the intended within-pair spacing.

The older 115200 reference had median round-start spacing **4273.905 ms**.
Both matched-round runs were within 2 ms of that value. This compensates for
UART handling after `ac`, separately from the permanent pair-delay correction.

**There is no consistent demonstrated benefit from the extra round delay.**
Its initial four-confirmation advantage reversed in the repeat. Moreover,
first-round successes were 9/24 for the matched-round group and 4/24 for the
normal group, before the extra inter-round wait could affect that request.
Among requests actually requiring retries, 10/15 versus 12/20 eventually
confirmed. These are small, nonrandom samples whose starting phase depends on
prior completion times; the aggregate 19/24 versus 16/24 does not establish a
reliable improvement. The normal **4.000-second wait is retained**.

## What the timeouts show

The earlier remaining timeout (request 4 in the pair-spacing trial) contained
all six queries, correct data/routing/suffix and valid EC checksums. Its phase
trailers were within 0.402 ms of independently measured heartbeat age. The last
`10` received `fa/10` after **2.618 ms**, but the following `ac`, 236.502 ms after
`10`, produced no captured `fa/ac` or state report. There was no delayed state
report during the **7.135 seconds** before the next request's first query.

Across the two new runs, **13 requests exhausted retries**. Ten received a
native `fa/10` without `fa/ac` or `05`; three received no captured query reply.
Every query from these failed requests was captured. There were no late `05`
reports before the next query: observation after the last attempt was at least
**7.034 seconds**, and longer for the last request of each run. Fresh state
reports remain the confirmation criterion; a `fa/10` alone is insufficient.
Successful refreshes can have no captured `fa/10` before the `ac`/state exchange,
so the replies are tracked separately rather than treated as a mandatory
handshake.

These observations separate unanswered queries from UART loss or a state report
arriving slightly beyond the response wait. They do not prove the switch's
internal rejection reason. Matching round spacing changes the retry sampling
phase; it does not simply give a late response more time. Limited receive
opportunities, scheduling relative to native announcements and packet loss
remain possibilities. Folding queries onto a putative 200 ms cycle anchored to
native `07` traffic gives overlapping accepted/unanswered phases; no exact
receive window should be hard-coded from these samples.

Heartbeat cadence also remains different from stock: host scheduling includes
UART work and maintenance-loop latency, whereas the stock capture has almost
exact ten-second spacing. This is a separate timing variable. The current tests
do not isolate its contribution or justify changing it during the comparison.

## Capture and deployment checks

Nordic captured **203/204** reported query transmissions: 99/100 in the first
run and 104/104 in the repeat. The missing frame was request 16's third-round
`10` in the first run, which subsequently confirmed. All **48 latest TX metadata
records** and all **35 fresh state report counters** match captured packets.
All captured query phase errors were within **0.407 ms**.

Each run retained one radio session, with zero new UART RX errors, overruns,
framing/parity errors, input errors, UART/RX/radio drops, RX sequence gaps or
ACK failures. All **178 automatic ACK transmissions** succeeded. Aggregate radio
counters nevertheless recorded **434 attempts and 428 successful sends**; their
six unsuccessful attempts are not individually attributed. The combined
unsupported/malformed RX counter rose **5057**, which is not itself a UART
corruption counter. Do not describe every diagnostic as zero.

During the query windows, median heartbeat intervals were **10.0435 s** and
**10.0400 s**, ranging from 10.0226 to 10.0996 s across both runs. This cadence
is not the stock gateway's approximately 10.0007 s.

All fresh confirmations matched native counters and reported OFF/OFF. No query
was captured after a fresh confirmation. Nordic's clock alone supplies RF phase
and spacing measurements; cross-host matching uses exact TX bytes/report counters
and a documented 250 ms request-window margin. Nordic reception does not prove
every frame was received by the switch.

The temporary runner and systemd override were removed after each trial. The
normal service is active/enabled at 460800, MQTT is connected, and the switch is
online. Final diagnostics report **224 ms pair delay, 4.000 s response wait,
three rounds and a 14 s deadline**. The round-wait experiment has not been made
permanent.

Evidence: [first run](sonoff-query-round-spacing-20261024.json),
[reversed-order repeat](sonoff-query-round-spacing-repeat-20261024.json),
[per-query timeout investigation](sonoff-query-round-timeouts-20261024.json),
[deployment and aggregate results](../../host/verification/deployment-sonoff-query-timing-20261024.json),
[first Nordic capture](../../captured/development/sonoff-query-round-spacing-20261024-nrf52840.pcapng),
and [repeat Nordic capture](../../captured/development/sonoff-query-round-spacing-repeat-20261024-nrf52840.pcapng).
