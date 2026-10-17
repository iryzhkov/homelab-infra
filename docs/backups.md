# Backups

Three tiers, two of which this repository owns.

| Tier | What | When | Where |
|---|---|---|---|
| Hourly | Vaultwarden and Actual Budget, one consistent snapshot per database | every hour | `apps/homelab/backups/sqlite`, `rclone sync` |
| Daily | InfluxDB, a full `influx backup` archive | 00:40 | `apps/homelab/backups/influxdb`, `rclone sync` |
| Weekly | a check that the bucket's lifecycle rule is still sane | Sunday 03:20 | one API call |
| Daily, normandy | Immich's Postgres dump and a tar of `~/app` | 01:30 on normandy, shipped 06:40 by homelab | `/mnt/backup/normandy` on the NAS, then `apps/normandy`, `rclone copy` |
| Hourly, Swarm workers | the stateful Swarm stacks: SQLite snapshots, `pg_dump -Fc`, file mirrors | every hour, on the worker holding the stack | `<pool>/snapshots`, the other worker's `<pool>/peer-snapshots`, then `apps/<worker>/swarm` from homelab, `rclone sync` |
| Twice weekly | everything else: `~/app`, `~/apps`, `~/cv-site`, the Immich library and black-pearl's NAS trees, homelab's own Postgres dumps (`homelab-pg-dump`, `roles/backup/tasks/pg_dump.yml`, which replaced the hand-written `pg_backup.sh` on 2026-10-02) | Mondays and Thursdays, 01:30–06:00 | various, `rclone copy`, still in cron |

The hourly, daily and weekly tiers, normandy's daily dump and the Swarm tier
are `roles/backup`, deployed by `playbooks/backup.yml`. That role also sets the schedule of
normandy's shipping line in homelab's crontab. The twice-weekly jobs are the
pre-existing crontab on homelab and are otherwise untouched.

## Why the hourly tier exists

The 2026-09-06 restore test pulled Vaultwarden's database out of B2 and found
its `db.sqlite3` dated six days before its own write-ahead log: rclone had been
copying the three SQLite files independently while the service wrote to them.
SQLite recovered from that pair, but nothing had made sure it would.

`homelab-sqlite-backup` uses SQLite's online backup API instead, which produces
one file consistent by construction, then gzips it. The databases are small —
Vaultwarden compresses to 210 KB, the largest Actual budget to 1.2 MB, nine
databases and 2.1 MB in total, taking three seconds — so hourly is affordable
in a way that copying the live files never needed to be.

Actual keeps one database per budget under a UUID name, so that entry is a
glob: a budget added later is backed up without an edit to the inventory.

48 snapshots are kept, which is two days of hourly history, and `rclone sync`
mirrors the pruning offsite. The twice-weekly jobs use `copy` on purpose —
nothing there should vanish from the bucket because it vanished from the host —
but a rolling window is exactly the case where mirroring is right.

## Why the daily tier exists

InfluxDB had no backup at all. Its data is in Docker named volumes, which are
outside `~/app` where the rclone jobs look, and unlike Postgres it had no dump
job: 400 MB of Home Assistant history with no copy anywhere.

`influx backup` is used rather than copying the volume, because it is the only
way to get a consistent picture out of a running InfluxDB. The archive is
217 MB, takes 43 seconds, and seven are kept.

## Normandy's daily Postgres dump

Since phase 2 (2026-10-02) normandy's `/mnt/data` is a `backup=0` zvol, so
vzdump no longer holds Immich's Postgres data directory and this dump is the
only offsite copy of the database. It is `backup_nas_dump` in
`inventory/host_vars/normandy.yml`, deployed by `roles/backup/tasks/nas_dump.yml`
as `normandy-backup.timer` (daily 01:30) and `~/.local/bin/homelab-nas-dump`.
The script runs `pg_dump` inside `immich_postgres` over the container's local
socket, writes `/mnt/backup/normandy/postgres/immich_*.sql.gz` through a
`.partial` file it checks with `gzip -t`, tars `~/app` into `config/`, and keeps
14 days of both. A failure goes to the feed and the phone.

homelab ships `/mnt/backup/normandy` to `b2:igor-offsite-backup/apps/normandy`
with the crontab line `homelab-bulk-backup normandy`. Its schedule comes from
`backup_nas_dump.ship_cron` (`40 6 * * *`), and `roles/backup/tasks/ship_schedule.yml`
rewrites that one line in place. The offsite RPO for Immich's database is about
a day; it was up to four days when both jobs ran Mondays and Thursdays. Because
the shipping job uses `rclone copy`, every daily dump used to stay in the
bucket, about 185 MB a day. Since 2026-10-02 `homelab-bulk-backup normandy`
follows the copy with `rclone delete --min-age 30d` on `apps/normandy/postgres`
and `apps/normandy/config`, but only when the bucket holds a dump from the last
three days, so a dump job that stopped can never empty it. The bucket's
lifecycle rule then removes the hidden versions 30 days later: about 30 days of
dumps are live and up to 30 more recoverable.

Immich also writes its own nightly dump (02:00 UTC, 14 kept) to
`/mnt/shared/immich/backups` on the media share. `rclone_exclude.txt` keeps
those out of Backblaze, so they are a second NAS copy only.

## Swarm stacks

A stateful Swarm stack (phase 5) keeps its state under `/srv/swarm/<stack>`, a
symlink to `<pool>/swarm` on whichever worker holds the stack's
`pin.<stack>` label (`<pool>` is `/mnt/docker-data` on homelab, `/mnt/data` on
normandy; both are `backup=0` zvols, outside vzdump). The tier is
`roles/backup/tasks/swarm.yml`, configured in
`inventory/group_vars/swarm/backup.yml`, and each stack declares what to
snapshot in the `snapshot` key of its `swarm_stacks_*` entry.

Every hour `homelab-backup-run hourly` (both workers) runs
`/usr/local/sbin/homelab-swarm-snapshot` as root. For each stack whose
`/srv/swarm/<stack>/.swarm-owner` names this host, it

- snapshots the SQLite databases (`homelab-sqlite-backup`, 48 kept) and dumps
  the Postgres databases (`pg_dump -Fc` inside the running task, 48 kept) into
  `<pool>/snapshots/<stack>/`;
- mirrors that directory to the other worker's
  `<pool>/peer-snapshots/<this host>/<stack>/snapshots/`, and each `files:`
  directory to `.../<stack>/files/<dir>/` (`--delete`, with the stack's
  excludes), then writes `.../<stack>/sync/last-sync`;
- on homelab only, `rclone sync`s `snapshots/` to `apps/homelab/swarm` and
  normandy's copy to `apps/normandy/swarm`, so normandy needs no Backblaze
  credentials. While homelab is down, normandy's snapshots exist only on
  normandy.

With no stack listed the run does nothing at all. A failure goes to the feed
and the phone with the other hourly tier. A deployed stack pinned to the
worker whose `.swarm-owner` is missing or names another host would never be
snapshotted, so it is posted to the feed as a `warn`; `backup.yml`
(`tasks/swarm_owner.yml`) claims such a stack when its state directory exists
and no owner file does, and never overwrites one.

**Transport.** root on each worker sends with `/etc/swarm-sync/id_ed25519`
(one key per sender, in `secrets/swarm-sync.sops.yaml`) and its own
`/etc/swarm-sync/known_hosts`. The receiver's `swarmsync` account accepts
that key only from the sender's VLAN 70 address, only as
`sudo -n /usr/bin/rrsync -wo <pool>/peer-snapshots/<sender>`
(`/etc/sudoers.d/swarmsync` allows exactly that command and keeps
`SSH_ORIGINAL_COMMAND`). A compromised sender can overwrite its own peer
directory on the other worker and read nothing.

**Planned move.** `playbooks/swarm-move.yml -e stack=<name> -e to=<host>`
(dry-run with `--check`) refuses unless all three managers are Ready and
Reachable, OCI answers on the mesh from both workers, exactly one node holds
the pin, the old node's `.swarm-owner` agrees and the peer copy is under two
hours old. It then scales the stack to 0, copies `/srv/swarm/<stack>/` cold to
`peer-snapshots/<from>/<stack>/final/` on the target, restores it there,
writes `.swarm-owner`, sets the old copy aside as
`/srv/swarm/.moved/<stack>-<stamp>`, flips the pin label, scales back to the
recorded replica counts (`.swarm-replicas`, kept with the data), waits for
healthy tasks, probes the stack's routes and posts to the feed.

**Crash (old node gone).** By hand, on the survivor: stop the stack's services
(`docker service scale <svc>=0`); restore into `/srv/swarm/<stack>/` from
`<pool>/peer-snapshots/<dead>/<stack>/`: `files/<dir>/` for the directories,
then each SQLite snapshot gunzipped over its database, and for Postgres start
the database service on an empty data directory and `pg_restore` the newest
`.dump`; write `.swarm-owner`; move the pin label (needs quorum: the survivor
and OCI); scale up. Up to an hour of loss. If OCI is unreachable too, the
survivor first runs `docker swarm init --force-new-cluster` (see
`docs/transition/phase5/migration-plan.md`).

The Compose-era jobs find their containers by Swarm service label first and by
Compose name second (`homelab-influx-backup`: `logging_influxdb` or
`influxdb`; the NAS dump: the configured name as a service or a container),
and the monthly restore check takes the newest Vaultwarden snapshot and
Authentik dump from either the Compose-era paths or the Swarm tier, so the
cutovers do not silently break them.

## Restoring

**A SQLite database.** Fetch the snapshot, gunzip it, put it where the service
expects it while the service is stopped. Verified working on 2026-09-06:

```sh
rclone copy b2:igor-offsite-backup/apps/homelab/backups/sqlite/vaultwarden .
gunzip -c vaultwarden_2026-09-06_1553.sqlite3.gz > db.sqlite3
python3 -c 'import sqlite3; print(sqlite3.connect("db.sqlite3").execute("PRAGMA integrity_check").fetchone())'
```

Vaultwarden's attachments and icon cache are **not** in the snapshot — they are
files, and they come from the twice-weekly copy of `~/app`. A full restore of
that service is the snapshot for the database plus the file copy for the rest.

**InfluxDB.** Extract the archive and hand the directory to `influx restore`:

```sh
tar -xzf influxdb_2026-09-06_1554.tar.gz     # gives influxdb-backup/
docker cp influxdb-backup influxdb:/var/lib/influxdb2/
docker exec -e INFLUX_TOKEN=... influxdb influx restore /var/lib/influxdb2/influxdb-backup --full
```

## Monthly restore verification

A backup that was restored once in September proves something about September.
`homelab-restore-verify` runs on the first of each month and proves it again:

- fetches the newest Vaultwarden snapshot **from Backblaze**, not from the
  local copy, because what is being tested is whether the offsite copy is
  fetchable, intact and current
- fails if that snapshot is more than six hours old, which catches an hourly
  timer that has quietly stopped
- checks its integrity, compares its cipher count against the live database,
  and starts a Vaultwarden container against it on port 8198 to prove the
  restored file actually serves
- restores the newest Authentik dump into a scratch Postgres and counts users
- removes both containers and the scratch directory afterwards

First run, 2026-09-06: `vaultwarden: 309 ciphers, served from the restored
copy, 0h old` and `authentik: 9 users`, in eleven seconds.

This is the one tier that reports success. A monthly line in the feed saying
the offsite copies were fetched and restored is the evidence that the backups
work; silence cannot say that.

## Akasha event-store package

`playbooks/akasha-backup.yml` installs only `roles/backup/tasks/akasha.yml` on
homelab. It reuses the existing backup owner and private rclone/B2 config,
packages the completed hourly SQLite snapshot under its shared exclusion lock,
and uploads/readbacks it every twelve hours. It never snapshots live Akasha
state itself. Private bucket/default encryption and <24h snapshot age are
required. Gatus reports failures and silence to Discord. See the
[ordered deployment, remote verification and isolated restore commands](akasha-backup-alerts.md).
No NAS mount or broad backup-role run is needed.

## Failure reporting

Every tier runs through `homelab-backup-run`, which is quiet on success and,
when a tier or its upload does not work, posts to the fleet feed as a `fail`
**and pushes to the phone** through notify-relay. Configuration drift does the
same at a lower priority.

The feed is the record; the push is for the subset that should interrupt
someone. A backup that stopped working costs more the longer it goes unnoticed,
and the alternative is finding out during the next audit — which is exactly how
the InfluxDB gap and the stale cv-site URLs survived for weeks.
That path was exercised the first time the daily tier ran: the wrapper sourced
its environment file without exporting, `INFLUX_TOKEN` never reached the backup
script, and the failure arrived in the feed as entry 21.

## The exclude list

`~/app/rclone_exclude.txt` is deployed by this role. The two new backup
directories are excluded from the twice-weekly `rclone copy` of `~/app`;
without that, every hourly snapshot and every 200 MB InfluxDB archive would get
a second, never-pruned copy in the bucket under a different path.

## What still has no backup

Docker named volumes other than InfluxDB's and Authentik's: Authentik's Redis,
and whatever anonymous volumes containers create. None of them holds anything
that could not be rebuilt.
