"""Reject unsafe USB release identities before an application can be built."""

from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SHARED = (
    "EC_USB_RELEASE=1",
    "EC_USB_VID=0x16c0",
    "EC_USB_PID=0x27dd",
    "EC_USB_SHARED_LIVOLO=1",
)


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
            ("EC_USB_SHARED_LIVOLO=1",),
            ("EC_USB_SHARED_LIVOLO=2",),
            (*SHARED, 'EC_USB_SERIAL_PREFIX="unqualified:"'),
        ):
            with self.subTest(defines=defines):
                status, errors, output = self.compile_identity(defines)
                self.assertNotEqual(status, 0)
                self.assertIn("error:", errors)
                self.assertEqual(output, "")

    def test_approved_shared_identity(self):
        """Require the fixed namespace opt-in for the approved shared numeric pair."""
        self.assertEqual(self.compile_identity(SHARED), (0, "", "16c0:27dd"))

    def test_production_descriptors(self):
        """Exercise actual callbacks, stable serial ordering, language and bounds."""
        stub = """
#include <stdint.h>
#define TUSB_DESC_DEVICE 1
#define TUSB_DESC_STRING 3
#define TUSB_CLASS_MISC 239
#define MISC_SUBCLASS_COMMON 2
#define MISC_PROTOCOL_IAD 1
#define CFG_TUD_ENDPOINT0_SIZE 64
#define TUD_CONFIG_DESC_LEN 9
#define TUD_CDC_DESC_LEN 66
#define TUD_CONFIG_DESCRIPTOR(...) 0
#define TUD_CDC_DESCRIPTOR(...) 0
typedef struct {
 uint8_t bLength, bDescriptorType;
 uint16_t bcdUSB;
 uint8_t bDeviceClass, bDeviceSubClass, bDeviceProtocol, bMaxPacketSize0;
 uint16_t idVendor, idProduct, bcdDevice;
 uint8_t iManufacturer, iProduct, iSerialNumber, bNumConfigurations;
} tusb_desc_device_t;
"""
        harness = r"""
#include <assert.h>
#include <stdio.h>
#include <string.h>
#include "usb-descriptors.c"
struct ficr_stub ficr;
/** @brief Check descriptor identity and bounded serial conversion.
 * @return Zero after the assertions pass.
 */
int main(void) {
 const uint16_t *s = tud_descriptor_string_cb(0, 0);
 assert(s && s[0] == 0x0304 && s[1] == 0x0409);
 const tusb_desc_device_t *d = (const void *)tud_descriptor_device_cb();
 assert(d->idVendor == EC_USB_VID && d->idProduct == EC_USB_PID);
 assert(d->iSerialNumber == 3 && d->bDeviceClass == TUSB_CLASS_MISC);
 assert(tud_descriptor_configuration_cb(0));
 assert(!tud_descriptor_configuration_cb(1));
 assert(!tud_descriptor_string_cb(3, 0x0415));
 assert(!tud_descriptor_string_cb(5, 0x0409));
 uint32_t values[][2] = {{0xf452d37e, 0xf1bf2323}, {1, 2}, {0, 0},
                         {0xffffffff, 0xffffffff}};
 for (unsigned v = 0; v < 4; v++) {
  ficr.DEVICEID[0] = values[v][0]; ficr.DEVICEID[1] = values[v][1];
  s = tud_descriptor_string_cb(3, 0x0409);
  unsigned n = strlen(EC_USB_SERIAL_PREFIX) + 16;
  assert(s && (s[0] & 255) == 2 * n + 2 && (s[0] >> 8) == 3);
  for (unsigned i = 1; i <= n; i++) {assert(s[i] < 128); putchar(s[i]);}
  putchar('\n');
  for (unsigned i = 1; i <= 4; i++) {
   s = tud_descriptor_string_cb(i, 0x0409);
   assert(s && (s[0] & 255) <= 128);
  }
 }
 return 0;
}
"""
        suffixes = ["f1bf2323f452d37e", "0000000200000001", "0" * 16, "f" * 16]
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            (work / "tusb.h").write_text(stub)
            (work / "nrf.h").write_text(
                "#include <stdint.h>\n"
                "struct ficr_stub {uint32_t DEVICEID[2];};\n"
                "extern struct ficr_stub ficr;\n#define NRF_FICR (&ficr)\n"
            )
            (work / "harness.c").write_text(harness)
            for defines, prefix in (((), ""), (SHARED, "livolo.sfires.net:")):
                with self.subTest(prefix=prefix):
                    subprocess.run(
                        [
                            "cc",
                            "-std=c11",
                            "-Wall",
                            "-Wextra",
                            "-Werror",
                            "-fsanitize=address,undefined",
                            "-I",
                            str(work),
                            "-I",
                            str(ROOT),
                            *["-D" + d for d in defines],
                            str(work / "harness.c"),
                            "-o",
                            str(work / "descriptor"),
                        ],
                        capture_output=True,
                        text=True,
                        check=True,
                    )
                    output = subprocess.check_output(
                        [str(work / "descriptor")], text=True
                    )
                    self.assertEqual(
                        output.splitlines(), [prefix + s for s in suffixes]
                    )
