# ZB-GW04 physical provider

`host/livolod/zbgw04_provider.py` supplies Linux identity, cooperative leases and
explicit serial inspections to the guided installer. It does not register an
API, launch a flasher or enable a deployment by itself. Its tests perform no
physical radio operations. The existing
[manual engineering trial](verification/zbgw04-manual-engineering-20261108.json)
is evidence for the selected board and artifacts, not qualification of this new
provider or the HA update workflow.

## Sonoff engineering profile

The same ownership implementation also serves the separately selected
`sonoff-zbdongle-e` profile in `gecko_profiles.py`. The historical
`ZBGW04Provider` class name remains for compatibility; its target selects an
immutable board contract, rather than inheriting ZB-GW04 identity or loader
approval. The ZB-GW04 details below remain the default profile.

Sonoff requires CP210x `10c4:ea60`, an exact nonempty USB serial, and separately
recorded board confirmation. The protected policy uses **`target.serial`**;
`usb_serial` is not an alias. Legacy ZB policies omit the serial key entirely.
Both the prepared attachment binding and the stable normal-service selector
include the Sonoff serial. Reattaching the same unit may renew its generation
after normal service closes, but another serial at the same USB port is rejected,
including after daemon restart. A populated untagged legacy setup journal is
treated as ZB-GW04 and cannot be silently relabeled for Sonoff.

For loader-only inspection after manual or software entry, Sonoff sends **newline only** at
115200 baud and requires the complete Gecko menu. Only wire versions `2.04.02`
and `2.4.2` normalize to the reviewed `2.4.2` policy. Partial menus, other
versions/families, contradictory security text and timeouts fail closed without
numeric probes, automatic modem-line reset or fallback. Inactive DTR/RTS at open
is retained. The pinned upload helper runs only after this verification and the
existing durable flash acknowledgment.

The separate Sonoff artifact record remains `qualified:false` and
`engineering_only:true`. Its evidence identifier is reserved for an exact-unit
loader review, not for the offline artifact inspection. Deployment requires that
review and a private policy bound to the current attachment; adding a profile or
seeing a menu alone does not establish signing/encryption policy. See
[the Sonoff qualification procedure](SONOFF_GUIDED_QUALIFICATION.md).

Targets may advertise `entry_methods: ["manual", "sonoff_sniffer_0_12"]` when no
gateway baseline is captured. This advertises a conditional supported method,
not an already observed application. An explicitly confirmed Prepare request
can add `entry_method: "sonoff_sniffer_0_12"` and `entry_confirmed: true`.
Omitting both preserves the original loader-only request and manual UI behavior.
No new endpoint or generic reset strategy is introduced.

Remote Prepare first discards buffered RX input at fixed 921600 baud and requires
two fresh CRC-valid, strictly matching sniffer 0.12 status records with progressing
sequence and timestamps. At least one qualifying new status must arrive half a
second after observation begins; an immediate buffered burst cannot authorize
entry. Observation is bounded to three seconds and 300,000 bytes. Wrong or absent
identity causes zero serial writes. Once the parent commits `bootloader_entry`,
the child writes exactly `\nBOOTLOADER\n` once, retains the same descriptor and
lease, allows 1.1 seconds for the firmware's bounded drain/reset, switches to
115200, clears old RX bytes and verifies the full loader menu. Total provider
time is bounded to eight seconds. There is no modem-line reset, automatic fallback
or implicit firmware installation.

The protected loader policy must honestly identify its reviewed historical
exact-unit security expectation; it is not a claim of fresh security-bit readback.
The fresh matching target/menu observation is obtained during Prepare, before
issuing the ordinary plan. Review and Install retain their separate confirmation.

After possible entry, interruption leaves
`recovery: manual_bootloader_inspection_required`, no plan and no automatic replay.
Once cleanup is proven and no flash recovery exists,
`info.manual_reinspection_supported` permits an explicit loader-only Prepare.
That action first checks the current loader without requiring another reset;
manual BOOT/RESET remains a separately chosen fallback. A successful inspection
atomically records reconciliation and a new plan while preserving the original
request result. A failed reinspection leaves the original uncertainty intact.

First use starts explicitly in bootstrap mode without opening normal gateway
UART, creating a network or inventing a gateway baseline for the sniffer.
Cancellation without a baseline performs no serial work and reports
`setup_cancelled`, `cancelled_unconfigured:true`, `network_ready:false` and
`setup_required:true`. It does not restore sniffer firmware or leave bootloader
mode. Cancellation revokes prepared consent by rotating the setup session and
clearing inspection deadlines; later installation requires a fresh inspection.
Historical operation lookup remains idempotent.

Firmware postflight must independently verify the selected Sonoff board/version,
protocol 3, 460800 baud, `armed=0` and `ack=0`. Network initialization and normal
handoff are separate explicit operations. First handoff requires
`output_mode: disabled` before creating normal state. Normal Service then arms
reception and ACKs using its existing protocol behavior; **output controls remain
disabled, but normal network readiness does not mean a disarmed radio**.

## Supported ownership contract

The user explicitly dedicates the selected radio to Livolo. The controller must
stop its own radio task, retain the physical lease, and reject detected conflicts
or busy devices. It must not automatically stop unrelated integrations. Normal
HA protection and app privileges remain unchanged. A host-wide process scan is
not required, and a container-local scan must not be described as one.

`DedicatedAccess` records the current attachment, physical guard name, dedicated
use confirmation, actual Livolo task quiescence and detected conflicts. The
trusted controller supplies this observation through `access_check(target)`;
neither an HTTP boolean nor a callback returning `True` satisfies the provider.
Consent is an operating assumption, not proof of absence of other processes.
Do not discard a known conflict because the user confirmed dedicated use.

Every cooperating Livolo owner, including the normal daemon, must use the same
protected host-shared guard directory and canonical sysfs view. Separate app
mounts with different lock inodes do not coordinate. The guard key is the full
USB topology and interface, independent of configuration/database paths, tty
names, USB aliases and attachment generations. The directory must already exist,
be owned by the service UID or root and not be group/world writable. Guard files
are service-UID-owned, mode 0600, regular, single-link files. Symlinks, hardlinks,
guard replacement and directory replacement are rejected.

`PhysicalLease.acquire(target, directory)` obtains the guard. `adopt(fd, target,
directory)` duplicates an already held descriptor for handoff, refusing an
unlocked descriptor. Pass the checked descriptor explicitly through every worker
and helper. `close()` closes only this owner's references: it never unlocks or
unlinks the guard. Surviving descendants retain exclusion after a parent exits.
The parent must supervise those descendants and retain/reconcile ownership on
timeouts. Closing a handle is not proof that a helper exited. Normal service
handoff must inherit or retain ownership without a release/reacquire gap.

Normal-service teardown drains its session tasks and closes UART before releasing
the deployment firmware lock. That cleanup has its own supervised task: repeated
cancellation of the normal owner cannot skip it, release the lock early, or report
setup quiescence while old consumers remain active. A setup timeout leaves pending
cleanup fenced rather than allowing inspection to compete with it.
If UART closure fails, the service retains its firmware guard and propagates the
cleanup failure; setup does not claim quiescence or permit another owner. Recovery
requires resolving that owner, rather than clearing its lock file.

Unexpected setup exceptions retain their durable failure result and emit a bounded
server diagnostic with operation, stage, request ID, exception class and project
function/line locations. Exception messages, source text, absolute paths and local
variables are omitted because they can contain credentials. A generic failure at
`accepted` is not proof that no inspection reached UART; inspect ownership and
receipts before deciding on recovery, and never automatically replay the request.

Each inspection opens serial with inactive DTR/RTS, pyserial exclusive mode and
no flow control. It checks the opened character-device number before requesting
kernel `TIOCEXCL`. Busy/open/exclusion failures stop inspection. The descriptor
closes after each bounded action. `TIOCEXCL` does not evict prior opens or prevent
privileged bypass; the fixed flasher's later pathname reopen is not a continuous
kernel-exclusive session. The held cooperative lease and dedicated-use contract
cover that interval. Noncooperating external access is an unsupported concurrency
case, not something this provider claims to exclude globally. No new PTY transport
or generic Sonoff reset procedure is introduced.

## Identity and explicit inspection

`LinuxUSB.inventory()` reads sysfs and node metadata without opening serial.
It records canonical USB topology, interface, current character-device number
and a versioned attachment token derived from host boot ID, USB bus/device
numbers and the **complete 64-bit sysfs kernfs node ID**. The physical provider
requires native Linux arm64 or amd64, a 64-bit ABI and real sysfs directories;
unsupported or narrow inode environments fail before serial access. Two metadata
samples must agree. This is a generation fence, not cryptographic authentication
of a serial-less bridge. The reviewed profile
is CH340 `1a86:7523`, with no USB serial. This identifies a transport only.

Sysfs inode timestamps can differ between container mounts of the same device,
so neither timestamps nor mount-local `st_dev` enter the token. On the supported
64-bit kernels, `st_ino` exposes the complete kernfs allocation ID, including its
high-bit wrap generation. USB addresses can wrap and be reused; a new kernfs node
still changes the token even when its USB address and low inode bits are reused.
The boot ID separates host restarts. This relies on the kernel allocation contract,
not an adversarial-root or hardware-authentication guarantee; an entire 64-bit ID
cycle within the same boot is outside the supported lifetime. See the upstream
Linux 6.18 [allocation code](https://github.com/torvalds/linux/blob/v6.18/fs/kernfs/dir.c),
[inode ID mapping](https://github.com/torvalds/linux/blob/v6.18/include/linux/kernfs.h)
and [timestamp initialization](https://github.com/torvalds/linux/blob/v6.18/fs/kernfs/inode.c).

The `linux-usb-kernfs64-v1` hash scheme deliberately invalidates older policies
and pinned operations. There is no old-token fallback or automatic policy rewrite.
After independent metadata validation, begin a new attempt with a newly reviewed
exact-unit policy; do not migrate an active or uncertain firmware operation.

`select_target(port, board_evidence, inventory)` requires an exact canonical
inventory endpoint, a unique physical mapping and recorded confirmation of
`zb-gw04-1v2`. Generic by-id names are not selected implicitly. The controller
must bind its configured physical selector to the frozen target, persist that
target in the plan and compare it again during install. Missing, ambiguous or
changed metadata blocks the operation.

The concrete provider interface is:

```python
provider = ZBGW04Provider(
    target,
    lease,
    board_evidence=BOARD_EVIDENCE,
    access_check=controller_access_observation,
    loader_policy=reviewed_attachment_policy,
)
```

`observe()`, `probe_loader(port, baud, timeout=3)` and
`verify_application(port, baud, timeout=3)` implement the callbacks expected by
`preserved_gecko`. `lease_fd` exposes the checked descriptor. The provider also
supports a context manager and `close()`. Inventory, serial factory, monotonic
clock, descriptor stat, menu parser and exclusive-claim function are injectable
for offline tests; they are never client-selectable API fields.

Only an explicit Prepare/Inspect action may call the loader probe. GET handlers
serve stored/static facts; they must not call serial methods or renew inspection
by polling. The probe uses 115200 baud, sends only `\n3`, bounds input to 4096
bytes and requires the complete menu parsed by pinned
`universal-silabs-flasher` 0.0.31. It accepts only the reviewed Gecko 1.9.2
profile. It never selects upload, runs an app, resets hardware or retries a
partial write.

The menu cannot establish signing/encryption requirements. A separate trusted
`LoaderPolicy` must name its evidence, match the exact attachment and identify
the reviewed unsigned/unencrypted application-upload policy. Unknown policy,
secure requirements, stale attachment or contradictory observed security text
blocks inspection. Do not synthesize that policy solely from a version string
or USB bridge match. Physical instructions are the verified BOOT/RESET procedure
for the selected board; factory-stock conversion remains untested.

Postflight uses 460800 baud and three separate request IDs for VERSION, CAPS and
STATUS, with one shared timeout budget of at most three seconds. It rejects
wrong board/protocol/baud, inconsistent versions, active RF or ACK, duplicate
fields and oversized/malformed replies. The adapter independently compares the
returned version with the selected artifact. Loader-only and postflight inspections
send no NETWORK, ARM, pairing, switch-control or BOOTLOADER commands; the separate
explicitly consented remote-Prepare entry phase is described above. Metadata/lease/access checks
surround the I/O. Serial reads and writes use the remaining action budget and
hard byte limits; the outer worker supervisor still bounds kernel/driver or
filesystem stalls that Python cannot preempt.

## Cancel before installation

`resume_intact_application(expected_version, *, mode, timeout=8, before_run=None)`
supports explicit cancellation while the original application remains intact.
The controller must reject cancellation after any possible installation attempt
or recovery requirement, bind the saved version to the same attachment, and use
a fresh trusted mode observation. Mode is not an arbitrary HA request field.
Unknown or stale mode requires explicit inspection/operator guidance; never try
ordinary numeric application requests against an unknown Gecko menu, where
request-ID digits could select loader actions.

In known application mode the provider only verifies the saved version with
separate disarmed application replies. In known bootloader mode it checks the
exact policy and complete menu, invokes the mandatory durable `before_run()`
acknowledgement, rechecks identity and sends exactly one `2` on that same serial
descriptor. It allows a bounded half-second startup grace, then verifies the
exact baseline VERSION/CAPS/STATUS. All stages share an eight-second maximum
budget. A partial write, timeout or wrong version is uncertain/failure, never a
retry or automatic clearing of recovery state. This returns application evidence,
not network readiness; the controller still owns normal-service handoff.

## Network setup and candidate acceptance

For a new network, an explicit, idempotent Initialize action may propose a
securely random four-byte wire-order gateway identity excluding `00000000` and
`ffffffff`, and a tag from `01` through `fe`. No IEEE MAC-bit rule is established
for this proprietary identity. Persist the accepted proposal, rather than
generating another identity after a lost response. Random selection reduces
collision probability; it does not prove absence of neighboring networks.
Use heartbeat parameter 1, empty peers, closed pairing and disabled output
control as initial defaults. Existing-network handoff preserves its identity and
registry. Firmware verification must continue to report `network_ready: false`;
only the normal daemon's matching identity and readiness can complete handoff.

Before claiming the real HA path is qualified, retain evidence from the exact
candidate runtime and selected artifact: genuine Prepare/Install actions with
durable IDs, no serial activity from GET, selected attachment continuity, normal
radio task quiescence, actual shared lease through worker/helper/handoff, fresh
loader inspection and independent disarmed postflight. Verify response-loss and
restart reconciliation without replay, safe recovery classification, preserved
existing configuration/registry and normal-service readiness afterward. Test
detected conflicts and worker termination offline first. The manual trial alone
does not qualify these new paths, RF control, interrupted flashing or other
boards. Release Engineering owns the final candidate evidence and eligibility.

## Offline checks

```sh
PYTHONPATH=host:host/tests python -m unittest \
  test_zbgw04_provider test_preserved_gecko -v
```

These use fake sysfs/serial and real temporary file locks, including inheritance
into a child process. They cover stale attachment, ambiguity, explicit board and
loader policy, detected conflicts, busy/exclusion failures, partial writes,
bounded replies, independent application identity and lease survival/replacement.
They are not a physical hardware or packaged HA lifecycle qualification.
