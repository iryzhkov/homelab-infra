# OV curator backend snapshot repair

This exact backend selection repairs resource-document snapshots containing a
relations sidecar. The snapshot retains the sidecar bytes in its typed revision,
protected backup and rollback. Direct control targets and resource reindexing
remain restricted. No personal implementation, grants, owner/profile settings,
credentials, inference selection, mounts or runtime controls change.

## Exact artifact and source qualification

Selected product: 6b058cf62f580946066b1518ba9898b2ee3a6f7e, tree
a7907749c7ec416a4f5b33ca5f2d334c98e44799. Its tree equals independently accepted
6a7df7b074a019891674256044d2ec6fdc78074f. PR7 merged after final CI
41148979733 passed five report-gate tests, 20 document-write cases and 30 reindex
effect-bound cases. The gate rejects missing, skipped and expected-failure cases.

Closed local config/image ID:
sha256:91833db853e6dd1949b2810a2a4fdee6f56751354511a7b2e0b76bb7fa065f32.
Assembly job: 9e650b65-ea2e-4d81-ab4d-a45dce865596.
Patch bundle SHA256:
6e00a2a71b20500adf7c6b4eafe7c434a44575f33c5a1b54dfa74f694dbc4546.
Custody manifest SHA256:
92373caa64219cbab3867b8c1d314ba90c7aaedbda4043b0e410cf7e09594884.

All 915 runtime product files match the selected source. Only
openviking/service/document_write.py and openviking/storage/resource_rfv.py
differ from the previous selected product. Qualified native SHA256
8be7869a9f326ccd6cb67e093216f74296d1e98e93a73290918e6f33744ea1ad,
dependencies and assembler are unchanged. Artifact validation checked all 20
custody entries and the full config and 14 ordered RootFS layers. This is a patch
bundle requiring retained base blobs, not a Docker-save or runtime receipt.

## Preserved personal contract and runtime gates

The HTTP image remains c7030fad1a8a47c8976739aacfdae5e50ffaa93600e1dadf6924f56b54067859,
source 6b30cf13a6193fc61385d08e0d255da58741116f. Its configured personal contract
revision remains 7f3faf6982f81116d6626bc7091f87f786a309c7. That pin identifies the
qualified personal protocol, not deployed runtime identity; the selected backend
retains those personal files byte-for-byte. Actual backend source/image identity
is bound separately by the closed release above. No HTTP rebuild, owner input,
subject, namespace, issuer or credential change is required for this repair.

Root must verify artifact custody and image-only target loading, review the
owning backend CHECK, then perform authorized ordinary Swarm convergence and an
identical zero-change repeat. Preserve current data/account lifetimes, all
unrelated services and timers. No cold cutover, initializer, data restoration,
old-image rollback or queue drain is part of this upgrade.

The held native OV dry run reported invalid_document_uri without dispatch, but
live sidecar presence remains unproven. Source tests and artifact assembly do
not establish target loading, runtime behavior or resolution of that refusal.
After deployment, root must use native HTTPS OV WebMCP for a fresh dry run and
a new bounded recoverable resource-only pilot before qualifying scheduling.
No successful pilot or recurring job is claimed by this selection.

Private receipts: /home/igor/.cache/ov-curator-20261030/backend-artifact-01 and
backend-image-stage-01. Image staging and deployment are separate acceptance
states; these locations do not imply either has completed.
