# 0.13 binary distribution review

Reviewed 2026-11-05 for the exact four images listed in
[the standalone trial record](../docs/FIRMWARE_013_TRIAL.md). This records
packaging findings and unresolved release conditions; it does not change the
project license or establish hardware qualification.

## Inputs and collected evidence

The private build record `.local/release-0.13/build-inputs.json` hashes 675
external dependency/header/runtime inputs. `dependency-notices.txt` retains
license-bearing comments from those compiled dependencies, root SDK/CMSIS
licenses, Debian GCC/newlib copyright inventories and GPLv3 with its GCC runtime
exception. Its bounded `dependency-notices-0.txt` and `-1.txt` copies are included
in the local catalog alongside the project license, Monocypher license and
upstream provenance, and `THIRD_PARTY_NOTICES.md`.

This is deliberately broader than the selected archive members. It preserves
notices without asserting that every header or libc component contributes code
to every image. The build does not distribute an SDK archive, flashing helper,
factory bootloader, stock recovery image or signing secret.

## Board-specific findings

| Images | Finding for a public trial download |
| --- | --- |
| TI CC2652P | Contiki/TI BSD notices, CMSIS Apache-2.0, Monocypher BSD-2-Clause-or-CC0, and runtime notices are collected. No hardware-vendor distribution prohibition was found in these inspected inputs. Carry the notices with the complete package; keep the private key separate. The bundled public key is the test deployment's identity, not an assertion that all user-built bootloaders trust it. |
| Nordic PCA10059 | TinyUSB MIT, nrfx/radio-driver BSD notices, CMSIS Apache-2.0, the CodeSourcery linker notice and the IRQ header exception are retained. The IRQ exception limits use to Nordic ICs and restricts supplied binary-code modification/reverse engineering. Therefore describe this as a PCA10059/Nordic-only image with mixed dependency terms, not an unrestricted BSD binary. The `cafe:4001` development USB identity remains a product-distribution limitation. |
| Both MG21 profiles | The exact RAIL release archive is explicitly covered by the pinned SDK's Zlib exception. Most inspected emlib/device/radio inputs are Zlib or Apache-2.0. Two bootloader-interface headers retain MSLA wording; those requirements need to be handled separately rather than applying the RAIL exception to the whole SDK. |

Primary input notices are linked in
[the dependency inventory](THIRD_PARTY_NOTICES.md). In particular, see the
[pinned Gecko SDK license](https://github.com/SiliconLabs/gecko_sdk/blob/59d2160fe448b13730a91c0f019a8b4c53c43eb2/License.txt)
for the archive exception and the
[Nordic IRQ header](https://github.com/NordicSemiconductor/nRF-IEEE-802.15.4-radio-driver/blob/0b972851eed5e1fddc6b9a6ac022fe46fe445ff4/src/platform/irq/nrf_802154_irq.h)
for the narrower Nordic terms.

## Concrete MG21 condition

The official MSLA page currently identifies version **20260804**. Sections
4.2.2 and 4.2.5 permit compilation and distribution for authorized Silicon Labs
applications. Sections 5.1.3–5.1.6 preserve vendor restrictions and notices;
5.4 requires downstream restrictions, and 7.4 addresses end-user license terms.
Thus the remaining question is how this project's download terms satisfy those
conditions for the MSLA-covered portions, including the treatment of a firmware
download for an existing third-party dongle. Merely attaching the BSD project
license is insufficient. Record the applicable agreement revision and resolve
that packaging question before a public MG21 binary release. This is not a
finding that all MG21 binary distribution is prohibited.
[Official MSLA](https://www.silabs.com/about-us/legal/master-software-license-agreement).

The local standalone test uses the existing SDK installation and does not
redistribute those binaries to the public. It can proceed independently of the
public-download decision.

## Compiler runtime and final release gate

The GCC Runtime Library Exception permits eligible compiler output combining
independent modules with covered runtime code without imposing the compiler's
GPL license on the whole application. These builds use the ordinary GCC
compile/link process; no proprietary intermediate-representation optimizer is
used. Keep the runtime exception and notices with the package, and do not
describe the compiler license as the firmware's sole license.
[GCC 14.2 runtime exception](https://github.com/gcc-mirror/gcc/blob/releases/gcc-14.2.0/COPYING.RUNTIME).
Newlib's collected per-component notices are retained rather than replaced by a
single invented license label.

Before publication, bind the frozen build source hashes to a reviewed commit,
retain exact package hashes and notices, and describe the actual trial scope.
Catalog install eligibility and public download availability are separate
decisions. A successful Nordic update trial must not automatically qualify
other boards' updater lifecycles or create a production firmware channel.
