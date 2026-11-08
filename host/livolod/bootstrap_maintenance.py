"""Explicit stopped-owner reconciliation of the reviewed Sonoff preflash failure only.

This administrative command never opens UART, flashes, replays a request, edits
an operation receipt or releases a consumed plan. It requires the original
journal and separate reviewed entry/reopen/intact-sniffer evidence. Observation
before a prior close is not current device-mode proof: a new Prepare must obtain
fresh physical evidence and a new Install still requires separate consent.
"""

import argparse
from contextlib import ExitStack
import fcntl
import hashlib
import json
import os
from pathlib import Path
import stat

from .bootstrap_store import BootstrapStore
from .bootstrap_worker import RadioOwnership, target_json
from .zbgw04_provider import LoaderPolicy


def reviewed_evidence(result_path, events_path, request_id, target):
    """Validate the bounded operator-reviewed experiment transcript without UART I/O.

    The protected files are administrative evidence, not signed hardware attestation.
    @param result_path Private completed experiment result file.
    @param events_path Private incremental experiment transcript file.
    @param request_id Exact failed HA install request to reconcile.
    @param target Current physical target matching the consumed plan.
    """
    raw = []
    for path in (result_path, events_path):
        path = Path(path)
        if not path.is_file() or not 0 < path.stat().st_size <= 1024 * 1024:
            raise ValueError("Missing or oversized maintenance evidence")
        raw.append(path.read_bytes())
    result = json.loads(raw[0])
    expected = dict(
        entry_attempted=True,
        reopen_verified=True,
        run_attempted=True,
        sniffer_verified=True,
        firmware_upload=False,
        original_journal_modified=False,
        cleanup_errors=[],
    )
    if result != expected:
        raise ValueError("Experiment did not complete the reviewed no-flash sequence")
    rows = [json.loads(line) for line in raw[1].splitlines()]

    def selected(event):
        """Select one transcript event type.

        @param event Fixed expected event name.
        """
        return [row for row in rows if row.get("event") == event]

    target_hash = hashlib.sha256(
        json.dumps(target_json(target), sort_keys=True).encode()
    ).hexdigest()
    entry = selected("durable_bootloader_entry_intent")
    basis = selected("failed_install_read_only_basis") + selected(
        "intact_sniffer_run_basis"
    )
    writes = selected("write_intent")
    completed = selected("write_completed")
    opened = selected("opened")
    if (
        len(entry) != 1
        or entry[0].get("target_sha256") != target_hash
        or len(basis) != 2
        or any(row.get("failed_request_id") != request_id for row in basis)
        or [(row.get("baud"), row.get("hex")) for row in writes]
        != [
            (921600, b"\nBOOTLOADER\n".hex()),
            (115200, b"\n".hex()),
            (115200, b"\n".hex()),
            (115200, b"2".hex()),
        ]
        or [row.get("count") for row in completed] != [12, 1, 1, 1]
        or len(opened) != 2
        or any(row.get("modem_bits", 0) & 6 != 6 for row in opened)
        or len(selected("closed")) != 2
        or len(selected("durable_intact_sniffer_run_intent")) != 1
        or len(selected("intact_sniffer_verified")) != 1
        or len(selected("reopen_loader_verified")) != 1
    ):
        raise ValueError("Maintenance transcript does not bind the reviewed experiment")
    names = [row.get("event") for row in rows]
    ordered = [
        "durable_bootloader_entry_intent",
        "entry_loader_verified",
        "closed_review_interval",
        "reopen_loader_verified",
        "durable_intact_sniffer_run_intent",
        "intact_sniffer_verified",
        "finished",
    ]
    if any(names.count(name) != 1 for name in ordered) or [
        names.index(name) for name in ordered
    ] != sorted(names.index(name) for name in ordered):
        raise ValueError("Maintenance evidence phases are out of order")
    return dict(
        result_sha256=hashlib.sha256(raw[0]).hexdigest(),
        events_sha256=hashlib.sha256(raw[1]).hexdigest(),
    )


def reconcile(config, request_id, result_path, events_path):
    """Acquire all existing guards and append one reviewed maintenance record.

    @param config Protected stopped runtime configuration, containing daemon/bootstrap sections.
    @param request_id Exact failed install, never inferred from a latest pointer.
    @param result_path Reviewed experiment result file.
    @param events_path Reviewed experiment transcript file.
    """
    settings = config["bootstrap"]
    if settings.get("profile") != "sonoff-zbdongle-e":
        raise ValueError("Maintenance is restricted to the reviewed Sonoff profile")
    if not Path(settings["journal"]).is_file():
        raise ValueError("Original journal is required")
    owner = RadioOwnership(settings)
    with ExitStack() as cleanup:
        cleanup.callback(owner.close)
        target, lease = owner.acquire()
        policy = settings["loader_policy"]
        if policy["target"] != target_json(target):
            raise ValueError("Reviewed policy attachment changed")
        LoaderPolicy(**policy["loader"]).validate(target)
        for suffix in (".lock", ".firmware.lock"):
            fd = os.open(
                settings["database"] + suffix, os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK
            )
            cleanup.callback(os.close, fd)
            info = os.fstat(fd)
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_nlink != 1
                or info.st_uid != os.geteuid()
            ):
                raise ValueError("Unexpected service guard")
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        store = BootstrapStore(settings["journal"], recover_pending=False)
        cleanup.callback(store.close)
        evidence = reviewed_evidence(result_path, events_path, request_id, target)
        repeated, _ = owner.acquire()
        if repeated != target:
            raise ValueError("Physical attachment changed")
        lease.check()
        record = store.reconcile_preflash(request_id, target_json(target), evidence)
        return {
            key: record[key]
            for key in (
                "reconciliation_id",
                "request_id",
                "kind",
                "plan_id",
                "target_id",
                "resolution",
                "permits",
                "setup_session",
            )
        }


def main(argv=None):
    """Run only explicitly acknowledged local maintenance, never an HTTP action.

    @param argv Optional command arguments for offline validation.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("config", "request-id", "evidence-result", "evidence-events"):
        parser.add_argument("--" + name, required=True)
    parser.add_argument(
        "--confirm-no-flash-reconciliation", action="store_true", required=True
    )
    args = parser.parse_args(argv)
    value = reconcile(
        json.loads(Path(args.config).read_text()),
        args.request_id,
        args.evidence_result,
        args.evidence_events,
    )
    print(json.dumps(value, sort_keys=True))


if __name__ == "__main__":
    main()
