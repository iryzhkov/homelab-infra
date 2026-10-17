# Akasha v1 infrastructure runbook (definitions only)

Authority: `jocasta:2d21001bcb0769a46f3331f858935cdb@1`, the full
2026-10-17 step-7 brief. This supersedes the broad deployment sequence in
the original PR #10. Nothing here has been deployed. Every command below
that changes a host needs a separately approved rollout.

## Hosts and scope

The selected controller is **omarchy-pc**: local checkout, checks and operator
NATS contexts. **homelab** is the only deployment target: existing single NATS
Swarm service, new Akasha system service, local DB, health and backup timers.
The targeted playbook has `hosts: homelab`; broker deployment is limited to
homelab and the stacks tag.

**public-api** and **public-api-b** are the two existing HTTPS edges. They
are *not* touched by these playbooks. A later HTTPS ingress route to Akasha
must name and obtain approval for both hosts before changing them; use a
separate targeted routing change. The existing authenticated NATS WSS route
is reused without edge changes. **normandy**, **agent-a**, **agent-b**,
**laptop** and the NAS have no rollout action in this lane. Client canaries
and step-6 trust deployment belong to their owners and need their own host
list/approval. No broad site convergence, agent-tools build or fleet rollout.

## Required subset and parked definitions

- Required NKeys: SYS `sys-admin` and CHALDEA `chaldea-admin` on omarchy-pc,
  and CHALDEA `akasha` for the service on homelab. This lane handles no
  credentials. Provision private material out of band with its owner.
  Only public keys go in `inventory/group_vars/swarm/nats.yml`.
- `nats_chaldea_hosts: []` is enforced before any deployment work.
  Optional future work/quota identity definitions and public-key slots are
  parked. A nonempty list is refused: host JetStream APIs can redirect their
  replies into Akasha subjects even without a publish grant. Enabling hosts
  requires account isolation and new review. Disabled keys may stay empty;
  the three required public keys must pass the pinned server parser.
- Gateway owns `akasha.>` within CHALDEA, not SYS. JWT source registry
  authorization is the service's job; JSON host/source fields are not
  authentication. No host user may be enabled in this shared account.
  The protocol test rejects host authentication plus direct-publication and
  API-reply redirection attempts before any event can enter the stream.
  C ingest must also reject redirected `Nats-Stream` messages and envelopes
  without a matching admitted outbox/receipt; that is defense in depth, not
  a substitute for the broker identity boundary.
- Akasha can pull/ACK only `akasha-ingest`, inspect its stream/consumer and
  get events by sequence; replies use `_INBOX_akasha`. It cannot create,
  update, purge or delete streams/consumers. The operator creates them.
- `AKASHA_EVENTS`: file/LimitsPolicy, discard old, replicas 1, 14d or 1 GiB,
  maximum 8 consumers, 10m dedup, `max_msg_size=327680`. Headers count
  towards NATS's message size: the C publisher must reserve space for
  `Nats-Msg-Id` and `Nats-Expected-Stream` inside that cap. Never truncate
  an envelope to make it fit. Legacy ingress is still capped at 256 KiB.
- `akasha-ingest`: durable pull, explicit ACK, 60s ack wait, 10 deliveries,
  1000 ACK pending, maximum batch 500 (application fetch at most 100).
  Commit projection, receipt and cursor before ACK. PubAck proves broker
  persistence, not SQL visibility. Alert on lag, exhausted delivery and
  sequence gaps before eviction; 14d is a replay buffer, not history.
- Account/server caps remain 2 GiB/4 GiB file and 16 MiB/128 MiB memory.
  `max_bytes_required` and 1 GiB per-stream limit remain enforced.
- CHALDEA_WORK, its fleet/host consumers and CHALDEA_QUOTA definitions stay
  parked. Apply defaults to only AKASHA_EVENTS and its consumer;
  `--stream AKASHA_EVENTS` makes the choice explicit. `--all` requires a
  separate work/quota rollout. Apply never deletes anything.
- Garage, S3 routes/probes, bootstrap, secrets/keys and NAS mounts remain
  parked. The foundation's old 10/50 GiB buckets are not approved for use;
  revisit the newer plan if real blob storage becomes necessary. Loki
  retains raw logs for 30d; no logging infrastructure changes here.

## Runtime handoff and enablement blockers

C owns the accepted Akasha artifact and exact invocation. C owns the host trust updater, clock-health adapter and signing CLI in its versioned
host artifact. F installs the pinned version and SHA-256 and renders configuration. This role deliberately does not guess
their CLI/config schema or edit their trust/sandbox roles. Before activation:

1. Freeze reviewed C/access/artifact commits, install the immutable artifact
   under `/opt/akasha` with pinned Node **26.10.0**, and supply
   `akasha_exec_start` beginning with `/opt/akasha/`. Do not compile on
   homelab. The role neither downloads nor replaces application artifacts.
2. Owner provisions `/etc/akasha/runtime.env` (root-owned 0600), gateway NKey
   (service-readable 0600 in a private directory), public trust bundle and
   clock-health adapter. No shared feed key, Loki credential or host NKey
   goes to restricted runs or agent-a/b.
3. Verify the command/config binds HTTP to **127.0.0.1:1935**, distinct from
   feedd's **:1934**, DB to **/var/lib/akasha/events.db**, and NATS to
   `wss://nats.ryzhkov.dev` with `_INBOX_akasha`. The environment file is
   only a systemd input, not a promise about C's variable names.
4. Configure read-only adapter paths `akasha_trust_bundle` and
   `akasha_clock_health`; ensure the service can traverse/read them and
   that updater replacements remain visible. Missing, stale or rollback
   trust/clock evidence must fail closed. Verify restart high-water state,
   freshness (120s fetch, 24h maximum signed-bundle age), clock evidence refreshed
   within 30s with healthy offset <=30s, and scoped
   token vectors with F/C. Never restore older trust/revocation high-water
   state. Set `akasha_interfaces_verified=true` only after this evidence.
5. Verify local filesystem, free disk and existing host-wide Chaldea
   allocation. Role permits ext4/xfs/btrfs at /var/lib, never NFS/CIFS.
   DB/WAL/SHM and all auxiliary service state must be 0600 in service-owned
   0700 directories; unit UMask=0077. One writer, WAL + FULL synchronous,
   checkpoints and bounded readers are C's enforcement responsibility.
   Main DB <=4 GiB (including receipts/indexes/outbox <=128 MiB/quarantine
   <=16 MiB), WAL <=64 MiB, total state/backup <=10 GiB. Preserve protected
   pending intents and unexpired receipt/trust history; fail admission with
   503 on protected-state or disk exhaustion. Prune hourly/startup, summaries
   180d, tool calls 90d, receipts 180d. Maintenance alarms do not replace
   application admission/capacity enforcement.
6. Complete backup packaging and alert routing below; only then set
   `akasha_backup_packaging_verified=true`. These flags record operator
   verification, not automatic proof of deployment readiness.

The path contract comes from `chaldea_access_paths` in
[chaldea-fleet-config inventory](https://github.com/iryzhkov/chaldea-fleet-config/blob/main/inventory/group_vars/all.yml).
Render the Akasha runtime JSON and adapter settings from that same mapping:
`bundle_file` / `akasha_trust_bundle` use `bundle`;
`clock_health_file` / `akasha_clock_health` use `clock_health`;
`access_state` uses `verifier_state`. Do not invent Akasha-specific bundle
extensions or clock paths. The updater's private high-water database uses
`updater_state`; a verifier owns its own database in a separate service-owned private directory,
using `verifier_state`. Never share one SQLite database between updater and verifier,
change its ownership in place, or restore old high-water state.

Import the reviewed mapping when activating this role; homelab activation remains
blocked until that configuration and service-user access have been verified. This
runbook change does not enroll homelab or apply any fleet role.

## Narrow rollout, after approval

First commit only actual public keys for the three required identities.
On omarchy-pc, using the repository's Ansible environment:

```sh
ansible-playbook playbooks/swarm.yml --tags stacks --limit homelab -e only=nats --check --diff
ansible-playbook playbooks/swarm.yml --tags stacks --limit homelab -e only=nats
NATS_CONTEXT=chaldea-admin bash scripts/nats-jetstream-apply.sh --stream AKASHA_EVENTS --check
NATS_CONTEXT=chaldea-admin bash scripts/nats-jetstream-apply.sh --stream AKASHA_EVENTS
NATS_CONTEXT=chaldea-admin bash scripts/nats-jetstream-apply.sh --stream AKASHA_EVENTS --check
ansible-playbook playbooks/akasha.yml --limit homelab --check --diff
ansible-playbook playbooks/akasha.yml --limit homelab
```

The first Akasha apply stages definitions only and refuses to overwrite a
running/enabled installation with the inactive defaults. Always retain the
approved variables for subsequent updates. After owner verification,
use a private variables file containing the accepted invocation, verified
paths and three explicit true values (activate/interfaces/backup packaging);
do not include credential contents in commands, PRs or logs. Check then
apply `playbooks/akasha.yml --limit homelab -e @<approved-vars>`.
No edge route or client cutover occurs through this playbook.

Confirm only AKASHA_EVENTS and akasha-ingest were created, second apply
matches, NATS caps/ACL negatives pass, and :1934 feedd continues unchanged.
Check local `GET http://127.0.0.1:1935/healthz` => 200 `{"ok":true}`,
service/timer status, disk bounds and backup age. Application health must
include admission, trust/clock, outbox/consumer lag, sequence gaps and
delivery exhaustion; a successful local HTTP probe alone is insufficient.

## Backup, RPO and restore

Proposed disaster RPO is **<=24h**, subject to Igor's confirmation and a
measured off-host shipping schedule/restore. No HA or zero-loss claim.
NATS state is `/srv/swarm/nats/data/jetstream` on homelab local disk
(container `/data/jetstream`); VM backups exclude it. Stream retention does
not protect against homelab disk loss.

The hourly backup unit uses SQLite's online backup API against events.db,
checks integrity and fsyncs a single 0600 snapshot at
`/var/lib/akasha/backup/events.db`. The snapshot includes events,
projections, receipts, cursors and outbox in the same database. It never
copies a live DB/WAL pair. It has 5min/4 GiB bounds and local disk reserve.

At the 4 GiB limit, retaining old + staging + live DB would exceed 10 GiB.
The helper therefore removes the prior local generation before making the
next. A failed/interrupted snapshot can leave no local snapshot: health
fails and the last off-host packaged generation is the fallback. Do not
enable this schedule without that verified fallback and backup-failure
alerts. Snapshot work is serialized by its systemd oneshot unit. First
snapshot is due within 10 minutes of activation; initial missing-backup
health is expected until it completes.

Integrate the snapshot path into homelab's **existing backup packaging**
with its owner, preserving 0700/0600 and encryption/access restrictions;
do not add a NAS mount or run the broad backup role. The backup package
must acquire a stable snapshot between hourly runs (stop the timer and wait
for the oneshot, or use an equivalent exclusion lock), copy/verify it and
resume the timer. Never package live events.db, SHM/WAL or temp files.
Prove that at least one off-host copy is <24h old and that failed/stale
packaging and `akasha-health.service`/`akasha-backup.service` failures reach
the existing operator alert channel. The local probe checks snapshot age,
HTTP and physical disk bounds; it cannot prove off-host shipping or detect
all ingest gaps. No new global Gatus/edge deployment is implied.

Restore in isolation first: stop intake/consumer and timers; restore a
consistent snapshot to private local disk; check integrity, receipt
idempotency, legacy IDs/allocator and cursors. Preserve independently
verified current trust/revocation/clock high-water state and refresh the
signed bundle before admitting requests. Replay the available JetStream
tail with SQL uniqueness; expose missing sequences as gaps. Restore tests
must include receipts and cursors, loss after PubAck and redelivery after
SQL commit. The lane-I test proves consistent WAL backup of receipts/cursors;
C owns end-to-end admission/ACK/crash recovery evidence.

## Parity and rollback

Keep feedd and its shipper through at least 7d measured parity and 7d
rollback observation. Step-9 retirement and shared-key rotation require
separate approval and a complete consumer inventory. To roll back this
service, stop/disable only akasha and its timers, retain its state and keep
feedd on :1934. Never delete streams, receipts or imported IDs as rollback.
Reverting the gateway ACL to host-publish permissions is not a safe rollback.
