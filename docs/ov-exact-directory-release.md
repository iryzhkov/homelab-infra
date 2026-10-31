# Combined OV exact-directory release

This source binding pairs the measured [HTTP artifact](ov-http-compose.md#explicit-local-config-identity)
with the [current verified-ACK backend artifact](ov-verified-ack-release.md).
The [previous exact-directory backend binding](ov-curator-backend-qualified-release.md)
is retained as historical evidence.

| Binding | Docker config ID | Source |
| --- | --- | --- |
| HTTP | sha256:ce94410e1002fbaaa6fd9f06dd0e3bf90ed7db6e98cb8515f01b189e5c80a696 | 7d833b8d51b2d8074be1ef376140cd08a9190a9d |
| Backend | sha256:48b046b5c2b43814fb091a1fa2cf60920af07f443679960a60e4e826b0a68576 | e60c92e22ac924a864a5a3c7699538ff8955fb36 |

HTTP exact-directory-v2 preserves original target URI bytes and supports optional typed
stat/get checks. `resources-document-write` version 2 is checked as a runtime capability,
not a new grant. Document POSTs have a 120-second HTTP timeout; real document
writes are capped at the lesser of 130 seconds and remaining JWT lifetime.
The shared request gate is 135 seconds; other per-tool caps are unchanged.
The current lifetime-admission HTTP selection adds a pre-application 401 for
write-capable POSTs with total signed lifetime at least 160 seconds when less
than 145 seconds remain. Real document writes require 135 seconds before backend
I/O. [Admission details and native refresh qualification limits](ov-http-compose.md#explicit-local-config-identity)
remain separate from runtime acceptance. No 401 is introduced after application
admission or effects; no backend mutation is retried.
The full-resource envelope pin remains
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
