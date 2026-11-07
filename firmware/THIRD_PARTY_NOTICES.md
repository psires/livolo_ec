# Firmware third-party inputs and notices

This inventory covers release **0.12**, its TI bootloader **0.2**, the passive
receiver/sniffer targets, and the separately identified experimental XIAO inputs.
Project-owned code uses the repository's
[BSD-2-Clause licence](../LICENSE). That licence does not replace the terms of
the dependencies below. Preserve existing copyright and licence headers.

The SDKs are fetched separately and are not included in this source tree.
Monocypher is the exception: its unmodified sources, full licences and provenance
are checked in. This document records the build inputs and known exceptions;
it is **not a complete binary-distribution licence bundle**. A binary release
must carry the applicable notices from its exact SDK/toolchain inputs, including
per-file copyright holders. The unresolved packaging work is listed below.

## TI CC2652P gateway, receiver and bootloader

| Input | Pin and use | Licence / retained notices |
| --- | --- | --- |
| [Contiki-NG](https://github.com/contiki-ng/contiki-ng/tree/7a4df8a941d552b10c730db70e934a9be10937af) | `7a4df8a941d552b10c730db70e934a9be10937af`; OS, simplelink platform, raw radio driver and board support for gateway/receiver | Root `LICENSE.md` is BSD-3-Clause. Individual sources name their own holders, including SICS/RISE, Adam Dunkels, George Oikonomou, universities and other contributors; retain those notices rather than substituting the root template's placeholder copyright. |
| TI Core SDK | Contiki submodule `arch/cpu/simplelink-cc13xx-cc26xx/lib/coresdk_cc13xx_cc26xx`, commit `60e7325e122e95660fa9a4019e7868aedb4c317d`; TI drivers, device headers, startup, NoRTOS and radio support | Root `LICENSE`: BSD-3-Clause, Copyright (c) 2018-2019 Texas Instruments Incorporated. Source headers carry additional TI years and notices. |
| TI driverlib archive | Same SDK; `source/ti/devices/cc13x2_cc26x2/driverlib/bin/gcc/driverlib.lib`, explicitly linked by the bootloader and used by the platform build | TI SDK licence and archive provenance must accompany a binary build. It is a prebuilt input, not project-owned source. |
| [CMSIS 5](https://github.com/ARM-software/CMSIS_5/tree/2b7495b8535bdcb306dac29b9ded4cfb679d7e5c) | Contiki `arch/cpu/arm/CMSIS`, commit `2b7495b8535bdcb306dac29b9ded4cfb679d7e5c`; ARM core interfaces | Apache-2.0; retain `LICENSE.txt` and applicable ARM copyright/NOTICE files. |
| Monocypher 4.0.3 | Signed-update builds and TI bootloader; public-key verification and SHA-512 | BSD-2-Clause **or** CC0. Full [LICENCE.md](update/third_party/monocypher/LICENCE.md) and [UPSTREAM.txt](update/third_party/monocypher/UPSTREAM.txt) remain with unmodified sources. Copyright holders include Loup Vaillant, Michael Savage and Fabio Scotoni. |

`receiver/prepare_board.py` creates a board adapter from the pinned CC1352P-2
support files and patches the TI PIN implementation for the verified CC2652P.
It retains the upstream notices. These generated files stay in the external
Contiki checkout; they are not relicensed as project-only code. The compatibility
define does not make this a CC2652P7 target.

The Monocypher source archive is
`https://monocypher.org/download/monocypher-4.0.3.tar.gz`, SHA-512:

```text
40904ada5c7ee4f7741733e38b69a30a4b0561cbffba5ffe7c2dce16136d540251ec0d9056ff606510d3b5b708fb8a40db7e0870d4a0b2dc17ba2bfb880f8965
```

## EFR32MG21 gateway and passive sniffer

Both targets use [Gecko SDK 4.4.6](https://github.com/SiliconLabs/gecko_sdk/tree/59d2160fe448b13730a91c0f019a8b4c53c43eb2),
revision `59d2160fe448b13730a91c0f019a8b4c53c43eb2`.

| Linked or included input | Licence evidence |
| --- | --- |
| EFR32MG21 device/system headers and system source; emlib CMU, EMU, GPIO, USART, LDMA, core, MSC and system sources; common headers | The inspected Silicon Labs files are marked `SPDX-License-Identifier: Zlib`, with file-specific copyright years. |
| RAIL public headers and PA conversion/curve sources | Inspected files are Zlib, Copyright Silicon Laboratories Inc.; preserve each file's notice. |
| `platform/radio/rail_lib/autogen/librail_release/librail_efr32xg21_gcc_release.a` | The SDK's [License.txt](https://github.com/SiliconLabs/gecko_sdk/blob/59d2160fe448b13730a91c0f019a8b4c53c43eb2/License.txt) explicitly assigns archives under this directory to **Zlib**, despite the SDK's general MSLA default. |
| `platform/Device/SiliconLabs/EFR32MG21/Source/startup_efr32mg21.c` | ARM Apache-2.0 notice, Copyright (c) 2009-2021 Arm Limited, preceded by Silicon Labs' MSLA third-party-software wording. Preserve both notices; do not label the whole SDK Zlib. |
| `platform/bootloader/api/application_properties.h` and `btl_reset_info.h` | Copyright 2021 Silicon Laboratories Inc.; explicitly governed by the **MSLA source-code terms**, without a Zlib exception in those headers. These headers are used by both MG21 targets. |
| `platform/CMSIS/Core/Include` | ARM Apache-2.0 headers; the SDK contains `platform/CMSIS/LICENSE.txt`. |
| `firmware/mg21/application.ld` | Adapted Silicon Labs linker script, marked as modified for this project; the original Copyright 2024 and full Zlib notice remain in the file. Also used by the sniffer. |

The linked RAIL archive SHA-256 is:

```text
630a5f8a07aaf034c3de83cd99b832ba4eefa658d8332bc12e537f1df6fee9f0
```

The MSLA header exceptions require review against the terms applicable to the
exact SDK distribution before publishing a binary bundle. This inventory does
not conclude that those terms prevent distribution; it also does not claim that
the RAIL archive's Zlib exception applies to unrelated bootloader headers.
The board's existing Gecko bootloader is preserved, not built or distributed
with the application.

## Experimental XIAO MG24 and ESP32-C6 RF diagnostics

These targets remain experimental and outside the supported firmware release
set and updater catalog. The following records their reviewed RF dependencies;
it does not approve a binary download or transfer qualification from another
board. The earlier transport-only images did not initialize the radio.

| Input | Pinned source and scope | Licence evidence |
| --- | --- | --- |
| MG24 Gecko SDK | The same `59d2160fe448b13730a91c0f019a8b4c53c43eb2` revision above; EFR32MG24 startup/system code, emlib, CMSIS, application-property headers and RAIL/PA inputs | Preserve the applicable per-file notices, including the bootloader-interface MSLA terms; the MG21 inventory is not a substitute for the MG24 build's dependency list. |
| MG24 RAIL archive | `platform/radio/rail_lib/autogen/librail_release/librail_efr32xg24_gcc_release.a`; the fetched archive matches its pinned Git LFS content hash and appears in the MG24 link map | This archive path falls within the pinned Gecko `License.txt` Zlib exception. The inspected RAIL headers and PA conversion/curve sources also carry Zlib notices. That exception does not relicense the entire SDK or the stock bootloader. |
| ESP-IDF | [v5.5.1, `fcae32885b0296b32044cb99ecbdc50d98dddb83`](https://github.com/espressif/esp-idf/tree/fcae32885b0296b32044cb99ecbdc50d98dddb83); IEEE 802.15.4, USB, GPIO, timer and their selected dependencies | The inspected IEEE 802.15.4 public API and implementation carry Apache-2.0 notices. Other components and submodules retain their own terms; an SDK-wide Apache label is insufficient. |
| ESP PHY binary inputs | [Submodule `45ce2703403784b4c3983a52e453988d61eb0e9d`](https://github.com/espressif/esp-phy-lib/tree/45ce2703403784b4c3983a52e453988d61eb0e9d), supplying `esp32c6/libphy.a` and `esp32c6/libbtbb.a` | Its inspected `README.rst` explicitly applies Apache-2.0 to the binary libraries, with Copyright 2015-2021 Espressif Systems. Retain that attribution and `LICENSE`. |
| ESP coexistence binary input | [Submodule `2d68674e3d522fb025e4666217f9cc1ca2af9399`](https://github.com/espressif/esp-coex-lib/tree/2d68674e3d522fb025e4666217f9cc1ca2af9399), supplying `esp32c6/libcoexist.a` | Its inspected `README.md` explicitly applies Apache-2.0 to the binary libraries, with Copyright 2015-2023 Espressif Systems. Retain that attribution and `LICENSE`. |

The reviewed archive SHA-256 values are:

| Input | SHA-256 |
| --- | --- |
| MG24 `librail_efr32xg24_gcc_release.a` | `29f09f2ee00396372ea15944966df0fe59d30f23a48582095edced6763bcb67f` |
| C6 `libphy.a` | `69d43dbc33da2b72abd7c251c0b61e7f1ace000884a448e0b8790704cad368b7` |
| C6 `libbtbb.a` | `9e1e15c9f0bdbb6fcfca245afec7eb082ff243dcdac212b2921ad9b62f6db198` |
| C6 `libcoexist.a` | `79fae7fbe27f4a8d9aeabe25ae9cf18759cab2fa20ce5a596ebddd764f0e28a2` |

The C6 link map contains retained sections from all three listed Espressif
archives; disabling Wi-Fi and Bluetooth operation does not remove these shared
radio inputs. The reviewed map has no selected members from the separately
collected `librfate.a`, `librftest.a` or `libbttestmode.a` test archives. The two
binary submodules had no top-level NOTICE file at their recorded revisions.

Private review evidence is retained under `.local/xiao-rf-20261107/`, including
`rf-review-inputs.json`, the selected licences/headers, generated C6 configuration,
ELFs and link maps. All 29 entries in that initial manifest were independently
size/hash checked; the two binary-submodule READMEs were inspected separately.
This selected-input review is not a complete compiled-header or runtime notice
inventory. The C6 toolchain/runtime must be inventoried independently of the
GNU Arm runtime described below. Before a binary release, collect the complete
applicable notices and bind them to its exact images. Neither source publication
nor a passing radio test establishes that binary-distribution package.

## Nordic nRF52840 PCA10059 gateway

| Input | Pin and use | Licence / retained notices |
| --- | --- | --- |
| [TinyUSB 0.18.0](https://github.com/hathach/tinyusb/tree/86ad6e56c1700e85f1c5678607a762cfe3aa2f47) | `86ad6e56c1700e85f1c5678607a762cfe3aa2f47`; device core, FIFO, CDC and Nordic USB controller driver | MIT. Root Copyright (c) 2018 hathach; individual sources also name Ha Thach and Reinhard Panhuber. Retain the source notices and `LICENSE`. |
| [nrfx 1.8.6](https://github.com/NordicSemiconductor/nrfx/tree/d68126c1acc9e2a7575a2c5de4a06f279d780f4f) | `d68126c1acc9e2a7575a2c5de4a06f279d780f4f`; HAL, power and atomic support, MDK startup/device files | Root BSD-3-Clause, Copyright (c) 2017-2020 Nordic Semiconductor ASA. MDK files also carry ARM Apache-2.0 notices and the separate CodeSourcery linker notice below. |
| [nRF IEEE 802.15.4 radio driver 1.8.0](https://github.com/NordicSemiconductor/nRF-IEEE-802.15.4-radio-driver/tree/0b972851eed5e1fddc6b9a6ac022fe46fe445ff4) | `0b972851eed5e1fddc6b9a6ac022fe46fe445ff4`; bare-metal single-PHY radio, IRQ/timers, filtering and frame handling | Root BSD-3-Clause, Copyright (c) 2017-2019 Nordic Semiconductor ASA. **File exception:** `src/platform/irq/nrf_802154_irq.h` has Nordic's five-condition licence, including Nordic-IC-only use and a restriction on supplied binary code. Full notice below. |
| CMSIS 5 | TinyUSB `lib/CMSIS_5`, commit `2b7495b8535bdcb306dac29b9ded4cfb679d7e5c` | Apache-2.0; retain ARM notices and `LICENSE.txt`. |
| `firmware/nrf52840/nrfx_glue.h` | Adapted from TinyUSB's Nordic glue | The file itself retains a Nordic Copyright (c) 2017-2018 **BSD-3-Clause** notice. Its presence in TinyUSB does not turn that file's licence into MIT. |

`nrf52840/application.ld` includes nrfx's `mdk/nrf_common.ld`; the latter's
CodeSourcery notice requires verbatim inclusion in distributions. The factory
MBR/USB DFU bootloader is neither linked into nor distributed with the gateway
application. Stock sniffer recovery packages and legacy `nrfutil` are separate
tools/artifacts; their redistribution has not been cleared by this inventory.

### Nordic IRQ header exception (verbatim licence notice)

```text
Copyright (c) 2020, Nordic Semiconductor ASA
All rights reserved.

Redistribution and use in source and binary forms, with or without modification,
are permitted provided that the following conditions are met:

1. Redistributions of source code must retain the above copyright notice, this
   list of conditions and the following disclaimer.

2. Redistributions in binary form, except as embedded into a Nordic
   Semiconductor ASA integrated circuit in a product or a software update for
   such product, must reproduce the above copyright notice, this list of
   conditions and the following disclaimer in the documentation and/or other
   materials provided with the distribution.

3. Neither the name of Nordic Semiconductor ASA nor the names of its
   contributors may be used to endorse or promote products derived from this
   software without specific prior written permission.

4. This software, with or without modification, must only be used with a
   Nordic Semiconductor ASA integrated circuit.

5. Any software provided in binary form under this license must not be reverse
   engineered, decompiled, modified and/or disassembled.

THIS SOFTWARE IS PROVIDED BY NORDIC SEMICONDUCTOR ASA "AS IS" AND ANY EXPRESS
OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED WARRANTIES
OF MERCHANTABILITY, NONINFRINGEMENT, AND FITNESS FOR A PARTICULAR PURPOSE ARE
DISCLAIMED. IN NO EVENT SHALL NORDIC SEMICONDUCTOR ASA OR CONTRIBUTORS BE
LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR
CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE
GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION)
HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT
LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT
OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
```

### CodeSourcery linker notice (verbatim)

```text
Copyright (c) 2007, 2008, 2009, 2010 CodeSourcery, Inc.

The authors hereby grant permission to use, copy, modify, distribute,
and license this software and its documentation for any purpose, provided
that existing copyright notices are retained in all copies and that this
notice is included verbatim in any distributions.  No written agreement,
license, or royalty fee is required for any of the authorized uses.
Modifications to this software may be copyrighted by their authors
and need not follow the licensing terms described here, provided that
the new terms are clearly indicated on the first page of each file where
they apply.
```

## Compiler and C runtime inputs

The offline target-build audit used GNU Arm Embedded GCC **14.2.1 20241119**,
Debian package `gcc-arm-none-eabi` **15:14.2.rel1-1**, and newlib packages
`libnewlib-arm-none-eabi` / `libnewlib-dev` **4.5.0.20241231-1**.
All targets link runtime code; `nano.specs`/`nosys.specs`, libc, libm where used,
libgcc and any selected libgloss stubs are not covered by the project licence.

- GCC is GPL-3.0-or-later. Its libgcc runtime is covered by GPL-3.0-or-later
  **with GCC Runtime Library Exception 3.1**; preserve the applicable runtime
  notices. Compiler licensing and linked-runtime licensing are distinct.
- Newlib is a collection of per-file licences, largely BSD-style and other
  permissive notices. Do not describe it as one uniform BSD licence. The exact
  Debian package's `/usr/share/doc/libnewlib-arm-none-eabi/copyright` contains
  the collected notices; the link map identifies the archive members selected
  for a particular image.
- `/usr/share/doc/gcc-arm-none-eabi/copyright` covers Debian packaging and points
  to `/usr/share/doc/gcc-14-base/copyright` for GCC/runtime terms. The packaging
  file alone is not a sufficient runtime notice bundle.

## Audit scope and unresolved binary-release work

The 2026-11-01 audit compiled the TI signed-update gateway, TI bootloader, TI
receiver, both named MG21 gateway profiles, Sonoff passive sniffer and Nordic
gateway without deploying them. Compiler dependency files identified 682
external source/header paths. The audit inspected licence headers and the
explicit archive inputs above; it did not establish a complete member-level
licence manifest for the prebuilt TI/RAIL archives or the C runtime.

Before distributing compiled firmware:

1. Bundle the exact SDK and runtime licences, per-file copyright notices and any
   applicable NOTICE files for the linked inputs. Keep Monocypher's existing
   licence/provenance files with TI signed-update distributions.
2. Resolve/document the MG21 MSLA source-header and startup wording against the
   applicable SDK terms. Retain the Nordic IRQ header's five-condition notice
   and Nordic-only use limitation; do not claim a uniformly permissive Nordic
   dependency stack.
3. Record the exact toolchain/runtime archive versions and selected link-map
   members. The package versions above describe this audit, not a guarantee
   about another developer's compiler installation.
4. Keep factory bootloaders, stock recovery images and flashing tools separate
   unless their own licences and redistribution requirements are also reviewed.

These remaining binary-packaging checks do not alter the licences of the
project-owned sources or require importing SDK binaries into this repository.
