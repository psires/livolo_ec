# Physical-button reporting — 2026-10-17

The user started with output 1 off / output 2 on, pressed button 1 and then
button 2 as instructed, and confirmed the final physical state was **1 on / 2
off**. EC remained unplugged. Left/right or top/bottom naming was not supplied.

The local controller used `--monitor-only`: it sent heartbeats and the MCU
acknowledged supported packets; it sent no admission replies, queries or output
commands. Nordic independently captured zero `8c/02` commands for this switch.

Frame references are from `physical-buttons-local-01-nrf52840.pcapng`:

| Frames | Meaning | State / counter / token | ACK |
| --- | --- | --- | --- |
| 310 | First button, mask `01`, broadcast destination | `03 / 1d / b3` | 311, 1.871 ms |
| 326, 340 | Full-state refresh, mask `03`, now directly addressed to Pi | `03 / 1e / b3` | 327 and 341, 1.868–1.870 ms |
| 432, 436 | Second button, mask `02`, directly addressed to Pi | `01 / 1f / b3` | 437, 1.870 ms, for frame 436 |

TI missed frame 432 over the air; Nordic saw it. The switch repeated that report
about 200 ms later, and TI received/ACKed the repeat. All four state reports seen
by TI were acknowledged and independently observed by Nordic. MCU logs show no
RX/UART logging drops; that does not imply perfect RF reception. ACK times here
are Nordic SFD-to-SFD intervals, not end-of-frame turnaround.

After the first physical report and ACK, the switch resumed directly addressed
traffic and emitted `a0` capability-like reports (frames 333/336), a full-state
refresh and properties `24→2`, `25→4`, `26→0` (347/359/362). It then exchanged
direct keepalives with the Pi. No host queries preceded this refresh. This
suggests a recovery/refresh sequence, but does not isolate its trigger or prove
complete route-maintenance semantics.

**Token semantics:** every manual state report retained `b3`, the token of the
last local output command from the previous experiment. Thus token zero/nonzero
cannot classify physical versus commanded changes, and token-only deduplication
would lose real manual changes. The counter advanced from the previous `1c`
to `1d`, then `1e` for refresh and `1f` for the second button. The repeated full
state and second-button packets retained their respective counters.

The trial was stopped after the confirmed presses and settled traffic. Its
cleanup saved evidence and disarmed transmission. The controller printed a
KeyboardInterrupt traceback after running cleanup; subsequent versions handle
an intentional interrupt explicitly. Nordic finalized its capture and slept.
The final state remains **1 on / 2 off**; no restore command was sent.

Evidence: the Nordic and UART-derived TI pcaps in `captured/development/`, the
decoded directory `physical-buttons-local-01/`, and
`physical-buttons-local-01-verification.json`, trial JSON and UART log here.
All 28 existing tests passed after adding monitor-only mode. No firmware changes
were needed for physical-button ACKs.
