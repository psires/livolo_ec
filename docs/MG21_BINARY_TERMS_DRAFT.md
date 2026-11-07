# MG21 binary notice: review draft only

**Not effective, accepted or approved for publication.** This proposal is for
the project owner/license holder to review against the agreement applicable to
the SDK. It does not authorize distribution, change the repository license or
record anyone's assent. Do not automatically insert it into release packages.

## Proposed notice

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

The supplied agreement **[revision and accompanying file to be fixed before
approval]** governs the affected material. Obtain project application binaries
through the project's designated download channels; this notice does not
grant an independent right to mirror their restricted components.

## Proposed channel presentation

This is a delivery proposal, not a determination that displaying a notice alone
creates an enforceable agreement:

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

The owner must approve the actual notice, delivery mechanism and covered
channels. If a further acceptance step is necessary for the chosen mechanism,
design it explicitly before publishing; do not claim a clickthrough is always
required or silently treat installation as proven assent.

## Facts needed to complete the review

1. Identify the license holder and whether the SDK was acquired personally or
   on an employer's behalf, including authority to accept its terms.
2. Record acquisition source/date and the agreement revision supplied with or
   applicable to that acquisition, including any separate agreement. The SDK
   source pin is `59d2160fe448b13730a91c0f019a8b4c53c43eb2`; it does not itself
   identify a contract revision. Current website version 20260804 is evidence
   of the current page, not proof of the acquisition terms.
3. Confirm that the exact compiled input list still limits MSLA exceptions as
   described in the review, and identify any additional restricted input.
4. Review whether these application images, named hardware and the proposed
   direct distribution channels fit the applicable grant, and whether the
   downstream notice/acceptance mechanism satisfies its obligations.
5. Record the accepted wording, document hashes and reviewer identity with the
   firmware release evidence. If coverage remains unclear, use the prepared
   vendor inquiry in [the distribution plan](FIRMWARE_DISTRIBUTION.md).

Primary references: [official MSLA](https://www.silabs.com/about-us/legal/master-software-license-agreement),
[pinned SDK license](https://github.com/SiliconLabs/gecko_sdk/blob/59d2160fe448b13730a91c0f019a8b4c53c43eb2/License.txt),
and the [exact dependency inventory](../firmware/THIRD_PARTY_NOTICES.md).
