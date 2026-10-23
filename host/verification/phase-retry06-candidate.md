# Six-attempt phase candidate

Status: deployed as release 20261017-06 after 69 passing tests and joint review.
The exact source hash, phase reference and radio/MQTT readiness were verified;
configuration, database and firmware were preserved. No output commands were sent
by deployment. See [verification](deployment-phase06-2026-10-17.json); captured
output acceptance remains separate.

The captured release-06 trial subsequently produced matching ON and OFF reports.
The user confirmed a physical button press between the requests, which explains
an intermediate OFF report before the OFF command. ON confirmed after two sends;
this does not demonstrate that six attempts were needed. The final native report
and live API state are both OFF/counter 50/token `0c`. See
[web acceptance](WEB_ACCEPTANCE.md) and the independent
[air analysis](../../analysis/development/gui-toggle-restore-06.json). This is
qualified command/report verification, not an uninterrupted transition or
long-term reliability test.

The complete trial-05 capture validated all six actual 41-byte command frames:
their changing phase trailers differed from measured RF intervals by 132–593
microseconds. Last-attempt UART raw bytes matched Nordic frames 267 and 291.
ON token 08 had no immediate or delayed native report. OFF token 0d confirmed
bits 0, counter 45, 663 ms after the first attempt and about 193 ms after the
third. The original EC gateway has a distinct identity/tag; no identity collision
was found. See `analysis/development/gui-toggle-restore-05.json`.

The only runtime change from the exact release-05 archive is
`PHASE_SET_ATTEMPTS = 3` → `6`. Legacy mode remains at three attempts. Keep the
same four-second absolute deadline, roughly 200 ms post-SEND waits, token/body,
fresh MAC sequence and MCU phase stamping, normal heartbeat cadence, strict native
confirmation and immediate stop after confirmation. No firmware or configuration
change is proposed. The nominal transmit window expands from roughly 0.43 seconds
to roughly 1.1 seconds including UART time; it does not extend the overall deadline.

This is a coverage experiment. The stock capture's successful OFF had a third
direct send at 0.600 seconds and its first report at 0.611 seconds, outside the
current local three-attempt transmit span. That motivates testing a longer span;
it does not prove extra attempts are necessary or sufficient for reliable control.

Eight targeted phase-runtime tests pass, including fifth-attempt confirmation with
one reserved token and fresh sequence/trailer, early stop, six-attempt cap across
a heartbeat boundary, and one deadline including UART latency. Existing reference,
capability, disabled-mode and unknown-outcome/reconnect checks also pass. All 69
tests passed in 36.014 seconds, including MQTT, serial PTY and HTTP integrations.
Runtime source was compared directly against the release-05
archive to verify that the attempt constant is the sole implementation change.

Candidate `service.py` SHA-256:
`45c386a2d19c234877e248d55963cddf72ea029f3ac5f73ecf9d7e59c57a7e27`.
Verify this value against the final build record before any deployment.
