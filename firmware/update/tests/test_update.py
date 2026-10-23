"""Execute the real C updater with NOR-flash semantics and power cuts."""

import ctypes as C
import hashlib
import json
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "tools"))
from firmware_package import sign_image, generate_keys, inspect_package, public_key

FLASH = 0x58000
APP = 0x8000
STAGE = 0x20000
META = 0x52000
JOURNAL = 0x50000


class UpdateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        """Build the real firmware core with deterministic platform test adapters."""
        cls.temp = tempfile.TemporaryDirectory()
        p = Path(cls.temp.name)
        generate_keys(p / "keys")
        cls.keys = p / "keys"
        wrapper = p / "flash.c"
        wrapper.write_text(
            """#include "ec-update.h"
#include <string.h>
#include "update-public-key.h"
const uint8_t ec_update_public_key[32] = EC_UPDATE_PUBLIC_KEY;
uint8_t flash[EC_FLASH_SIZE];
static int cut = -1, calls, torn;

/**
 * @brief Reset simulated transfer state and select a flash failure scenario.
 *
 * @param c Mutation count before failure, or -1 to disable failures.
 * @param t Whether failures partially modify flash.
 */
void configure(int c, int t)
{
	cut = c;
	calls = 0;
	torn = t;
	ec_update_abort();
}

/**
 * @brief Select a flash failure scenario without discarding transfer progress.
 *
 * @param c Mutation count before failure, or -1 to disable failures.
 * @param t Whether failures partially modify flash.
 */
void configure_cut(int c, int t)
{
	cut = c;
	calls = 0;
	torn = t;
}

/**
 * @brief Report counted flash mutations for exhaustive failure injection.
 *
 * @return Number of mutations counted while failure injection was enabled.
 */
int count(void)
{
	return calls;
}

/**
 * @brief Expose emulated flash bytes to the real updater.
 *
 * @param a First flash byte address.
 * @param n Number of bytes requested.
 *
 * @return Requested emulated flash range, or NULL when out of bounds.
 */
const uint8_t *ec_flash_read(uint32_t a, size_t n)
{
	return a + n <= EC_FLASH_SIZE ? flash + a : 0;
}

/**
 * @brief Satisfy the watchdog interface for host-side update tests.
 */
void ec_update_watchdog_feed(void)
{
}

/**
 * @brief Emulate a sector erase with optional power-cut damage.
 *
 * @param a Sector-aligned flash address.
 *
 * @return Zero on success, or -1 for a rejected or interrupted erase.
 */
int ec_flash_erase(uint32_t a)
{
	if (a < EC_APP || a >= EC_FLASH_SIZE - EC_SECTOR || a % EC_SECTOR)
		return -1;
	if (cut >= 0 && calls++ >= cut) {
		if (torn)
			memset(flash + a, 255, EC_SECTOR / 2);
		return -1;
	}
	memset(flash + a, 255, EC_SECTOR);
	return 0;
}

/**
 * @brief Emulate NOR programming with optional power-cut damage.
 *
 * @param a Word-aligned flash address.
 * @param p Bytes requested for programming.
 * @param n Word-aligned byte count.
 *
 * @return Zero on success, or -1 for a rejected or interrupted write.
 */
int ec_flash_program(uint32_t a, const uint8_t *p, size_t n)
{
	int fail;
	size_t limit;

	if (a < EC_APP || a + n > EC_FLASH_SIZE - EC_SECTOR || ((a | n) & 3))
		return -1;
	fail = cut >= 0 && calls++ >= cut;
	limit = fail ? (torn ? n / 2 : 0) : n;
	for (size_t i = 0; i < limit; i++) {
		if ((flash[a + i] & p[i]) != p[i])
			return -1;
		flash[a + i] &= p[i];
	}
	return fail ? -1 : 0;
}
"""
        )
        u = ROOT / "firmware/update"
        m = u / "third_party/monocypher"
        lib = p / "core.so"
        subprocess.run(
            [
                "cc",
                "-std=c99",
                "-O2",
                "-Wall",
                "-Wextra",
                "-Werror",
                "-fPIC",
                "-dynamiclib" if sys.platform == "darwin" else "-shared",
                "-I" + str(u),
                "-I" + str(cls.keys),
                str(wrapper),
                str(u / "ec-update.c"),
                str(m / "monocypher.c"),
                str(m / "monocypher-ed25519.c"),
                "-o",
                str(lib),
            ],
            check=True,
        )
        cls.lib = C.CDLL(str(lib))
        cls.flash = (C.c_ubyte * FLASH).in_dll(cls.lib, "flash")
        cls.lib.ec_update_begin.argtypes = [C.c_char_p]
        cls.lib.ec_update_write.argtypes = [C.c_uint32, C.c_char_p, C.c_size_t]
        cls.lib.ec_manifest_valid.argtypes = [C.c_char_p]
        cls.lib.ec_update_state.restype = C.c_uint32
        cls.old = cls.package("0.1", 17)
        cls.new = cls.package("0.2", 39)

    @classmethod
    def package(cls, v, fill):
        """Create an authenticated fixture image for a requested firmware version.

        @param v Firmware version string to embed and sign.
        @param fill Byte value used to fill the synthetic image.

        @return Signed manifest followed by image bytes."""
        image = bytearray([fill] * 1024)
        struct.pack_into("<II", image, 0, 0x20014000, APP + 257)
        marker = b"EC-FW-VERSION=" + v.encode() + b"\0"
        image[80 : 80 + len(marker)] = marker
        return sign_image(bytes(image), v, cls.keys / "signing.pem")

    def setUp(self):
        """Restore clean firmware state before each independent test."""
        self.flash[:] = b"\xff" * FLASH
        self.flash[APP : APP + len(self.old) - 192] = self.old[192:]
        self.flash[META : META + 192] = self.old[:192]
        self.lib.configure(-1, 0)

    def stage(self):
        """Transfer the complete candidate image into the staging slot."""
        self.assertEqual(self.lib.ec_update_begin(self.new[:192]), 0)
        data = self.new[192:]
        for offset in range(0, len(data), 96):
            block = data[offset : offset + 96]
            self.assertEqual(self.lib.ec_update_write(offset, block, len(block)), 0)

    def snapshot(self):
        """Capture persistent flash state for later power-cut scenarios.

        @return Immutable copy of the emulated flash."""
        return bytes(self.flash)

    def restore(self, data):
        """Restore saved flash state and clear simulated transient failures.

        @param data Complete flash snapshot to restore."""
        self.flash[:] = data
        self.lib.configure(-1, 0)

    def assert_bootable(self):
        """Require recovery to produce either the previous or candidate image."""
        self.lib.configure(-1, 0)
        self.assertEqual(self.lib.ec_update_boot(), 1)
        self.assertIn(
            bytes(self.flash[APP : APP + 1024]), (self.old[192:], self.new[192:])
        )

    def test_signed_package_and_version_checks(self):
        """Reject altered manifests and upgrades that do not advance the version."""
        self.assertEqual(
            inspect_package(self.new, public_key(self.keys / "public.key"))["version"],
            "0.2",
        )
        for offset in (0, 12, 24, 28, 32, 40, 104, 128, 191):
            bad = bytearray(self.new[:192])
            bad[offset] ^= 1
            self.assertEqual(self.lib.ec_manifest_valid(bytes(bad)), 0)
        self.assertNotEqual(self.lib.ec_update_begin(self.old[:192]), 0)

    def test_transfer_bounds_replay_corruption(self):
        """Enforce transfer bounds, identical retries, and candidate integrity."""
        self.stage()
        self.assertEqual(self.lib.ec_update_write(0, self.new[192:288], 96), 0)
        self.assertNotEqual(self.lib.ec_update_write(0, b"\0" * 96, 96), 0)
        self.assertNotEqual(self.lib.ec_update_write(0x18000, b"\0" * 4, 4), 0)
        self.flash[STAGE + 800] ^= 1
        self.assertNotEqual(self.lib.ec_update_finish(), 0)
        self.assert_bootable()
        self.assertEqual(bytes(self.flash[APP : APP + 1024]), self.old[192:])

    def test_trial_confirm_and_reset_rollback(self):
        """Keep confirmed upgrades and restore the old image after an unconfirmed reset."""
        self.stage()
        self.assertEqual(self.lib.ec_update_finish(), 0)
        committed = self.snapshot()
        self.assertEqual(self.lib.ec_update_boot(), 1)
        self.assertEqual(self.lib.ec_update_state(), 0xFFFFFFF8)
        self.assertEqual(bytes(self.flash[APP : APP + 1024]), self.new[192:])
        self.assertEqual(self.lib.ec_update_boot(), 1)
        self.assertEqual(bytes(self.flash[APP : APP + 1024]), self.old[192:])
        self.restore(committed)
        self.assertEqual(self.lib.ec_update_boot(), 1)
        self.assertEqual(self.lib.ec_update_confirm(), 0)
        self.assertEqual(self.lib.ec_update_boot(), 1)
        self.assertEqual(bytes(self.flash[APP : APP + 1024]), self.new[192:])

    def test_confirmation_power_cut_preserves_old_or_confirmed_new(self):
        """Require interrupted confirmation to leave either the old or confirmed image bootable."""
        self.stage()
        self.assertEqual(self.lib.ec_update_finish(), 0)
        self.assertEqual(self.lib.ec_update_boot(), 1)
        trial = self.snapshot()
        for torn in (0, 1):
            self.restore(trial)
            self.lib.configure(0, torn)
            self.lib.ec_update_confirm()
            self.assert_bootable()

    def test_interrupted_transfer_keeps_current_bootable(self):
        """Require interrupted staging to preserve the active application."""
        base = self.snapshot()
        for cut in range(13):
            for torn in (0, 1):
                self.restore(base)
                self.lib.configure(cut, torn)
                self.lib.ec_update_begin(self.new[:192])
                self.assert_bootable()
                self.assertEqual(bytes(self.flash[APP : APP + 1024]), self.old[192:])

    def test_power_cut_during_each_backup_and_commit_mutation(self):
        """Require recovery after every possible backup or commit power cut."""
        self.stage()
        base = self.snapshot()
        # Count operations without taking a power cut; a high cut enables counting.
        self.lib.configure(10000, 0)
        self.stage()
        self.lib.configure(10000, 0)
        # configure resets volatile transfer state, so restage before counting finish.
        self.stage()
        prior = self.lib.count()
        self.assertEqual(self.lib.ec_update_finish(), 0)
        operations = self.lib.count() - prior
        for torn in (0, 1):
            for cut in range(operations + 1):
                self.restore(base)
                self.stage()
                # Change fault controls without clearing the transfer state.
                self.lib.configure_cut(cut, torn)
                self.lib.ec_update_finish()
                self.assert_bootable()

    def test_power_cut_during_each_install_and_rollback_mutation(self):
        """Require recovery after every possible installation or rollback power cut."""
        self.stage()
        self.assertEqual(self.lib.ec_update_finish(), 0)
        committed = self.snapshot()
        for phase in ("install", "rollback"):
            if phase == "rollback":
                self.restore(committed)
                self.assertEqual(self.lib.ec_update_boot(), 1)
            base = self.snapshot()
            self.lib.configure(10000, 0)
            self.assertEqual(self.lib.ec_update_boot(), 1)
            operations = self.lib.count()
            for torn in (0, 1):
                for cut in range(operations + 1):
                    self.restore(base)
                    self.lib.configure(cut, torn)
                    self.lib.ec_update_boot()
                    self.assert_bootable()


if __name__ == "__main__":
    unittest.main()
