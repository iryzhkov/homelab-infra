# Preserve legacy search records during embedding migration

`scripts/ov-vector-migration.py` replaces dense embeddings in a private clone of a
legacy collection. It requires exactly OpenViking 0.5.2. Native `vectors_only`
reindex derives context types from paths and does not preserve every legacy
classification. Use this offline path when stable IDs, legacy memory and skill
types, ownership and ACL metadata must survive.

The tool snapshots the retained database before opening the SDK. It re-hashes the
cached snapshot against the original source binding on every resume, regardless
of its ready marker. All SDK reads use a fresh disposable clone, because opening
the SDK can recover or write collection state. The immutable snapshot supplies
the candidate copy and remains hash-checked before publication. Raw metadata is
captured from the disposable clone before opening the collection API.
Physical candidate rows are enumerated with the pinned SDK's version-aware
`CandidateData.from_bytes` codec. The collection API fetches every physical ID.
The codec's schema is the candidate storage schema, not the collection field
schema. No identity-filtered HTTP scroll defines migration coverage.

Each retained record keeps its original scalar JSON byte-for-byte, including
absent legacy fields, ID, URI, level, context_type, account and ownership fields,
ACL grants, timestamps and stored text. Sparse values, physical label and expiry
also remain exact. Only the dense vector changes. The collection description\'s
embedding provenance changes to the new model. Raw metadata equality, a logical
24-field SDK comparison and a physical row recount must pass before publication.

High-level Collection.update_data expands 879 legacy rows with seven absent
scalar defaults, including owner_user_id and ACL fields. It must not be used
for this migration. The pinned offline StoreManager.add_cands_data API preserves
CandidateData.fields, emits the SDK delta records, and collection recovery
replays those records into the index. These internal SDK interfaces are version
pinned. A future SDK requires a new qualification.

## Inputs and recovery

Use an immutable vector-store copy, the original Viking filesystem and its
SHA-256 manifest. Level 0 reads `.abstract.md`, level 1 reads `.overview.md`,
and level 2 reads the exact content file. Paths include the record's account ID.
The tool never regenerates summaries, truncates documents, changes identity
records, adds unindexed namespaces or writes source files.

All manifest files are checked before and after migration. A missing source is
a blocker. An optional exclusions JSON maps exact record IDs to evidence-backed
reasons. An exclusion is accepted only when its source file is absent and its
path never appeared in the original manifest. Absence alone does not justify
calling a record stale.

Work, source, filesystem and publication paths must be disjoint. Keep them on
disk in private directories. Embeddings are cached with text, metadata, model
and context digests. A request is journaled before sending and its complete
result is fsynced before updating the clone. An interrupted stateless request
requires an explicit `--retry-uncertain`; its result may be computed again.
Completed cache entries are reused. SDK writes rebuild an unpublished clone on
resume. Source changes or cache binding changes stop migration.

The embedding endpoint must be fixed IPv4 HTTP loopback. Proxies and redirects
are disabled. The expected Ollama model manifest is
`d84267e5df12e19a8be357ab23b88966d61137226724f399707dee270cebccd3`,
with GGUF SHA-256
`06507c7b42688469c4e7298b0a1e16deff06caf291cf0a5b278c308249c3e439`.
Requests use native `/api/embed`, plain document text, normalized 1024-dimensional
vectors and `truncate:false`. SDK logging is disabled because rejected-record
logs can contain private text. Retain operational stdout/stderr privately too.

Publication is a same-filesystem directory rename under a private parent lock,
after validation. The destination must not exist. This produces a new offline
index directory; it does not retarget any running server. Keep the retained
source and original backup unchanged.

Example with separate read-only input mounts and a private work mount:

```sh
python /migration.py \
  --source /old --files /files --manifest /manifest.json \
  --work /work/ingest --publish /work/candidate-lossless-raw \
  --endpoint http://127.0.0.1:19436 \
  --exclusions /work/exclusions.json --long-overviews-8192
```

Use the candidate image
`ghcr.io/volcengine/openviking@sha256:2ae2799dac1e606218d9ffeb06af3b65c8218b3556555620a369ed81b7c5aa1f`
with `/app/.venv/bin/python` as entrypoint. Mount the retained vectors,
filesystem, manifest and script read-only. Bind only the work directory writable.
The acceptance run used two CPUs, 2 GiB RAM and no swap.

## Full-document oversize strategy

Default ingest context remains 4096. The explicit
`--long-overviews-8192` option selects 8192 only for level-1 records at these
four original paths:

- `/agent/ffb1327b18bf/memories/cases`
- `/user/default/memories/entities`
- `/user/default/memories/events`
- `/user/default/memories/preferences`

The matched Qwen3 0.6B Q8_0 GGUF advertises a 32768 context and RoPE frequency
base 1000000. No weights, query prefix, pooling or document text change.
Qualification on isolated PC Ollama 0.40.5 measured full input lengths of
6068, 6910, 6492 and 4185 tokens respectively, with 0.193, 0.205, 0.252 and
0.200 seconds per embedding. Two short inputs produced identical vectors at
4096 and 8192, cosine 1.0.

At 8192 residency, process memory was 746196992 bytes, observed peak 748072960
bytes, Ollama model VRAM was 1715396935 bytes and GPU free memory was 8588 MiB.
The isolated runtime retained its 2 GiB RAM, two CPU and no-swap limits.
Preflight free VRAM was 10717 MiB. The migration's later observed process peak
was 869380096 bytes while switching context capacity. The original Ollama
11434 service and its MAX_LOADED_MODELS setting were untouched.

A larger PC context can support these documents online with the same weights
and short queries. The interactive adapter also needs a matching route-specific
token guard; changing the runtime alone cannot bypass its current 4096 guard.
CPU fallback can retain 4096 for short queries. An oversized CPU ingest must
fail clearly. This migration does not edit adapter or runtime policy.

## Candidate evidence and promotion boundary

Private homelab evidence is under
`/home/iryzhkov/.cache/ov-selfhost-20261024/upgrade-stage/vector-migration/`.
The validated offline output is `candidate-lossless-raw/`, with
`ingest/report.json`, per-record embedding journals and
`long-context-qualification.json`. Keep journals private because they contain
vectors. The retained cloud collection is
`../acceptance/vectordb-cloud-before/`.

The source contains 922 physical rows, 701 resource, 211 memory and 10 skill.
The API's three-identity union contained 918 rows and is not the physical
inventory. The single excluded record
`98890402f175edd8907aa89ec3484a4f` was an absent candidate credential probe,
never an original manifest entry. Output contains 921 rows, 700 resource,
211 memory and 10 skill, including 881 original-manifest searchable records.
All retained metadata matches, including identity and ACL fields. The lost
identity group belongs exclusively to the excluded fixture.

The 938 session files and 18 system files remain preserved. The old search
index did not cover them. This migration neither adds that scope nor calls it
a regression.

The published 921-row index is staging evidence only. Its source includes
candidate bootstrap records. The physical row count of the original production
cold backup was not measured in this task; do not infer it from 922. Earlier
candidate-lossless and candidate-lossless-final directories expanded absent
scalar defaults and are rejected intermediate evidence, never promotion inputs.

Root owns review, candidate promotion and production cutover. For candidate
promotion, stop only the candidate server under root's authority, retain its
current vectordb directory as rollback, copy the validated output to a sibling
directory on the candidate data filesystem, then rename that sibling to
`data/vectordb`. Never use an index directory concurrently with the SDK or
another server. Restart the candidate with reviewed provider wiring and repeat
identity-scoped HTTP and wrapper acceptance. Rollback stops the candidate,
retains the attempted index for diagnosis, and restores the previously retained
vectordb directory. No auth database or original filesystem is replaced.
Production requires root's separate cutover decision and a fresh migration:

1. Quiesce production writes and pending indexing under root's approved cutover
   procedure. Capture a fresh consistent cold backup of the live vectordb and
   Viking filesystem, with hashes and a current full manifest. Retain the
   existing immutable 0.2.6 backup untouched. Its archive SHA-256 is
   4a1f813fd70a85380bc21e27cbe290d86bea5e9b8d1118ad786052e548c608a3.
2. Reconcile added, changed and removed live files against the accepted backup.
   The earlier check of 1915 unchanged originals does not prove cutover-time
   consistency. Capture every newly searchable physical row and its actual file.
3. Run this helper in a new private work directory against that fresh production
   cold vector store and matching live files/manifest. Inventory that source
   independently. Do not use the staging exclusions file blindly. An absent
   real original is a blocker; any stale exclusion needs its own evidence.
   Keep 8192 embedding isolated to this migration. Live adapter ingestion stays
   at 4096 in this release.
4. Validate raw and logical metadata, original files, vector dimensions,
   physical coverage, identity visibility and real wrapper retrieval on a copy
   of that freshly produced index. If writes resumed during preparation,
   quiesce again, reconcile and rerun from consistent input before switching.
5. Install only the fresh production-derived vectordb under root's approved
   service stop/switch/start procedure. Never copy candidate bootstrap data,
   candidate auth state or the published 921-row staging index into production.
   Preserve the previous production index and original file/auth state for
   rollback. Rollback stops the upgraded service and restores its compatible
   prior image, configuration and index from the matched cold backup.

No production state or runtime policy is changed by this helper.

The isolated HTTP acceptance server uses a disposable copy of candidate data
and of the published index. Its writes do not affect the published directory.
Temporary adapters, inference runtimes, containers and SSH tunnels must be
removed after acceptance. Preserve the offline output and private evidence.

## Verification

Run the pinned real-SDK tests only through Chaldea agent-a:

```sh
chaldea exec --host agent-a -- sh -c '
  export HOME="$PWD/.cache/ov-selfhost-20261024/test-home"
  export TMPDIR="$HOME/scratch"
  test "$HOME" = "$PWD/.cache/ov-selfhost-20261024/test-home" || exit 90
  mkdir -p "$TMPDIR"
  uv run --with openviking==0.5.2 python scripts/test-ov-vector-migration.py
'
```

Tests exercise the actual 24-field SDK round trip, raw legacy absent-field
preservation, context and ACL fields, missing original-file refusal, journaled
interruption and explicit recovery, physical recount, and refusal to overwrite
a publication. Final agent-a job 03eed89c-cd5a-4533-b109-d2818ed0495a passed.

The corrected raw-preserving index passed the installed unmodified wrapper:
both GPU and CPU queries achieved 9/14 target top-1 and 14/14 target top-5,
compared with cloud baseline 10/14 and 13/14. The two host-comparison diagnostic
queries are excluded from those totals. All 32 ordered CPU/GPU wrapper results
matched. Median find/search calls were 0.0697/0.07085 seconds on PC and
0.3295/0.31565 seconds on CPU at 4096. Three-identity API union coverage was
918 to 917 with only the excluded fixture missing, zero added IDs and zero
projected metadata changes. Individual visible counts were 752/752/894 before
and 751/751/893 after. Private evidence includes
http-identity-comparison-raw.json, retrieval-summary.json and
runtime-qualification.json. Root-local detailed wrapper reports are in
/home/igor/.cache/ov-selfhost-20261024/candidate-acceptance/
wrapper-lossless-raw-{gpu,cpu}.json. Scoring measures expected document presence,
not independently adjudicated answer correctness.
