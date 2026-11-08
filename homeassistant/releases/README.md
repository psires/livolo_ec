# Publishing HA releases

`homeassistant/releases.json` is the explicit publication manifest. Updating it
on `main` triggers `.github/workflows/ha-release.yml`; a manual workflow dispatch
can resume a failed publication. Only maintainers should add entries after source
review and validation. Each entry names an exact commit, HA version and existing
`ha-<version>` tag. Push the reviewed commit and annotated tags together before
the workflow runs. Historical component version differences are preserved. Entries may set
`"scope": "integration-only"` to publish just the integration; omitting `scope`
retains the historical `integration-and-app` behavior. Unknown scopes are rejected.

The workflow checks the tag target, checks out that exact source and uses
`package_release.py` to assemble and inspect the integration ZIP, companion app
source archive, provenance JSON and SHA-256 checksums. It creates a draft, uploads
all assets, then publishes. Integration-only entries omit the app build entirely
and publish only the integration ZIP, provenance JSON and checksums. Their
provenance explicitly marks app, daemon and radio payloads absent; it does not
claim to update the installed app or daemon. They cannot accept a firmware
bundle. The default combined mode retains its existing firmware qualification
gate; integration-only mode does not make an app candidate publishable. Its token has repository-content write permission
only in the publishing job; pull requests cannot trigger publication.

An already published release is left unchanged. A failed draft may resume with
replacement draft assets. Published tags/assets must not be moved or silently
replaced; correct a release with a new version. This workflow does not publish
radio firmware or container images, deploy HA, or change repository visibility.

For a local packaging check, export the release commit into a fresh directory and
run:

```sh
python homeassistant/package_release.py --source /path/to/clean-source \
  --version 0.2.0 --commit FULL_REVIEWED_COMMIT --output /path/to/new-assets
```

The release notes in this directory distinguish manual integration installation
from companion app source builds and identify the remaining hardware limits.

To package an integration-only release locally, add `--scope integration-only`
to the command above. The integration still must match the source manifest
version and include its independent client and BSD license.
