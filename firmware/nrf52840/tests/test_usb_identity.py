"""Reject unsafe USB release identities before an application can be built."""

from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class USBIdentityTests(unittest.TestCase):
    def compile_identity(self, defines=()):
        """Compile the production identity guards and read the chosen numeric pair.

        @param defines Explicit C preprocessor assignments for this test scenario.
        @return Compiler exit status, diagnostic text and successful program output.
        """
        with tempfile.TemporaryDirectory() as directory:
            binary = Path(directory) / "identity"
            result = subprocess.run(
                [
                    "cc",
                    "-std=c11",
                    "-Wall",
                    "-Wextra",
                    "-Werror",
                    "-I",
                    str(ROOT),
                    *["-D" + define for define in defines],
                    "-x",
                    "c",
                    "-",
                    "-o",
                    str(binary),
                ],
                input=(
                    '#include "usb-identity.h"\n#include <stdio.h>\n'
                    'int main(void) { printf("%04x:%04x", '
                    "(unsigned)EC_USB_VID, (unsigned)EC_USB_PID); return 0; }\n"
                ),
                text=True,
                capture_output=True,
            )
            output = ""
            if result.returncode == 0:
                output = subprocess.check_output([str(binary)], text=True)
            return result.returncode, result.stderr, output

    def test_development_defaults_and_explicit_release_inputs(self):
        """Keep existing development enumeration while accepting explicit numeric IDs."""
        self.assertEqual(self.compile_identity(), (0, "", "cafe:4001"))
        # Synthetic compile-only values, not an allocation or distributable identity.
        self.assertEqual(
            self.compile_identity(
                ("EC_USB_RELEASE=1", "EC_USB_VID=0x1234", "EC_USB_PID=0x5678")
            ),
            (0, "", "1234:5678"),
        )

    def test_invalid_or_unqualified_identities_fail_at_compile_time(self):
        """Reject development release IDs, DFU impersonation and incomplete shared IDs."""
        for defines in (
            ("EC_USB_RELEASE=1",),
            ("EC_USB_RELEASE=1", "EC_USB_PID=0x1234"),
            ("EC_USB_RELEASE=2",),
            ("EC_USB_VID=0",),
            ("EC_USB_VID=0xffff",),
            ("EC_USB_VID=0x10000",),
            ("EC_USB_PID=-1",),
            ("EC_USB_PID=0x10000",),
            ("EC_USB_VID=0x1915", "EC_USB_PID=0x521f"),
            ("EC_USB_VID=0x16c0", "EC_USB_PID=0x27dd"),
        ):
            with self.subTest(defines=defines):
                status, errors, output = self.compile_identity(defines)
                self.assertNotEqual(status, 0)
                self.assertIn("error:", errors)
                self.assertEqual(output, "")
