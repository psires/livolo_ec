"""Validate the narrowly supported PCA10059 application-only USB DFU package.

This independently implemented reader accepts the format produced by legacy
nrfutil 6.1.7. It does not import Nordic tooling or execute/extract ZIP members.
Package validation proves structural compatibility, not publisher authenticity
or the identity of a connected board.
"""

import hashlib
import io
import json
import re
import stat
import struct
import zipfile
import zlib

from .version import parse_version

MAX_PACKAGE = 1024 * 1024
APP_BASE = 0x1000
APP_END = 0xE0000


def _varint(data, offset):
    """Read one bounded canonical unsigned protobuf integer.

    @param data Message bytes.
    @param offset Starting byte position.
    @return Integer value and first unread byte position.
    """
    value = 0
    for shift in range(0, 35, 7):
        if offset >= len(data):
            raise ValueError("Truncated DFU metadata integer")
        byte = data[offset]
        offset += 1
        value |= (byte & 127) << shift
        if not byte & 128:
            if value > 0xFFFFFFFF or (shift and byte == 0):
                raise ValueError("Invalid DFU metadata integer")
            return value, offset
    raise ValueError("Oversized DFU metadata integer")


def _fields(data, schema):
    """Decode a strict flat protobuf message, rejecting duplicates and unknown fields.

    @param data Bounded message bytes; nesting is controlled explicitly by the caller.
    @param schema Mapping of accepted field numbers to wire types (0 or 2).
    @return Field values, represented as integers or bytes.
    """
    result, offset = {}, 0
    while offset < len(data):
        key, offset = _varint(data, offset)
        field, wire = key >> 3, key & 7
        if field not in schema or wire != schema[field] or field in result:
            raise ValueError("Unsupported or duplicate DFU metadata field")
        value, offset = _varint(data, offset)
        if wire == 2:
            end = offset + value
            if end > len(data):
                raise ValueError("Truncated DFU metadata bytes")
            value, offset = data[offset:end], end
        result[field] = value
    return result


def _object(pairs):
    """Reject ambiguous JSON objects in the embedded DFU manifest.

    @param pairs Ordered JSON key/value pairs.
    @return Dictionary with unique keys.
    """
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate DFU manifest field")
        result[key] = value
    return result


def _init(data, image, version):
    """Verify unsigned application-only initialization metadata against image bytes.

    @param data Legacy nrfutil protobuf init packet.
    @param image Application binary starting at the reserved application base.
    @param version Expected compiled major.minor release.
    """
    packet = _fields(data, {1: 2})
    if set(packet) != {1}:
        raise ValueError("DFU package requires one unsigned application command")
    command = _fields(packet[1], {1: 0, 2: 2})
    if set(command) != {1, 2} or command[1] != 1:
        raise ValueError("DFU metadata is not an INIT command")
    init = _fields(
        command[2], {1: 0, 2: 0, 3: 2, 4: 0, 5: 0, 6: 0, 7: 0, 8: 2, 9: 0, 10: 2}
    )
    major, minor = parse_version(version)
    if (
        not {1, 2, 3, 4, 7, 8} <= set(init)
        or init[1] != (major << 16) | minor
        or init[2] != 52
        or init[3] != b"\x00"
        or init[4] != 0
        or init.get(5, 0) != 0
        or init.get(6, 0) != 0
        or init[7] != len(image)
        or init.get(9, 0) != 0
    ):
        raise ValueError(
            "DFU application version, hardware, layout or debug mode mismatch"
        )
    digest = _fields(init[8], {1: 0, 2: 2})
    if digest != {1: 3, 2: hashlib.sha256(image).digest()[::-1]}:
        raise ValueError("DFU application SHA-256 mismatch")
    if 10 in init and _fields(init[10], {1: 0, 2: 2}) != {1: 1, 2: b""}:
        raise ValueError("Unsupported DFU boot validation mode")


def inspect_package(data, *, version):
    """Validate one exact-board package without extracting files or accessing hardware.

    @param data Complete DFU ZIP bytes, bounded to one MiB.
    @param version Expected compiled major.minor release, not a caller-selected downgrade.
    @return Public image compatibility metadata; raises ValueError on incompatibility.
    """
    parse_version(version)
    if not isinstance(data, bytes) or not 256 <= len(data) <= MAX_PACKAGE:
        raise ValueError("DFU package size is invalid")
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            entries = archive.infolist()
            names = [item.filename for item in entries]
            if (
                len(entries) != 3
                or len(set(names)) != 3
                or "manifest.json" not in names
            ):
                raise ValueError(
                    "DFU ZIP must contain exactly one manifest, application and init packet"
                )
            for item in entries:
                mode = item.external_attr >> 16
                if (
                    not re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", item.filename)
                    or item.filename in (".", "..")
                    or item.is_dir()
                    or (stat.S_IFMT(mode) and not stat.S_ISREG(mode))
                    or item.flag_bits & 1
                    or item.compress_type
                    not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED)
                    or item.file_size > MAX_PACKAGE
                ):
                    raise ValueError("Unsupported DFU ZIP member")
            info = archive.getinfo("manifest.json")
            if info.file_size > 4096:
                raise ValueError("Oversized DFU manifest")
            manifest = json.loads(archive.read(info), object_pairs_hook=_object)
            if not isinstance(manifest, dict) or set(manifest) != {"manifest"}:
                raise ValueError("Unsupported DFU manifest")
            contents = manifest["manifest"]
            if not isinstance(contents, dict) or set(contents) != {"application"}:
                raise ValueError(
                    "DFU must not contain bootloader or SoftDevice updates"
                )
            app = contents["application"]
            if not isinstance(app, dict) or set(app) != {"bin_file", "dat_file"}:
                raise ValueError("Unsupported DFU application manifest")
            binary, init = app["bin_file"], app["dat_file"]
            if (
                not isinstance(binary, str)
                or not isinstance(init, str)
                or not binary.endswith(".bin")
                or not init.endswith(".dat")
                or set(names) != {"manifest.json", binary, init}
            ):
                raise ValueError("DFU manifest members do not match ZIP contents")
            if archive.getinfo(init).file_size > 4096:
                raise ValueError("Oversized DFU init packet")
            image = archive.read(binary)
            init_data = archive.read(init)
    except (
        zipfile.BadZipFile,
        UnicodeError,
        RecursionError,
        RuntimeError,
        EOFError,
        zlib.error,
    ) as error:
        raise ValueError("Invalid DFU ZIP or manifest") from error
    if not 256 <= len(image) <= APP_END - APP_BASE or len(image) % 4:
        raise ValueError("Nordic application exceeds the protected flash layout")
    sp, pc = struct.unpack_from("<II", image)
    if (
        not 0x20000008 < sp <= 0x20040000
        or sp % 8
        or not pc & 1
        or not APP_BASE + 8 <= pc - 1 < APP_BASE + len(image)
    ):
        raise ValueError("Nordic vectors do not match PCA10059 application layout")
    if (
        b"EC-FW-VERSION=" + version.encode() + b"\0" not in image
        or b"EC-GATEWAY 3 board=nordic-pca10059 " not in image
    ):
        raise ValueError("Nordic gateway board or version marker mismatch")
    _init(init_data, image, version)
    return dict(
        board="nordic-pca10059",
        version=version,
        protocol=3,
        application_bytes=len(image),
        application_sha256=hashlib.sha256(image).hexdigest(),
        sha256=hashlib.sha256(data).hexdigest(),
        flash_start=APP_BASE,
        flash_end=APP_BASE + len(image),
        authentication="trusted_local_package",
        bootloader_preserved=True,
        rollback_supported=False,
    )
