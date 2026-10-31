# Historical OV curator backend exact-directory repair

This record describes the previous backend binding. The current selection and
artifact authority are in [OV verified ACK release](ov-verified-ack-release.md).
All selection, qualification and activation statements below are historical.

This source-only selection binds the exact-directory resource repair. Exact
canonical directory targets are accepted without broadening control targets or
resource reindexing. Relations sidecar snapshot preservation remains in place.
Personal implementation, roles, grants, owner/profile settings, credentials,
inference selection, mounts and runtime controls are unchanged.

## Exact artifact and source qualification

Selected product: 625cdec406678fcb176060fc5c331e0f07726108, tree
19f9e691b362e81765063dcedea110010c4b1a1a. Its tree equals reviewed source
3c0bd04bad9f95c3bb0a4a1aa75e8874273b266a. Root reports product PR8
merged with CI41178658242 green, including 21 document-write cases and 30
reindex cases. This binding lane does not independently qualify product behavior.

Closed local config/image ID:
sha256:1e3b1d3f2f56df027754ab9f090eb6b74c760c0978e95d0c9e313a09a2b0e963.
Distribution manifest:
sha256:bc5d0f1750224137f09625e14e2d5daad1d8457c3049e4a1587e25db7250985a.
Assembly job: 617351b9-b830-4457-aee3-5cbb94ba9eb7.
Patch bundle SHA256:
46bdfc39ddce4d337c43f57e5668adcc96d0204869abf484c0324645ff86fafa.
Custody manifest SHA256:
e214e006bef6530409ce71d6ceb7e6bfd0d203196db3ba342a7552d66da1bfce.

All 915 runtime product files match the selected merged source. Relative to the
previous release, only openviking/server/routers/resources.py and
openviking/service/document_write.py differ. Qualified native SHA256
8be7869a9f326ccd6cb67e093216f74296d1e98e93a73290918e6f33744ea1ad,
dependencies and assembler are unchanged. No native rebuild or new native
qualification is claimed. Independent in-job validation checked 20 custody
entries, 21 bundle members, 938 overlay members and the complete config with
14 ordered RootFS diff IDs. This patch bundle requires retained base blobs;
it is not a Docker-save or runtime receipt.

Historical artifact authority:
 /home/igor/.cache/exact-directory-repair/backend-artifact-02/receipt.md
and independent-validation.json in that directory. The bundle remains at its
original returned path recorded there. The guarded exclusive-copy operation was
refused for size; Chaldea reports artifact custody pending. In-job hashes do not
establish exclusive or durable transport custody or a fresh local bundle hash.
Image staging is owned by a separate lane.

## Historical snapshot-repair selection

The earlier config/image ID
sha256:91833db853e6dd1949b2810a2a4fdee6f56751354511a7b2e0b76bb7fa065f32
selected product 6b058cf62f580946066b1518ba9898b2ee3a6f7e.
Its historical artifact and staging records are
/home/igor/.cache/ov-curator-20261030/backend-artifact-01 and
backend-image-stage-01. Those records do not describe the new artifact.
That release qualified resource snapshots containing a relations sidecar.

## Preserved personal contract and runtime gates

The historical HTTP selection was config ID
sha256:324b4da5f9aab84f55b517eed9ab4394aca61c92e382583005f4fdd2002126ee,
source b8e678ae07fba3ec49ee142589ea9b61a22f6853. It is superseded by the
[current lifetime-admission HTTP binding](ov-http-compose.md#explicit-local-config-identity).
The configured personal protocol pin remains
7f3faf6982f81116d6626bc7091f87f786a309c7. It is a protocol pin, not the
deployed backend identity. The selected backend retains those personal files
byte-for-byte. Both artifact bindings belong to the
[combined release](ov-exact-directory-release.md).

Root must verify artifact custody and image-only target loading, review the
owning backend CHECK, then perform authorized ordinary Swarm convergence and an
identical zero-change repeat. Preserve current data/account lifetimes, all
unrelated services and timers. No cold cutover, initializer, data restoration,
old-image rollback or queue drain is part of this upgrade.

The held native OV dry run reported invalid_document_uri without dispatch, but
live sidecar presence remains unproven. Source tests and artifact assembly do
not establish target loading, runtime behavior or resolution of that refusal.
After deployment, root owns a fresh native HTTPS OV WebMCP dry run and a bounded
recoverable resource-only pilot before qualifying scheduling. No runtime,
native acceptance, pilot success or hold clearance is claimed by this selection.
