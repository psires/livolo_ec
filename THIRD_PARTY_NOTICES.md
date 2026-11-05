# Third-party notices

Project-owned code is licensed under [BSD-2-Clause](LICENSE). This licence does
not replace the copyright notices or licence terms of third-party code, build
inputs, runtime dependencies or tools.

- Vendored Monocypher retains its upstream
  [licence](firmware/update/third_party/monocypher/LICENCE.md) and provenance.
- Firmware SDKs, libraries and adapted linker/startup inputs are described in
  [firmware notices](firmware/THIRD_PARTY_NOTICES.md). Preserve the original
  notices in those files and distribute the applicable terms with firmware
  artifacts. A root project licence does not license an entire vendor SDK.
- Python runtime dependencies are installed separately from their upstream
  distributions. Their distribution metadata and included licence files remain
  authoritative. Container images and offline wheel bundles must retain those
  notices alongside this project's licence and record the resolved dependency
  inventory. The daemon's declared dependencies are in `host/pyproject.toml`;
  the independent client's are in `client/pyproject.toml`.
- Home Assistant, test tools, flashers, compilers and system packages retain
  their own terms. They are not relicensed by inclusion in a build or test
  environment. Any future bundle must include notices for its actual contents.

The MG21 update backend invokes `universal-silabs-flasher==0.0.31` as a separate
tool. Its [upstream distribution](https://pypi.org/project/universal-silabs-flasher/0.0.31/)
declares GPL-3.0; BSD-2-Clause applies to this project's own adapter code, not to
that tool. Images containing the flasher must preserve its licence and matching
source distribution, together with notices for the resolved dependencies.
Publish the matching source alongside downloadable image/offline bundles and
record its checksum. A link to a moving upstream branch is not a substitute for
the source corresponding to the distributed version. Review the complete image's
notice/source inventory before publication; including this paragraph alone does
not complete that review.

The root `LICENSE` is canonical. Independently built Python packages and
assembled Home Assistant distributions carry copies so recipients receive it
without requiring the monorepo. Keep those copies identical when updating it.

The optional Nordic update backend uses a separate Python 3.10 runtime with
legacy `nrfutil==6.1.7`. The Python helper modules from `pc-ble-driver-py` are
pinned to upstream commit `cc3a9d259b9363524823aa4a48dbe6991eca6bd7`; native BLE
libraries are not included. Nordic's original notices, including the restriction
to use with Nordic processors, remain applicable. These dependencies are not
relicensed under the project's BSD-2-Clause licence. Container images retain
the verified upstream archive, matching Python source distributions, dependency
versions, hashes and notices under `/usr/share/livolo-ec/sources/nordic`.
The project's fixed-port wrapper disables the vendor transport's automatic USB
discovery and reset behavior; the vendor sources themselves remain unchanged.
