# Sonoff guided setup engineering contract

Prepared 2026-11-08 for the dedicated Intel testbed. This is an implementation
and qualification plan, not a successful guided-install record or public
firmware approval. The Linux specialist is the sole live operator. The active
HA gateway and other radios are outside this trial.

## Selected hardware and images

The selected unit is a genuine Sonoff ZBDongle-E / Zigbee 3.0 USB Dongle Plus V2,
with EFR32MG21A020F768IM32 and CP210x `10c4:ea60`. No separate PCB revision
attestation is recorded. Its exact USB serial, topology, device node and current
attachment belong in the protected operator policy, not this public document.
Do not extend this profile to Sonoff batches with other USB bridges.

The Linux specialist reports five CRC-validated passive ESN1 status records after
the move: sniffer **0.12**, **921600 baud**, Sonoff board marker, with no UART
writes. This report is identification evidence; the current loader has not yet
been freshly observed. Historical records identify Gecko **2.4.2** at **115200**,
with menu spelling `v2.04.02`.

| Purpose | Version | SHA-256 |
| --- | --- | --- |
| Selected gateway application | 0.14 | `87de29744fd7b991e0da8cdfda0fd160e12bd749764d4bca8c1337683804a7e6` |
| Original project sniffer recovery | 0.12 | `c9d5bb865ab6734c9c739a36bcc4a71139ab7fcc01090e5a55371f1378e1896d` |

The candidate is 60,572 bytes, built from
`0fd515ca156823e513d2d86f0507b8da9b8558e8`. Its application occupies
`0x4000..0x12c50` (exclusive end), within the permitted `0x4000..0x30000` range.
Both retained build passes selected `BOARD=sonoff-zbdongle-e` and
`UART_BAUD=460800`. Offline validation checks the exact gateway/board/version
markers, vectors, GBL CRC and application-only tags. These checks do not establish
the running baud, successful installation, full-flash equality or RF behavior.

Preserve the artifact-specific notices and provenance listed in the candidate's
review-staging inventory, including the MG21 terms and full vendor agreement.
The catalog entry must remain `qualified: false`; the ordinary updater must
continue to reject it. The Sonoff needs its own reviewed engineering admission
record. The ZB-GW04 `b437e13` trial approval cannot authorize it.

## Loader policy and entry

The evidence for this unit accepting unsigned, unencrypted applications is
historical: successful gateway installation on 2026-10-24, then sniffer
installation and operation on 2026-11-01. This supports a bounded exact-unit
engineering policy reviewed together with a fresh loader observation. It is not
a query of security configuration, proof of unchanged loader bytes, or approval
for every Sonoff with the same version string. Reject contradictory security
indications, unexpected menus or versions, and changed target identity.

Retain explicit board confirmation and bind the Sonoff serial as well as the
existing topology/interface, full kernfs attachment generation and actual tty
device-number checks. Serial numbers identify the selected transport; they do
not authenticate hardware. Use a fixed Sonoff profile and explicit accepted
menu spellings. Do not accept arbitrary version suffixes through loose parsing.

The manufacturer documents manual entry by holding BOOT, pressing RESET, then
releasing both together. User confirmation permits inspection; only the complete
loader menu establishes the observed mode. Do not invent a three-second hold or
LED test. See [Sonoff's firmware instructions](https://sonoff.tech/en-es/blogs/news/how-to-flash-firmware-on-sonoff-zbdongle-e-step-by-step-tutorial).

For the already identified project sniffer, its documented software entry is
`\nBOOTLOADER\n` at 921600. This may be a separately reviewed operator action;
do not advertise an automatic UI transition before it is implemented and tested.
Historical CP210x RTS/DTR entry is a separate mechanism and must not be copied
from the ZB-GW04 profile. Never send gateway numeric request IDs or scan baud
rates with writes while the application/loader mode is unknown.

## First setup, verification and recovery

Start in explicit bootstrap mode, with no accepted network and no gateway
baseline. ESN1 sniffer status is not gateway VERSION/CAPS evidence. Preparation
observes the loader at 115200; installation requires the reviewed exact image,
durable permission and retained ownership guards before the single helper run.

After installation, separately correlate VERSION, CAPS and STATUS at 460800.
Require `board=sonoff-zbdongle-e`, `fw_version=0.14`, `proto=3`, the expected
baud, and `armed=0`, `ack=0`. The expected gateway profile includes volatile
peers, timed scheduling, MCU query/output transactions and phase-stamped ACKs.
MG21 reports `update=none`: it has no TI signed updater or automatic last-good
rollback. Preserve the existing daemon compatibility checks before normal use.

Only a separate explicit new-network initialization and handoff may create the
accepted network and start normal service. Firmware verification alone must
not report network readiness or create a provisional HA gateway entry.

Before installation, cancellation without a gateway baseline may close setup
truthfully. It must not claim that the sniffer was restored or send gateway
verification commands to it. After a possible flash, uncertainty remains a
recovery condition. Returning to the sniffer requires a separate explicit
operator recovery procedure and CRC-valid ESN1 identity/status verification at
921600. Do not weaken the gateway artifact validator to admit the sniffer into
the normal catalog. The known sniffer image is a project recovery artifact;
there is no exact factory application/configuration backup.

## Required offline fixtures and physical gates

Unix owns implementation fixtures; HA owns flow fixtures; Release Engineering
independently checks the frozen package. Required cases are:

- Correct CP210x serial/profile succeeds; another serial, missing serial,
  ambiguous alias, changed attachment or tty descriptor rejects before I/O.
  ZB-GW04 identity, policy, menu, artifact and postflight cannot authorize Sonoff,
  and Sonoff records cannot authorize ZB-GW04.
- Mount-local timestamps do not change attachment identity; reboot, USB
  reattachment and high-bit kernfs generation changes do. Unsupported native
  platforms and copied sysfs views fail closed.
- A complete expected Gecko 2.4.2 menu is required. Truncated/oversized menus,
  other loader families, upload formats, versions or security contradictions
  reject. Menu text alone never supplies security policy.
- With a sniffer or unknown mode and no gateway baseline, GET performs no UART
  I/O; startup does not run normal gateway probing; Prepare has no hidden reset,
  upload or run command; cancel cannot fabricate restored gateway state.
- Wrong board/version/hash, changed candidate bytes and the old ZB-GW04 trial
  approval reject. Ordinary loaders reject the unqualified Sonoff catalog.
- Install keeps durable intent/permission, exact target and artifact binding,
  inherited guards and bounded helper cleanup. Lost responses, parent shutdown,
  restart or duplicate requests cannot initiate a second transfer or release
  ownership before all workers are gone.
- Postflight requires three independently correlated replies with the Sonoff
  identity, correct version/baud and disarmed status. Wrong-board, wrong-version,
  partial, stale or armed replies cannot report application verification.
- Initialize/handoff and real normal readiness remain separate. No provisional
  HA entry, network identity or automatic RF success claim is allowed.

Linux must then verify the actual installed package and target metadata,
freshly observe the selected loader, exercise the HA-driven flow, check
independent application postflight and explicit handoff, and retain recovery
evidence. A successful manual transfer alone does not pass the HA flow gate.
Do not generalize the result to public updater eligibility, factory conversion,
power-loss recovery or long-term RF reliability without their own evidence.

Historical records: [Sonoff migration](../analysis/development/sonoff-migration-20261024.md),
[sniffer installation](../analysis/development/sonoff-sniffer-20261101.md),
[sniffer wire contract](../firmware/sniffer-mg21/README.md), and
[offline artifact checks](verification/sonoff-guided-offline-20261108.json).
