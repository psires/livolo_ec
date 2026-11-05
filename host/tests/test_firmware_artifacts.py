"""Validate all supported application containers without hardware or SDK tools."""

import hashlib
import io
import json
import os
from pathlib import Path
import struct
import tempfile
import unittest
import warnings
import zipfile

from livolod.firmware_artifacts import inspect_artifact, inspect_artifact_bytes
from livolod.firmware_bundle import BundleError
from livolod.nrf_dfu import inspect_package
from test_firmware_bundle import gbl, mg21_image, ti_package


def varint(value):
    """Encode an unsigned fixture integer without importing vendor tooling.

    @param value Nonnegative integer.
    @return Canonical protobuf varint bytes.
    """
    data = bytearray()
    while value > 127:
        data.append((value & 127) | 128)
        value >>= 7
    return bytes(data + bytes([value]))


def field(number, value):
    """Encode one fixture protobuf field.

    @param number Positive field number.
    @param value Unsigned integer or length-delimited bytes.
    @return Wire field bytes.
    """
    if isinstance(value, bytes):
        return varint((number << 3) | 2) + varint(len(value)) + value
    return varint(number << 3) + varint(value)


def nrf_image():
    """Build a deliberately nonexecutable fixture with application vectors and markers."""
    image = bytearray(512)
    struct.pack_into("<II", image, 0, 0x20040000, 0x1101)
    marker = b"EC-GATEWAY 3 board=nordic-pca10059 baud=%lu\0EC-FW-VERSION=0.12\0"
    image[100 : 100 + len(marker)] = marker
    return bytes(image)


def nrf_init(image, changes=None, extra=b""):
    """Build init metadata that can deliberately violate one invariant.

    @param image Application bytes to bind with SHA-256.
    @param changes Optional init field overrides.
    @param extra Raw extra init fields for ambiguity tests.
    @return Unsigned DFU INIT packet bytes.
    """
    fields = {
        1: 12,
        2: 52,
        3: b"\x00",
        4: 0,
        5: 0,
        6: 0,
        7: len(image),
        8: field(1, 3) + field(2, hashlib.sha256(image).digest()[::-1]),
        9: 0,
        10: field(1, 1) + field(2, b""),
    }
    fields.update(changes or {})
    init = b"".join(field(n, v) for n, v in fields.items()) + extra
    return field(1, field(1, 1) + field(2, init))


def nrf_zip(image=None, init=None, manifest=None, extra=None):
    """Package synthetic Nordic bytes for offline compatibility tests only.

    @param image Optional synthetic binary, otherwise the default fixture.
    @param init Optional init packet, otherwise generated from the binary.
    @param manifest Optional raw JSON manifest bytes.
    @param extra Optional sequence of ZIP names/bytes appended to the archive.
    @return ZIP bytes; never usable firmware.
    """
    image = nrf_image() if image is None else image
    init = nrf_init(image) if init is None else init
    manifest = (
        manifest
        or json.dumps(
            {
                "manifest": {
                    "application": {"bin_file": "app.bin", "dat_file": "app.dat"}
                }
            }
        ).encode()
    )
    out = io.BytesIO()
    with warnings.catch_warnings(), zipfile.ZipFile(out, "w") as archive:
        warnings.simplefilter("ignore", UserWarning)
        for name, value in [
            ("manifest.json", manifest),
            ("app.bin", image),
            ("app.dat", init),
            *(extra or []),
        ]:
            archive.writestr(name, value)
    return out.getvalue()


class ArtifactTests(unittest.TestCase):
    """Reject unsafe layout, mismatched identities and ambiguous DFU containers."""

    def test_all_four_boards_use_shared_container_validation(self):
        """Each supported board's application format is inspectable without helpers."""
        for board in ("sonoff-zbdongle-e", "zb-gw04-1v2"):
            info = inspect_artifact_bytes(
                gbl(mg21_image(board)), board=board, version="0.12"
            )
            self.assertEqual(info["board"], board)
            self.assertEqual(info["flash_start"], 0x4000)
        ti, public = ti_package()
        info = inspect_artifact_bytes(
            ti, board="codm-cc2652p", version="0.12", public_key=public
        )
        self.assertEqual(info["authentication"], "ed25519")
        info = inspect_artifact_bytes(
            nrf_zip(), board="nordic-pca10059", version="0.12"
        )
        self.assertEqual(info["flash_start"], 0x1000)
        self.assertFalse(info["rollback_supported"])

    def test_wrong_board_version_signature_and_public_key_are_rejected(self):
        """Artifact selection cannot bypass exact board, version and TI signer checks."""
        ti, public = ti_package()
        cases = [
            (gbl(mg21_image()), "zb-gw04-1v2", "0.12", None),
            (nrf_zip(), "nordic-pca10059", "0.13", None),
            (ti, "codm-cc2652p", "0.12", None),
            (ti, "codm-cc2652p", "0.12", bytes(32)),
            (nrf_zip(), "nrf-other", "0.12", None),
        ]
        for data, board, version, key in cases:
            with self.subTest(board=board), self.assertRaises(BundleError):
                inspect_artifact_bytes(
                    data, board=board, version=version, public_key=key
                )

    def test_nordic_init_cannot_write_other_images_or_ignore_integrity(self):
        """Init type, hardware, SoftDevice, sizes, version, debug and hash must match."""
        image = nrf_image()
        for changes in (
            {1: 13},
            {2: 51},
            {3: b"\xff\x01"},
            {4: 2},
            {5: 256},
            {6: 256},
            {7: 1024},
            {9: 1},
            {8: field(1, 3) + field(2, bytes(32))},
            {10: field(1, 0) + field(2, b"")},
        ):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                inspect_package(
                    nrf_zip(image, nrf_init(image, changes)), version="0.12"
                )

    def test_duplicate_unknown_signed_and_truncated_protobuf_are_rejected(self):
        """No alternate protobuf interpretation can hide a bootloader operation."""
        image = nrf_image()
        for init in (
            nrf_init(image, extra=field(4, 2)),
            nrf_init(image, extra=field(11, 0)),
            b"\x12\x00",
            nrf_init(image)[:-1],
            b"\x0a\xff\xff\xff\xff\xff\xff",
            b"\x8a\x00\x00",
        ):
            with self.subTest(init=init[:5]), self.assertRaises(ValueError):
                inspect_package(nrf_zip(init=init), version="0.12")

    def test_protected_ranges_vectors_and_gateway_role_are_rejected(self):
        """Matching checksums cannot authorize an out-of-range or wrong-role binary."""
        for offset, value in (
            (0, 0x20050000),
            (0, 0x20000008),
            (4, 0x101),
            (4, 0x1100),
            (4, 0xE0001),
        ):
            image = bytearray(nrf_image())
            struct.pack_into("<I", image, offset, value)
            with self.assertRaises(ValueError):
                inspect_package(nrf_zip(bytes(image)), version="0.12")
        for image in (
            bytes(512),
            nrf_image().replace(b"nordic-pca10059", b"nordic-otherxx"),
            nrf_image() + bytes(0xE0000),
        ):
            with self.assertRaises(ValueError):
                inspect_package(nrf_zip(image), version="0.12")

    def test_zip_traversal_duplicate_extra_and_bootloader_manifest_are_rejected(self):
        """Only the exact application manifest and its two files are accepted."""
        invalid = [
            nrf_zip(extra=[("app.bin", nrf_image())]),
            nrf_zip(extra=[("../x", b"x")]),
            nrf_zip(manifest=b'{"manifest":{"bootloader":{}}}'),
            nrf_zip(manifest=b'{"manifest":{},"manifest":{}}'),
            nrf_zip(
                manifest=b'{"manifest":{"application":{"bin_file":"../app.bin","dat_file":"app.dat"}}}'
            ),
        ]
        for package in invalid:
            with self.assertRaises(ValueError):
                inspect_package(package, version="0.12")

    def test_file_inspection_binds_immutable_bytes_and_rejects_fifo_symlink(self):
        """Local inspection is bounded and does not substitute changed files afterward."""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "app.zip"
            data = nrf_zip()
            path.write_bytes(data)
            candidate = inspect_artifact(
                path, board="nordic-pca10059", version="0.12", operation="recovery"
            )
            path.write_bytes(b"changed")
            self.assertEqual(candidate["_data"], data)
            self.assertEqual(candidate["sha256"], hashlib.sha256(data).hexdigest())
            link = Path(directory) / "link"
            link.symlink_to(path)
            pipe = Path(directory) / "pipe"
            os.mkfifo(pipe)
            for bad in (link, pipe, Path("relative.zip")):
                with self.assertRaises(BundleError):
                    inspect_artifact(bad, board="nordic-pca10059", version="0.12")
