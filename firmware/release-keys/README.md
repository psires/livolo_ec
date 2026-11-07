# TI release verification identity

The project owner selected laptop custody for a dedicated Ed25519 release
signing identity on 2026-11-07. `ti-release-20261107.pub` contains exactly 32 raw
public-key bytes. Its SHA-256 fingerprint is:

```text
bca880c386b2f437fb8a5807188dbb139960a29a3a17b28b262f7ea694051e92
```

Only public verification material belongs here. The private key and a checked
local recovery copy remain permission-restricted and Git-ignored on the owner's
laptop. An off-device backup has not been established. Neither private key material nor
its private custody record is copied into CI, source archives or containers.

This is the selected identity for a future qualified release, not approval of
an existing test artifact. No firmware was signed and no radio was reprovisioned
when it was created. A release must bind the signed application, bootstrap,
source/build provenance and hardware evidence to this exact public fingerprint.
Existing test-key and owner-key radios keep their current trust. They cannot
accept a release-key update without a separately planned first installation or
reprovisioning procedure; automatic key rotation is not implemented.
