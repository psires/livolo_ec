# MG21 license provenance review, 2026-11-07

The owner confirmed: "I'm the one that accepted the gecko-sdk license."
The owner subsequently identified the official MSLA URL below as the link seen
"probably two weeks ago." Relative to 2026-11-07, that is approximately late
October; the recollection is expressly uncertain. No exact acceptance date,
receipt or separate agreement is asserted. No new acceptance was performed by
the review.

## Retained evidence inspected

The ZigBee specialist inspected the existing SDK metadata read-only. The SDK
directory has no `.git` directory or file, so it contains no checkout reflog or
clone/acquisition record. Its root `License.txt` contains an unversioned link
to the vendor MSLA and the separately stated license exceptions. It does not
contain the full agreement or an acceptance record.

| Retained file | SHA-256 | What it establishes |
| --- | --- | --- |
| SDK `License.txt` (1977 bytes) | `d2abb2ff8f2882b3c46687889caf81af543461c60442292c7385b461d216b20e` | Default MSLA link and stated exceptions; no agreement revision |
| SDK `.properties` | `25d71ad94eb61332c52f3867a31e5f58e3f9afc1ba17dae7053a0621c0482cf5` | SDK version 4.4.6 |
| SDK `gecko_sdk.slcs` | `fd8bf7683b57f46336edf8295864a85d5f243beb796a857a7ea4c79321dc5c29` | SDK 4.4.6, RAIL 2.16.6 and bootloader 2.4.2 |
| SDK `README.md` | `222221877dffe79c8e0d9c2802a4a413b080119c438c3035cc53df096dde547d` | Generic archive/clone instructions and unversioned MSLA link |
| Local `build-inputs.json` | `a2f694a858fd5d48c5310943c0fa13643845cf331e56057e9921b0556e7a1847` | Recorded toolchain/input hashes/repeat builds, not agreement acceptance |
| Local `dependency-notices.txt` | `58b296a685f546f27fe8f87784c1d409cb8f804ad98cb50821ef3cf7af81e063` | Collected source/runtime notices, including SDK and header links; no full MSLA/version |

The last two files are retained under `.local/release-0.13/`, outside the public
source tree. Their catalog copies do not provide additional acceptance evidence.
Other existing build/evidence records inspected supplied no agreement version.
The SDK license file's modification time, 2026-10-23 21:59:05 UTC, is only a
filesystem timestamp; it must not be presented as an acceptance or download date.
The SDK source pin cited in the notices likewise identifies code, not a contract.

## Release-review conclusion

The [official MSLA page](https://www.silabs.com/about-us/legal/master-software-license-agreement)
reviewed on this date identifies version **20260804**. Section 30 states that
the current version supersedes earlier versions through continued use; section
31 also contains written-amendment wording. The page is a release-time review
reference, not recovered proof of which terms the owner originally accepted.
No conclusion about retrospective contractual effect is recorded here.

The owner's confirmation of existing acceptance, approximate late-October
recollection and identification of the same official link, together with the
published version and SDK references, sufficiently identify version 20260804 as
the working basis for this scoped release review. This is a reasoned assessment
of the available evidence, not proof of the page contents on an exact historical
day. Requiring a receipt or another acceptance solely to eliminate that residual
date uncertainty is not proportionate to the evidence or necessary to continue
the requested work.

The Tech Lead accepts the scoped application-distribution route in
[the distribution plan](../FIRMWARE_DISTRIBUTION.md) and finalized
[binary notice](../MG21_BINARY_TERMS.md). No additional license-holder question
or vendor permission is required for that route on the current facts. It does
not permit unrestricted redistribution of the restricted components or change
the BSD license of project-owned sources.

The complete current agreement is retained privately at
`.local/firmware-readiness-20261107/notices/Silicon-Labs-MSLA-20260804.txt`:
43,799 UTF-8 bytes, SHA-256
`e6966d27e4eab74f720c9705e3ebf8ecf240cd61f3d2e5d17d93ecbcd4286014`.
The browser capture on 2026-11-07 at 15:39 UTC retains the rendered agreement
container from its title through section 35, with a terminal newline. It is
readable text, not original HTML or a historical acceptance receipt. All 35
sections were checked. Its text agrees with the Tech Lead's independent web
capture after removing only whitespace and Markdown emphasis/heading markers;
the selected file above is the canonical notice copy for bundle preparation.
The separate `msla-retrieval.json` records the method, URL and hash.

Concrete remaining work is to collect exact dependency notices, bind the
complete agreement and notice hashes to the final artifacts, verify
channel presentation, and complete hardware qualification. Reopen this review
if different terms, dependencies or distribution channels emerge; do not claim
all of those implementation checks passed merely because the route is accepted.

No shell history, credential material, radio device or service state was
accessed for this provenance check; no SDK files were modified or downloaded.
