# Old homelab Immich directories removed

Date: 2026-10-02, 13:40 to 13:43 PDT.
Hosts: homelab (VM 101, iryzhkov@192.168.70.223); normandy read for comparison.
Approved by Igor on 2026-10-02 in the lead session as housekeeping, not a plan step.

Immich moved to normandy on 2026-08-30. The homelab copy of its Postgres data
directory (`~/app/immich`) and its old Compose project (`~/app/immich-stack`) had
been kept as a rollback path, and `roles/backup/files/rclone_exclude.txt` carried
two excludes only for them. No container, the Docker daemon or the VM was touched.

## 1. Proof the directories were unused

Sizes and contents (`du -sh`, `ls -la`, with `sudo -n` for the 0700 directory):

- `~/app/immich`: 1.6G, mode 0700, owner `notify-relay` (uid 999 on this host),
  group root. A Postgres data directory: `PG_VERSION`, `base`, `global`, `log`,
  `pg_wal`, `pg_vectors`, `postgresql.conf` and the other `pg_*` directories.
  Newest entries dated Aug 30 03:17 (`pg_logical`, `pg_stat`).
  `sudo -n find ~/app/immich -newermt 2026-09-01` printed nothing.
- `~/app/immich-stack`: 12K, `docker-compose.yml` and `stack.env`, both Aug 27 21:22.

Container mounts:

    docker ps -a --format '{{.Names}}' | xargs docker inspect --format '{{.Name}} {{range .Mounts}}{{.Source}} {{end}}' | grep -i immich

No output (grep exit 1). `docker ps -a --filter name=immich` and
`docker volume ls | grep -i immich` were also empty. Answer: no container
mounts either path.

Compose projects: `docker compose ls -a` lists 36 projects, none named immich and
none with a config file under `~/app/immich*`. Answer: no Compose project uses
them.

Live Immich: `inventory/host_vars/normandy.yml:42-48` declares the `immich`
stack with `dir: /home/igor/app/immich-stack` on normandy, from `stacks/immich`.
Answer: the running Immich is on normandy, and the `app/immich` and
`immich-stack` hits in `stacks/immich` and the normandy host_vars are normandy
paths.

Repository references (Huyang regex search `app/immich|immich-stack|immich/\*\*`,
14 hits in 6 files): `roles/backup/files/rclone_exclude.txt:74-77`, normandy
paths in `inventory/host_vars/normandy.yml` and `stacks/immich/docker-compose.yml`,
and prose in `docs/backblaze-audit-2026-09-06.md`, `docs/current-state.md:131`
and `docs/transition/phase0/2026-10-02-homelab-docker-cleanup.md`. Answer:
nothing operational on homelab references the directories except the excludes.

Host schedules: the user crontab's only Immich entry is
`homelab-bulk-backup immich`, whose source is the NAS library
`/mnt/shared/immich`, not `~/app/immich`. Root's crontab, `/etc/systemd`,
`/etc/cron*` and `~/.config/systemd` have no `app/immich` reference.

Exclude side effects: rclone patterns without a leading `/` match at any depth,
so removing `immich/**` and `immich-stack/**` could widen another job. Searched
the roots of every bulk job (`~/app`, `~/apps`, `~/cv-site`,
`/mnt/backup/blackpearl`, `/mnt/backup/normandy` in full; `/mnt/shared/immich`
to depth 4) for directories named `immich` or `immich-stack`: only the two being
deleted. Answer: removing the excludes changes nothing else that is uploaded.

## 2. Deletion

`sudo -n true` succeeded, so no password was needed.

    df -B1M --output=used,avail ~      # 114116 67562 (112G used, 66G avail)
    sudo -n rm -rf /home/iryzhkov/app/immich /home/iryzhkov/app/immich-stack   # rc=0
    ls -d /home/iryzhkov/app/immich /home/iryzhkov/app/immich-stack           # No such file or directory (both)
    df -B1M --output=used,avail ~      # 112495 69183

`df -h ~` went from 112G used / 66G available (63%) to 110G used / 68G available
(62%). Answer: both directories are gone; 1621 MiB freed on `/dev/vda1`.

## 3. Exclude list and deployment

`roles/backup/files/rclone_exclude.txt`: removed lines 71-77 (the rollback-path
comment and `immich/**`, `immich-stack/**`). Kept `immich-db-backup-*`,
`thumbs/**` and `encoded-video/**`, which serve the `/mnt/shared/immich` job.

The file is installed by `roles/backup/tasks/main.yml` (task "Install the rclone
exclude list") from `playbooks/backup.yml`.

    ansible-playbook playbooks/backup.yml --limit homelab --check --diff

Recap `homelab: ok=17 changed=1`. The only changed task was "Install the rclone
exclude list". Its diff removed the seven lines above and also added one comment
line, `# Retired Kuma data is retained locally for rollback, not bulk-synced.`,
above `kuma/**`: that comment was committed in `de3ab57` but had not been
deployed. It is a comment only, so it was deployed with this change.

    ansible-playbook playbooks/backup.yml --limit homelab

Recap `homelab: ok=18 changed=1 failed=0`. The copy module kept the previous
file as `~/app/rclone_exclude.txt.3596767.2026-10-02@13:41:35~`. The deployed
file now mentions immich only at line 55 (comment) and line 70
(`immich-db-backup-*`).

    ansible-playbook playbooks/backup.yml --check

Recap: homelab `ok=17 changed=0`; blackpearl, normandy and public-api
`ok=0 changed=0 skipped=1`. Answer: the repository and hosts agree.

## Not changed

Prose that still describes the homelab Immich rollback path, owned by no one in
this step: `docs/backblaze-audit-2026-09-06.md` (a dated audit, history),
`docs/current-state.md:131`, and the `immich_model-cache` bullet in
`docs/transition/phase0/2026-10-02-homelab-docker-cleanup.md`. The
`immich_model-cache` Docker volume on homelab, which phase 0 kept until the
rollback path was retired, was not touched.

## Implications for later phases

The homelab Immich rollback path no longer exists; rollback for Immich is now
normandy's own dumps (`pg_backup.sh`) and the NAS library. The
`immich_model-cache` volume on homelab can now be pruned on its own.
