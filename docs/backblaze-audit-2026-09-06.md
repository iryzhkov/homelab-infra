# What was in the Backblaze bucket, and what is not any more

Audited 2026-09-06, object by object, after the restore test showed the bucket
held things nobody had looked at in a while.

## Before

| | Objects | Size |
|---|---|---|
| `apps/` | 141,221 | 33.3 GiB |
| `data/` (the Immich library) | 65,463 | 160.1 GiB |
| **current versions** | **206,684** | **193.3 GiB** |
| **including superseded versions** | **347,382** | **289.3 GiB** |

## After

131,481 objects, 135.8 GiB — and `rclone size` and `rclone size --b2-versions`
now report the same number, meaning nothing is hidden behind what is stored.
153.5 GiB removed, 53% of the bucket.

A third of the bytes were versions of files that had since changed. The bucket
has no lifecycle rule, so B2 had kept every one of them since 2026-03.
Vaultwarden's directory was the clearest case: 4 MB of current files, 90 MB
with history.

## Why it accumulated

Two mechanisms, both working exactly as configured.

`rclone copy` never deletes. Anything that was ever backed up stays backed up,
so the bucket still held `jellyfin` and `shai` and `n8n`, services that have not
existed on the host for months, and 152 Postgres dumps where the host keeps 14.
An exclusion added later does not remove what an earlier run uploaded, which is
why 5.5 GB of Emby metadata sat there under a `metadata/**` exclude.

B2 keeps every version. Deleting or overwriting a file hides it rather than
removing it, and without a lifecycle rule nothing ever collects the hidden ones.

## What was removed

61.7 GB across roughly 75,000 objects, hard-deleted rather than hidden:

| What | Size | Why |
|---|---|---|
| `data/immich/backups` | 35.5 GB, 371 files | Immich's own database dumps. It prunes them to 14 locally; normandy's `pg_backup.sh` covers that database properly |
| `apps/homelab/emby/sync` | 6.2 GB, 2 files | Sync conversions: transcodes of media we already hold |
| `apps/homelab/backups/postgres/immich_*` | 5.7 GB, 49 dumps | From before Immich moved to normandy on 2026-08-30, and ten days staler than the real ones |
| `apps/homelab/emby/metadata` | 5.5 GB, 30,805 files | Rebuilt by a library scan; already excluded, uploaded before the exclude existed |
| `apps/homelab/shai` | 4.8 GB | Service archived 2026-08-30, source in Forgejo |
| `apps/homelab/jellyfin` | 1.8 GB | Superseded by Emby, gone from the host |
| `apps/homelab/immich` | 883 MB | The old homelab stack directory, kept on the host as a rollback path that is now a week stale |
| `apps/homelab/emby/logs` | 837 MB | Logs |
| `apps/homelab/dsh/data/npm-cache` | 380 MB, 31,290 files | A package cache |
| `apps/homelab/backups/cloud-sync-stage` | 62 MB | Left from the abandoned 2026-05 cloud failover project |
| `n8n`, `open-webui`, `ollama-proxy`, `test`, `immich-stack` | under 1 MB | Services that no longer exist |

**`shai` and `jellyfin` existed nowhere else.** Their B2 copies were the last
ones. shai's source is in Forgejo; jellyfin's configuration is gone for good,
which is the intended outcome for a service replaced two years ago, but it is
worth being clear that this was not a copy of something.

## What stops it coming back

**Excluded from future uploads**, in `roles/backup/files/rclone_exclude.txt`:
npm caches, `emby/sync`, Immich's `thumbs` and `encoded-video`, and
`immich-db-backup-*`.

**`--b2-hard-delete` on the hourly and daily syncs**, so an expired snapshot is
removed rather than hidden. Without it the hourly tier would have added about a
gigabyte and a half of invisible history every month.

**A bucket lifecycle rule**, set in the B2 console the same day:
`daysFromHidingToDeleting: 30`, `daysFromUploadingToHiding: null`, whole
bucket. Superseded versions are collected after a month; current files are
never hidden by age, which is the half that matters — the other setting would
expire live backups on a timer.

The weekly tier that used to run `rclone cleanup` now checks that rule instead
of standing in for it. A rule that quietly disappeared would take months to
notice, and by then the bucket would be back where this audit found it.

With the rule in place the syncs no longer pass `--b2-hard-delete`: a pruned
snapshot is hidden for thirty days before Backblaze collects it, so a mistaken
prune is recoverable. The window costs about a gigabyte and a half.

## What was deliberately kept

The Immich library itself, 102 GB of photographs, and its `upload` staging
directory. Every service configuration that still runs. The Postgres dumps for
authentik and miniflux. Vaultwarden's attachments and icon cache, which are
files rather than database rows and are not in the hourly snapshots.

Immich's `thumbs` and `encoded-video`, 21 GB, are excluded from future uploads
but were left in place: they are regenerable, so deleting them is safe, but
regenerating 42,000 thumbnails is a slow afternoon and the storage is worth
about a penny a month.
