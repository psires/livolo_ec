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

The root `LICENSE` is canonical. Independently built Python packages and
assembled Home Assistant distributions carry copies so recipients receive it
without requiring the monorepo. Keep those copies identical when updating it.
