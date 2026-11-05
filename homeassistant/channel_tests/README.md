# Public integration installation and upgrade checks

These explicit network tests exercise the published `psires/ha-livolo-ec`
0.2.1 and 0.3.0 releases. They are separate from offline source tests and must
run in disposable Python environments. They create a temporary HA configuration
and a simulator-backed daemon; they never connect to a radio or live HA host.

The test uses upstream release listing, actual GitHub asset URLs and the
installer's extraction and replacement code. It verifies update availability,
manifest versions, flat ZIP layout, existing configuration/token preservation,
entity IDs, custom names and areas, switch control, daemon state, restart
requirement and configuration-entry reload. HACS uses `async_install`;
Marketplace uses its GitHub custom-repository installation path. The repository
metadata refresh is stubbed after explicitly retrieving releases. This does not
test GitHub device authorization, catalog/UI onboarding, scheduled polling or
a full HA process restart. A reload alone does not activate updated Python code;
users must still restart HA after installing an integration update.

The default version pair is 0.2.1 to 0.3.0. Override it explicitly with
`LIVOLO_BASELINE_VERSION` and `LIVOLO_CURRENT_VERSION` when qualifying another
release pair. The expected current version must be the latest stable release;
the test fails rather than silently testing different code.

## Qualified environments

Use Python 3.14.7. Keep the two environments separate because their pytest
plugins and HA versions differ.

- HACS: `homeassistant/requirements-test.txt`, `aiogithubapi==26.0.0`, and
  [HACS source a15e87e](https://github.com/hacs/integration/tree/a15e87ec0d1c82e58f1ddbaf17669d3eaa261eb8).
  Set `LIVOLO_HACS_SOURCE` to that checkout's root.
- Marketplace: `homeassistant==2026.11.0`, `aiogithubapi==26.0.0`,
  `pyserial==3.5`, and the `packages/pytest-homeassistant` package from
  [HA core 74cf5a4](https://github.com/home-assistant/core/tree/74cf5a49c63ed8d4d87717844bf14f79c28c9cea).
  Install that package into this environment; its dependencies pin the pytest
  tooling. Do not install `pytest-homeassistant-custom-component` here.

Run from the project root using the appropriate environment's Python:

```sh
LIVOLO_INSTALLER=hacs LIVOLO_HACS_SOURCE=/path/to/hacs \
  /path/to/hacs-venv/bin/python homeassistant/channel_tests/run.py \
  -c homeassistant/pytest.ini homeassistant/channel_tests -q

LIVOLO_INSTALLER=marketplace \
  /path/to/marketplace-venv/bin/python homeassistant/channel_tests/run.py \
  -c homeassistant/pytest.ini homeassistant/channel_tests -q
```

The runner loads HA's HTTP compatibility aliases before pytest plugins.
The fixtures allow loopback and the three required GitHub hosts only, and disable
the upstream session-scoped DNS mock, whose event loop differs from the test
loop. They resolve those hosts through HA's actual resolver once, then use the
same addresses for downloads and the socket allowlist. This avoids a test-only
failure when OS DNS and HA's asynchronous resolver return different valid GitHub
addresses. Public URLs, TLS validation, release metadata and installer code are
unchanged. Anonymous GitHub API rate limits can prevent a run; no account token
is required or used.

Current publication checksums and results are recorded in
[`../verification/integration-channel-0.3.0-20261105.json`](../verification/integration-channel-0.3.0-20261105.json).
The [earlier 0.2.0 → 0.2.1 record](../verification/integration-channel-20261105.json)
is preserved separately.
Supervisor app tests have a separate verification record.
