# Combined OV exact-directory release

This source binding pairs the measured [HTTP artifact](ov-http-compose.md#explicit-local-config-identity)
with the [backend artifact](ov-curator-backend-qualified-release.md).

| Binding | Docker config ID | Source |
| --- | --- | --- |
| HTTP | sha256:324b4da5f9aab84f55b517eed9ab4394aca61c92e382583005f4fdd2002126ee | b8e678ae07fba3ec49ee142589ea9b61a22f6853 |
| Backend | sha256:1e3b1d3f2f56df027754ab9f090eb6b74c760c0978e95d0c9e313a09a2b0e963 | 625cdec406678fcb176060fc5c331e0f07726108 |

HTTP exact-directory-v2 preserves ORIGINALURI bytes and supports optional typed
stat/get checks. `resources-document-write` version 2 is checked as a runtime capability,
not a new grant. The full-resource envelope pin remains
1c80c90e02a50a168c7f7ef02fd7c9aebaeb8319. The personal protocol pin remains
7f3faf6982f81116d6626bc7091f87f786a309c7 and does not attest deployed backend
identity. Source guards, roles, templates, grants and native identity stay fixed.

Artifact custody and its limits remain in the linked binding records. Backend
patch custody is still pending; it is not a standalone Docker save.
Runtime images are only staged and the HTTP staging lane is active. This source
change activates neither service. Root owns the combined PR, review pair, CI,
owning CHECK/REAL/identical repeat and runtime qualification. No native acceptance,
timeout repair or hold clearance is claimed.
