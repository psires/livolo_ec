#!/usr/bin/env python3
"""Assemble a local firmware catalog from inspected images and review records.

This is an operator packaging tool, not a qualification test or a signing service.
The specification supplies already reviewed qualification records and notices.
Generated payloads stay outside Git; install the completed directory read-only.
"""

import argparse
import hashlib
import json
from pathlib import Path
import re
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "host"))
from livolod.firmware_artifacts import inspect_artifact
from livolod.firmware_bundle import BOARDS, BundleError, _manifest, _read
from livolod.version import parse_version


def local_bytes(name, limit=1024 * 1024):
    """Read a bounded regular input without following its final symlink.

    @param name Absolute operator-selected input filename.
    @param limit Maximum input length in bytes.
    """
    path = Path(name)
    if not path.is_absolute():
        raise ValueError("Catalog inputs must use absolute paths")
    return _read(path.parent, path.name, limit)[0]


def assemble(spec, output):
    """Validate all inputs and atomically publish a new immutable catalog directory.

    @param spec Operator specification with version, artifacts and notices.
    @param output New directory path; an existing destination is never replaced.
    @return Public manifest with payload digests and relative evidence paths.
    """
    version = spec["version"]
    parse_version(version)
    destination = Path(output).absolute()
    if destination.exists() or destination.is_symlink():
        raise ValueError("Catalog destination already exists")
    if not spec["notices"] or not spec["artifacts"]:
        raise ValueError("Artifacts and notices are required")
    notices = [local_bytes(name) for name in spec["notices"]]
    if any(not data for data in notices):
        raise ValueError("Notices cannot be empty")
    entries, files, boards = [], {}, set()
    for item in spec["artifacts"]:
        board = item["board"]
        if board not in BOARDS or board in boards:
            raise ValueError("Unsupported or duplicate board")
        boards.add(board)
        candidate = inspect_artifact(
            item["path"],
            board=board,
            version=version,
            public_key=item.get("public_key"),
        )
        record_bytes = local_bytes(item["qualification"])
        record = json.loads(record_bytes)
        if (
            record.get("board") != board
            or record.get("version") != version
            or record.get("artifact_sha256") != candidate["sha256"]
            or record.get("scope") != "standalone_local_update_test"
            or record.get("approved_for_installation_trial") is not True
            or not re.fullmatch(r"[0-9a-f]{40}", record.get("source_commit", ""))
            or not re.fullmatch(
                r"[0-9a-f]{64}", record.get("source_snapshot_sha256", "")
            )
            or not record.get("limitations")
            or record.get("checks")
            != {
                name: "passed"
                for name in (
                    "artifact_inspection",
                    "repeat_build",
                    "baseline_comparison",
                    "firmware_tests",
                    "notice_collection",
                )
            }
        ):
            raise ValueError("Qualification record does not approve these exact bytes")
        suffix = {"codm-cc2652p": "ecupd", "nordic-pca10059": "zip"}.get(board, "gbl")
        name = f"{board}-{version}.{suffix}"
        evidence = f"qualification/{board}.json"
        files[name] = candidate["_data"]
        files[evidence] = record_bytes
        entry = dict(
            id=f"{board}-{version}",
            board=board,
            role="gateway",
            version=version,
            protocol=3,
            backend=BOARDS[board][0],
            layout=BOARDS[board][1],
            path=name,
            size=len(candidate["_data"]),
            sha256=candidate["sha256"],
            app_baudrate=460800,
            qualified=True,
            qualification=evidence,
            source_commit=record["source_commit"],
            notices=[f"notices/{index:02d}.txt" for index in range(len(notices))],
            release_notes=(
                f"Local {version} installation trial; unchanged radio behavior. "
                "See the exact-image qualification record for evidence and limits."
            ),
        )
        if board == "codm-cc2652p":
            key = local_bytes(item["public_key"], 32)
            # Bind the copied key to the one used by the image inspector.
            if key.hex() != candidate["public_key"]:
                raise ValueError("Public key changed during inspection")
            files["ti-public.key"] = key
            entry.update(
                public_key="ti-public.key",
                bootloader_min="0.2",
                public_key_sha256=hashlib.sha256(key).hexdigest(),
            )
        elif board != "nordic-pca10059":
            entry["bootloader_baudrate"] = 115200
        entries.append(entry)
    manifest = dict(schema=1, host_version=version, artifacts=entries)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".catalog-", dir=destination.parent) as tmp:
        stage = Path(tmp) / "catalog"
        stage.mkdir()
        files.update({f"notices/{i:02d}.txt": data for i, data in enumerate(notices)})
        files["manifest.json"] = (json.dumps(manifest, indent=2) + "\n").encode()
        for name, data in files.items():
            target = stage / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            target.chmod(0o644)
        _manifest(stage, version)
        stage.rename(destination)
    return manifest


def main():
    """Build a catalog from an explicit local specification, without radio access."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--spec", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    try:
        spec = json.loads(local_bytes(args.spec))
        result = assemble(spec, args.output)
    except (OSError, ValueError, KeyError, TypeError, BundleError) as error:
        raise SystemExit(f"Catalog rejected: {error}") from error
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
