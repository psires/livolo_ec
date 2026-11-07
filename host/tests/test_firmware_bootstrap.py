"""Reject unsafe TI bootstrap containers without mistaking structure for trust."""

import hashlib
import struct
import unittest

from livolod.firmware_bootstrap import inspect_ti_bootloader


KEY = bytes(range(32))


def bootloader(size=256):
    """Create a structurally plausible but nonexecutable test image.

    @param size Word-aligned raw fixture size.
    @return Immutable bytes with vectors, supported marker and one test public key.
    """
    data = bytearray(b"\xff" * size)
    struct.pack_into("<II", data, 0, 0x20014000, 0x19)
    data[8:20] = b"EC-BOOT 0.2\0"
    data[64:96] = KEY
    return bytes(data)


class BootstrapTests(unittest.TestCase):
    def test_supported_layout_returns_bounded_identity_without_trust_claim(self):
        """Inspect minimum and maximum raw layouts, retaining explicit structural scope."""
        for size in (256, 0x8000):
            with self.subTest(size=size):
                data = bootloader(size)
                result = inspect_ti_bootloader(data, public_key=KEY)
                self.assertEqual(result["board"], "codm-cc2652p")
                self.assertEqual(result["role"], "bootloader")
                self.assertEqual(result["version"], "0.2")
                self.assertEqual(result["sha256"], hashlib.sha256(data).hexdigest())
                self.assertEqual(
                    result["public_key_sha256"], hashlib.sha256(KEY).hexdigest()
                )
                self.assertEqual(result["public_key_offset"], 64)
                self.assertEqual(result["flash_start"], 0)
                self.assertEqual(result["flash_end"], size)
                self.assertEqual(result["bytes"], size)
                self.assertEqual(result["application_base"], 0x8000)
                self.assertEqual(result["initial_sp"], 0x20014000)
                self.assertEqual(result["reset_vector"], 0x19)
                self.assertEqual(result["validation"], "structure_only")
                self.assertNotIn("qualified", result)

    def test_factory_and_other_container_sizes_are_rejected(self):
        """Full flash/CCFG, slot-sized, truncated and mutable inputs cannot enter the gate."""
        for data in (
            b"",
            bootloader()[:252],
            bootloader() + b"\0",
            bootloader(0x8004),
            bootloader(0x58000),
            bytearray(bootloader()),
            "not bytes",
        ):
            with self.subTest(size=len(data)), self.assertRaises(ValueError):
                inspect_ti_bootloader(data, public_key=KEY)

    def test_exact_version_marker_and_location_are_required(self):
        """A substring elsewhere or conflicting release text cannot identify the layout."""
        original = bootloader()
        for data in (
            original.replace(b"EC-BOOT 0.2\0", b"EC-BOOT 0.1\0"),
            original.replace(b"EC-BOOT 0.2\0", b"EC-BOOT 0.20"),
            original[:8]
            + b"\xff" * 12
            + original[20:128]
            + b"EC-BOOT 0.2\0"
            + original[140:],
            original[:128] + b"EC-BOOT 0.2\0" + original[140:],
        ):
            with self.subTest(marker=data[8:20]), self.assertRaises(ValueError):
                inspect_ti_bootloader(data, public_key=KEY)
        for version in ("0.1", "0.3", "0.2.0", "00.2", None, True):
            with self.subTest(version=version), self.assertRaises(ValueError):
                inspect_ti_bootloader(original, public_key=KEY, version=version)

    def test_vectors_must_target_real_boot_bytes_and_exact_sram_layout(self):
        """Reject non-Thumb, metadata, truncated-code and application-slot entry points."""
        for stack, reset in (
            (0x20000000, 0x19),
            (0x20010000, 0x19),
            (0x20014004, 0x19),
            (0x20014000, 0x18),
            (0x20014000, 1),
            (0x20014000, 9),
            (0x20014000, 0x101),
            (0x20014000, 0x8001),
            (0x20014000, 0xFFFFFFFF),
        ):
            data = bytearray(bootloader())
            struct.pack_into("<II", data, 0, stack, reset)
            with self.subTest(stack=stack, reset=reset), self.assertRaises(ValueError):
                inspect_ti_bootloader(bytes(data), public_key=KEY)

    def test_selected_key_must_be_exact_unambiguous_and_outside_metadata(self):
        """Neither wrong keys, duplicate matches nor overlapping repeated bytes qualify."""
        for key in (b"", KEY[:-1], KEY + b"x", bytes(32), bytearray(KEY), KEY.hex()):
            with (
                self.subTest(key_type=type(key).__name__),
                self.assertRaises(ValueError),
            ):
                inspect_ti_bootloader(bootloader(), public_key=key)
        data = bytearray(bootloader())
        data[128:160] = KEY
        with self.assertRaises(ValueError):
            inspect_ti_bootloader(bytes(data), public_key=KEY)
        data[64:97] = b"a" * 33
        with self.assertRaises(ValueError):
            inspect_ti_bootloader(bytes(data), public_key=b"a" * 32)
        data = bootloader()
        with self.assertRaises(ValueError):
            inspect_ti_bootloader(data, public_key=data[:32])

    def test_plausible_modified_code_still_requires_independent_hash_review(self):
        """Structural acceptance deliberately cannot authenticate executable instructions."""
        original = bootloader()
        changed = bytearray(original)
        changed[32] ^= 1
        first = inspect_ti_bootloader(original, public_key=KEY)
        second = inspect_ti_bootloader(bytes(changed), public_key=KEY)
        self.assertEqual(first["version"], second["version"])
        self.assertEqual(first["public_key_sha256"], second["public_key_sha256"])
        self.assertNotEqual(first["sha256"], second["sha256"])
        self.assertEqual(second["validation"], "structure_only")
