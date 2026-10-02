# Phase 0: homelab Docker build cache and unused volumes

Date: 2026-10-02, 13:02 to 13:04 PDT (first pass) and 13:12 to 13:13 PDT (second pass, after Igor's decision).
Host: homelab (VM 101, 192.168.70.223), over SSH from the laptop.
No container and no Docker daemon was restarted; a long agent task was running on homelab throughout.

## Before

Command: `docker system df` (13:02:12)

```
TYPE            TOTAL     ACTIVE    SIZE      RECLAIMABLE
Images          55        40        16.6GB    3.045GB (18%)
Containers      42        42        516.5MB   0B (0%)
Local Volumes   15        8         7.111GB   5.402GB (75%)
Build Cache     128       0         4.979GB   4.263GB
```

Command: `docker system df -v`, volume section (build cache section: 128 entries, `Build cache usage: 4.979GB`, none in use):

```
wyoming-piper_piper-data                                           1   703.4MB
595f5b51bfcb187444eba1faa675cbb23e0051b9a29cc70edbafc2326fb7973b   0   47.74MB
authentik_database                                                 1   118.9MB
logging_influxdb-config                                            1   476B
c52f6b809ddf2d45e4d0a55f66fe5ab4ed014babc0092304fc360eea3c74a95c   0   87.65MB
74c6a8e8d52ac819bdfb297037274c0b2aa0c21c1e35f57cd7acf6dd6b7ee1ab   0   83.25MB
86-challenge_pgdata                                                0   77.97MB
authentik_redis                                                    1   1.969kB
b446ba77f59567ef3958642d4ff990ee12b74ec9b904081a5fbd0a3aea5e08d5   1   0B
gatus_gatus-data                                                   1   5.505MB
gatus-public_gatus-data                                            1   4.665MB
86-challenge_uploads                                               0   188.5MB
immich_model-cache                                                 0   4.792GB
logging_influxdb-data                                              1   876.3MB
d8621f857fb565c27b0332051e9823e081233e92811da49df965741f609b98fa   0   125MB
```

## Build cache prune

Command: `docker builder prune -a -f`

Decisive output: `Total: 4.979GB`. Afterwards `Build Cache 0 0 0B 0B`.

Recorded answer: 4.979 GB of build cache reclaimed (the plan's estimate was 4.3 GB, which counted only the reclaimable part before `-a`).

## Unused volumes

Command: `docker volume ls -f dangling=true` returned seven volumes. For each, size is `sudo du -sh` of its mountpoint, labels and creation date are from `docker volume inspect`, users from `docker ps -a --filter volume=<name>` (none had any). The four anonymous volumes were identified by reading `PG_VERSION`, `postmaster.opts` and the role and database names in `global/1260` and `global/1262` of their Postgres data directories.

| Volume | Created | Size | Compose label | What it is | Decision |
|---|---|---|---|---|---|
| `74c6a8e8d52a…` | 2026-09-06 16:59 | 80 MB | none | Postgres 16 data directory, role and database `authentik` | prune |
| `c52f6b809ddf…` | 2026-09-15 10:28 | 84 MB | none | same | prune |
| `595f5b51bfcb…` | 2026-10-01 04:49 | 46 MB | none | same; `postmaster.opts` shows the entrypoint's init-time server, so that run was interrupted | prune |
| `d8621f857fb5…` | 2026-10-01 21:43 | 120 MB | none | same | prune |
| `86-challenge_pgdata` | 2026-03-15 | 75 MB | project `86-challenge` | Postgres 16 data of the archived 86-challenge app | kept in the first pass; removed after Igor's decision (below) |
| `86-challenge_uploads` | 2026-03-15 | 180 MB | project `86-challenge` | uploads of the same app | kept in the first pass; removed after Igor's decision (below) |
| `immich_model-cache` | 2026-03-18 | 4.5 GB | project `immich` | Immich machine-learning model cache of the old homelab Immich stack | kept in the first pass; removed after Igor's decision (below) |

Reasons for the first pass:

- The four anonymous volumes are scratch databases left by `roles/backup/files/homelab-restore-verify`. It starts `docker run -d ... -e POSTGRES_USER=authentik -e POSTGRES_DB=authentik postgres:16-alpine` (line 68) to restore the offsite Authentik dump, and its cleanup is `docker rm -f` without `-v` (line 12), so each run leaves the image's anonymous data volume behind. They hold copies of offsite dumps that still exist in B2, and nothing refers to them. Prune.
- `86-challenge_*`: `docs/current-state.md:88` says the app is archived under `~/app/_archive/86-challenge/` and not running, but nothing in the repository says whether its data is wanted or backed up elsewhere. These may be the only copy, so ownership of the data is unclear. Keep.
- `immich_model-cache`: Immich moved to normandy on 2026-08-30. The repository keeps the old homelab Immich stack and its Postgres data directory as a rollback path (`roles/backup/files/rclone_exclude.txt:71`, `docs/backblaze-audit-2026-09-06.md:51`), and the plan warns that some unused volumes may be the Immich rollback. The cache can be downloaded again, but it belongs to that retained stack, so it should go when Igor retires the rollback path, not on its own. Keep.
- Uptime Kuma's rollback data (commit `de3ab57`, `docs/gatus-trial.md:19`) is the bind directory `/home/iryzhkov/app/kuma`, not a Docker volume, and was not touched.

Commands run (13:03:30), each guarded by `[ -z "$(docker ps -aq --filter volume=$v)" ]`:

```
docker volume rm 74c6a8e8d52ac819bdfb297037274c0b2aa0c21c1e35f57cd7acf6dd6b7ee1ab
docker volume rm 595f5b51bfcb187444eba1faa675cbb23e0051b9a29cc70edbafc2326fb7973b
docker volume rm c52f6b809ddf2d45e4d0a55f66fe5ab4ed014babc0092304fc360eea3c74a95c
docker volume rm d8621f857fb565c27b0332051e9823e081233e92811da49df965741f609b98fa
```

All four were removed. `docker volume prune` was not used.

Recorded answer: four scratch Authentik volumes pruned, about 343 MB by `docker system df`; three volumes (about 4.8 GB) kept for Igor's decision.

After the first pass, `docker system df`:

```
TYPE            TOTAL     ACTIVE    SIZE      RECLAIMABLE
Images          55        40        16.6GB    3.045GB (18%)
Containers      42        42        516.5MB   0B (0%)
Local Volumes   11        8         6.768GB   5.059GB (74%)
Build Cache     0         0         0B        0B
```

Remaining dangling volumes then: `86-challenge_pgdata`, `86-challenge_uploads`, `immich_model-cache`.

## Igor's decision and second pass

Decision: Igor, 2026-10-02, in the lead session: the Immich migration to normandy is complete, so `immich_model-cache` can be cleared; 86-challenge is disposable, so both of its volumes can be deleted.

Command (13:12:42): `docker ps -a --filter volume=<name>` for each of the three. Output: empty for all three; no running or stopped container referenced them.

Command: `docker volume rm immich_model-cache 86-challenge_pgdata 86-challenge_uploads`. Output: the three names, all removed. Nothing was restarted.

## After

Command: `docker system df`

```
TYPE            TOTAL     ACTIVE    SIZE      RECLAIMABLE
Images          55        40        16.6GB    3.045GB (18%)
Containers      42        42        516.5MB   0B (0%)
Local Volumes   8         8         1.778GB   0B (0%)
Build Cache     0         0         0B        0B
```

`docker volume ls -f dangling=true`: empty.

Total reclaimed: about 10.3 GB: 4.979 GB build cache, 0.343 GB in the first volume pass, 4.990 GB in the second (volumes 7.111 GB to 1.778 GB overall).

Recorded answer: no unused volume or build cache remains on homelab.

## Follow-up: stale rollback references

The repository still describes the old homelab Immich stack as a retained rollback path. These files were not edited:

- `roles/backup/files/rclone_exclude.txt:71-77`: comment "The old Immich Postgres data directory, kept on the host as a rollback path for the 2026-08-30 move to normandy", and the `immich/**` and `immich-stack/**` excludes that exist only because of it.
- `docs/backblaze-audit-2026-09-06.md:51`: `apps/homelab/immich` described as "kept on the host as a rollback path". This is a dated audit, so it may be left as history.
- This file's first-pass reasons (`immich_model-cache` bullet) cite the two lines above.

On homelab itself the directories that rollback path refers to are still present: `~/app/immich` (1.6 GB by `sudo du -sh`) and `~/app/immich-stack`. They are bind directories, not volumes, and were not touched; removing them and the exclude lines is a separate decision.

Not stale: `stacks/immich/docker-compose.yml:47,76` (`model-cache`) is the live stack now deployed on normandy, and `inventory/host_vars/normandy.yml:46` is its directory.

## Implications for later phases

`homelab-restore-verify` leaks one anonymous Postgres volume per run, between 46 and 120 MB each. Its cleanup should use `docker rm -fv` (or the container should be started with `--rm`), otherwise the leak recurs on homelab and on any Swarm worker that runs the check after phase 5. That is a repository change for whoever owns the backup role, not part of this deliverable.
