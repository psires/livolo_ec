#!/usr/bin/env python3
"""Gate complete public firmware downloads separately from updater eligibility.

Review records are human attestations bound to bytes, not replacement hardware
experiments. Package staging can fetch a hash-pinned HTTPS bundle; this tool
never signs, flashes or publishes firmware.
"""

import argparse
import hashlib
import gzip
import io
import json
from pathlib import Path
import re
import sys
import tempfile
import tarfile
import urllib.parse
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "host"))
from livolod.firmware_artifacts import inspect_artifact_bytes
from livolod.firmware_bundle import BOARDS, _object, _read
from livolod.firmware_catalog import validate
from livolod.version import parse_version

PUBLIC_CHECKS = {
    "build_inputs": "build",
    "repeat_build": "build",
    "firmware_tests": "test",
    "boot_identity": "physical",
    "basic_control": "physical",
    "manual_install": "physical",
    "manual_recovery": "physical",
    "redistribution": "review",
}
UPDATE_CHECKS = {
    "installation": "physical",
    "exclusive_ownership": "physical",
    "restart_reenumeration": "physical",
    "interruption_recovery": "physical",
}
MAX_TOTAL = 16 * 1024 * 1024


def decode(data):
    """Reject ambiguous review JSON before interpreting approval fields.

    @param data UTF-8 JSON bytes from a bounded input.
    @return Decoded value with duplicate keys rejected.
    """
    return json.loads(data, object_pairs_hook=_object)


def text(value):
    """Require meaningful bounded reviewer text rather than a truthy placeholder.

    @param value Reviewer identity, limitation, summary or blocking explanation.
    @return True only for a nonempty string of at most 4096 characters.
    """
    return isinstance(value, str) and bool(value.strip()) and len(value) <= 4096


def reference(root, ref, prefix, files):
    """Snapshot a hash-bound, confined regular input into the export allowlist.

    @param root Review directory containing all explicitly selected inputs.
    @param ref Exact path/sha256 mapping supplied by the reviewed specification.
    @param prefix Required directory separating payloads, evidence, keys and notices.
    @param files Relative-path to immutable-byte map, updated after verification.
    @return Verified immutable file bytes.
    """
    if not isinstance(ref, dict) or set(ref) != {"path", "sha256"}:
        raise ValueError("References require exactly path and sha256")
    name, digest = ref["path"], ref["sha256"]
    if not isinstance(name, str) or not name.startswith(prefix + "/"):
        raise ValueError("Reference is outside its assigned directory")
    if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise ValueError("Reference requires a SHA-256 digest")
    data = files.get(name)
    if data is None:
        data = _read(root, name)[0]
    if not data or hashlib.sha256(data).hexdigest() != digest:
        raise ValueError("Referenced bytes do not match review")
    files[name] = data
    if len(files) > 256 or sum(map(len, files.values())) > MAX_TOTAL:
        raise ValueError("Public release exceeds its file or aggregate size limit")
    return data


def checks(root, records, required, files):
    """Require every acceptance result and retain the evidence its reviewer used.

    @param root Input review directory.
    @param records Named result/kind/summary/evidence mappings.
    @param required Exact required check names and evidence kinds.
    @param files Verified export allowlist receiving the cited evidence bytes.
    """
    if not isinstance(records, dict) or set(records) != set(required):
        raise ValueError("Required qualification checks are missing or unexpected")
    for name, kind in required.items():
        record = records[name]
        if (
            not isinstance(record, dict)
            or set(record) != {"result", "kind", "summary", "evidence"}
            or record["result"] != "passed"
            or record["kind"] != kind
            or not text(record["summary"])
            or not isinstance(record["evidence"], list)
            or not 1 <= len(record["evidence"]) <= 16
        ):
            raise ValueError("Check requires passed evidence of the correct kind")
        for ref in record["evidence"]:
            reference(root, ref, "evidence", files)


def review(root, item, version, source, files, scope="public_firmware_download"):
    """Accept public distribution only with exact-byte technical and rights reviews.

    @param root Input review directory.
    @param item One exact-board payload record from the release specification.
    @param version Compiled major.minor radio compatibility version.
    @param source Frozen firmware source commit shared by the complete board set.
    @param files Verified export allowlist receiving qualification and evidence.
    @param scope Separate public application or bootstrap review domain.
    @return Reviewed qualification record, including independent updater eligibility.
    """
    record = decode(reference(root, item["qualification"], "qualification", files))
    if not isinstance(record, dict) or set(record) != {
        "schema",
        "scope",
        "board",
        "version",
        "source_commit",
        "artifact_sha256",
        "technical_reviewer",
        "redistribution_reviewer",
        "limitations",
        "checks",
        "automatic_update",
    }:
        raise ValueError("Unsupported public qualification record")
    if (
        type(record["schema"]) is not int
        or record["schema"] != 1
        or record["scope"] != scope
        or record["board"] != item["board"]
        or record["version"] != version
        or record["source_commit"] != source
        or record["artifact_sha256"] != item["artifact"]["sha256"]
        or not text(record["technical_reviewer"])
        or not text(record["redistribution_reviewer"])
        or not isinstance(record["limitations"], list)
        or not 1 <= len(record["limitations"]) <= 32
        or not all(text(value) for value in record["limitations"])
    ):
        raise ValueError("Public approval does not bind this exact artifact/source")
    checks(root, record["checks"], PUBLIC_CHECKS, files)
    update = record["automatic_update"]
    if not isinstance(update, dict) or type(update.get("eligible")) is not bool:
        raise ValueError("Updater eligibility must be explicit")
    if update["eligible"]:
        if (
            set(update) != {"eligible", "reviewer", "tested_runtime_image", "checks"}
            or not text(update["reviewer"])
            or not isinstance(update["tested_runtime_image"], str)
            or not re.fullmatch(
                r"[a-z0-9./_-]+@sha256:[0-9a-f]{64}", update["tested_runtime_image"]
            )
        ):
            raise ValueError("Updater approval needs its exact tested runtime image")
        checks(root, update["checks"], UPDATE_CHECKS, files)
    elif set(update) != {"eligible", "reason"} or not text(update["reason"]):
        raise ValueError("Manual-only firmware needs an updater blocking reason")
    return record


def license_mapping(records):
    """Define the mixed-license reference without inventing a new licence grant.

    @param records Artifact identities mapped to exact bundled notice paths.
    @return UTF-8 mapping text; referenced licences remain authoritative.
    """
    lines = [
        "LicenseRef-Livolo-Firmware-Mixed",
        "",
        "This reference identifies the combined third-party terms of the listed",
        "firmware artifacts. It is not a new license or a grant of rights.",
        "Project-owned source remains BSD-2-Clause. Each dependency keeps its",
        "own terms, including any hardware-use or redistribution restrictions.",
        "Read every exact notice below before using or redistributing the binaries.",
        "Paths are relative to this firmware directory; in a daemon wheel the",
        "catalog directory is livolod/firmware. See release/qualification records",
        "for accepted scope and limitations. This file alone does not approve use.",
        "",
    ]
    for identity, names in records:
        lines.append(identity)
        lines.extend("  " + name for name in names)
    return ("\n".join(lines) + "\n").encode()


def release_files(spec_path):
    """Validate all four manual artifacts and derive only qualified updater entries.

    @param spec_path Reviewed release JSON beside its confined input directories.
    @return Complete relative-path/byte export map; no unreferenced files are copied.
    """
    path = Path(spec_path).absolute()
    root = path.parent
    spec = decode(_read(root, path.name, 65536)[0])
    if not isinstance(spec, dict) or set(spec) != {
        "schema",
        "version",
        "source_commit",
        "artifacts",
    }:
        raise ValueError("Unsupported public firmware release specification")
    if type(spec["schema"]) is not int or spec["schema"] != 1:
        raise ValueError("Unsupported public firmware release schema")
    version, source = spec["version"], spec["source_commit"]
    parse_version(version)
    if not isinstance(source, str) or not re.fullmatch(r"[0-9a-f]{40}", source):
        raise ValueError("A frozen firmware source commit is required")
    items = spec["artifacts"]
    if not isinstance(items, list) or len(items) != len(BOARDS):
        raise ValueError("Every supported exact board is required")
    files, entries, seen, public_notices = {}, [], set(), []
    for item in items:
        if not isinstance(item, dict):
            raise ValueError("Invalid board record")
        board = item.get("board")
        if not isinstance(board, str) or board not in BOARDS or board in seen:
            raise ValueError("Unsupported or duplicate board")
        seen.add(board)
        fields = {"board", "artifact", "qualification", "notices"}
        if board == "codm-cc2652p":
            fields.update({"public_key", "bootstrap"})
        if (
            set(item) != fields
            or not isinstance(item["notices"], list)
            or not 1 <= len(item["notices"]) <= 32
        ):
            raise ValueError("Payload, qualification and notices are mandatory")
        data = reference(root, item["artifact"], "artifacts", files)
        key = (
            reference(root, item["public_key"], "keys", files)
            if board == "codm-cc2652p"
            else None
        )
        inspect_artifact_bytes(data, board=board, version=version, public_key=key)
        for ref in item["notices"]:
            reference(root, ref, "notices", files)
        if key is not None:
            application = data[192:]
            key_offset = application.find(key)
            if key_offset < 0 or application.find(key, key_offset + 1) != -1:
                raise ValueError(
                    "TI application must embed the selected release key exactly once"
                )
            from livolod.firmware_bootstrap import inspect_ti_bootloader

            bootstrap = item["bootstrap"]
            if (
                not isinstance(bootstrap, dict)
                or set(bootstrap) != {"version", "artifact", "qualification", "notices"}
                or bootstrap["version"] != "0.2"
            ):
                raise ValueError("TI needs its separately reviewed bootstrap component")
            boot = reference(root, bootstrap["artifact"], "artifacts", files)
            inspect_ti_bootloader(boot, public_key=key, version=bootstrap["version"])
            if (
                not isinstance(bootstrap["notices"], list)
                or not 1 <= len(bootstrap["notices"]) <= 32
            ):
                raise ValueError("TI bootstrap notices are mandatory")
            for ref in bootstrap["notices"]:
                reference(root, ref, "notices", files)
            boot_review = review(
                root,
                dict(bootstrap, board=board),
                bootstrap["version"],
                source,
                files,
                scope="public_firmware_bootstrap",
            )
            if boot_review["automatic_update"]["eligible"]:
                raise ValueError(
                    "Bootstrap must never enter the application updater catalog"
                )
        public_notices.append(
            (board + " application", [ref["path"] for ref in item["notices"]])
        )
        if key is not None:
            public_notices.append(
                (
                    board + " bootstrap",
                    [ref["path"] for ref in item["bootstrap"]["notices"]],
                )
            )
        record = review(root, item, version, source, files)
        if not record["automatic_update"]["eligible"]:
            continue
        entry = dict(
            id=f"{board}-{version}",
            board=board,
            role="gateway",
            version=version,
            protocol=3,
            backend=BOARDS[board][0],
            layout=BOARDS[board][1],
            path=item["artifact"]["path"],
            size=len(data),
            sha256=item["artifact"]["sha256"],
            app_baudrate=460800,
            qualified=True,
            qualification=item["qualification"]["path"],
            source_commit=source,
            notices=[ref["path"] for ref in item["notices"]],
            release_notes="Public download and automatic updater separately reviewed; see qualification limitations.",
        )
        refs = [item["artifact"], item["qualification"], *item["notices"]]
        if key is not None:
            entry.update(
                bootloader_min="0.2",
                public_key=item["public_key"]["path"],
                public_key_sha256=hashlib.sha256(key).hexdigest(),
            )
            refs.append(item["public_key"])
        elif board != "nordic-pca10059":
            entry["bootloader_baudrate"] = 115200
        for ref in refs:
            files["catalog/" + ref["path"]] = files[ref["path"]]
        if len(entry["notices"]) >= 32:
            raise ValueError(
                "Updater catalog must reserve one notice slot for the license mapping"
            )
        entry["notices"].append("FIRMWARE-LICENSES.txt")
        entries.append(entry)
    files["FIRMWARE-LICENSES.txt"] = license_mapping(public_notices)
    if entries:
        files["catalog/FIRMWARE-LICENSES.txt"] = license_mapping(
            [
                (
                    entry["board"] + " application",
                    [
                        name
                        for name in entry["notices"]
                        if name != "FIRMWARE-LICENSES.txt"
                    ],
                )
                for entry in entries
            ]
        )
    manifest = dict(schema=1, host_version=version, artifacts=entries)
    files["catalog/manifest.json"] = (json.dumps(manifest, indent=2) + "\n").encode()
    files["release.json"] = (json.dumps(spec, indent=2) + "\n").encode()
    if sum(map(len, files.values())) > 2 * MAX_TOTAL:
        raise ValueError("Generated release exceeds aggregate size limit")
    return files


def verify(directory, *, inventory_sha256, source_commit, version):
    """Recheck a public bundle against the inventory pinned by its software release.

    @param directory Complete exported firmware directory, never an archive to extract.
    @param inventory_sha256 Expected release.json hash from reviewed software metadata.
    @param source_commit Expected immutable firmware source revision.
    @param version Expected firmware compatibility version.
    @return Verified relative-path/immutable-byte map for package staging.
    """
    root = Path(directory).absolute()
    raw = _read(root, "release.json", 65536)[0]
    if hashlib.sha256(raw).hexdigest() != inventory_sha256:
        raise ValueError("Firmware inventory differs from the release pin")
    spec = decode(raw)
    if not isinstance(spec, dict):
        raise ValueError("Firmware inventory must be an object")
    if spec.get("source_commit") != source_commit or spec.get("version") != version:
        raise ValueError("Firmware source/version differs from the release pin")
    files = release_files(root / "release.json")
    checksums = "".join(
        f"{hashlib.sha256(data).hexdigest()}  {name}\n"
        for name, data in sorted(files.items())
    ).encode()
    files["SHA256SUMS"] = checksums
    actual = set()
    for path in root.rglob("*"):
        if path.is_symlink():
            raise ValueError("Release bundle must not contain symlinks")
        if not path.is_dir():
            actual.add(path.relative_to(root).as_posix())
    if actual != set(files):
        raise ValueError("Release contains missing or unreviewed files")
    for name, expected in files.items():
        if _read(root, name)[0] != expected:
            raise ValueError(
                "Exported firmware, catalog or evidence differs from review"
            )
    return files


def verify_installed(directory, catalog, **pins):
    """Compare an installed runtime catalog with the independently pinned bundle.

    @param directory Public firmware directory delivered with the package or image.
    @param catalog Actual installed daemon firmware data directory.
    @param pins Expected inventory hash, frozen firmware source and compatibility version.
    @return Verified public file map after checking installed eligibility and payload bytes.
    """
    files = verify(directory, **pins)
    expected = {
        name[len("catalog/") :]: data
        for name, data in files.items()
        if name.startswith("catalog/")
    }
    root = Path(catalog)
    actual = set()
    for path in root.rglob("*"):
        if path.is_symlink():
            raise ValueError("Installed firmware catalog contains a symlink")
        if not path.is_dir():
            actual.add(path.relative_to(root).as_posix())
    if actual != set(expected):
        raise ValueError("Installed catalog differs from approved updater inventory")
    for name, data in expected.items():
        if _read(root, name)[0] != data:
            raise ValueError("Installed firmware bytes differ from public downloads")
    return files


def archive(directory, output, **pins):
    """Create a deterministic transport archive from a pinned verified directory.

    @param directory Complete public bundle directory.
    @param output New archive filename; existing artifacts are never overwritten.
    @param pins Inventory hash, source commit and compatibility version for verify().
    @return SHA-256 of the compressed transport bytes for software release metadata.
    """
    files = verify(directory, **pins)
    data = io.BytesIO()
    with gzip.GzipFile(fileobj=data, mode="wb", filename="", mtime=0) as compressed:
        with tarfile.open(
            fileobj=compressed, mode="w", format=tarfile.USTAR_FORMAT
        ) as bundle:
            for name, payload in sorted(files.items()):
                info = tarfile.TarInfo(name)
                info.size, info.mode, info.mtime = len(payload), 0o644, 0
                bundle.addfile(info, io.BytesIO(payload))
    raw = data.getvalue()
    with Path(output).open("xb") as stream:
        stream.write(raw)
    return hashlib.sha256(raw).hexdigest()


def fetched_files(pin):
    """Fetch only a pinned HTTPS release archive and reject unsafe archive members.

    @param pin Reviewed software metadata with archive URL/hash and inventory identity.
    @return Verified export bytes; the download never reaches a live catalog.
    """
    url = urllib.parse.urlsplit(pin["archive_url"])
    if (
        url.scheme != "https"
        or not url.netloc
        or url.username
        or url.password
        or url.fragment
    ):
        raise ValueError(
            "Firmware archive URL must be public HTTPS without credentials"
        )
    request = urllib.request.Request(
        pin["archive_url"], headers={"User-Agent": "livolo-firmware-release"}
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        if urllib.parse.urlsplit(response.geturl()).scheme != "https":
            raise ValueError("Firmware download must retain HTTPS")
        raw = response.read(2 * MAX_TOTAL + 1)
    if (
        len(raw) > 2 * MAX_TOTAL
        or hashlib.sha256(raw).hexdigest() != pin["archive_sha256"]
    ):
        raise ValueError("Firmware archive differs from the pinned transport")
    with tempfile.TemporaryDirectory(prefix="firmware-download-") as temporary:
        root, seen, total = Path(temporary), set(), 0
        with tarfile.open(fileobj=io.BytesIO(raw), mode="r:gz") as bundle:
            for member in bundle:
                name = member.name
                if (
                    not member.isfile()
                    or name in seen
                    or len(seen) >= 512
                    or name.startswith("/")
                    or "\\" in name
                    or any(part in ("", ".", "..") for part in name.split("/"))
                    or not 0 <= member.size <= 1024 * 1024
                ):
                    raise ValueError("Unsafe firmware archive member")
                seen.add(name)
                total += member.size
                if total > 2 * MAX_TOTAL:
                    raise ValueError("Expanded firmware archive exceeds size limit")
                payload = bundle.extractfile(member).read(member.size + 1)
                if len(payload) != member.size:
                    raise ValueError("Truncated firmware archive member")
                target = root / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(payload)
        return verify(
            root,
            inventory_sha256=pin["inventory_sha256"],
            source_commit=pin["source_commit"],
            version=pin["version"],
        )


def stage_for_source(source, bundle=None, candidate=False):
    """Inject explicitly pinned firmware into a temporary release source export.

    @param source Private clean build export, never the developer's working checkout.
    @param bundle Optional local public bundle; otherwise fetch its pinned archive.
    @param candidate Allow missing public firmware only for explicitly non-publishing builds.
    @return Firmware provenance summary, or None for historical software-only sources.
    """
    source = Path(source)
    descriptor = source / "host/release/firmware.json"
    policy = source / "host/release/firmware-policy.json"
    if policy.exists():
        from release_versions import source_versions

        rule = decode(_read(policy.parent, policy.name, 65536)[0])
        if (
            not isinstance(rule, dict)
            or set(rule) != {"schema", "require_firmware_after"}
            or type(rule["schema"]) is not int
            or rule["schema"] != 1
            or not isinstance(rule["require_firmware_after"], str)
            or not re.fullmatch(
                r"[0-9]+\.[0-9]+\.[0-9]+", rule["require_firmware_after"]
            )
        ):
            raise ValueError("Unsupported complete-release firmware policy")
        current = tuple(
            map(int, source_versions(source)["distribution_version"].split("."))
        )
        previous = tuple(map(int, rule["require_firmware_after"].split(".")))
        if current > previous and not descriptor.exists() and not candidate:
            raise ValueError(
                "The next project release requires all-board qualified firmware"
            )
    if not descriptor.exists():
        if bundle is not None:
            raise ValueError("Source has not approved a firmware inventory")
        return None
    pin = decode(_read(descriptor.parent, descriptor.name, 65536)[0])
    if (
        not isinstance(pin, dict)
        or set(pin)
        != {
            "schema",
            "inventory_sha256",
            "source_commit",
            "version",
            "archive_url",
            "archive_sha256",
        }
        or type(pin["schema"]) is not int
        or pin["schema"] != 1
    ):
        raise ValueError("Unsupported firmware release pin")
    from release_versions import source_versions

    versions = source_versions(source)
    if pin["version"] != versions["firmware_compat_version"]:
        raise ValueError("Pinned firmware does not match software compatibility")
    if (
        not isinstance(pin["archive_url"], str)
        or not isinstance(pin["archive_sha256"], str)
        or not re.fullmatch(r"[0-9a-f]{64}", pin["archive_sha256"])
    ):
        raise ValueError(
            "Firmware source pin needs an immutable transport hash and URL"
        )
    files = (
        fetched_files(pin)
        if bundle is None
        else verify(
            bundle,
            inventory_sha256=pin["inventory_sha256"],
            source_commit=pin["source_commit"],
            version=pin["version"],
        )
    )
    catalog = source / "host/livolod/firmware"
    if set(path.name for path in catalog.iterdir()) != {"manifest.json"} or decode(
        _read(catalog, "manifest.json", 65536)[0]
    ) != dict(schema=1, host_version=pin["version"], artifacts=[]):
        raise ValueError("Firmware injection requires a clean empty source catalog")
    delivery = source / "firmware-release"
    if delivery.exists() or delivery.is_symlink():
        raise ValueError("Firmware release staging already exists")
    delivery.mkdir()
    for name, data in files.items():
        target = delivery / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        if name.startswith("catalog/"):
            target = catalog / name[len("catalog/") :]
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
    updater_count = len(decode(files["catalog/manifest.json"])["artifacts"])
    if updater_count:
        project = source / "host/pyproject.toml"
        content, licenses = re.subn(
            r'(?m)^license = "BSD-2-Clause"$',
            'license = "BSD-2-Clause AND LicenseRef-Livolo-Firmware-Mixed"',
            project.read_text(),
        )
        content, notice_fields = re.subn(
            r'(?m)^license-files = \["LICENSE"\]$',
            'license-files = ["LICENSE", "FIRMWARE-LICENSES.txt"]',
            content,
        )
        if licenses != 1 or notice_fields != 1:
            raise ValueError(
                "Daemon license metadata needs explicit firmware-aware review"
            )
        project.write_text(content)
        (source / "host/FIRMWARE-LICENSES.txt").write_bytes(
            files["catalog/FIRMWARE-LICENSES.txt"]
        )
    return dict(
        pin,
        board_count=len(BOARDS),
        updater_board_count=len(decode(files["catalog/manifest.json"])["artifacts"]),
        delivery="firmware-release/",
        catalog="host/livolod/firmware/",
    )


def assemble(spec_path, output):
    """Export a complete reviewed download directory without replacing prior bytes.

    @param spec_path Public review specification; local trial records are rejected.
    @param output New immutable directory for public artifacts and a restricted catalog.
    @return SHA-256 of the exported release inventory.
    """
    destination = Path(output).absolute()
    if destination.exists() or destination.is_symlink():
        raise ValueError("Release destination already exists")
    files = release_files(spec_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix=".firmware-release-", dir=destination.parent
    ) as temporary:
        stage = Path(temporary) / "release"
        stage.mkdir()
        for name, data in files.items():
            target = stage / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            target.chmod(0o644)
        version = decode(files["release.json"])["version"]
        validate(stage / "catalog", version)
        (stage / "SHA256SUMS").write_text(
            "".join(
                f"{hashlib.sha256(data).hexdigest()}  {name}\n"
                for name, data in sorted(files.items())
            )
        )
        stage.rename(destination)
    return hashlib.sha256(files["release.json"]).hexdigest()


def main():
    """Validate an explicitly reviewed complete firmware release without radio access."""
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    build = commands.add_parser("assemble")
    build.add_argument("--spec", required=True, type=Path)
    build.add_argument("--output", required=True, type=Path)
    check = commands.add_parser("verify")
    check.add_argument("--installed-catalog", type=Path)
    pack = commands.add_parser("pack")
    pack.add_argument("--output", required=True, type=Path)
    for command in (check, pack):
        command.add_argument("--directory", required=True, type=Path)
        command.add_argument("--inventory-sha256", required=True)
        command.add_argument("--source-commit", required=True)
        command.add_argument("--version", required=True)
    args = parser.parse_args()
    if args.command == "assemble":
        print(assemble(args.spec, args.output))
    else:
        pins = dict(
            inventory_sha256=args.inventory_sha256,
            source_commit=args.source_commit,
            version=args.version,
        )
        if args.command == "pack":
            print(archive(args.directory, args.output, **pins))
        else:
            files = (
                verify_installed(args.directory, args.installed_catalog, **pins)
                if args.installed_catalog
                else verify(args.directory, **pins)
            )
            print(json.dumps({"verified_files": len(files), "version": args.version}))


if __name__ == "__main__":
    main()
