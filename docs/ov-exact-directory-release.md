# Combined OV exact-directory release

This source binding pairs the measured [HTTP artifact](ov-http-compose.md#explicit-local-config-identity)
with the [current verified-ACK backend artifact](ov-verified-ack-release.md).
The [previous exact-directory backend binding](ov-curator-backend-qualified-release.md)
is retained as historical evidence.

| Binding | Docker config ID | Source |
| --- | --- | --- |
| HTTP | sha256:324b4da5f9aab84f55b517eed9ab4394aca61c92e382583005f4fdd2002126ee | b8e678ae07fba3ec49ee142589ea9b61a22f6853 |
| Backend | sha256:48b046b5c2b43814fb091a1fa2cf60920af07f443679960a60e4e826b0a68576 | e60c92e22ac924a864a5a3c7699538ff8955fb36 |

HTTP exact-directory-v2 preserves original target URI bytes and supports optional typed
stat/get checks. `resources-document-write` version 2 is checked as a runtime capability,
not a new grant. The full-resource envelope pin remains
1c80c90e02a50a168c7f7ef02fd7c9aebaeb8319. The personal protocol pin remains
7f3faf6982f81116d6626bc7091f87f786a309c7 and does not attest deployed backend
identity. Source guards, roles, templates, grants and native identity stay fixed.

Artifact custody and its limits remain in the linked binding records. Backend
patch custody is still pending; it is not a standalone Docker save.
Image staging is a separate parallel lane and remains pending for this binding.
This record establishes file validation and source binding only, with no live-state
claim. This source change activates neither service. Root owns the combined PR, review pair, CI,
owning CHECK/REAL/identical repeat and runtime qualification. No native acceptance,
timeout repair or hold clearance is claimed.
