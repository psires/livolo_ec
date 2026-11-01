"""Reject unsafe release artifacts before an updater can own the radio."""

import binascii
import hashlib
import json
import os
from pathlib import Path
import struct
import tempfile
import unittest

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from livolod.firmware_bundle import BundleError, load_candidate, select_candidate
from livolod.update_package import BOARD, FORMAT, HEADER


def mg21_image(board="sonoff-zbdongle-e", version="0.12", gateway=True):
    """Build synthetic vectors/properties and compiled identity markers.

    @param board Board marker to embed, or None for a legacy unspecified image.
    @param version Compiled major.minor release.
    @param gateway Whether to include the gateway banner rather than sniffer-only identity.
    @return Word-aligned synthetic application bytes; never deployable firmware.
    """
    image = bytearray(1024)
    struct.pack_into("<II", image, 0, 0x20010000, 0x4101)
    struct.pack_into("<I", image, 52, 0x4080)
    image[128:144] = bytes.fromhex("13b779fac925ddb7adf3cfe0f1b614b8")
    struct.pack_into("<III", image, 144, 0x201, 0, 0)
    major, minor = map(int, version.split("."))
    struct.pack_into("<III", image, 156, 4, (major << 16) | minor, 0)
    image[168:184] = b"LIVOLO-MG21".ljust(16, b"\0")
    markers = f"EC-FW-VERSION={version}\0"
    if board:
        markers += f"EC-MG21-BOARD={board}\0"
    if gateway:
        markers += f"EC-GATEWAY 3 board={board} baud=%u\0"
    image[300 : 300 + len(markers)] = markers.encode()
    return bytes(image)


def gbl(image, address=0x4000, extra=b""):
    """Encode a fixture container whose CRC remains valid even for wrong targets.

    @param image Synthetic application bytes.
    @param address Program destination used to exercise protected-range rejection.
    @param extra Extra tag bytes inserted before the final CRC tag.
    @return Complete GBL bytes with a valid whole-file CRC residue.
    """
    values = (
        (0x03A617EB, struct.pack("<II", 0x03000000, 0)),
        (0xF40A0AF4, image[156:184]),
        (0xFE0101FE, struct.pack("<I", address) + image),
    )
    data = b"".join(
        struct.pack("<II", tag, len(value)) + value for tag, value in values
    )
    data += extra + struct.pack("<II", 0xFC0404FC, 4)
    return data + struct.pack("<I", binascii.crc32(data))


def ti_package(version="0.12"):
    """Create an ephemeral-key signed fixture with TI application identity.

    @param version Compiled and signed major.minor release.
    @return Signed bytes and their raw public verification key; never a release key.
    """
    image = bytearray(512)
    struct.pack_into("<II", image, 0, 0x20014000, 0x8101)
    marker = (
        f"EC-FW-VERSION={version}\0EC-GATEWAY 3 board=codm-cc2652p baud=%u\0".encode()
    )
    image[100 : 100 + len(marker)] = marker
    major, minor = map(int, version.split("."))
    header = HEADER.pack(
        b"ECUPD01\0",
        FORMAT,
        BOARD,
        major,
        minor,
        3,
        0x8000,
        len(image),
        0,
        hashlib.sha512(image).digest(),
        bytes(24),
    )
    key = Ed25519PrivateKey.generate()
    public = key.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    return header + key.sign(header) + image, public


class BundleTests(unittest.TestCase):
    def setUp(self):
        """Create a complete synthetic local bundle without external artifacts."""
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.entry = dict(
            id="sonoff-0.12",
            board="sonoff-zbdongle-e",
            role="gateway",
            version="0.12",
            protocol=3,
            backend="mg21_gecko_gbl_v1",
            path="image.gbl",
            app_baudrate=460800,
            layout="mg21-app-4000-30000",
            qualified=True,
            qualification="qualification.md",
            source_commit="a" * 40,
            notices=["NOTICE"],
            bootloader_baudrate=115200,
        )
        (self.root / "qualification.md").write_text("Synthetic test fixture only")
        (self.root / "NOTICE").write_text("Test fixture notices")
        self.write(gbl(mg21_image()))

    def write(self, data=None):
        """Persist fixture metadata, optionally replacing its hash-bound payload.

        @param data New artifact bytes, or None to retain the existing digest.
        """
        if data is not None:
            (self.root / "image.gbl").write_bytes(data)
            self.entry.update(size=len(data), sha256=hashlib.sha256(data).hexdigest())
        (self.root / "manifest.json").write_text(
            json.dumps(dict(schema=1, host_version="0.12", artifacts=[self.entry]))
        )

    def candidate(self):
        """Select the exact Sonoff fixture through the production validator.

        @return Private validated candidate dictionary.
        """
        return select_candidate(
            self.root,
            board=self.entry["board"],
            host_version="0.12",
            app_baudrate=460800,
        )

    def test_hash_bound_identity_and_maximum_client_identifier(self):
        """A 96-character artifact name produces a 161-character hash-bound ID."""
        self.entry["id"] = "x" * 96
        self.write()
        candidate = self.candidate()
        self.assertEqual(len(candidate["id"]), 161)
        loaded = load_candidate(
            self.root, candidate["id"], host_version="0.12", app_baudrate=460800
        )
        self.assertEqual(loaded["_data"], candidate["_data"])
        with self.assertRaises(BundleError):
            load_candidate(
                self.root,
                candidate["id"][:-1] + "z",
                host_version="0.12",
                app_baudrate=460800,
            )
        self.entry["id"] += "x"
        self.write()
        with self.assertRaises(BundleError):
            self.candidate()

    def test_no_bundle_no_candidate_and_exact_host_rate(self):
        """Missing artifacts, incompatible host and changed baud never offer installs."""
        for changes, code in (
            ({"bundle_dir": self.root / "missing"}, "bundle_missing"),
            ({"board": "nordic-pca10059"}, "artifact_missing"),
            ({"board": None}, "board_identity_unknown"),
            ({"board": ["sonoff-zbdongle-e"]}, "unsupported_board"),
            ({"host_version": "0.13"}, "incompatible_host"),
            ({"app_baudrate": 115200}, "incompatible_baudrate"),
        ):
            values = dict(
                bundle_dir=self.root,
                board=self.entry["board"],
                host_version="0.12",
                app_baudrate=460800,
            )
            values.update(changes)
            with self.subTest(code=code), self.assertRaises(BundleError) as caught:
                select_candidate(**values)
            self.assertEqual(caught.exception.code, code)

    def test_modified_payload_is_rejected_even_with_unchanged_candidate_id(self):
        """Valid metadata cannot authorize payload bytes changed on disk."""
        (self.root / "image.gbl").write_bytes(bytes(self.entry["size"]))
        with self.assertRaises(BundleError) as caught:
            self.candidate()
        self.assertEqual(caught.exception.code, "artifact_invalid")

    def test_excessive_json_nesting_is_reported_as_invalid_bundle(self):
        """A bounded but deeply nested manifest produces a public validation error."""
        (self.root / "manifest.json").write_text("[" * 2000 + "0" + "]" * 2000)
        with self.assertRaises(BundleError) as caught:
            self.candidate()
        self.assertEqual(caught.exception.code, "bundle_invalid")

    def test_named_pipe_is_rejected_without_waiting_for_a_writer(self):
        """A nonregular bundle file cannot block the daemon's validation path."""
        self.entry["path"] = "pipe.gbl"
        os.mkfifo(self.root / "pipe.gbl")
        self.write()
        with self.assertRaises(BundleError):
            self.candidate()

    def test_confined_paths_symlinks_and_required_notices(self):
        """Payload and supporting-file paths cannot escape or silently disappear."""
        for path in (
            "../image.gbl",
            "/tmp/image.gbl",
            "./image.gbl",
            "dir//image.gbl",
            "dir\\image.gbl",
        ):
            self.entry["path"] = path
            self.write()
            with self.subTest(path=path), self.assertRaises(BundleError):
                self.candidate()
        self.entry["path"] = "link.gbl"
        (self.root / "link.gbl").symlink_to(self.root / "image.gbl")
        self.write()
        with self.assertRaises(BundleError):
            self.candidate()
        self.entry["path"] = "image.gbl"
        self.entry["notices"] = []
        self.write()
        with self.assertRaises(BundleError):
            self.candidate()

    def test_ambiguous_metadata_and_duplicate_json_fields_fail_closed(self):
        """Duplicate keys, IDs or board candidates cannot influence selection order."""
        for text in (
            '{"schema":1,"schema":1,"host_version":"0.12","artifacts":[]}',
            json.dumps(
                dict(schema=1, host_version="0.12", artifacts=[self.entry, self.entry])
            ),
        ):
            (self.root / "manifest.json").write_text(text)
            with self.assertRaises(BundleError):
                self.candidate()

    def test_valid_crc_does_not_authorize_wrong_board_sniffer_or_protected_flash(self):
        """Identity and address checks reject dangerous GBLs despite valid checksums."""
        for data in (
            gbl(mg21_image("zb-gw04-1v2")),
            gbl(mg21_image(None)),
            gbl(mg21_image(gateway=False)),
            gbl(mg21_image(version="0.11")),
            gbl(mg21_image(), address=0),
            gbl(mg21_image(), extra=struct.pack("<II", 0xF50909F5, 0)),
            gbl(mg21_image()) + b"trailing",
        ):
            self.write(data)
            with self.subTest(size=len(data)), self.assertRaises(BundleError):
                self.candidate()

    def test_both_mg21_board_profiles_validate_separately(self):
        """Shared chip family does not collapse the two board identities."""
        self.entry["board"] = "zb-gw04-1v2"
        self.write(gbl(mg21_image("zb-gw04-1v2")))
        self.assertEqual(self.candidate()["board"], "zb-gw04-1v2")

    def test_ti_signature_key_and_compiled_identity_are_all_checked(self):
        """Only a matching signed image and explicitly fingerprinted key are accepted."""
        data, public = ti_package()
        (self.root / "public.key").write_bytes(public)
        self.entry.update(
            board="codm-cc2652p",
            backend="ti_uart_signed_v1",
            layout="ti-app-8000-20000",
            public_key="public.key",
            public_key_sha256=hashlib.sha256(public).hexdigest(),
            bootloader_min="0.2",
        )
        self.write(data)
        candidate = self.candidate()
        self.assertEqual(
            candidate["image_sha512"], hashlib.sha512(data[192:]).hexdigest()
        )
        self.assertTrue(candidate["rollback_supported"])
        bad = bytearray(data)
        bad[140] ^= 1
        self.write(bytes(bad))
        with self.assertRaises(BundleError):
            self.candidate()
        self.write(data)
        (self.root / "public.key").write_bytes(bytes(32))
        with self.assertRaises(BundleError) as caught:
            self.candidate()
        self.assertEqual(caught.exception.code, "signing_key_mismatch")

    def test_nordic_cannot_be_enabled_by_manifest_flags(self):
        """A declared qualified Nordic entry still cannot bypass the absent backend."""
        self.entry.update(
            board="nordic-pca10059",
            backend="nrf52840_usb_dfu_v1",
            layout="nrf-app-1000-e0000",
        )
        self.write()
        with self.assertRaises(BundleError) as caught:
            self.candidate()
        self.assertEqual(caught.exception.code, "backend_unavailable")
