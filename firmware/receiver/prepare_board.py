#!/usr/bin/env python3
"""Generate a cod.m board adapter from the pinned, licensed upstream P2 BSP."""
from pathlib import Path
import shutil
import subprocess
import sys

PINNED = "7a4df8a941d552b10c730db70e934a9be10937af"
root = Path(sys.argv[1]).resolve()
revision = subprocess.check_output(
    ["git", "-C", str(root), "rev-parse", "HEAD"], text=True
).strip()
if revision != PINNED:
    raise SystemExit(f"Expected Contiki {PINNED}, got {revision}")
base = root / "arch/platform/simplelink/cc13xx-cc26xx/launchpad"
src, dst = base / "cc1352p-2", base / "codm-cc2652p"
dst.mkdir(exist_ok=True)
for path in src.iterdir():
    if path.is_file():
        shutil.copy2(path, dst / path.name)
make = (dst / "Makefile.cc1352p-2").read_text()
# Keep the upstream CC1352P compatibility define: it selects the shared x2 PA
# radio settings/libraries used by this CC2652P module. Never enable sub-GHz.
make = make.replace("SUPPORTS_PROP_MODE  = 1", "SUPPORTS_PROP_MODE  = 0")
make = make.replace("SUPPORTS_BLE_BEACON = 1", "SUPPORTS_BLE_BEACON = 0")
make += "\nBOARD_SOURCEFILES += CC2652P_PIN.c\n"
(dst / "Makefile.codm-cc2652p").write_text(make)
(dst / "Makefile.cc1352p-2").unlink()
fxns = dst / "CC1352P_2_LAUNCHXL_fxns.c"
text = fxns.read_text()
old = "    CC1352P_2_LAUNCHXL_shutDownExtFlash();"
assert text.count(old) == 1
fxns.write_text(
    text.replace(old, "    /* cod.m module has no LaunchPad external flash. */")
)
board = dst / "CC1352P_2_LAUNCHXL.c"
text = board.read_text()
start = text.index("const PIN_Config BoardGpioInitTable[] = {")
end = text.index("};", start)
block = text[start:end]
block = "\n".join(
    line
    for line in block.split("\n")
    if not any(
        token in line
        for token in ("_SPI_FLASH_CS |", "_SPI0_MOSI |", "_SPI0_MISO |", "_SPI0_CLK |")
    )
)
board.write_text(text[:start] + block + text[end:])
# This old SDK identifies a CC2652P as CC2652, missing the five reserved
# RF pins. Compile the licensed pin driver with the narrow chip-specific fix.
sdk = root / "arch/cpu/simplelink-cc13xx-cc26xx/lib/coresdk_cc13xx_cc26xx"
pin_source = (sdk / "source/ti/drivers/pin/PINCC26XX.c").read_text()
switch = "    switch (ChipInfo_GetChipType()) {"
assert pin_source.count(switch) == 1
pin_source = pin_source.replace(switch, switch + "\n        default: break;")
marker = "    // Get the last addressable DIO number"
assert pin_source.count(marker) == 1
fix = """    /* CC2652P shares the P-family DIO5..30 GPIO layout. The bundled
     * ChipInfo implementation predates its chip-type enumeration. Restrict
     * this correction to the factory identity verified on this module. */
    if (HWREG(FCFG1_BASE + FCFG1_O_USER_ID) == 0x3202F000 &&
        ((HWREG(FCFG1_BASE + FCFG1_O_ICEPICK_DEVICE_ID) >> 12) & 0xFFFF) == 0xBB41) {
        pinLowerBound = 5;
        reservedPinMask |= 0x1F;
    }

"""
(dst / "CC2652P_PIN.c").write_text(pin_source.replace(marker, fix + marker))
print(dst)
