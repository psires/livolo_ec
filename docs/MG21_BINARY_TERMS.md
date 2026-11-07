# MG21 binary terms and channel requirements

Finalized for the scoped release route on 2026-11-07. Include the following
notice with each distributed MG21 application and present it in the download
channel. It does not approve an unqualified artifact or replace the full vendor
agreement. The [provenance record](verification/mg21-license-provenance-20261107.md)
documents the owner's confirmation, approximate recollection and review basis.

## Binary notice

The project-owned parts of Livolo EC are BSD-2-Clause. Separately licensed
dependencies retain their own licenses; see the supplied third-party notices.
This additional notice concerns only Silicon Labs material governed by the
Master Software License Agreement (MSLA) within the named MG21 application
images. It does not relicense the BSD, Zlib, Apache or other separately licensed
parts of the project.

Use the affected material only as incorporated in the Livolo EC application
for the specified Silicon Labs EFR32MG21 devices. Do not extract or distribute
it as a standalone SDK/library. Preserve applicable proprietary notices.
Further sublicensing, distribution, sale, marketing, reproduction,
modification, reverse engineering or decompilation of the MSLA-covered
Licensed Programs is prohibited except as authorized by the applicable
agreement. Do not subject that material to an open-source license or disclose
it beyond authorized recipients. These restrictions do not remove permissions
granted separately for other components.

The applicable vendor reference for this release is the Silicon Labs
[Master Software License Agreement, version 20260804](https://www.silabs.com/about-us/legal/master-software-license-agreement).
Its full terms govern the affected material and accompany the release notice
bundle. Obtain project application binaries through the project's designated
download channels; this notice grants no independent right to mirror their
restricted components.

## Required release presentation

The Release Engineer must verify the following in the actual frozen assets
and channel pages before publication:

- GitHub: describe the exact board images and mixed terms on the release page
  before the asset links. Include the approved notice, applicable full agreement
  and dependency notices inside every archive containing MG21 payloads.
- GHCR: include the same documents in the image; identify their location in
  image/package documentation and OCI description. Describe mixed terms rather
  than labeling the whole image solely BSD. Retain source licenses unchanged.
- Standalone/HA app: keep the documents with the bundled firmware, and make
  their location and scope visible in installation/update documentation. HA
  users obtain the project image; independent redistribution is not assumed.
- Other mirrors/repackagers: do not describe them as authorized unless the
  reviewed agreement or a separate permission covers that relationship.

The reviewed scope is Livolo EC application binaries for Sonoff ZBDongle-E and
ZB-GW04 1v2, delivered by the project through GitHub releases, GHCR and the HA
app. It excludes SDK/bootloader redistribution and unrestricted third-party
mirrors of restricted portions. Project-owned BSD code and separately licensed
dependencies retain their existing permissions. No new vendor agreement is
accepted by finalizing this notice.

Retain a readable complete copy of the referenced MSLA and its retrieval/hash
record with the final notice bundle; links alone do not fulfill this packaging
requirement. Check the final dependency inventory, license mappings and visible
channel wording against the reviewed scope. Reopen the review if a dependency,
agreement or distribution channel changes. A special vendor permission or
additional owner assent is not a routine prerequisite for this scoped route.

Primary references: [official MSLA](https://www.silabs.com/about-us/legal/master-software-license-agreement),
[pinned SDK license](https://github.com/SiliconLabs/gecko_sdk/blob/59d2160fe448b13730a91c0f019a8b4c53c43eb2/License.txt),
and the [exact dependency inventory](../firmware/THIRD_PARTY_NOTICES.md).
