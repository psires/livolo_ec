# Public firmware distribution decisions

Reviewed 2026-11-07. This is the plan for the next firmware-bearing release,
not approval of the existing private 0.13 trial images. Software release 0.5.0
contains no radio firmware. The Release Engineer owns publication and its
evidence; the ZigBee specialist owns board/build qualification; the Tech Lead
reviews architecture and the resulting release scope.

Project-owned sources remain BSD-2-Clause. Dependency terms apply separately
to the distributed firmware. Start with the [dependency inventory](../firmware/THIRD_PARTY_NOTICES.md)
and [exact 0.13 review](../firmware/RELEASE_013_DISTRIBUTION_REVIEW.md).

## Distribution and update eligibility

The complete release inventory must account for `codm-cc2652p`,
`sonoff-zbdongle-e`, `zb-gw04-1v2` and `nordic-pca10059`. Sharing an MG21 chip
does not make the two dongles interchangeable.

Public downloads and automatic update offers have separate acceptance gates:

- A public artifact needs an exact board, frozen source and input hashes,
  complete notices, redistribution review, demonstrated boot/basic control,
  and a tested manual installation/recovery path.
- An installable catalog entry additionally needs the actual updater lifecycle
  qualified for that board, artifact, helper, trust key and supported host path.
  It must include state restoration, verification and restart evidence.

Keep accepted manual-only downloads in a separate inventory/directory. Do not
set runtime `qualified` merely to make a download visible. A local-trial flag,
successful compilation or another board's test cannot satisfy either gate.
Standalone bundles, Docker images and the HA app must carry notices alongside
any payloads they redistribute. HA integration releases do not themselves
establish radio qualification.

## MG21: resolve the actual header grant

The owner confirmed accepting the Gecko SDK license and identified the official
MSLA link seen approximately two weeks before 2026-11-07. Combined with the
published version 20260804 and current SDK references, this is a reasonable
release-review basis for the scoped route below. The approximate date is not
an exact acceptance timestamp or an archived contract receipt; see the
[provenance findings](verification/mg21-license-provenance-20261107.md).

The pinned Gecko SDK's [license](https://github.com/SiliconLabs/gecko_sdk/blob/59d2160fe448b13730a91c0f019a8b4c53c43eb2/License.txt)
expressly gives the RAIL release archive a Zlib exception. That exception does
not cover `application_properties.h` or `btl_reset_info.h`. Both application
targets include them; the SDK startup file also includes application properties
when `SL_APP_PROPERTIES` is enabled. Removing includes only from `main.c` would
leave a transitive dependency.

The [MSLA](https://www.silabs.com/about-us/legal/master-software-license-agreement)
page reviewed identifies version 20260804. Section 4.2.5 allows distribution
through the licensee's channels. Section 5.4 allows commercially reasonable
means besides an executed EULA, but requires downstream restrictions including
further redistribution; section 7.4 requires corresponding EULA provisions.
Neither a clickthrough nor separate written vendor permission is universally
required by these clauses. Record the agreement applicable to SDK acquisition;
the current web page alone does not establish that fact.

Checking Gecko's `master` and Simplicity SDK's `sisdk-2025.6` versions on the
review date found the same MSLA wording on these two headers. No permissively
licensed substitute has been verified. Do not replace their notices with BSD,
claim a clean-room implementation after inspecting their source, or infer
permission from the small size of the interface.

The reviewed packaging route under the existing grant uses separate
MG21 binary terms and the applicable agreement in every download/image,
release-page notice before download, and a visible container license/notice
location. Limit those terms to the affected vendor portions, preserving the
project's BSD license and other dependencies' grants. Treat GitHub/GHCR/HA
delivery as project distribution channels; do not promise unrestricted mirrors
of the restricted payload. The Release Engineer must verify the complete notice
bundle and channel presentation in the final assets; retaining copyrights alone
does not implement this route. This closes the route/notice decision, not the
remaining artifact, packaging or hardware qualification checks.

The finalized [binary terms and channel requirements](MG21_BINARY_TERMS.md)
provide the scoped wording. No further acceptance or receipt is requested from
the owner on the evidence now supplied.

If that review cannot resolve channel coverage or the project wants unrestricted
downstream redistribution, the owner/license holder can submit this prepared
vendor inquiry. It has **not** been sent:

> Livolo EC is a BSD-2-Clause application for third-party EFR32MG21 dongles
> (Sonoff ZBDongle-E and ZB-GW04 1v2). We use Gecko SDK 4.4.6, revision
> 59d2160fe448b13730a91c0f019a8b4c53c43eb2. We preserve the installed Gecko
> bootloader and do not distribute it or the SDK. Our application includes
> platform/bootloader/api/application_properties.h and btl_reset_info.h,
> including the startup file's application-properties reference. Please
> confirm the applicable permission and required notices/end-user terms for
> distributing these application binaries as GitHub release assets and inside
> public GHCR images/Home Assistant app packages, including downstream mirrors
> and redistribution. Alternatively, can these two interface headers be
> supplied under Zlib/BSD terms? We do not seek to relicense the SDK or RAIL.

Record the accepted packaging review or vendor response, scope and notice
obligations against the final artifacts. If neither covers the intended
channels, an independently authored
interoperability layer is an engineering alternative requiring provenance and
license review, not an automatic clearance. It must remove both direct and
startup dependencies and verify binary layout/vector references, reset entry,
GBL metadata and operation with each preserved bootloader. The vendor's
[documented reset interface](https://docs.silabs.com/mcu-bootloader/2.4.3/gecko-bootloader-api/reset-interface)
is interface evidence, not a replacement source-code license.

## Nordic: mixed terms and a usable USB identity route

The pinned [IRQ header notice](https://github.com/NordicSemiconductor/nRF-IEEE-802.15.4-radio-driver/blob/0b972851eed5e1fddc6b9a6ac022fe46fe445ff4/src/platform/irq/nrf_802154_irq.h)
permits redistribution subject to its conditions, including Nordic-IC-only use
and restrictions on supplied binary code. Retain the complete notice already
recorded in the dependency inventory and identify the download as a PCA10059
image with mixed dependency terms. These findings do not justify calling the
whole binary unrestricted BSD or the full dependency stack OSI-licensed.

The development identity `cafe:4001` is not the selected public product identity.
[pid.codes](https://pid.codes/howto/) requires recognized open-source licensing;
we cannot attest that this exception meets that prerequisite. A dedicated PID
remains possible after resolving eligibility with the allocator, but it is not
necessary to block all engineering progress on that route.

Objective Development's [shared-ID grant](https://github.com/obdev/v-usb/blob/801242072fa035e1839e3571df0d9a18e9cec95c/usbdrv/USB-IDs-for-free.txt)
offers CDC-ACM `16c0:27dd` with serial-based identification, including use with
technology other than V-USB. It requires an owner-controlled domain/email
prefix followed by a colon, continued control of that namespace, and matching
that prefix as well as VID/PID. The class driver must be used. This is a shared
identity, not an exclusive allocation to Livolo EC.

The owner selected `livolo.sfires.net` on 2026-11-07. The selected application
serial is `livolo.sfires.net:` followed by the unchanged 16-hex-digit hardware
identity. That decision resolves the namespace choice, not the implementation
or physical migration tests below:

1. Define the application serial as `<controlled-namespace>:<device-id>`,
   retaining the stable hardware-derived device ID and English/US descriptor.
2. Update firmware descriptors and host identity/profile handling together.
   Firmware and host changes have now passed offline review and tests; their
   final artifact and physical migration qualification remain pending. Treat the new application
   identity, legacy development identity and factory DFU `1915:521f` as
   distinct modes with explicit transition rules. The optional same-suffix
   `application_serial_after_update` profile admits a deliberate legacy-to-new
   transition and requires the new identity after transfer. Existing profiles
   remain legacy-only; see [the host migration instructions](../host/docker/NORDIC.md).
3. Require the correct namespace and exact expected serial/topology for an
   update. Shared VID/PID alone never identifies the intended radio. Preserve
   explicit mappings to the separate factory-DFU serial. Reject unrelated
   devices and ambiguous matches; never broaden scanning to make migration
   appear successful.
4. Test legacy-to-new identity, normal bootloader entry, physical DFU recovery,
   reconnect and daemon restart with the final bytes. Keep factory MBR/DFU
   outside the distributed application. Record what users must change in
   persistent device paths or USB/container mappings.

The USB grant does not license the radio driver, provide USB certification, or
replace the project's stricter device-selection requirements.

## TI: establish a release trust anchor without rotating installed keys

The inspected TI/Contiki/CMSIS/Monocypher inputs have the notice obligations
recorded in the inventory. No vendor redistribution prohibition was identified
in that inspected set. Exact runtime/archive notices still belong with the
final binaries. The remaining release trust decision is separate from licensing.

Use a dedicated release Ed25519 identity, separate from the existing test key.
The owner selected custody on their laptop on 2026-11-07. The Release Engineer
created the separate release identity there and recorded its
[public trust material](../firmware/release-keys/README.md). The additional local
recovery copy is not an off-device backup. The Release Engineer coordinates
signing of frozen, reviewed image hashes. No private key belongs in Git, a build
artifact, a container, a radio or a general CI log/cache.

The existing `tools/firmware_package.py keygen` produces a raw 32-byte public
key, its build header and a private PEM, and reports the public key's SHA-256.
Run it in an owner-approved protected location, not implicitly in the checkout.
Publish the public key and its full fingerprint with the bootstrap and release
metadata. Verify the package signature and embedded bootloader key before
accepting that identity; build the signed-update application's embedded key
from the same public trust material. The generated-key files must not be
mistaken for a completed release-signing custody process.

First installation and routine updates are different products:

- Publish the reviewed project bootloader, signed application package and
  public trust material with instructions for the existing factory-image tool.
  The operator builds the installation image using **that board's own** backup
  to retain CCFG and ROM recovery configuration. Never distribute one test
  board's merged factory image as a universal installer.
- Routine `.ecupd` packages contain the signed application/manifest and preserve
  the resident bootloader/key. A radio provisioned with the test key or an
  owner's key must not be offered a release-key package as a compatible update.
  Preflight must reject or clearly report trust mismatch before a transfer.
- Moving an existing radio to the release key requires explicit reprovisioning
  with a compatible bootloader, backup and tested recovery. Do not silently
  rotate trust or promise an in-band key migration that is not implemented.
  Owner-built firmware/keys remain supported through the documented build path.

For the exact release key and image, qualify signed transfer, trial start,
session restoration, durable confirmation and daemon restart. Exercise wrong
key/hash/version rejection, interrupted transfer and unconfirmed-trial rollback
on an authorized recoverable test board. Loss or compromise of the release key
requires a separately reviewed recovery/rotation plan; storing only a public
key on the host does not solve private-key custody.

## Closure record and owner dependencies

| Item | Concrete closure evidence | Owner |
| --- | --- | --- |
| MG21 distribution | Applicable agreement and accepted scoped binary terms/channel review; vendor clarification or a reviewed dependency alternative if that route is insufficient | Project license holder + Tech Lead |
| Nordic USB identity | Accepted controlled namespace or approved dedicated allocation, descriptor/host migration tests and exact-board lifecycle evidence | Project owner + ZigBee/Unix specialists |
| TI release trust | Designated custodian/backup, published public fingerprint, matching bootstrap and qualified signed package | Project owner + Release Engineer/ZigBee specialist |
| Four-board acceptance | Final source/input/payload hashes, notices, manual recovery evidence and separately qualified updater paths | Release Engineer + specialists |

New headers, identity, keys or firmware bytes require a newly assigned firmware
version and fresh evidence. Do not reuse the existing 0.13 artifact identity.
Offline tests can check packaging, notices, wrong-board/trust rejection and
identity selection now. They cannot prove physical boot, RF control, USB
re-enumeration or recovery. Those need an authorized bench radio and operator;
an active Home Assistant gateway is not an implicit test target.
