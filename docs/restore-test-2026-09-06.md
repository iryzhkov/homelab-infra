# Restore test, 2026-09-06

The Backblaze B2 backups had never been restored from. A backup nobody has
restored is a claim rather than a capability, so this is the first exercise of
that path. Nothing in it touched live data: everything was restored into a
scratch directory and served by temporary containers on unused ports, then
removed.

## What was tested

**Vaultwarden, the whole data directory.** Restored from
`b2:igor-offsite-backup/apps/homelab/vaultwarden` into a scratch directory,
4.7 MB. `PRAGMA integrity_check` returned ok. The restored database holds 1
user, 309 ciphers, 1 folder and 10 devices, newest cipher updated 2026-09-02
22:30:03 — identical to the live database, compared through a read-only
`sqlite3.backup` copy of it. A `vaultwarden/server:latest` container was then
started against the restored directory on port 8199: `/alive` and the web vault
both returned 200.

**Authentik's database.** The 2026-09-03 dump, 1.7 MB gzipped, restored into a
fresh `postgres:16-alpine` container. psql exited 0 with no errors. Row counts:
9 users, 9 applications, 9 OAuth2 providers, 14 flows — the first two match the
live database exactly.

So "could we come back from B2" is now a measured yes for the two services that
matter most, rather than an assumption.

## What the test found

**Vaultwarden's SQLite files are copied live, and got lucky.** rclone copied
`db.sqlite3` (dated 2026-08-31 23:25), `db.sqlite3-wal` (2026-09-03 00:26) and
`db.sqlite3-shm` as three independent files while the database was being
written. SQLite recovered cleanly from that pair, which is why the restored data
is current rather than six days stale. That is timing, not design: the correct
method is `sqlite3 .backup` or `VACUUM INTO` before the upload, so that what
reaches B2 is one consistent snapshot rather than three files caught at three
different moments. Actual Budget, also SQLite, is copied the same way.

**Two InfluxDB volumes are backed up by nothing.** `logging_influxdb-config`
and `logging_influxdb-data` are Docker named volumes, so they sit outside
`~/app` where no rclone job sees them, and unlike Postgres they have no dump
job. Authentik's database and Redis are named volumes too, but the twice-weekly
`pg_backup.sh` covers the database, which is what this test restored.

**Stale immich dumps in the homelab path.** `apps/homelab/backups/postgres`
still holds immich dumps up to 2026-08-27, from before Immich moved to
normandy. The current ones are in `apps/normandy/postgres`, latest 2026-09-03.
Both paths look plausible to someone restoring in a hurry, and one of them is
ten days out of date.

**The recovery point is Monday and Thursday.** Both the Postgres dumps and the
file copies run at 01:30 and 02:00 on those days, so the worst case is about
three and a half days of loss. For a password manager that is a long time; for
Vaultwarden and Actual, whose databases are a few hundred kilobytes, an hourly
copy would cost almost nothing.

Black Pearl and normandy do reach the NAS as expected: `/mnt/backup/blackpearl`
held files from today and `/mnt/backup/normandy` from 2026-09-03, and both trees
are uploaded by the 06:00 and 06:40 jobs.

## What was not tested

Restoring onto a different machine, and restoring anything large: Immich's
library lives on a 44 TB NAS volume and nothing here says how long that would
take. The next useful exercise is a cloud standby seeded from these same
backups — restoring into a scratch container proves the archive, not the
runbook.
