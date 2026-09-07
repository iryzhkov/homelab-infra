# Backups

Three tiers, two of which this repository owns.

| Tier | What | When | Where |
|---|---|---|---|
| Hourly | Vaultwarden and Actual Budget, one consistent snapshot per database | every hour | `apps/homelab/backups/sqlite`, `rclone sync` |
| Daily | InfluxDB, a full `influx backup` archive | 00:40 | `apps/homelab/backups/influxdb`, `rclone sync` |
| Weekly | a check that the bucket's lifecycle rule is still sane | Sunday 03:20 | one API call |
| Twice weekly | everything else: `~/app`, `~/apps`, `~/cv-site`, the NAS trees, the Postgres dumps | Mon and Thu, 01:30–06:40 | various, `rclone copy`, still in cron |

The first two are `roles/backup`, deployed by `playbooks/backup.yml`. The third
is the pre-existing crontab on homelab and is untouched.

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
