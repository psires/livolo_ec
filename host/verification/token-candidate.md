# Local token-allocation candidate — 2026-10-17

Status: deployed as release 20261017-04 after review and the user's physical OFF
confirmation. Exact source and radio/MQTT readiness were verified; captured control
acceptance remains partial. No output commands were sent while preparing or deploying
this candidate. See [deployment verification](deployment-token04-2026-10-17.json).

The GUI-owned first trial reported ON token 09 unconfirmed, followed by OFF token
0b confirmed with bits 0, counter 44 at Unix timestamp 1792266728.6699536. This
demonstrates OFF acceptance in this trial, but not a successful ON/OFF cycle or
reliable control. Live Nordic analysis confirms ON checksum ca and OFF checksum
cb, three ON transmissions without a matching ON report, and the OFF report with
a 1.879 ms gateway ACK. First attempts followed heartbeats by 24.450/73.430 ms,
with 10.030236 seconds between those heartbeats. The guard is insufficient for
reliable ON. The exact duplicate-detection hypotheses remain unproven. No further
controls or code changes were made in response.

Further capture comparison contradicts checksum-only duplicate suppression across
opcodes: successful trial-03 ON token 06 and its immediately preceding heartbeat
(counter 50, parameter 1) both had checksum c7. Failed trial-04 ON token 09 had
checksum ca, while its preceding heartbeat had c7. This does not rule out a
command-specific duplicate key, but no such key is established.

Retry timing remains another untested possibility. Earlier successful helper
transactions reported after roughly 22, 182, 502 and 501 ms from their first
attempts; the latter two used second attempts around 288 ms. Daemon trial-02/03
ON reports arrived around 195 ms, with retry spacing around 215–219 ms. Trial-04
OFF reported 434 ms after its first attempt and 214 ms after its second. These
observations do not establish that changing retry spacing would fix ON failures.
No retry timing or confirmation rules were changed on this evidence.

## Evidence and limits

Release 03 confirmed ON token 06, but not OFF token 07, despite normal heartbeat
spacing. Both commands have checksum c7. Earlier pairs 02/03 and 04/05 likewise
share c3 and c5 respectively. The successful and unsuccessful tokens in each pair
also differ only in bit zero. Duplicate detection based on checksum or a partial
token is a hypothesis; the captures do not isolate the mechanism.

The previously successful `analysis/development/local-set-02.json` used exactly
mask 1, value 0, token fd. The current builder reproduces that complete frame
byte for byte with the same sequence and token. Mask/value encoding therefore
does not explain the regression. Proven fd→6e→b3 transactions differ in checksum
and upper token bits; no simple odd/even token restriction fits that evidence.

## Candidate behavior

For each device, retain the existing nonzero token range and 60-second reuse
quarantine. Exclude tokens equal or adjacent (modulo 256) to the latest reservation
or last reported token, and exclude those with identical upper seven bits.
Also exclude a checksum collision with the preceding reserved output command.
For a fixed peer/header, comparing `(mask + value + token) mod 256` is equivalent
to comparing the 8c02 checksum; all other summed bytes are invariant. A suffix
change can make this conservative comparison unnecessarily exclude a candidate,
but does not weaken freshness or confirmation.

Select the least recently used eligible token. Atomically persist the token and
sum in the existing `tokens` and `meta` tables before SEND, including uncertain
transactions. No schema migration is required. On first use of an older database,
recover the prior sum from its latest token-bearing set result, whether confirmed
or unconfirmed; support explicit mask/value and whole-device ON/OFF requests.
The latest token reservation remains excluded even if an older daemon crashed
before recording a result. Its missing checksum cannot be recovered from the
old token table alone.

Cadence, heartbeat epoch, zero trailer, mask/value bytes, retry token, UART
recovery, quarantine, command-ID deduplication and strict native confirmation are
unchanged. This candidate deliberately avoids both observed patterns; success
would not distinguish which, if either, caused the failure.

## Validation

Regression coverage reproduces the trial-03 checksum collision, checks new ON/OFF
checksums and token spacing, restart before result persistence, legacy unconfirmed
ledger bootstrap, independent checksum rejection with whole-device state aliases,
device isolation, quarantine and exhaustion. All 44 tests passed in 29.855 seconds,
including real MQTT/TCP, serial PTY and HTTP integrations:

```sh
PYTHONPATH=host /tmp/livolod-venv/bin/python -m unittest discover -s host/tests -v
```

Keep output control with the GUI chat and capture ownership with
the firmware chat; the daemon remains the only UART owner.
