# Development Pi deployment

## Pi retired after Home Assistant handover — 2026-11-01

The HA coordinator has activated the migrated gateway on **10.1.1.40**. The old
Pi at **10.1.1.21** is retained for rollback: Docker is stopped, its restart
policy and `/opt/livolod-docker/compose.yaml` both specify `no`, and native
`livolod.service` is inactive/disabled. The boot audit found no dependent unit,
timer or cron hook that starts this deployment. No reboot or radio access was
performed during this audit.

Nordic was explicitly disarmed before the final backup, with `armed=0`, `ack=0`
and stable TX/ACK counters. Final migration state is root-only at
`/var/backups/livolod/ha-migration-final-20261101T210845Z`, database
`data/state.sqlite3`, gateway `2a992b12`, tag `a7`.

For rollback, first stop/disarm HA and migrate its **current** database back with
the correct UID/GID 10001 ownership and radio mapping. Only after that coordinated
handover, restore `restart: unless-stopped` in the Pi Compose file and run
`sudo livolodctl start`. Keep the native service disabled. The original Compose
file is backed up at
`/var/backups/livolod/retired-restart-policy-20261101T211300Z/compose.yaml`;
local instructions are `/opt/livolod-docker/HA-HANDOVER.txt`. Do not start the old
daemon alongside HA. The deployment sections below describe earlier states.

## Earlier Docker deployment — 2026-11-01

Pi **10.1.1.21** runs daemon **0.12 and its web UI** in the ARM64 image
`livolod:0.12`. Compose project `livolod` lives at `/opt/livolod-docker` and its
container is `livolod-livolod-1`. Docker Engine 29.9.2 and Compose 5.6.1 are
installed. Docker starts at boot; the container uses `restart: unless-stopped`.
The former native `livolod.service` is **stopped and disabled**.

The active radio is **official Nordic PCA10059, firmware 0.12, native USB CDC**,
using host device
`/dev/serial/by-id/usb-Livolo_local_gateway_EC_gateway_PCA10059_f1bf2323f452d37e-if00`,
mapped to `/dev/livolo` inside Docker. The authoritative database is now
`/opt/livolod-docker/data/state.sqlite3`; the original native database is stale.
Active configuration is `/opt/livolod-docker/config.json`, with container paths.
The daemon web listener remains `127.0.0.1:8080` on the Pi. **nginx now exposes
the UI at [http://10.1.1.21/](http://10.1.1.21/)**, so the SSH tunnel is optional.
Registry, network identity and saved MQTT credentials were preserved.

The configured 460800 rate is compatibility metadata for native USB rather than
an actual UART clock. TI CC2652P on `/dev/ttyAMA0` and ZB-GW04 are disarmed.
The genuine Sonoff ZBDongle-E now runs the passive reference sniffer on
**10.1.1.120**. See the [Nordic qualification record](../analysis/development/nrf52840-012-20261101.md)
for the handover, RF evidence and application-only factory-DFU recovery procedure.
The image, registry, MQTT configuration and query/retry settings were preserved;
only the `.env` serial-device mapping changed during this handover.

### LAN web proxy — 2026-11-01

Host nginx **1.26.3** is active and enabled at boot, listening on `10.1.1.21:80`.
Its site is `/etc/nginx/sites-available/livolod`, linked from `sites-enabled`;
the default welcome site is disabled. The checked-in configuration is
[nginx-development-pi.conf](docker/nginx-development-pi.conf). This development
endpoint uses HTTP and the existing UI without an additional login.

The proxy validates the public Host and Origin before translating them for the
daemon's loopback checks. CSRF tokens and cross-site request rejection remain
active. The page, assets and state API were verified over the LAN, along with an
invalid command reaching validation without operating a switch. Foreign/null
origins, cross-site requests, missing CSRF tokens and unexpected hosts returned
403. See [verification](verification/deployment-nginx-20261101.json).

Manage nginx with `sudo systemctl status nginx` and, after configuration edits,
`sudo nginx -t && sudo systemctl reload nginx`. Logs are
`/var/log/nginx/livolod-access.log` and `/var/log/nginx/livolod-error.log`.
If the Pi IP changes, update the listen address and both Host/Origin maps.
During daemon maintenance nginx can stay running; it returns 502 until Docker
starts again and the upstream web listener is ready.

### Radio maintenance

Before direct UART, GPIO or firmware development, run these commands **on the Pi**:

```sh
sudo livolodctl stop
# Perform the board-specific development task with exclusive radio access.
sudo livolodctl start
sudo livolodctl status
sudo livolodctl logs -f
```

The root-owned helper at `/usr/local/sbin/livolodctl` works from any directory.
`start` recreates the container to resolve its USB mapping after replug/flashing;
`restart` performs stop followed by recreation. An explicit stop remains stopped
across reboot until started again. Do not start the native service or use a
firmware updater's `--service livolod` option. Host maintenance tools must use
the host serial path and, when needed, the current Docker database path rather
than the paths inside the container. ZigBee Specialist has been notified of this
ownership change and these commands.

Verified Docker load, matching installed daemon/web sources, physical radio
ownership by only the container, clean stop with exit 0 and serial release,
successful recreation, SQLite integrity, preserved registry and persistent
metadata, MQTT connection, both switches online, and HTTP 200 for the web page,
JavaScript and stylesheet. The changing `firmware_status` runtime record was
excluded from the persistent metadata comparison. No firmware or output-setting
commands were issued during deployment. Boot recovery is configured but a Pi
reboot was not performed.

Pre-migration configuration and a consistent SQLite backup are protected under
`/var/backups/livolod/before-docker-20261101`. Any later return to the native
service must first stop Docker and transfer its **current** database with correct
native ownership; reusing the old database would lose intervening state.
See [deployment evidence](verification/deployment-docker-0.12-20261101.json),
[Docker installation and maintenance](docker/README.md), and the
[helper source](docker/livolodctl).

## Historical deployment — 2026-10-24

At this earlier deployment the radio was **TI CC2652P**, running application **0.7 at 460800** through
`/dev/ttyAMA0`, with host **0.7** and signed-update bootloader **0.2**. Gateway
identity, device registry and MQTT configuration are retained. Sonoff ZBDongle-E
0.4 remains connected and explicitly disarmed as a fallback; its Gecko bootloader
2.4.2 remains at 115200.

## Complete MCU state queries — 2026-10-24

TI application/host **0.7** now execute the full bounded state-query transaction
on the radio MCU: both opcodes, CCA backoff, rounds, deadline and fresh-report
stopping. The Pi independently validates and persists the retained native frame;
all authoritative network state remains on the Pi. Three rounds, four CCA attempts
per opcode and the 14-second deadline are unchanged.

Nordic measured **235.602–235.686 ms** pair spacing, eliminating the preceding
353–703 ms outliers. **21/24** queries confirmed versus 17/24 on 0.6; three still
timed out after `fa/10` without state. All 21 retained confirmations matched
Nordic and no captured query followed a confirming report. One of 94 query
frames was missed by the sniffer, with its native `fa/ac` and state captured.

All **6000 UART requests**, **68 firmware tests** and **109 host tests** passed.
Complete transactions also ran without UART access for 14.5 seconds and during
880 CAPS requests. The production signed updater confirmed 0.7 without GPIO,
preserving bootloader 0.2. Configuration, registry and MQTT settings are unchanged.

The normal MCU profile uses 15434 ticks between 10/ac and 262930 ticks after ac,
matching prior measured TI wire timing. The temporary extra-wait override is
removed. Service is active/enabled, MQTT connected, and the switch online with
latest fresh state **OFF/OFF, counter 124**. Sonoff is disarmed. No output-setting
commands were sent. Installed service SHA-256:
`149ba0f40bbcf56001ccadae4a2aa782430643c730f8cf2a51a9a7aa02d87374`.
See the [comparison and remaining limits](../analysis/development/ti-query-mcu-comparison-20261024.md)
and [deployment evidence](verification/deployment-ti-query-mcu-20261024.json).

## Earlier MCU timed queues and heartbeats — 2026-10-24

TI now owns an absolute ten-second heartbeat cadence and eight volatile timed
job slots with retained results, cancellation and lease/session invalidation.
The Pi retains authoritative state and the query retry/freshness loop. The
production UART updater confirmed 0.6 without GPIO or bootloader intervention.

All **6000 UART integrity requests** passed. Nordic verified seven queued probes
during twelve seconds of host silence, no cancelled/expired probes, and stable
heartbeats during 768 UART requests. In the 24-query comparison, heartbeat RF
intervals were **10.000772–10.000854 s**, versus 10.017983–10.144293 s on TI 0.4.
Queries confirmed **17/24** versus 18/24 before; all seven failures had `fa/10`
without state. This stage improves cadence but does not solve query reliability.

The service is active/enabled, MQTT connected and the switch online, with latest
fresh state OFF/OFF, counter **103**. Registry/MQTT values match the pre-test
backup; configuration changed only to the TI port. Permanent timing remains
224 ms / 4.000 s / three rounds / 14 seconds, with no runtime override. No output
commands were sent. The installed service source SHA-256 is
`24fff03cfc8466510d72ceff40a7e176544293c16c525168bdd3fe380aab9a52`.
See the [scheduler comparison](../analysis/development/ti-scheduler-comparison-20261024.md)
and [deployment evidence](verification/deployment-ti-scheduler-20261024.json).
The sections below describe earlier deployment states.

## TI comparison and Sonoff restoration — 2026-10-24

The TI CC2652P was updated from 0.2 to signed application **0.4 at 460800**,
preserving its 0.2 bootloader. Its trial was confirmed by the daemon. The actual
board has 352 KiB flash / 80 KiB SRAM; it is not a P7. All 6000 UART integrity
requests passed, but only **18/24** state refreshes confirmed: **9/12** with each
of the normal and extra-23-ms response waits. Nordic captured all queries in the
six timed-out requests, reproducing the Sonoff failure pattern. TI lacks the
additional MG21 hardware UART error counters.

**Sonoff was restored as the active radio after that comparison**, with its original configuration restored
byte-for-byte and registry/MQTT values preserved. The service is active/enabled,
MQTT is connected and the switch is online. The latest fresh report, received
during the TI trial, is OFF/OFF, counter **86**. Permanent timing remains 224 ms
between queries, 4.000 s response wait, three rounds and a 14 s deadline. No
output-setting commands were sent and no timing override remains.

TI was left **disarmed at 460800**, with application 0.4 and bootloader 0.2.
The test used a coordinated baud migration; ordinary signed updates must still
preserve the running application baud. See the [comparison and timing limits](../analysis/development/ti-query-comparison-20261024.md),
[deployment evidence](verification/deployment-ti-comparison-20261024.json), and
[proposed volatile radio scheduler](../analysis/development/radio-transaction-scheduler.md).

## Permanent query-pair correction and round timing — 2026-10-24

Host 0.4 now permanently uses a **224 ms query-pair delay at 460800**; other baud
rates retain 200 ms. `diagnostics.query_timing` exposes the effective policy.
The installed service source SHA-256 is
`2dc8019bad6e27a6b8469604f20b08a43a2716854a204ebc5b09b454f91ef71c`.
All 103 host tests passed, including three new timing-policy checks. The daemon
is active/enabled, MQTT is connected, and the test switch is online with its
latest reported state OFF/OFF, counter 68. Configuration and registry were preserved.

Two interleaved 24-request comparisons tested a temporary **4.023 s** response
wait against the normal **4.000 s**, retaining three rounds and the 14-second
scheduling deadline. The extra wait matched the earlier 115200 RF round interval
within 0.313 ms. Its initial result, 10/12 versus 6/12, did not repeat: reversing
the order produced 9/12 versus 10/12. Aggregate results were 19/24 versus 16/24,
with different first-round success rates. This does not establish a consistent
benefit; **the permanent response wait remains 4.000 s**.

Nordic captured 203/204 queries; all 48 final TX metadata records and 35 fresh
report counters matched. Captured phase error stayed within 0.407 ms. Both runs
retained one session with zero UART errors or transport drops. Thirteen refreshes
exhausted retries: ten had `fa/10` without `fa/ac`/state and three had no captured
query reply. No late state report arrived during at least seven seconds after
their last attempt. See the [investigation and capture limitations](../analysis/development/sonoff-query-round-spacing-20261024.md)
and [final deployment evidence](verification/deployment-sonoff-query-timing-20261024.json).

## Sonoff query spacing comparison — 2026-10-24

A temporary 224 ms host delay at 460800 produced **237.302 ms** median on-air
query-pair spacing, matching the earlier 115200 reference of 237.019 ms.
**11/12 refreshes confirmed**, versus 2/12 with the Sonoff's normal 200 ms delay.
Nordic captured all 54 queries; phase error stayed within 0.402 ms and UART
diagnostics remained clean in one session. The remaining timeout received
`fa/10` but no `fa/ac` or state report.

The three-round limit, four-second response wait and 14-second deadline were
unchanged. Round-start spacing remained approximately 23 ms shorter than the
115200 reference, so only typical within-pair spacing was matched. The normal
**200 ms delay has been restored**, with application baud still 460800, MQTT
connected and the switch online. No persistent configuration or firmware changed.
See the [comparison and limitations](../analysis/development/sonoff-query-matched-spacing-20261024.md)
and [restoration evidence](verification/deployment-sonoff-spacing-20261024.json).

## Sonoff migration and UART receive correction — 2026-10-24

A stronger test reproduced incoming-command corruption on Sonoff too. Separate
hardware counters identified receive FIFO overruns. The MG21 UART writer masked
all interrupts while copying long outgoing messages. It now stages bytes with
interrupts enabled and publishes the queue head once after a memory barrier.
The radio algorithms, host sources and query retry schedule were unchanged.

After the correction, **6000 UART requests** passed without a mismatch, timeout,
receive error or drop. A **ten-minute live run** retained one session and zero
UART errors, RX gaps or ACK failures. The daemon remains active/enabled, MQTT is
connected, and the test switch is online with its last fresh state OFF/OFF.
This verifies a bounded stability improvement, not long-term qualification.

RF queries remain unreliable: **2/12 confirmed**, with ten exhausting retries.
Nordic captured 63/64 transmissions, median query spacing 213.397 ms and phase
error at most 0.404 ms. Six failed requests received `fa/10` but no `fa/ac` or
state report. This test did not resume the interrupted spacing-match experiment.
The earlier ZB-GW04 USB bulk-IN truncation remains a separate unresolved issue.

No exact original Sonoff firmware/configuration backup was obtainable through
the tested interfaces. Its bootloader was preserved; an official stock recovery
image was saved and is explicitly not a device readback. The Pi configuration
and database were backed up before migration and their registry/MQTT values
were verified afterward. Only `serial_port` changed. No output commands were sent.

See [full Sonoff findings and recovery paths](../analysis/development/sonoff-migration-20261024.md),
[deployment evidence](verification/deployment-sonoff-20261024.json), and
[RF capture verification](../analysis/development/sonoff-query-stability-20261024.json).
The ZB-GW04 experiments below describe the earlier hardware and deployment state.

## Reboot recovery and separate sniffer Pi — 2026-10-24

The gateway at `10.1.1.21` recovered automatically at its previous 115200 setting:
`livolod` was enabled and active, firmware/host reported 0.4, MQTT connected,
and a fresh query confirmed switch `46715621` OFF/OFF in the first round.
The Nordic sniffer is now on **`10.1.1.120`**. The address `10.1.1.20` was a typo.

The same existing 0.4 application was then installed at **460800**, with matching
daemon configuration. Gecko remained at **115200**. While Nordic recorded on the
second Pi, **200/200 CAPS replies matched exactly**, with no timeout. The earlier
same-Pi test had 84/200 corrupted replies. This demonstrates a working separated
setup for this bounded test; the reboot is an additional changed condition, so
it does not prove the precise earlier USB/bridge failure mechanism.

The unchanged twelve-request query trial completed with **five confirmed and
seven unconfirmed** (1, 3, 4, 5, 8, 9, 10). Limits remained three rounds, 200 ms
between queries, four seconds after each pair, and a 14-second deadline. Nordic
captured **61 of 62** reported query transmissions. Request 5's first `10` was
missed by the sniffer, but its native `fa/10` reply was captured. Complete pairs
had median spacing **213.286 ms** (range 211.023–399.032 ms), compared with
237.019 ms in the earlier 115200 trial. All 61 captured query phase stamps were
within **0.430 ms** of independently captured heartbeat-relative RF age.

All five fresh reports matched daemon state/counters 11–15, with no further
queries after confirmation. All reported OFF/OFF. Failed requests 1, 5 and 9
received `fa/10` but no `fa/ac` or state report; requests 3, 4, 8 and 10 had no
captured query acknowledgement or state report. There was no UART session loss.
RX/UART/radio drop, UART RX error, input error, RX sequence-gap and ACK-failure
deltas were zero. The adapter nevertheless recorded ten unsuccessful radio send
attempts, and the host's combined malformed/unsupported RX counter rose by 1050;
the aggregate counters do not identify their individual causes. These are not
an all-errors-zero result. RF query reliability remains unresolved and this
five-of-twelve result is worse than the earlier ten-of-twelve 115200 sample;
those trials were not a concurrent controlled A/B comparison.

The unmodified capture timestamps for the five counter-identified state reports
were approximately 120–123 ms later than daemon reception timestamps on the
other Pi. Offline request matching therefore uses a documented 250 ms margin,
well below the three-second gap between requests, together with exact TX bytes
and native report counters. RF spacing and phase comparisons use Nordic's clock
alone. Both hosts reported NTP synchronized, which did not ensure submillisecond
cross-host alignment.

The development setup remains at **460800 with the separate sniffer**. This is
a passing UART smoke test, not qualification of RF query reliability. Final
checks found the daemon ready, MQTT connected, the switch online, and registry
and MQTT settings preserved; only the configured baud changed. No output
commands were sent. Default builds/configuration remain 115200.

Evidence: [deployment and integrity results](verification/deployment-separate-sniffer-460800-20261024.json),
[per-request RF verification](../analysis/development/mg21-query-separate-sniffer460800-20261024.json),
[Nordic capture](../captured/development/mg21-query-separate-sniffer460800-20261024-nrf52840.pcapng).
Gateway test files and private pre-test backups are under
`/home/sfires/livolo-ec/separate-sniffer-20261024`; sniffer files are under
`/home/sfires/livolo-sniffer/separate-host-20261024` on `10.1.1.120`.
The running image is `firmware/mg21/build/ec-mg21-460800.gbl` under the earlier
`/home/sfires/livolo-ec/uart-baud-20261024` staging directory. Captures and sanitized
results are also preserved locally; the two experiments below are historical.

## Initial shared-Pi application baud experiment, 0.4 — 2026-10-24

**This initial trial ended at 115200. Its 460800 test failed concurrent-sniffer
UART integrity; the later separate-Pi trial above supersedes its deployment state.** Firmware now selects application baud
with `UART_BAUD=115200|230400|460800|921600`; the daemon JSON `serial_baudrate`
must match. Both default to 115200. This is a build-time choice, not live
negotiation. Startup and `CAPS baud=` report the actual compiled rate; the daemon
checks it before arming. Host hardware tools and the TI application updater use
the configured rate. TI hardware and other higher rates were not tested here.

The MG21 application and daemon report **0.4**. Gecko bootloader **1.9.2 remains
unchanged at 115200**. Remote `BOOTLOADER` entry from the 460800 application,
application-only GBL upload at 115200, and reconnect at 460800 were exercised
successfully. The final restoration used the same path to install 0.4 at 115200.

An AST comparison against deployed 0.3 found `refresh_state`, `_transmit`, both
heartbeat methods and maintenance unchanged. Limits remain three rounds,
200 ms between queries, four seconds after each pair and a 14-second deadline.

The first 460800 Nordic run confirmed two refreshes, then aborted the third when
the UART session failed. Eight queries were independently captured. Median
query-pair spacing fell from **236.987 ms** in the earlier 115200 run to
**213.041 ms**, a **23.946 ms** reduction. All eight phase fields matched captured
heartbeat-relative age within **0.399 ms**. A diagnostic repeat aborted its first
refresh on the same class of session failure; its four phase fields were also
within 0.400 ms. These incomplete samples cannot establish RF reliability.
No output commands were sent; confirmed state remained OFF/OFF.

UART tracing showed deleted characters in otherwise readable replies: for
example, `gateway=2a992b12` arrived as `gateway=2a99`, losing four characters
starting at offset 64. The daemon correctly treated the altered identity as
configuration drift and reconciled the session. A separate read-only test gave:

| Application baud | Nordic | Capability replies | Truncated | Timed out |
| --- | --- | ---: | ---: | ---: |
| 460800 | idle | 200 | 0 | 0 |
| 460800 | recording | 200 | 84 | 0 |
| 115200 | recording | 200 | 0 | 0 |

A USB trace filtered to the MG21 CH340 device contains the same missing characters
in bulk-IN payloads, before Python receives them. MCU UART drop/error counters
remained zero in the captured failure statuses. This localizes the loss ahead of
the daemon's parser; it does not yet distinguish the bridge, USB scheduling,
controller or driver mechanism. The 460800 failures must not be counted as
ordinary unanswered RF queries. Temporary UART instrumentation was removed.

Final 115200 query verification completed all twelve requests: **10 confirmed
and two unconfirmed** (requests 3 and 11 exhausted three rounds). Nordic
verified **48** phase-stamped queries, median pair spacing **237.019 ms**, phase
error at most **0.413 ms**, and no queries after an accepted fresh state report.
The session stayed unchanged. RX/UART/radio drop, UART RX error, RX sequence-gap
and ACK-failure deltas were all zero. All ten confirmed states were OFF/OFF;
counters advanced from 135 to 144. The remaining RF timeouts are distinct from
the 460800 UART failures and remain unresolved.

The release passed **100 host tests**, **38 shared firmware tests** (115200 and
460800 builds), two MG21 packager tests, formatting, documentation and version
checks. Both MG21 rate variants built with the pinned SDK. Network identity,
registry and MQTT settings were preserved; the only config addition is
`serial_baudrate: 115200`.

Evidence: [comparison and transport diagnostics](../analysis/development/mg21-uart-baud-comparison-20261024.json),
[deployment and final health](verification/deployment-uart-baud-0.4.json),
[first 460800 RF capture](../captured/development/mg21-query-baud460800-initial-20261024-nrf52840.pcapng),
[diagnostic 460800 RF capture](../captured/development/mg21-query-baud460800-diagnostic-20261024-nrf52840.pcapng),
[restored 115200 RF capture](../captured/development/mg21-query-baud115200-20261024-nrf52840.pcapng).

This initial trial staged sources, images, wheel and private diagnostic logs under
`/home/sfires/livolo-ec/uart-baud-20261024`. Its restored application was
`firmware/mg21/build-115200/ec-mg21-115200.gbl` there. The 0.3 host, firmware,
config and database backup is `/opt/livolod-releases/0.4-uart-baud-20261024`.
A rollback to that backup also uses application/bootloader 115200. Restore the
matching host/config together; do not overwrite the live database merely to
change firmware. Ordinary future updates should preserve application baud;
changing it requires coordinated configuration and rollback handling.

Before the planned Pi reboot/sniffer relocation, all Pi development files,
installed host/configuration, firmware builds and rollback releases were archived
under `/home/sfires/livolo-reboot-backup-20261024T155901Z`. A second archive
preserves project files found in `/tmp` and auxiliary daemon state. The live
SQLite database was copied through its backup API and passed integrity checks.
Both private archives were copied to the workstation at
`.local/reboot-backup-20261024T155901Z/`, with matching SHA-256 checksums and a
second SQLite integrity check. They contain private configuration and remain
outside Git. `livolod` was active and enabled for startup at 115200. A normal
reboot does not require restoring these archives.

## Phase-stamped state queries, 0.3 — 2026-10-24

Firmware now advertises `phase_queries=10_ac_v1` and accepts the observed direct
`10/ac` query payload through `SEND_PHASE`. In the Pi's existing
`experimental_phase` mode, the host validates the returned stamped bytes and
timing metadata. The native query body/checksum remains unchanged. Missing
references, session failures and uncertain TX outcomes never fall back to raw
queries; definite CCA-busy attempts retain their existing bounded backoff.

An AST comparison against the exact deployed 0.2 service verified that refresh
control flow changed only in transport selection. Limits remain three rounds,
200 ms between queries, four seconds waiting after each pair, and a 14-second
scheduling deadline. Heartbeat and maintenance algorithms are unchanged.

Twelve sequential read-only requests on switch `46715621` produced **11 confirmed
and one unconfirmed** result. Four confirmed in round one, four in round two and
three in round three. Request 8 exhausted six queries in 12.871 seconds. The
earlier raw-query run confirmed four of six requests. These small sequential
samples do not establish a causal reliability improvement, and phase stamping
alone does not eliminate timeouts.

Nordic independently verified all **48** queries had nonzero timing trailers,
matching captured heartbeat-relative RF age within **0.061–0.424 ms**. Every
confirmed result had a matching native state report; no further query followed
that report within its request. State counters advanced from 122 to 132 and all
reports remained OFF/OFF. No output commands or pairing operations were sent.
There were no additional RX/UART/radio drops, UART RX errors or ACK failures.

The remaining timeout is particularly informative: query `10` packet **553**
received `fa/10` **554** after **2.591 ms**, but the following `ac` **558** received
no captured `fa/ac` or state report. All six queries in that request were stamped
within 0.406 ms of the RF heartbeat reference. Thus accurate phase fields do not
guarantee completion even after the first query is answered.

Although configured delays are identical, the larger UART timing replies change
actual on-air spacing: pair gaps were **235.798–296.026 ms** (median 236.987 ms),
versus about 217–219 ms normally before this change. Round-start gaps were
4.272–4.393 seconds. Heartbeats remained approximately 10.064–10.101 seconds
apart. That transport latency is a confound when comparing success rates; query
slot alignment and heartbeat scheduling remain untested variables.

All **97 host tests**, **19 shared firmware tests** and **2 MG21 packager tests**
passed. Formatting, documentation and release-consistency checks passed. The
final daemon reports host/firmware 0.3, `query_mode=phase`, radio ready and MQTT
connected. Registry, MQTT settings and startup configuration match their backups.
Initial deployment helper failures (a reused UART ID, then installer permissions)
were corrected; the original application/host were restored before the final
successful deployment. No changes were made to the TI module.

Build directory: `/home/sfires/livolo-ec/query-phase-20261024`.
Application SHA-256: `0d54dbe674a979e9c0961e06568f0e77d3eac0f0c6aaad7d9db00bff2837d450`.
Application-only GBL SHA-256: `bf5de3120acf71910a0c6d398618b0fa799ffebe65a6bc58a7512ae163eeae2f`.
Host wheel SHA-256: `bbe5e07012c1c8fd5fdb2e76a0f15326cbf4ce4dce7ec37a87bac5d46eadd687`.
These are build hashes; Gecko validation/startup and RF checks establish deployment,
not full-flash readback. The preserved Gecko bootloader remains 1.9.2.

Rollback backup: `/opt/livolod-releases/0.3-query-phase-20261024-verified`.
It contains `firmware-before.gbl`, the previous `livolod` package and dist-info,
entry points, configuration and SQLite snapshot. For rollback, stop the service,
enter Gecko through a fresh-ID `BOOTLOADER` command, upload the previous GBL,
restore the previous package/metadata/entry points with readable permissions,
then restart the service. Keep the current database and configuration.

[Deployment and schedule verification](verification/deployment-query-phase-0.3.json),
[live requests and phase measurements](../analysis/development/mg21-query-phase-20261024.json),
and [Nordic capture](../captured/development/mg21-query-phase-20261024-nrf52840.pcapng).

## Bounded state-query retries — 2026-10-24

Host 0.2 now retries state refreshes for up to three `10`/`ac` query rounds
(six queries), with a 14-second scheduling deadline. A fresh accepted state
report stops further queries, including when the reported output state is
unchanged. Exhaustion returns `unconfirmed`; it does not invent a device state.
The existing UART operation timeout still bounds an already submitted transfer.

Six live refresh requests through MG21 produced four confirmations and two
bounded timeouts. One confirmation required the second round; the other three
arrived in the first round. Both failures stopped after six queries in about
12.8 seconds. Nordic independently captured all 22 query transmissions and the
four matching state reports, with no queries after confirmation and no output
commands. The test switch remained OFF/OFF. Intermittent missing responses remain
unresolved; this change improves recovery and makes failure bounded.

The full 93-test host suite passed, followed by a rerun of all 46 daemon tests
after the final lock-wait refinement. Formatting and documentation checks passed.
Radio and MQTT were ready after the live test; device registry, saved MQTT
settings and startup configuration matched the pre-deployment backups. Capture
diagnostics recorded no RX/UART/radio drops or ACK failures. The host also counted
329 rejected RX records; this counter includes frames unsupported by the EC
decoder and does not establish that these were corrupted EC packets.

Only the installed host `livolod/service.py` was replaced; firmware remains 0.2.
Build label: `bounded-query-retries-20261024`.
Service SHA-256: `16ed2a691dd6ff6503dfc183d2de1edf287e8923fe9cbcafb367120547f5fc74`.
Backup directory: `/opt/livolod-releases/0.2-query-retries-20261024`.
To roll back this host patch, stop `livolod`, restore `service-before.py` from that
directory to `/opt/livolod/lib/python3.13/site-packages/livolod/service.py` with
mode 0644, then restart `livolod`. Keep the current database and configuration.

[Deployment identity](verification/deployment-query-retries-20261024.json),
[live request and packet evidence](../analysis/development/mg21-query-retries-20261024.json),
and [Nordic capture](../captured/development/mg21-query-retries-20261024-nrf52840.pcapng).

## TI signed-update deployment history — 2026-10-18

**TI release: `0.2`**, with host, application firmware and resident
bootloader all reporting `0.2`. The daemon detects running/target firmware versions,
reports whether an update is available, and confirms a matching trial after
restoring the radio session. UART protocol remains version 3. Release numbering
now follows `major.minor`; dated identifiers below are historical deployments.

The signed UART updater completed a managed service stop, transfer, activation,
restart and confirmation. A subsequent UART software reset retained the confirmed
image. An intentionally unconfirmed trial rolled back from `0.2` to `0.1` in
about 62 seconds after trial startup. Resetting an incomplete transfer after
288 bytes also preserved the working image. These routine update/recovery tests
used no GPIO operations and sent no switch output commands. Initial bootloader
installation used the existing GPIO/ROM recovery route.

Radio and MQTT are ready, with zero systemd automatic restarts. Device registry,
saved MQTT settings and startup configuration were verified unchanged against
the pre-deployment backups. The daemon now reports `update_available=false`.
All 87 host tests, 35 tool tests, 18 gateway protocol tests and 7 update-core tests
passed. Hardware validation caught and corrected missing TI VIMS cache/line-buffer
invalidation around flash operations before the final successful tests.

Host source: `/opt/livolod-releases/0.2/host`.
Wheel SHA-256: `90fd8e60734fca6aa1af402defcf3ff32bb4d18a60021d59830366bed2a4202c`.
Application package SHA-256: `4faee78653439b80e81b6e26c183f62f2e7b467b1aa569dd1e21234c45426203`.
Database backup: `/var/lib/livolod/backups/before-update-0.2-20261018.sqlite3`.
Configuration backup: `/etc/livolod/config.before-update-0.2-20261018.json`.
Original MCU backup: `/home/sfires/livolo-ec/backups/before-uart-update-0.2-20261018.bin`.

[Deployment evidence](verification/deployment-uart-update-0.2.json),
[timeout rollback](../analysis/development/uart-update-rollback-0.2.json),
[interrupted transfer](../analysis/development/uart-update-interruption-0.2.json),
[confirmed reboot](../analysis/development/uart-update-confirmed-reset-0.2.json), and
[build identity](../analysis/development/uart-update-0.2-build.json).
See [update operations](README.md#firmware-updates) for future releases.

Host rollback: stop `livolod`, reinstall the retained `20261017-08` wheel, then
restart, keeping the database/configuration. This does not downgrade the MCU.
Normal application updates only accept a newer signed version. To restore the
pre-updater MCU image, stop the daemon, use the configurable GPIO/ROM recovery
tools and the full backup, verify flash CRC, then restart the daemon. Bootloader
replacement is deliberately outside routine application updates.

These checks qualify the update path on this development module. Physical
power-cut testing, other board variants and long-term RF reliability remain
separate validation work.

## Release 08 history

**Previous release: `20261017-08`** adds Home Assistant MQTT discovery. Both
outputs of **Development switch** are advertised as separate MQTT switch
entities under one device. Retained configuration and state were independently
received from the configured broker at `10.1.1.40:1883`, prefix `livoloec`.
Radio and MQTT are ready, with no service restarts or startup errors.

All 78 host tests passed, including real TCP discovery, exact HA command payloads
against the simulator, channel isolation, retained-command rejection, reconnect,
HA birth, renaming and removal. No physical output commands were sent during
deployment or verification; the HA frontend itself was not inspected.

Source: `/opt/livolod-releases/20261017-08/host`.
Wheel SHA-256: `12f49b3ac45d353a8f9afed2ee6fea94acce8fd1fc0b64dfcc5bede630273960`.
Source archive SHA-256: `2639f864bdbed5d5e6314baff0475b1750915e4bff09dcb7b8b68a87923f9698`.
Installed runtime/static source hashes were verified. Startup configuration,
SQLite registry and saved MQTT settings were preserved; firmware was not changed.
Database backup: `/var/lib/livolod/backups/before-ha08-20261017.sqlite3`.
Configuration backup: `/etc/livolod/config.before-ha08-20261017.json`.
[Deployment evidence](verification/deployment-ha08-2026-10-17.json).

Rollback: stop `livolod`, install the retained release-07 wheel with
`/opt/livolod/bin/pip install --no-deps --force-reinstall`, then start the service.
Keep the existing database/configuration. Retained HA discovery entries remain
on the broker after rollback, and their existing command/state topics still work
with release 07; remove those retained config topics explicitly if withdrawing
HA integration. See [HA behavior and permissions](README.md#home-assistant).

## Release 07 history

**Previous release: `20261017-07`** adds MQTT configuration under **Network →
MQTT settings** (server, port, topic prefix, username, keep/replace/clear password).
Settings persist in private SQLite metadata and reconnect MQTT without resetting
the radio. Startup configuration, registry and firmware were preserved during
deployment. All installed source hashes were verified; radio and MQTT are ready.
The live form successfully saved the existing settings unchanged. All 74 host
tests passed, including real TCP credentials/topic replacement, HTTP security,
password redaction, persistence and simulation namespace isolation.

Source: `/opt/livolod-releases/20261017-07/host`.
Wheel SHA-256: `7f380bd58684b2a2ada25800803b1562154008ef57b565ca930ffba907173ef6`.
Source archive SHA-256: `a5d9a2ec88e094d3779ac6bdafda133c9565c69c51f889bf84080bfb5a426a17`.
Database backup: `/var/lib/livolod/backups/before-mqtt07-20261017.sqlite3`.
Configuration backup: `/etc/livolod/config.before-mqtt07-20261017.json`.
[Deployment evidence](verification/deployment-mqtt07-2026-10-17.json).

Rollback: stop livolod, install the retained release-06 wheel using
`/opt/livolod/bin/pip install --no-deps --force-reinstall`, then start livolod.
Preserve the database; release 06 ignores the new MQTT metadata and uses the
startup JSON instead. After any UI settings change, account for this difference
before rollback. Password-bearing databases and backups must remain private.
See [MQTT settings documentation](WEB_UI.md#mqtt-settings) for storage precedence
and returning to startup JSON configuration.

## Release 06 history

Latest control verification on release 06: both HTTP requests received matching
native ON/OFF reports. A user-confirmed physical press occurred between them;
the final native report and API snapshot show both outputs OFF, counter 50,
token `0c`. Radio and MQTT are ready. This is not an uninterrupted automatic
transition or long-term reliability test. See [web acceptance](verification/WEB_ACCEPTANCE.md)
for the complete evidence and remaining limits.

**Previous release: `20261017-06`**, six attempts in experimental phase mode.
The sole runtime change from release 05 is the phase-attempt limit from three to
six. Legacy mode stays at three; the four-second deadline, token/body, MCU timing
and confirmation rules are unchanged. All 69 tests and joint reviews passed.

One controlled host restart preserved configuration bytes, database/registry and
MCU firmware. Every installed source/UI hash was verified, including service hash
`45c386a2d19c234877e248d55963cddf72ea029f3ac5f73ecf9d7e59c57a7e27`.
Radio/MQTT and a fresh phase reference are ready. No output or pairing commands
were sent by deployment. Saved state remains bits 0, counter 45, token 0d at
1792268345.980656; immediate post-restart availability was offline awaiting native
RX, so this was not a new physical confirmation. Captured acceptance is separate.

Source: `/opt/livolod-releases/20261017-06/host`.
Wheel: `/opt/livolod-releases/20261017-06/wheels/livolo_ec_daemon-0.1.0-py3-none-any.whl`.
Wheel SHA-256: `0e2424def78acabf1a5c4ec49818b4bbe408ef4463125a910b2c1ec8f247a6f3`.
Source archive SHA-256: `84b4f612bf6474b1c6a1b35c0258de6de57bf5c6a4e1ec49caf78c46fdbb6d07`.
Database backup: `/var/lib/livolod/backups/before-phase06-20261017.sqlite3`.
Configuration backup: `/etc/livolod/config.before-phase06-20261017.json`.
[Live verification](verification/deployment-phase06-2026-10-17.json) and
[candidate review](verification/phase-retry06-candidate.md).

Rollback to release 05: stop livolod, install the retained release-05 wheel with
`/opt/livolod/bin/pip install --no-deps --force-reinstall`, then start livolod.
Keep the same configuration and database; neither schema nor configuration changed.

## Release 05 history

Release `20261017-05` introduced explicit experimental phase mode.
All 67 tests and joint GUI/firmware reviews passed before deployment. One
controlled stop/install/start installed the exact reviewed code and UI, preserved
the database/registry, and set `output_mode=experimental_phase`. Capability
negotiation and an initial heartbeat reference passed before readiness; radio and
MQTT are ready. No output or pairing commands were sent by the deployment.

The saved baseline remains bits 0, counter 44, token 0b at timestamp
1792266728.6699536. The immediate post-restart availability was offline while
awaiting native traffic; the saved state is not a new physical confirmation.
The GUI/firmware chats coordinate captured output acceptance separately.

The first release-05 phase-enabled trial was **partially successful**: ON token
`08` (request `web-live-ff53451272304608871050133e4f706d`) was unconfirmed.
OFF token `0d` (request `web-live-a040e5980cd04f16b6758266b0604094`) received a
matching native report at Unix timestamp 1792268345.980656: bits 0, counter 45.
Both outputs are freshly reported OFF, but a successful ON/OFF cycle has not been
established. Completed air analysis validates all six command trailers within
132–593 microseconds of RF heartbeat-relative timing and finds no delayed ON
report. OFF confirmed 663 ms after the first attempt. A local six-phase-attempt
[candidate](verification/phase-retry06-candidate.md) is under review; release 05
remains installed and no further cycles have been sent.

Source: `/opt/livolod-releases/20261017-05/host`.
Wheel: `/opt/livolod-releases/20261017-05/wheels/livolo_ec_daemon-0.1.0-py3-none-any.whl`.
Wheel SHA-256: `2e002e53d71924e3019a2fa0e613a0ceb0fb504de135548e1bc66618a60dc461`.
Source archive SHA-256: `7ca95f17d0449c33850735be1eb49d5169b88a7b26052180643b05e47718eb9f`.
Database backup: `/var/lib/livolod/backups/before-phase05-20261017.sqlite3`.
Configuration backup: `/etc/livolod/config.before-phase05-20261017.json`.
[Live verification](verification/deployment-phase05-2026-10-17.json) and
[configuration/runtime review](verification/phase-integration.md).

The MCU remains on the probe-validated candidate
`c15d9b87cdec11761526cbac674eff3732b26b755251b23318a9ed315fe2a69f`; it was not
reflashed during this host update. UART remains exclusively owned by livolod.

To roll back to release 04:

```sh
sudo systemctl stop livolod
sudo cp -p /etc/livolod/config.before-phase05-20261017.json /etc/livolod/config.json
sudo /opt/livolod/bin/pip install --no-deps --force-reinstall /opt/livolod-releases/20261017-04/wheels/livolo_ec_daemon-0.1.0-py3-none-any.whl
sudo systemctl start livolod
```

Keep the current database. Restoring the configuration is required because release
04 rejects the new `output_mode` field. The older host remains compatible with the
installed MCU's unchanged raw SEND interface.

## Release 04 history

Release `20261017-04` was the reviewed token compatibility candidate.
It was restored unchanged after the non-actuating phase-firmware measurement.
The MCU now runs candidate image SHA-256
`c15d9b87cdec11761526cbac674eff3732b26b755251b23318a9ed315fe2a69f`, ROM-verified
by the firmware owner. Host phase controls remain disabled; radio and MQTT
readiness and the exact release-04 source hashes were reverified. See
[probe/handoff record](verification/phase-probe-plan.md) and
[restoration snapshot](verification/restored-after-phase-probe01-2026-10-17.json).

All 44 tests passed before deployment. After the user's physical confirmation
that both outputs were OFF, the native baseline was bits 0, counter 43, token 06.
One controlled stop/install/start preserved the database and association. Exact
installed source hashes, radio readiness, MQTT connection and the unchanged
baseline timestamp were verified. No output or pairing commands were sent by the
deployment procedure. Captured control acceptance is coordinated separately.

The first release-04 HTTP trial was **partially successful**: ON token `09`
(request `web-live-a54a63ca9ced454887b467efa871a1c8`) was unconfirmed. OFF token
`0b` (request `web-live-c336efc6c2a14d6e9595be22882b3916`) received a matching
native report at Unix timestamp 1792266728.6699536: bits 0, counter 44. Both
outputs are freshly reported OFF; this does not establish that the preceding ON
was applied or that control is reliable. Live Nordic analysis confirms all three
ON transmissions (checksum `ca`) without a matching ON report, and OFF checksum
`cb` followed by the matching report and a 1.879 ms gateway ACK. The first ON/OFF
attempts followed their heartbeats by 24.450/73.430 ms; heartbeat spacing was
10.030236 seconds. The allocation guard alone is insufficient for reliable ON.

Source: `/opt/livolod-releases/20261017-04/host`.
Wheel: `/opt/livolod-releases/20261017-04/wheels/livolo_ec_daemon-0.1.0-py3-none-any.whl`.
Wheel SHA-256: `7e23253c8c4355d489e86526c874463d705206ed2137a93392825e3a3cb358e0`.
Source archive SHA-256: `78b894f8775d9074751f30eaf14471db87429864ffaaae52c881a43ee409cc8a`.
Database backup: `/var/lib/livolod/backups/before-token04-20261017.sqlite3`.
[Live verification](verification/deployment-token04-2026-10-17.json) and
[candidate design/tests](verification/token-candidate.md).

Release 04 preserves release 03's cadence and strict confirmation, but avoids
adjacent tokens and the preceding reserved output checksum. This is a compatibility
experiment, not proof of the switch's duplicate-detection algorithm.

Rollback: stop `livolod`, install the retained release-03 wheel with
`/opt/livolod/bin/pip install --no-deps --force-reinstall`, then start `livolod`.
Keep the current database. The added metadata uses the existing schema and is
ignored by release 03; after any rollback, re-evaluate that metadata before a
later re-upgrade because older code does not maintain the new reservations.

## Release 03 history

Release `20261017-03` was the cadence-isolation experiment.
The user confirmed physical OFF restoration before deployment. The existing
database, peer association, device name and command ledger were preserved through
one controlled stop/install/start. Radio and MQTT readiness and the exact tested
source hash were verified. The restored report is bits 0, counter 39, token `04`.
No output or pairing commands were sent during deployment; controlled acceptance
remains coordinated by the GUI and firmware chats.

Captured release-03 acceptance remains **partially successful**: ON token `06`
was confirmed (bits 1, counter 40), but OFF token `07` was unconfirmed. Nordic
observed 10.046603 seconds between their preceding heartbeats, and first-command
phases of 18.814 and 34.879 ms. Both commands shared checksum `c7`; adjacent token
and checksum hypotheses remain confounded. No matching OFF report appeared in
the 119.542-second capture. Release 03 is not a reliable control fix.

Release 03 reserves the next regular heartbeat for an active set, enforcing at
least ten seconds since the previous successful heartbeat completion. Lease/status
work continues while waiting outside the RF lock. Epoch, tokens, wire bodies and
strict confirmation remain unchanged. Normal added latency can be ten seconds;
this is an experimental scheduling limitation, not the final intended UX. UI
result polling allows 20 seconds. See the [design and test record](verification/cadence-candidate.md).

Source: `/opt/livolod-releases/20261017-03/host`.
Wheel: `/opt/livolod-releases/20261017-03/wheels/livolo_ec_daemon-0.1.0-py3-none-any.whl`.
Wheel SHA-256: `e012dc28b1adba6f3fb1833398d5dde699dfd5dae231a139325fbd00d7916911`.
Source archive SHA-256: `4ca2daa2e9703699c7a880e3d24a05be2ed6e7829a11cb4080d42e9e6c0f3eb8`.
Database backup: `/var/lib/livolod/backups/before-cadence03-20261017.sqlite3`.
[Live source/readiness verification](verification/deployment-cadence03-2026-10-17.json).

To roll back to release 02, stop `livolod`, run
`sudo /opt/livolod/bin/pip install --no-deps --force-reinstall /opt/livolod-releases/20261017-02/wheels/livolo_ec_daemon-0.1.0-py3-none-any.whl`,
then start `livolod`. Keep the current database; no schema change was made.

## Release 02 history

Release `20261017-02` was a heartbeat-phase compatibility candidate.
The initial deployment details below remain as history. Release 02 preserves
the same configuration/database and unchanged process-relative heartbeat counter
epoch, token allocation, lease/session handling and report confirmation rules.
It emits a heartbeat immediately before each set transaction, serializing host
radio sends through its initial attempts. Maintenance rechecks its deadline after
acquiring that lock so an already-waiting periodic heartbeat cannot interrupt
the prelude. MCU automatic ACKs and UART lease renewal remain independent.

The prior successful trial's first commands followed heartbeats by 17–31 ms.
The failed GUI commands followed them by 8.525 and 3.604 seconds while using the
same zero timing trailer. This change reproduces the observed successful phase;
it is not yet a claim that phase is the sole cause. Controlled radio-captured
acceptance is owned by the GUI/firmware chats.

The first captured release-02 trial was **partially successful**: ON token `04`
received a matching native report (mask 1, bits 1, counter 38); the subsequent
OFF token `05` remained unconfirmed. Nordic observed the first commands 14.826 ms
and 18.968 ms after their respective heartbeats, so the intended prelude ordering
worked for both. Those heartbeats were only 1.312 seconds apart. Whether that
cadence affects switch acceptance remains unproven; release 02 must not be
described as a reliable control fix. No matching rules were relaxed.

Release 02 source: `/opt/livolod-releases/20261017-02/host`.
Wheel: `/opt/livolod-releases/20261017-02/wheels/livolo_ec_daemon-0.1.0-py3-none-any.whl`.
Wheel SHA-256: `ef19a26ae118c931a8d4f44cbe9d418db9b6fb6ae96eb594fcd1409961c56b50`.
Source archive SHA-256: `c0bd88e1a70841907ba2c25632c635a479492ebac8fa14c4551f306b5642924f`.
Database backup before upgrade:
`/var/lib/livolod/backups/before-phase02-20261017.sqlite3`.
[Live source/readiness verification](verification/deployment-phase02-2026-10-17.json).
The final 33-test suite passed after narrowing the candidate to phase-only,
including real Paho TCP, pySerial PTY, HTTP integration, prelude ordering,
unchanged counter epoch and rejection of prelude-time report confirmation.

Rollback to initial daemon code: stop `livolod`, run
`sudo /opt/livolod/bin/pip install --no-deps --force-reinstall /opt/livolod-releases/20261017-01/host`,
then start `livolod`. Preserve the current database; no schema change was made.

## Initial deployment

Installed on `10.1.1.21` (`livolodev.home.sfires.net`, system hostname `livolo`).
The user authorized persistent deployment in the GUI chat. There was no existing
Livolo service/configuration/database or MQTT broker to migrate.

| Component | Installed location / setting |
| --- | --- |
| Service | `livolod.service`, enabled, runs as `livolod`, supplementary group `dialout` |
| Python environment | `/opt/livolod`, Python 3.13.5, Paho 2.1.0, pySerial 3.5 |
| Source release | `/opt/livolod-releases/20261017-01/host`, root-owned |
| Configuration | `/etc/livolod/config.json`, root:livolod, mode 0640 |
| Database | `/var/lib/livolod/state.sqlite3`, livolod:livolod, mode 0600 |
| State directory | `/var/lib/livolod`, mode 0700 |
| Initial backup | `/var/lib/livolod/backups/initial-deployment-20261017.sqlite3`, SQLite online backup |
| Unit file | `/etc/systemd/system/livolod.service` |
| MQTT | Mosquitto 2.0.21, enabled, `127.0.0.1:1883` only |
| Broker listener config | `/etc/mosquitto/conf.d/livolod-local.conf` |
| Web UI | `127.0.0.1:8080` on Pi; access through SSH forwarding |
| Radio | `/dev/ttyAMA0`, UART v3; daemon is its exclusive owner |
| Network | Gateway `2a992b12`, tag `a7`, existing peer `46715621`, slot 0, two outputs |

Deployment archive SHA-256:
`16af2ac7ed01628cc36aceb91ec35fa159f6fb3ee96207d99c99570921958e76`.
Installed wheel SHA-256:
`a85435b07d468aec97be01f8ce91c758d7a249a7366984296edd2733166a3dbe`.

The daemon reached radio/MQTT readiness and received a native state report on
startup: bits 1 (output 1 on, output 2 off), counter 36, token `b3`. A later
explicit `refresh` using the observed `10`/`ac` queries did not elicit another
state report within its four-second window, and correctly returned `unconfirmed`.
Those queries must not be advertised as guaranteed state reads. The service
continues to receive/acknowledge native traffic; the UI distinguishes device
availability from the age of its last output-state report.

A single coordinated service restart verified clean shutdown, persistent
registry/name/state recovery, and re-establishment of radio/MQTT readiness. It
did not immediately produce a fresh output-state report; the saved state retained
its original timestamp. See the [deployment snapshot](verification/deployment-2026-10-17.json).
An API control checker in the GUI chat retains its fresh-baseline gate, so service
readiness must not be confused with completed output-control acceptance.

No firmware changes, pairing commands or output-control commands were made by
the deployment procedure. Real HTTP control acceptance is coordinated separately
by the GUI chat. The current switch association is preserved.

## Access and operations

```sh
ssh -N -L 127.0.0.1:8080:127.0.0.1:8080 sfires@10.1.1.21
```

Then open `http://127.0.0.1:8080/`. The GUI chat already manages a tunnel on this
Mac; do not start a second listener on the same port. Broker and HTTP ports are
not exposed to the LAN.

On the Pi:

```sh
sudo systemctl status livolod mosquitto
sudo journalctl -u livolod -f
sudo systemctl restart livolod
```

Restart preserves registry/names/state and installs a fresh volatile radio
session. It does not replay pending output commands. Stop the service before
running any standalone UART, capture or firmware tool.

## Rollback / disable

This was the first persistent host deployment, so rollback means disabling it:

```sh
sudo systemctl disable --now livolod
```

Graceful stop sends DISARM; the MCU lease independently expires if the serial
link is lost. Keep `/etc/livolod` and `/var/lib/livolod` to preserve device names,
state and command history. Do not erase the database as part of a routine
rollback. Mosquitto can remain running or be disabled with
`sudo systemctl disable --now mosquitto` if no other consumer uses it.

For a future code rollback, stop `livolod`, reinstall a retained source release
into `/opt/livolod` using its pip, and restart with the same configuration and
database. Check schema compatibility before reverting to a different future
version. The retained source/archive is the recovery source for this deployment;
no earlier daemon release existed.


## Sonoff MCU scheduler activation — 0.7, 2026-10-24

The verified Sonoff 0.7 application is now the active test adapter at 460800.
Only `serial_port` changed in the service configuration; gateway identity,
network tag, registry and broker settings were preserved. TI was explicitly
disarmed. The original EC network remained running throughout.

The deployed `livolod/scheduler.py` now supports the advertised 65536 Hz TI and
1000000 Hz MG21 clocks. Its SHA-256 is
`349b535d8c5cfeccbc1769dfd3cf2891dd7239712332d1bccd7c47b5332ce5b0`.
The service implementation is unchanged; the temporary ABBA timing wrapper was
removed after testing. Normal settings remain a 235.5 ms MCU pair target,
4.012 s response target, three rounds, four CCA attempts and a 14-second deadline.
Authoritative network state remains on the Pi.

A complete Nordic-verified run confirmed 21/24 refreshes, matching TI 0.7's
count, with 92/92 query sends captured and no queries after fresh confirmation.
An initial interrupted run recorded `ERR SYNTAX` and one UART RX overrun; it is
preserved separately and remains an open transport issue despite a clean repeat.
The autonomous silence/load checks both confirmed. No output commands were sent.

The first atomic configuration replacement lost its service-readable group;
restoring `root:livolod` mode 0640 recovered startup. Preserve file ownership as
well as mode when replacing configuration files. An overlapping initial test
controller also prevented rollback UART access; exclusive locking rejected it.
Subsequent experiment ownership was serialized. These setup issues and the
separate UART interruption are recorded in the verification data.

Final checks: service active/enabled, MQTT connected, switch online OFF/OFF,
TI disarmed, and no timing override. Private backups and full UART logs reside
under `/home/sfires/livolo-ec/sonoff-query-mcu-20261024`; Nordic originals reside
under `/home/sfires/livolo-sniffer/sonoff-query-mcu-20261024` on 10.1.1.120.
See [deployment verification](verification/deployment-sonoff-query-mcu-20261024.json)
and [capture comparison](../analysis/development/sonoff-query-mcu-comparison-20261024.json).

## Original EC shutdown comparison — 2026-10-25

With the original EC shut down by the user, the unchanged Sonoff 0.7/460800
24-request comparison confirmed 19/24 refreshes (normal 10/12, extra wait 9/12),
versus 21/24 with EC running. Nordic captured no original-network frames during
the query window. All five timeouts retained the `fa/10` without `fa/ac` or state
pattern, despite unchanged MCU timing and no new UART errors in this run.

The temporary test entry point was removed and the normal daemon restarted.
Configuration bytes, installed host sources, registry and MQTT settings were
preserved. Service is active/enabled, MQTT connected, Sonoff owns MCU heartbeats
and queries, TI is disarmed, and switch 46715621 is online OFF/OFF. Firmware and
retry limits were not changed; no output commands were sent.

Separately, seven additional UART overruns accumulated between the October 24
final snapshot and this trial. Six overnight reconnects (four `ERR SYNTAX`, two
`ERR SESSION`) recovered automatically. Their cause remains unresolved; the
clean short trial does not establish long-term UART stability.

Private backup/results/UART trace: `/home/sfires/livolo-ec/sonoff-ec-off-20261025`.
Nordic originals: `/home/sfires/livolo-sniffer/sonoff-ec-off-20261025` on 10.1.1.120.
See [restoration checks](verification/deployment-sonoff-ec-off-20261025.json),
[comparison](../analysis/development/sonoff-ec-off-comparison-20261025.json) and
[transport observations](../analysis/development/sonoff-between-trials-transport-20261025.json).


### Sonoff UART receive fix (0.10, 2026-10-25)

Application-only Gecko deployment preserved the 115200 bootloader and retained
460800 application UART. The daemon and firmware both report 0.10. RX now uses
an 8 KiB LDMA ring with explicit loss detection and command-line resynchronization.
Query timing, limits and normal paired mode are unchanged; authoritative state
stays in the Pi database.

The completed 6000-request concurrent UART load passed without retries or errors.
Across 13217 traced requests there were no lost replies, error responses or UART
loss-counter increments. Nordic verified the unchanged query cadence; RF timeouts
remain, including the final normal-service refresh. All harness interruptions and
one uncaptured preparation frame are retained in the [protocol report](../analysis/protocol.md#sonoff-uart-dma-receive-fix-release-010--2026-10-25).
The [deployment record](verification/deployment-sonoff-uart-dma-20261025.json)
confirms the restored normal service, preserved configuration and registry.

### Sonoff automatic ACK phase (0.11, 2026-10-25)

The Sonoff and daemon now report **0.11** at 460800 baud. The application-only
update preserves the 115200 Gecko bootloader. Automatic replies carry live
heartbeat phase while keeping immediate dispatch, with zero-trailer fallback
when no live reference exists. Query scheduling, retry limits and authoritative
Pi state are unchanged.

The 72-query comparison confirmed 60 requests; the separate final ordinary
paired refresh also confirmed, reporting OFF/OFF. The normal paired-mode service
is active/enabled and MQTT-connected, with configuration, registry and MQTT
settings preserved and no temporary override. UART/loss counters remain zero.
The feature is verified on air but did not eliminate RF timeouts; see the
[protocol report](../analysis/protocol.md#sonoff-phase-stamped-automatic-acks-release-011--2026-10-25)
and [restoration record](verification/deployment-sonoff-ack-phase-20261025.json).

## Sonoff output grid 0.12 — 2026-11-01

Sonoff ZBDongle-E at 10.1.1.21 now runs firmware and daemon 0.12 at 460800 baud.
The original Gecko 2.4.2 bootloader remains at 115200. Configuration bytes were
preserved; both peers remain registered, pairing disabled, and all outputs were
restored OFF. The normal enabled service has no temporary override.

Output control uses `output_grid=transaction_200ms_v1` automatically, with the
Pi retaining registry/state/token ownership. The same-firmware ABBA test yielded
24/24 aligned confirmations versus 12/24 host-timed confirmations; no UART errors
or overruns were recorded. Six attempts/four seconds remain the output bounds.
Read-only query timing and bounds are unchanged; the final one-gang query timed
out and the two-gang query succeeded. See
[comparison and limitations](../analysis/development/output-grid-20261101.md) and
[deployment record](verification/deployment-sonoff-output-grid-20261101.json).


## ZB-GW04 v1.2 0.12 Docker handoff — 2026-11-01

The reconnected ZB-GW04 is now the active radio at 460800, selected by
`LIVOLO_SERIAL_DEVICE=/dev/serial/by-id/usb-1a86_USB_Serial-if00-port0` in
`/opt/livolod-docker/.env`. Sonoff is disarmed and transmitted no packets during
the test. The Docker image, config JSON, Compose YAML, daemon environment and
authoritative database location are unchanged; the native service stays disabled.
Only the ZB-GW04 application was flashed; Gecko 1.9.2 remains at 115200.

Qualification passed 6,000 exact UART requests, a 601-second single-session
observation and 24/24 fresh control requests after the user was asked to leave
controls untouched. Earlier blocks overlapped possible user activity and are
not clean reliability comparisons. Both switches remained online, with MQTT
connected and pairing disabled. State-query policy remains unchanged/tier 3.
See [evidence, limits and maintenance workflow](../analysis/development/zbgw04-012-20261101.md).


## TI 0.12 handoff — 2026-11-01

The Docker daemon now uses the cod.m **CC2652P** at `/dev/ttyAMA0`, 460800 baud,
mapped to `/dev/livolo`. Sonoff and ZB-GW04 remain RF-disarmed. The signed TI
application was upgraded from 0.7 to 0.12 through UART and confirmed by the
matching Docker daemon; bootloader 0.2 was preserved and no GPIO entry was used.
The Docker image/configuration/database remain in place; only the serial device
mapping changed. Native `livolod.service` remains stopped/disabled.

Qualification: 12 dry protocol checks, scheduler dry checks, 6,000 exact UART
requests, and 600.532 seconds of stable observation. Initial controls confirmed
20/24, then 24/24 after settling; missing and old-token replies in the initial
block remain a follow-up item. Retry timing and limits were unchanged.
[Detailed TI results and capture](../analysis/development/ti-012-20261101.md).
Private pre-test backups: `/home/sfires/livolo-ec/ti-012-20261101/private-backup`.
Use `sudo livolodctl stop/start` for maintenance; do not use the updater's native
`--service livolod` handoff with Docker. Start the matching Docker release within
the signed updater's 60-second trial window so it restores and confirms firmware.
