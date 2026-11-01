# Source finalization — 2026-11-01

The owner selected BSD-2-Clause for project-owned code. The root `LICENSE` is
canonical; the independently built host/client packages and assembled HA
distributions retain it. Existing third-party licences remain in effect; see
the root and firmware `THIRD_PARTY_NOTICES.md` files.

The ZigBee, Unix, Web and Home Assistant specialists finalized their existing
work in coordinated commits. Intent/parameter documentation and source layout
were reviewed without changing radio policy or timing. The HA diagnostics fix
corrects `update` to `update_supported` and has regression coverage.

## Clean source verification

The coordinator exported commit `507b705cf057a9a5e4d1c64005f89be7c9334025` with
`git archive` into a new temporary directory. Tests ran from that export, without
the working checkout's untracked captures, generated files or local state.

| Suite | Result |
| --- | --- |
| Protocol/analysis tools | 46 passed |
| Shared gateway and UART firmware | 183 passed |
| Signed-update authentication/rollback | 7 passed |
| MG21 UART receive core | 9 passed |
| Nordic clock adapter | 1 passed |
| Host daemon, web, native API and ingress | 140 passed |
| HA integration, client and packaging | 28 passed |
| Wireshark Lua dissector through TShark | 5 passed, 1 skipped |
| **Total** | **419 passed, 1 skipped** |

The skipped Wireshark case requires an optional local relay capture; mandatory
fixtures are tracked. All six cases passed in the working checkout where that
capture was available. Host/HA tests required permission to open isolated
loopback sockets; their first sandboxed attempts were blocked by the environment.
The successful runs used fake/simulated radios, not live services or devices.

Python 3.9.6 ran the tools/host/firmware suites; Python 3.14.7 ran HA tests against
HA Core 2026.10.4. The HA specialist also ran the official 2026.10.4 hassfest
validator successfully and verified client wheel/sdist licence contents. The
Unix specialist verified the daemon wheel and simulated process startup/shutdown.

Source checks passed on the export:

- Purpose/parameter documentation and Black 24.8.0 for all 105 Python files.
- clang-format 18.1.8 for all 46 project-owned C/header files; vendored sources
  excluded. The firmware specialist reviewed 255 C function definitions,
  including embedded test stubs, against definition or declaration contracts.
- Prettier 3.3.3 and Node syntax validation for the browser JavaScript.
- Project version consistency at 0.12 and identical root/host/client licences.
- The HA packaging test verifies bundled client and licence contents.

## Target builds and limits

The firmware specialist built seven targets in an isolated directory on the
development Pi using GCC 14.2.1 and the pinned SDK inputs: TI signed-update
gateway, TI bootloader, TI receiver, Sonoff and ZB-GW04 MG21 gateways, Sonoff
passive sniffer and Nordic PCA10059 gateway. All compiled successfully. TI checks
used a dummy public key and are not deployable release artifacts. Nordic emitted
five upstream unused-parameter warnings. No radios were accessed or flashed.

Firmware cleanup preserved Python executable syntax trees and C tokens after
excluding comments/formatting. These checks and builds do not establish new RF
qualification, bit-reproducible release builds or public binary readiness.
The detailed SDK/runtime notice bundle and identified vendor licence exceptions
still need completion before binary distribution, as documented in
[firmware notices](../firmware/THIRD_PARTY_NOTICES.md).

CI now covers the expanded source tree, all offline firmware suites, HA tests and
the actual Wireshark runtime. These results are local verification; they do not
assert that GitHub-hosted Actions have completed. Public image publication,
HACS/Marketplace installation, integrated firmware updates and additional
hardware qualification remain the work described in the [release plan](RELEASE_PLAN.md).
