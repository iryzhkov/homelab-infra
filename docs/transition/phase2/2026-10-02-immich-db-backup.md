# Phase 2: Immich's Postgres dump made daily and managed by roles/backup

Date: 2026-10-02, 14:20 to 14:40 PDT. Executor E13.
Hosts: normandy (VM 202, igor@192.168.70.234), homelab (VM 101, crontab only).

Decision (Igor, lead session, 2026-10-02): Immich's Postgres dump on normandy runs daily and is managed by the backup role. Reason: phase 2 moved normandy's `/mnt/data` to a `backup=0` zvol, so vzdump no longer holds Immich's Postgres data directory.

## Survey (read-only)

### The hand-written units on normandy

Command: `ssh normandy 'systemctl cat normandy-backup.timer normandy-backup.service'`

```
normandy-backup.timer:   OnCalendar=Mon,Thu 01:30, Persistent=true  (files dated Aug 30 09:42)
normandy-backup.service: Type=oneshot, User=igor, Group=igor, SupplementaryGroups=docker,
                         Requires=mnt-backup.mount, ExecStart=/home/igor/app/pg_backup.sh
```

Answer: a system-scope timer, Mondays and Thursdays 01:30, running a script from `~/app` as igor.

### The script

Command: `ssh normandy 'cat /home/igor/app/pg_backup.sh'` (no secrets in it).

- Refuses to run unless `/mnt/backup` is a mountpoint (`192.168.40.40:/var/nfs/shared/services_backup`, NFSv3).
- `docker exec immich_postgres pg_dump -U iryzhkov -d immich | gzip > /mnt/backup/normandy/postgres/immich_<date>.sql.gz`.
- `sudo tar -C /home/igor -czf /mnt/backup/normandy/config/app_<date>.tar.gz --exclude=app/frigate/config/model_cache app`.
- Prunes `*.sql.gz` and `*.tar.gz` with `-mtime +14`.

Authentication: `pg_dump` runs inside the container as the database superuser over the container's local socket, so no password is used or stored. The tar needs igor's passwordless sudo.

Last run (journal): `2026-10-01 01:30:31 OK: .../immich_2026-10-01_0130.sql.gz (166M)`, config tar 12M, `Completed successfully`, 30 s wall clock.

Answer: dumps 173 MB compressed each, five on the NAS before this change (Sep 17 to Oct 1).

### The repository

- `roles/backup` modelled homelab only: `playbooks/backup.yml` included it on `docker_hosts` when `backup_sqlite_sources` was defined, which only `inventory/host_vars/homelab.yml` sets. Its tiers are user-scope systemd units from templates (`homelab-backup-{hourly,daily,weekly,verify}`), each running a script through `homelab-backup-run`, which posts failures to the feed and the phone.
- normandy was not modelled. The repository knew of `pg_backup.sh` only in comments (`playbooks/backup.yml`, `docs/backups.md`, `rclone_exclude.txt`).
- homelab's bulk crontab is hand-written. `playbooks/helper-maintenance.yml` rewrote its commands to `homelab-bulk-backup <job>` with an inline Python crontab rewrite, "without changing schedules"; the lines carry no Ansible marker, so `ansible.builtin.cron` cannot manage them without adding duplicates.

Answer: no existing per-host dump job; the idiom is role templates enabled by a host variable, scripts that report failures to the feed and phone, and in-place crontab rewrites for homelab.

### The homelab shipping job

Command: `ssh homelab 'crontab -l'`

```
40 6 * * 1,4 /home/iryzhkov/.local/bin/homelab-bulk-backup normandy
```

`~/.local/state/homelab-backup/bulk-normandy.last-success`: `2026-10-01T06:40:18-07:00`, `176.661 MiB` transferred at 11 MiB/s.
`rclone size b2:igor-offsite-backup/apps/normandy`: `Total objects: 28`, `Total size: 2.417 GiB`; 13 Immich dumps under `postgres/`, the oldest `immich_2026-08-30_0941.sql.gz`. `homelab-bulk-backup` uses `rclone copy`, so B2 keeps every dump.

Answer: shipped Mondays and Thursdays 06:40, five hours after the dump.

### Immich's built-in dump

Command: `ssh normandy 'ls -la /mnt/shared/immich/backups'`

Nightly `immich-db-backup-<date>T020000-v3.1.0-pg14.19.sql.gz` (02:00 UTC, 19:00 PDT), about 175 MB, 14 kept, newest 2026-10-02. On the media export (`192.168.40.40:/var/nfs/shared/media`). `rclone_exclude.txt` has `immich-db-backup-*`, so none reach B2.

Answer: a daily NAS-only copy, independent of the job above.

## Change

Repository:

- `roles/backup/tasks/main.yml` is now a dispatcher: notify client and facts, then `tiers.yml` when `backup_sqlite_sources` is defined (homelab's existing tasks, moved unchanged), `nas_dump.yml` when `backup_nas_dump` is defined, `ship_schedule.yml` on `backup_bulk_shipper` (new `roles/backup/defaults/main.yml`, `homelab`).
- `roles/backup/tasks/nas_dump.yml` with templates `homelab-nas-dump.j2` (script to `~/.local/bin/homelab-nas-dump`), `homelab-nas-dump.service.j2` and `homelab-nas-dump.timer.j2`, deployed as `/etc/systemd/system/<host>-backup.{service,timer}`. Keeping the host-named unit names means the managed units overwrite the hand-written ones, so there is no second timer. The script keeps the old behaviour (dump, config tar, 14-day prune, mount guard) and adds: write through a `.partial` file, check it is non-empty and passes `gzip -t` before renaming, and post failures to the feed and phone as every other tier does. The unit uses `RequiresMountsFor=/mnt/backup/normandy` instead of naming the mount unit.
- The config tar stays in the same run, so it is daily too. The role's daily tier is for things small enough to run daily; the tar is 12 MB.
- `roles/backup/tasks/ship_schedule.yml`: for each docker host with `backup_nas_dump.ship_cron`, rewrites the schedule fields of the one crontab line running `homelab-bulk-backup <host>` (fails if there is not exactly one), backs up the crontab once to `~/crontab.before-ship-schedule-<host>`, and runs in check mode without writing.
- `playbooks/backup.yml`: also includes the role where `backup_nas_dump` is defined or on the shipper.
- `inventory/host_vars/normandy.yml`: new `backup_nas_dump` key (root, `on_calendar: "*-*-* 01:30"`, `ship_cron: "40 6 * * *"`, `keep_days: 14`, the Immich database, `config_dir`, `config_exclude`, `retired_files: [/home/igor/app/pg_backup.sh]`). No other key touched.
- `docs/backups.md`: a table row and a section on this job.

### Converge

Command: `ansible-playbook playbooks/backup.yml -l normandy,homelab --check --diff`

```
homelab   : ok=20 changed=1   (the normandy cron line)
normandy  : ok=12 changed=3   (script, both units, pg_backup.sh removal)
```

The unit diffs were as described above; the timer diff was `-OnCalendar=Mon,Thu 01:30` / `+OnCalendar=*-*-* 01:30`.

Command: `ansible-playbook playbooks/backup.yml -l normandy,homelab -v`

```
homelab   : ok=21 changed=1
normandy  : ok=14 changed=3
```

Host changes this made:

- normandy: `~/.local/bin/homelab-nas-dump` installed; `/etc/systemd/system/normandy-backup.{service,timer}` replaced; `systemctl daemon-reload`; timer enabled and started; `/home/igor/app/pg_backup.sh` deleted (its content is in the config tars on the NAS and is superseded by the template).
- homelab: crontab line changed from `40 6 * * 1,4 ...homelab-bulk-backup normandy` to `40 6 * * * /home/iryzhkov/.local/bin/homelab-bulk-backup normandy`; previous crontab saved to `/home/iryzhkov/crontab.before-ship-schedule-normandy`. Nothing restarted.

### Timers after the converge

Command: `ssh normandy 'systemctl list-timers --all | grep backup; systemctl list-unit-files | grep -i backup'`

```
Sat 2026-10-03 01:30:00 PDT 10h Fri 2026-10-02 14:31:59 PDT 36s ago normandy-backup.timer normandy-backup.service
normandy-backup.timer    enabled
snapper-backup.timer     disabled   (unrelated, snapper)
```

`NeedDaemonReload=no` on both units. Answer: exactly one dump timer, daily 01:30.

## Run now and verify

The timer has `Persistent=true`. When the converge restarted it with the daily calendar, today's 01:30 had elapsed without a run, so systemd started `normandy-backup.service` immediately at 14:31:59. That catch-up run through the managed unit served as the run this step asked for; a second manual start was not made, because it would only have added another 173 MB dump that `rclone copy` keeps in B2 for good.

Command: `journalctl -u normandy-backup.service --since 14:31 -o cat`

```
[2026-10-02 14:32:32] OK: /mnt/backup/normandy/postgres/immich_2026-10-02_1431.sql.gz (166M)
[2026-10-02 14:32:33] OK: /mnt/backup/normandy/config/app_2026-10-02_1431.tar.gz (12M)
/mnt/backup/normandy/postgres/immich_2026-09-17_0130.sql.gz      (pruned, -mtime +14)
/mnt/backup/normandy/config/app_2026-09-17_0130.tar.gz
[2026-10-02 14:32:33] Completed successfully
Consumed 33.313s CPU time over 34.104s wall clock time, 225.6M memory peak.
```

Command: `stat`, `gzip -t`, `zcat | head -5` on the new dump.

```
173797009 bytes 2026-10-02 14:32:30
gzip-ok
--
-- PostgreSQL database dump
--
```

No `.partial` file left in `postgres/`. Answer: fresh, non-empty, valid gzip, pg_dump header.

## Idempotence and the other playbooks

```
ansible-playbook playbooks/backup.yml -l normandy,homelab        homelab changed=0, normandy changed=0
ansible-playbook playbooks/site.yml   -l normandy,homelab --check homelab ok=386 changed=0, normandy ok=67 changed=0
ansible-playbook playbooks/base.yml   -l normandy,homelab --check homelab ok=13 changed=0, normandy ok=11 changed=0
```

The rendered script passes `bash -n`. shellcheck is not installed on the laptop, normandy or homelab, so it was not run; the template is not in the CI shellcheck list either, because it is a Jinja template.

## RPO for Immich's database, before and after

Worst case, measured from the moment of loss back to the newest copy that survives it.

| Copy | Before | After |
|---|---|---|
| Offsite (B2, `apps/normandy/postgres`) | dump Mon and Thu 01:30, shipped 06:40; worst just before Monday 06:40, newest offsite dump from Thursday 01:30: about 4 d 5 h | dump daily 01:30, shipped daily 06:40; worst just before 06:40, newest offsite dump from the previous day's 01:30: about 1 d 5 h |
| NAS, this job (`services_backup` export) | Thursday 01:30 to Monday 01:30: about 4 d | about 1 d |
| NAS, Immich's own dump (`media` export, `immich-db-backup-*`) | daily 19:00 PDT: about 1 d, never offsite | unchanged |
| NAS, both together | about 1 d (Immich's own dump) | about 17.5 h: the two daily dumps are 6.5 h and 17.5 h apart |

Before phase 2 vzdump of VM 202 also held the data directory (on `nas-backup`); with `backup=0` it no longer does, which is why the dump is now the only offsite copy.

## Cost and load of daily shipping

- rclone load: one more run a day of about 185 MB (dump plus config tar), about 18 s at the 11 MiB/s the last run measured. Negligible for homelab and the uplink.
- B2: `rclone copy` never deletes, so the bucket grows by every dump. Twice weekly that was about 370 MB a week (about 19 GB a year); daily it is about 1.3 GB a week (about 66 GB a year). At Backblaze's list price of roughly $6 to $7 per TB-month that is under $0.50 a month after a year, but it grows without bound. Uploads are free and nothing is downloaded.
- The NAS holds about 15 dumps plus 15 tars (about 2.8 GB) instead of five of each.

## Implications for later phases

- Phase 5: the per-service snapshot design should give `apps/normandy/postgres` a bounded retention in B2 (a `rclone sync` of that subdirectory, as the hourly and daily tiers do, or a prefix lifecycle rule). Until then every daily dump is kept forever.
- Phase 5: Frigate's media on `/mnt/data` is still not covered by anything (recorded in `2026-10-02-normandy-data-and-tank-b.md`).
- The NAS copy of the old `/mnt/data` (`/mnt/backup/normandy-migration/data-2026-10-02/`) was to be kept until the 2026-10-05 dump was verified. The managed job produced and verified a dump on 2026-10-02 14:32, so the lead can treat that condition as met; deletion is still Igor's decision.
