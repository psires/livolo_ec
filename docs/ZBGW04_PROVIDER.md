# ZB-GW04 physical provider

`host/livolod/zbgw04_provider.py` supplies Linux identity, cooperative leases and
explicit serial inspections to the guided installer. It does not register an
API, launch a flasher or enable a deployment by itself. Its tests perform no
physical radio operations. The existing
[manual engineering trial](verification/zbgw04-manual-engineering-20261108.json)
is evidence for the selected board and artifacts, not qualification of this new
provider or the HA update workflow.

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
and an attachment token derived from host boot ID, USB bus/device numbers and
sysfs inode/ctime. This detects ordinary reattachment and host restart; a
serial-less bridge is not cryptographically authenticated. The reviewed profile
is CH340 `1a86:7523`, with no USB serial. This identifies a transport only.

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
returned version with the selected artifact. Neither inspection sends NETWORK,
ARM, pairing, switch control or BOOTLOADER commands. Metadata/lease/access checks
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
