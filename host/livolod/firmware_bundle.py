"""Validate local, operator-trusted firmware bundles before radio downtime.

The bundle is installed with the daemon or selected in local configuration; an
HTTP caller may select only the returned hash-bound candidate ID. SHA-256 binds
bytes to that trusted manifest, not to an independently authenticated publisher.
SDKs, compilers, signing secrets and downloads are never needed here.

select_candidate/load_candidate return dictionaries. `_data` is an immutable
validated byte snapshot for the worker only; never serialize the whole result
into a public snapshot or database. Public keys are raw hexadecimal strings.
"""

import binascii
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import struct

from .version import parse_version

MAX_FILE = 1024 * 1024
BOARDS = {
    "codm-cc2652p": ("ti_uart_signed_v1", "ti-app-8000-20000"),
    "sonoff-zbdongle-e": ("mg21_gecko_gbl_v1", "mg21-app-4000-30000"),
    "zb-gw04-1v2": ("mg21_gecko_gbl_v1", "mg21-app-4000-30000"),
    "nordic-pca10059": ("nrf52840_usb_dfu_v1", "nrf-app-1000-e0000"),
}
REQUIRED = {
    "id",
    "board",
    "role",
    "version",
    "protocol",
    "backend",
    "path",
    "size",
    "sha256",
    "app_baudrate",
    "layout",
    "qualified",
    "qualification",
    "source_commit",
    "notices",
}
OPTIONAL = {
    "bootloader_baudrate",
    "bootloader_min",
    "public_key",
    "public_key_sha256",
    "release_notes",
    "release_url",
}


class BundleError(ValueError):
    """A stable machine-readable reason why installation is unavailable."""

    def __init__(self, code, message):
        """Attach a public reason without exposing filesystem or device details.

        @param code Stable lowercase reason identifier.
        @param message Safe human-readable explanation.
        """
        super().__init__(message)
        self.code = code


def _fail(message, code="bundle_invalid"):
    """Reject invalid bundle data with a public diagnostic.

    @param message Safe explanation of the rejected invariant.
    @param code Stable unavailable reason; defaults to invalid metadata.
    """
    raise BundleError(code, message)


def _object(pairs):
    """Decode JSON objects without silently accepting duplicate keys.

    @param pairs Ordered JSON key/value pairs.
    @return Unique-key dictionary, or raise BundleError.
    """
    result = {}
    for key, value in pairs:
        if key in result:
            _fail("Duplicate manifest field")
        result[key] = value
    return result


def _read(root, relative, limit=MAX_FILE):
    """Read one bounded regular file confined to a trusted bundle directory.

    @param root Resolved bundle directory controlled by the operator.
    @param relative Portable relative path without symlinks or parent traversal.
    @param limit Maximum byte length to read.
    @return Exact file bytes and its absolute path.
    """
    if not isinstance(relative, str) or not relative or "\\" in relative:
        _fail("Invalid bundle path")
    parts = PurePosixPath(relative)
    if parts.is_absolute() or any(x in ("", ".", "..") for x in relative.split("/")):
        _fail("Bundle paths must remain inside the bundle")
    path = root
    try:
        for part in parts.parts:
            path = path / part
            if path.is_symlink():
                _fail("Symlinks are not permitted in firmware bundles")
        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_NOFOLLOW", 0))
        with os.fdopen(fd, "rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
                _fail("Bundle file type or size is invalid")
            data = stream.read(limit + 1)
            if len(data) > limit or len(data) != info.st_size:
                _fail("Bundle file changed while being read")
        return data, str(path)
    except OSError as error:
        raise BundleError(
            "artifact_missing", "A required bundle file is unavailable"
        ) from error


def _manifest(bundle_dir, host_version):
    """Load and check a manifest before selecting any device-specific artifact.

    @param bundle_dir Operator-configured local bundle directory.
    @param host_version Exact installed daemon release in major.minor form.
    @return Resolved root and validated metadata entries.
    """
    try:
        parse_version(host_version)
        root = Path(bundle_dir).resolve()
    except (TypeError, ValueError, OSError) as error:
        raise BundleError(
            "bundle_missing", "No firmware bundle is configured"
        ) from error
    if not root.is_dir() or not (root / "manifest.json").exists():
        _fail("No firmware bundle is installed", "bundle_missing")
    try:
        data, _ = _read(root, "manifest.json", 65536)
        manifest = json.loads(data, object_pairs_hook=_object)
    except BundleError:
        raise
    except (UnicodeError, ValueError, RecursionError) as error:
        raise BundleError("bundle_invalid", "Invalid firmware manifest JSON") from error
    if not isinstance(manifest, dict) or set(manifest) != {
        "schema",
        "host_version",
        "artifacts",
    }:
        _fail("Unsupported firmware manifest fields")
    if type(manifest["schema"]) is not int or manifest["schema"] != 1:
        _fail("Unsupported firmware manifest schema")
    if manifest["host_version"] != host_version:
        _fail("Bundle does not match this daemon release", "incompatible_host")
    entries = manifest["artifacts"]
    if not isinstance(entries, list) or len(entries) > 32:
        _fail("Invalid artifact list")
    ids, boards = set(), set()
    for entry in entries:
        if (
            not isinstance(entry, dict)
            or not REQUIRED <= set(entry)
            or set(entry) - REQUIRED - OPTIONAL
        ):
            _fail("Invalid artifact metadata fields")
        ident, board = entry["id"], entry["board"]
        if not isinstance(ident, str) or not re.fullmatch(
            r"[a-zA-Z0-9][a-zA-Z0-9._-]{0,95}", ident
        ):
            _fail("Invalid artifact identity")
        if not isinstance(board, str) or board not in BOARDS:
            _fail("Unsupported board in bundle", "unsupported_board")
        if ident in ids or board in boards:
            _fail("Ambiguous artifact or board selection")
        ids.add(ident)
        boards.add(board)
        if entry["role"] != "gateway" or entry["qualified"] is not True:
            _fail("Artifact is not a qualified gateway image", "artifact_unqualified")
        if (
            entry["version"] != host_version
            or type(entry["protocol"]) is not int
            or entry["protocol"] != 3
        ):
            _fail("Artifact version or protocol is incompatible", "incompatible_host")
        if (entry["backend"], entry["layout"]) != BOARDS[board]:
            _fail("Artifact backend/layout does not match its board")
        if type(entry["size"]) is not int or not 256 <= entry["size"] <= MAX_FILE:
            _fail("Invalid artifact size")
        if not isinstance(entry["sha256"], str) or not re.fullmatch(
            r"[0-9a-f]{64}", entry["sha256"]
        ):
            _fail("Invalid artifact digest")
        if type(entry["app_baudrate"]) is not int or entry["app_baudrate"] != 460800:
            _fail(
                "Only the qualified 460800 application profile is supported",
                "incompatible_baudrate",
            )
        if not isinstance(entry["source_commit"], str) or not re.fullmatch(
            r"[0-9a-f]{40}", entry["source_commit"]
        ):
            _fail("Artifact source identity is missing")
        if (
            not isinstance(entry["notices"], list)
            or not entry["notices"]
            or len(entry["notices"]) > 32
        ):
            _fail("Artifact notices are required")
        for name in [entry["qualification"], *entry["notices"]]:
            if not _read(root, name)[0]:
                _fail("Qualification and notice files must not be empty")
        for name in ("release_notes", "release_url"):
            if name in entry and (
                not isinstance(entry[name], str) or len(entry[name]) > 4096
            ):
                _fail("Invalid release information")
        if entry.get("release_url") and not entry["release_url"].startswith("https://"):
            _fail("Release links must use HTTPS")
        if board in ("sonoff-zbdongle-e", "zb-gw04-1v2"):
            if (
                type(entry.get("bootloader_baudrate")) is not int
                or entry["bootloader_baudrate"] != 115200
            ):
                _fail("Gecko bootloader must use 115200 baud")
        elif board == "codm-cc2652p":
            if entry.get("bootloader_min") != "0.2":
                _fail("TI signed updates require the qualified 0.2 bootloader")
    return root, entries


def _image_identity(image, board, version):
    """Reject mismatched, legacy or sniffer images behind gateway metadata.

    @param image Application bytes without container headers.
    @param board Exact board identity authorized by the manifest.
    @param version Exact release authorized by the manifest.
    """
    if b"EC-FW-VERSION=" + version.encode() + b"\0" not in image:
        _fail("Compiled firmware version does not match metadata")
    if b"EC-GATEWAY 3 board=" + board.encode() + b" " not in image:
        _fail("Image lacks the expected gateway and board identity")


def _gbl(data, board, version):
    """Validate the narrowly supported application-only Gecko container.

    @param data Complete GBL bytes from the trusted bundle.
    @param board Exact supported MG21 board identity.
    @param version Expected compiled release.
    @return Validated application bytes; no bootloader/SE/token writes are allowed.
    """
    expected = (0x03A617EB, 0xF40A0AF4, 0xFE0101FE, 0xFC0404FC)
    offset, values = 0, []
    for tag in expected:
        if len(data) - offset < 8:
            _fail("Truncated GBL header")
        actual, length = struct.unpack_from("<II", data, offset)
        offset += 8
        if actual != tag or length > len(data) - offset:
            _fail("Unsupported or truncated GBL tag")
        values.append(data[offset : offset + length])
        offset += length
    if (
        offset != len(data)
        or values[0] != struct.pack("<II", 0x03000000, 0)
        or len(values[3]) != 4
    ):
        _fail("GBL is not the supported application-only format")
    if binascii.crc32(data) != 0x2144DF1C or len(values[2]) < 260:
        _fail("GBL checksum or program data is invalid")
    address = struct.unpack_from("<I", values[2])[0]
    image = values[2][4:]
    if address != 0x4000 or len(image) > 0x2C000 or len(image) % 4:
        _fail("GBL application is outside the supported flash range")
    sp, pc = struct.unpack_from("<II", image)
    if (
        not 0x20000000 < sp <= 0x20010000
        or sp % 8
        or not pc & 1
        or not address + 8 <= pc - 1 < address + len(image)
    ):
        _fail("Invalid MG21 application vectors")
    prop = struct.unpack_from("<I", image, 52)[0] - address
    if prop % 4 or not 0 <= prop <= len(image) - 56:
        _fail("Invalid MG21 application properties address")
    magic = bytes.fromhex("13b779fac925ddb7adf3cfe0f1b614b8")
    if image[prop : prop + 16] != magic:
        _fail("Invalid MG21 application properties magic")
    structure, signature = struct.unpack_from("<II", image, prop + 16)
    app = image[prop + 28 : prop + 56]
    major, minor = parse_version(version)
    if structure & 255 != 1 or signature or len(values[1]) != 28 or app != values[1]:
        _fail("Unsupported MG21 properties or inconsistent application tag")
    if struct.unpack_from("<II", app) != (4, (major << 16) | minor) or app[
        12:
    ] != b"LIVOLO-MG21".ljust(16, b"\0"):
        _fail("MG21 application product/version mismatch")
    markers = re.findall(rb"EC-MG21-BOARD=([^\x00 ]+)", image)
    if not markers or set(markers) != {board.encode()}:
        _fail("MG21 image has missing or conflicting board markers")
    _image_identity(image, board, version)
    return image


def _candidate(root, entry, app_baudrate):
    """Verify selected bytes and construct a private worker candidate.

    @param root Resolved trusted bundle root.
    @param entry Structurally validated artifact metadata.
    @param app_baudrate Configured application transport rate to preserve.
    @return Candidate dictionary with immutable `_data` and public metadata.
    """
    if type(app_baudrate) is not int or app_baudrate != entry["app_baudrate"]:
        _fail(
            "Update must preserve the configured application rate",
            "incompatible_baudrate",
        )
    data, path = _read(root, entry["path"])
    if (
        len(data) != entry["size"]
        or hashlib.sha256(data).hexdigest() != entry["sha256"]
    ):
        _fail("Firmware payload length or digest mismatch", "artifact_invalid")
    public, digest = None, None
    if entry["backend"] == "ti_uart_signed_v1":
        public_bytes, _ = _read(root, entry.get("public_key"), 32)
        if len(public_bytes) != 32 or hashlib.sha256(
            public_bytes
        ).hexdigest() != entry.get("public_key_sha256"):
            _fail("Invalid TI public-key fingerprint", "signing_key_mismatch")
        try:
            from cryptography.exceptions import InvalidSignature
            from cryptography.hazmat.primitives.asymmetric.ed25519 import (
                Ed25519PublicKey,
            )
            from .update_package import inspect_package
        except ImportError as error:
            raise BundleError(
                "backend_unavailable", "TI signature verification is not installed"
            ) from error
        try:
            info = inspect_package(
                data, Ed25519PublicKey.from_public_bytes(public_bytes)
            )
        except (ValueError, InvalidSignature) as error:
            raise BundleError(
                "artifact_invalid", "Invalid signed TI firmware package"
            ) from error
        if info["version"] != entry["version"]:
            _fail("Signed TI version does not match the manifest")
        _image_identity(data[192:], entry["board"], entry["version"])
        public, digest = public_bytes.hex(), info["sha512"]
    elif entry["backend"] == "mg21_gecko_gbl_v1":
        _gbl(data, entry["board"], entry["version"])
    elif entry["backend"] == "nrf52840_usb_dfu_v1":
        from .nrf_dfu import inspect_package

        try:
            inspect_package(data, version=entry["version"])
        except ValueError as error:
            raise BundleError(
                "artifact_invalid", "Invalid Nordic application package"
            ) from error
    else:
        _fail("Unsupported firmware backend", "backend_unavailable")
    return {
        "id": entry["id"] + ":" + entry["sha256"],
        "artifact_id": entry["id"],
        "board": entry["board"],
        "version": entry["version"],
        "backend": entry["backend"],
        "sha256": entry["sha256"],
        "path": path,
        "app_baudrate": app_baudrate,
        "image_sha512": digest,
        "public_key": public,
        "rollback_supported": entry["backend"] == "ti_uart_signed_v1",
        "bootloader_min": entry.get("bootloader_min"),
        "release_notes": entry.get("release_notes"),
        "release_url": entry.get("release_url"),
        "_data": data,
    }


def select_candidate(bundle_dir, *, board, host_version, app_baudrate):
    """Select the sole compatible exact-board artifact for an installed host.

    @param bundle_dir Trusted local bundle directory, never supplied by HTTP clients.
    @param board Live radio board identity, not a guess from the USB bridge.
    @param host_version Installed daemon release; firmware must match exactly.
    @param app_baudrate Current application rate, preserved by this update.
    @return Validated candidate dictionary; unavailable cases raise BundleError.
    """
    if not board:
        _fail("Radio board identity is unknown", "board_identity_unknown")
    if not isinstance(board, str) or board not in BOARDS:
        _fail("Radio board is not supported", "unsupported_board")
    root, entries = _manifest(bundle_dir, host_version)
    matches = [entry for entry in entries if entry["board"] == board]
    if not matches:
        _fail("No qualified firmware is bundled for this board", "artifact_missing")
    return _candidate(root, matches[0], app_baudrate)


def load_candidate(bundle_dir, candidate_id, *, host_version, app_baudrate):
    """Resolve a client-selected ID only if its digest still matches the bundle.

    @param bundle_dir Trusted local bundle directory.
    @param candidate_id Artifact identity bound to its complete SHA-256 digest.
    @param host_version Installed daemon release.
    @param app_baudrate Application rate that must remain unchanged.
    @return Revalidated private candidate dictionary or BundleError.
    """
    root, entries = _manifest(bundle_dir, host_version)
    for entry in entries:
        if entry["id"] + ":" + entry["sha256"] == candidate_id:
            return _candidate(root, entry, app_baudrate)
    _fail("Firmware selection is absent or has changed", "candidate_stale")
