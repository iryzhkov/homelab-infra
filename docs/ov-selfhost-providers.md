# OpenViking provider migration qualification

Qualification on 2026-10-24 covers a copied OV 0.2.6 store on homelab. Production
cutover, provider selection, client credential replacement and reviews belong to
the root task. This playbook is staging-only and is not imported by site.yml.

## Qualified image and storage

Official source and release:
https://github.com/volcengine/OpenViking/releases/tag/v0.5.2

- Published tag: `ghcr.io/volcengine/openviking:v0.5.2`.
- Multi-platform index: `sha256:d1cc56a356b4d4e81019b3612d7dba01484ef2391055817d9c48c77adead4dcd`.
- Tested linux/amd64 image: `ghcr.io/volcengine/openviking@sha256:2ae2799dac1e606218d9ffeb06af3b65c8218b3556555620a369ed81b7c5aa1f`.
- OCI version: v0.5.2; OCI source revision: `7f3ae1af822d936c1d0b070537772b0f1d59122f`.
  The release text separately names target `d164def87a2a376df6653eab5c206e00109777b1`.
  Do not substitute that text for the actual image provenance.
- Compressed layers total 333,876,575 bytes. Runtime module and /health both report v0.5.2.
- Original 0.2.6 config, /app/data workspace, Viking tree and embedded vector store
  open directly. No intermediate release or storage rewrite was needed for this
  resource corpus. Legacy session migration was not qualified.

Production remains `openviking_openviking`, one replica, at
`git.ryzhkov.dev/igor/openviking@sha256:e9a7222f1f2d4b43bb6e76c50c8a71b70be5d96e7ed0a4a515c1920dcda69b3f`.
Config and data remain under /srv/swarm/openviking. The existing current cloud
models were retained only for baseline validation; no bulk reindex was submitted.

## Retained artifacts on homelab

All paths below are relative to
`/home/iryzhkov/.cache/ov-selfhost-20261024/upgrade-stage`, a private root-owned directory.

- `backup-0.2.6/{config/ov.conf,data}`: quiescent original copy, never mounted.
- `backup-0.2.6.tar`: mode 0400 rollback archive, SHA-256
  `4a1f813fd70a85380bc21e27cbe290d86bea5e9b8d1118ad786052e548c608a3`.
- `production-service.json`: original replica count, image and capture time.
- `viking-manifest.json`: checksums of all 1,915 original Viking files,
  including 532 .abstract.md files and 532 .overview.md files.
- `rest-baseline.json`, `preservation.json`: initial baseline metadata and preservation evidence.
- `candidate-admin.json`: private candidate-only admin credential. Never print or commit it.
- `candidate`: retained first exploratory copy and its candidate-only identity state.
- `candidate-0.5.2/{data,config/ov.conf,compose.json,original-viking.json}`:
  reproducible candidate from the helper.
- `candidate-startup.log`: private diagnostics, never copied into reports.
- `image-runtime-evidence.json`: image labels/digests and actual runtime limits/mounts.

The backup operation recorded one replica, installed an independent 90-second
systemd restore timer, scaled only OV to zero, copied config/data, and restored
the original replica count in a finally block. Quiesce/copy/restore took 30.26
seconds. Production health was confirmed before staging, then the timer was
removed. No production image/config/provider was replaced.

## Reproduce staging

The operator must first create a consistent stopped-service backup at
`STAGE/backup-0.2.6`, containing data/, config/ov.conf and a complete file whose
contents are `quiescent-copy`. A live file copy is not a consistent database backup.
The helper deliberately cannot stop production or fabricate this assertion.

Run on homelab with the retained helper:

```sh
STAGE=/home/iryzhkov/.cache/ov-selfhost-20261024/upgrade-stage
sudo python3 "$STAGE/ov-provider-migration.py" prepare --stage-root "$STAGE"
sudo docker compose -f "$STAGE/candidate-0.5.2/compose.json" up -d
sudo python3 "$STAGE/ov-provider-migration.py" verify --stage-root "$STAGE"
```

Or use an explicitly selected single inventory host:

```sh
ansible-playbook -i /absolute/staging-inventory playbooks/ov-provider-migration.yml \
  -e ov_migration_target=homelab -e ov_migration_enabled=true \
  -e ov_migration_stage=/home/iryzhkov/.cache/ov-selfhost-20261024/upgrade-stage
```

Start is separate and requires `-e ov_migration_start=true`. The candidate publishes
only 127.0.0.1:19335, limits memory and swap to 1536m total and CPU to one, disables
the optional bot, and mounts only its copied config/data. Before the initial
stage, available RAM was about 4.7 GiB; observed baseline candidate RSS accounting
was about 603 MiB. Check headroom again before combining it with model services.

Preparation publishes a complete candidate by rename, rejects partial/existing
unrecognized candidates and symlinks, serializes CLI operations, preserves the backup,
and never overwrites candidate content on rerun. Verification requires every
original Viking file to remain byte-identical. New runtime metadata is allowed.
On failure, inspect the private stage; do not reset it from production or delete
the retained backup. No tests or staging writes use /tmp.

## REST and credential compatibility

OV 0.5.2 api_key mode returns HTTP 403 for the old root key on tenant data APIs.
The error explicitly requires a user/admin key. It also disallows root keys on
native /mcp. Changing only the image would break the existing REST wrapper.

For the isolated candidate, the default account existed after startup. A ROOT
authenticated `POST /api/v1/admin/accounts/default/users` with
`{"user_id":"upgrade-stage","role":"admin"}` issued a candidate-only user_key.
The response was saved privately. Reruns must reuse a retained valid key or use
the documented admin key-regeneration endpoint; do not create duplicate users
or copy this candidate identity into production.

With that tenant credential, unchanged routes work:

- GET /api/v1/fs/ls?uri=viking%3A%2F%2Fresources%2F returns a result list with 18 entries.
- GET /api/v1/content/read for agent-access.md/.abstract.md returns the same
  492-byte string, SHA-256 `3dc9288b1fe20adb1da7ad4aae2561c1067db92b7411b3854e16bac973d934cc`.
- POST /api/v1/search/find retains the memories/resources/skills/total result
  object. Existing wrappers must still group raw chunk/sidecar hits into documents.

All four supplied queries returned expected documents with limit=10:
agent-access-on-normandy.md and agent-access.md; local-llm-ollama.md;
homelab-infrastructure.md; normandy-vm.md and frigate.md.
Initial candidate query times were 0.225–0.360 seconds; the independently
recreated candidate took 0.237–0.757 seconds with the existing cloud embedding
provider. Rankings differ, including archived and unrelated results. This is
route/data compatibility evidence, not local-model retrieval acceptance.
Production's existing wrapper MCP failure remains a separate issue.

## Historical resource-only reindex handoff, superseded

This section records the earlier native resource-only experiment. It is superseded
by [the raw-preserving physical migration procedure](ov-vector-migration.md).
Never use this request as a full-store migration procedure. It omits non-resource
namespaces, and native reindex can change legacy classification and metadata.
Production requires a fresh production-derived offline migration using that guide;
the published 921-row staging index is evidence only.

The helper's `reindex-plan` action prints this historical request and never submits it:

```http
POST /api/v1/content/reindex
Content-Type: application/json
Authorization: Bearer <tenant admin credential>

{"uri":"viking://resources/","mode":"vectors_only","recursive":true,"force":true,"wait":false}
```

The endpoint and schema were confirmed from the running pinned image's OpenAPI.
Force avoids skipping unchanged source hashes when changing embedding model.
Recursive resources scope targets resource L0/L1 summaries and L2 content vectors;
it does not cover user/agent/skill/session namespaces. Existing .abstract.md and
.overview.md are inputs, not requests to regenerate summaries. Namespace metadata,
empty or unsupported content can affect counts; 1,915 files is a preservation
count, not an asserted vector count. Check returned counters, failures and task
completion rather than treating submission as success.

Source qualification:
https://github.com/volcengine/OpenViking/blob/v0.5.2/openviking/service/reindex_executor.py
and https://github.com/volcengine/OpenViking/blob/v0.5.2/docs/en/api/02-resources.md

The earlier candidate plan required a prepared internal OVMS endpoint and
validated query instruction/config seam. That plan is superseded. Equal
1024-dimensional outputs do not make old OpenAI vectors comparable to Qwen vectors.
Do not expose mixed-model retrieval as accepted. Preserve the original index;
do not clear production or regenerate summaries. The historical request returned
a task_id tracked through GET /api/v1/tasks/{task_id}; submission alone was never
acceptance. Use the superseding migration guide's metadata and physical-coverage
checks for the controlled cutover.

The selected embedding providers use the identical Qwen3-Embedding 0.6B Q8_0
GGUF on the gaming PC GPU and Normandy CPU. The GGUF SHA-256 is
`06507c7b42688469c4e7298b0a1e16deff06caf291cf0a5b278c308249c3e439`.
The earlier OpenVINO int8 runtime was a qualification alternative; its vectors
are a different embedding space and it is not a fallback for this index.
See [the provider integration guide](../stacks/openviking/PROVIDERS.md) for
checksummed assets, query instructions, runtime bounds and deployment commands.
The internal adapter adds the Qwen query instruction and uses native Ollama
`truncate:false`; documents remain plain. Existing summaries remain unchanged.

Client credentials must also transition before production cutover. OV 0.5.2
rejects the old ROOT key on tenant data routes. The guarded rollover updates
existing per-request keyring or key-file sources and preserves private backups;
see [client credential cutover](ov-client-credentials.md). No MCP restart is
needed for the observed bindings, but environment or cached TOML overrides
must be refused and resolved through their owners. Candidate credentials are
not production credentials. Final generation transport qualification and
real-note retrieval acceptance remain required before production cutover.

## Implementation checks

All executable code checks ran through Chaldea on agent-a, with HOME asserted to
a scratch home and TMPDIR under that home's .cache/ov-selfhost-20261024 directory.

- Job `5a570312-5adb-4e20-9470-256003bcf080`: five failure-boundary unit tests passed.
- Final source job `05f598a2-c83e-4e78-a487-c2421083f56c`: five unit tests and
  Ansible syntax check passed.
- Job `da93a84f-16c4-4281-8297-99eed8a0db9f`: Ansible check-mode scope/path/copy
  tasks passed against a scratch stage, with prepare/start correctly skipped.
  Fixture privilege escalation was disabled because the Chaldea user namespace
  has no root passwd entry. The preceding fixture job
  `a8ea1776-4482-4384-bf7f-05ce209f9e7b` failed at sudo for that reason.

On homelab, the guarded helper's prepare returned changed=true, the repeated
prepare returned changed=false, and post-start verify retained all 1,915 original
files with 50 additional runtime metadata files. Recreated health/readiness and
all six authenticated REST baseline requests passed; every expected document was
present. No vector reindex or production cutover was run.
