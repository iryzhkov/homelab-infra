# Phase 1: downloads cutover on black-pearl

Date: 2026-10-02, 13:44 to 13:55 PDT.
Hosts: black-pearl (VM 102, 192.168.60.53), homelab (VM 101), run from the laptop.
Layout: option 1 of `2026-10-02-downloads-export.md`, chosen by Igor. No NAS export was created or changed.

## What changed

- black-pearl's mount of the NAS `media` share at `/mnt/shared` is now kept by the base role (`nfs_mounts` in `inventory/host_vars/blackpearl.yml`), `hard` instead of `soft`, mounted at boot instead of through `x-systemd.automount` with a 600 s idle timeout. The hand-written fstab line for the same path was replaced in place, so there is one definition.
- docker.service has a drop-in, `/etc/systemd/system/docker.service.d/nfs-mounts.conf`, with `RequiresMountsFor=/mnt/shared`. Docker therefore starts only after the mount, and stopping the mount stops Docker.
- qBittorrent's incomplete folder (`temp_path`) is `/mnt/shared/downloads/incomplete`, set through the WebUI API. It was `/mnt/tmp/downloads/incomplete` on the 1.8 TB disk.
- The torrent stack no longer binds `/mnt/tmp`.
- The `services_backup` automount at `/mnt/apps` on black-pearl is unchanged and still hand-written: the brief left it alone and nothing on black-pearl uses it. homelab's and normandy's NFS mounts are untouched.

## Sequence

qBittorrent API calls ran on black-pearl against `localhost:8080`, logged in with the credentials in `~/apps/torrent/.env`.

| Time (PDT) | Command | Decisive output |
|---|---|---|
| 13:44:54 | `POST /api/v2/app/setPreferences` `json={"temp_path":"/mnt/shared/downloads/incomplete","temp_path_enabled":true}` | `200`; preferences read back `/mnt/shared/downloads/incomplete True` |
| 13:44:54 | `POST /api/v2/torrents/stop` `hashes=all` (qBittorrent 5.1.4, where stop replaces pause) | `200`; `stoppedUP: 1243` |
| 13:45:02 | `cd /home/iryzhkov/apps/torrent && docker compose stop` | the three containers stopped; `diun` left running; `fuser -vm /mnt/shared` shows only the kernel mount |
| 13:46:10 | `ansible-playbook playbooks/base.yml -l blackpearl --diff` (after a `--check --diff` with the same 6 changes) | `changed=5`: fstab line, unmount of `mnt-shared.automount` and `mnt-shared.mount`, mount, drop-in directory, drop-in |
| 13:46 | `findmnt /mnt/shared` | `192.168.40.40:/var/nfs/shared/media nfs rw,noatime,vers=3,...,hard,proto=tcp,nconnect=8,timeo=50,retrans=3` |
| 13:46 | `systemctl show docker -p RequiresMountsFor -p Requires` | `RequiresMountsFor=/mnt/shared`; `Requires=... mnt-shared.mount docker.socket` |
| 13:46:19 | `ansible-playbook playbooks/site.yml -l blackpearl -e only=torrent --diff` (after `--check --diff` showing only the `/mnt/tmp` bind removed) | `changed=2` (Compose file, `up -d`); the stack started with the new file |
| 13:46:59 | `POST /api/v2/torrents/start` `hashes=all` | `200`; 60 s later `stalledUP: 1236, uploading: 3`, none below 100 % |

The stack was down from 13:45:02 to about 13:46:30, under two minutes.

## Checks

### Torrent counts and rechecks

Command: `GET /api/v2/torrents/info`, counted by state and by `progress < 1`.

```
before: 1243 (stalledUP 1241, uploading 2)
after:  1239 (stalledUP 1236, uploading 3); progress<1: 0; missingFiles 0; error 0
```

Answer: every torrent is complete and seeding; none is missing or errored. No recheck was forced: no torrent was incomplete, no data moved, and the complete files stay at the same path, so no torrent was affected by the change. The 1.5 GB of partial data on the old disk belongs to no listed torrent (see the retire file).

The four torrents fewer are qBittorrent's own share-limit policy, not the cutover. The log at 13:45:03 PDT (20:45:03 UTC), as qBittorrent shut down, reads `Torrent reached the seeding time limit. Removing torrent and deleting its content.` for "Nusrat Fateh Ali Khan - Mustt Mustt", "One-Punch.Man.S03E11...", "The.Muppet.Show.S05E12..." and "With.you.Our.Love.will.Make.it.Through.S01E11...". `Session\ShareLimitAction=RemoveWithContent` is set in `qBittorrent.conf`. Library copies are separate hardlinks and survive this.

### New partial data lands on the NAS

Command: `docker exec -u abc gt-qb sh -c 'dd if=/dev/zero of=/mnt/shared/downloads/incomplete/.e6-write-test bs=1M count=16; ls -ln ...; rm ...'`; `docker exec gt-qb ls /mnt/tmp`.

```
uid=1000(abc) gid=1000(users)
-rw-r--r-- 1 977 988 16777216 Oct  2 20:48 /mnt/shared/downloads/incomplete/.e6-write-test
ls: cannot access '/mnt/tmp': No such file or directory
```

Answer: qBittorrent's user can write the new incomplete folder (the export maps it to 977:988), and the container can no longer reach the old disk. No new download started during the window, so no real partial file was observed; the next grab will be the first.

### Seeding

Command: `GET /api/v2/transfer/info`, `GET /api/v2/app/preferences`.

```
connection_status connected, up_info_speed 33287, dht_nodes 279, listen_port 46561 (set by the port manager)
```

Answer: completed torrents seed through gluetun as before.

### *arr apps

Commands on homelab: `POST /api/v3/downloadclient/test` (v1 for Lidarr) with each app's qBittorrent client, `GET /api/v3/health`, `GET /api/v3/queue`. API keys read from `config.xml`, not printed. No *arr container was restarted and no *arr setting changed: completed downloads keep the same path, `/mnt/shared/downloads/complete`.

```
sonarr-tv 200 {}   radarr-movie 200 {}   lidarr-music 200 {}
health: no download-client, remote-path or import warnings in any of the three
sonarr queue: 2 importPending, 1 importBlocked (reasons: TBA episode title; an .exe in the release; series matched by ID). Radarr and Lidarr queues empty.
```

Answer: all three reach qBittorrent and report no path problem.

### Hardlinks

Commands on homelab: inside each of Sonarr, Radarr and Lidarr, `echo t > /mnt/shared/downloads/complete/.e6-hl-<app>; ln` it to `/mnt/shared/media/.e6-hl-<app>`; `stat -c %h`; both removed. Then `stat` of today's last import whose download still exists.

```
Sonarr 2   Radarr 2   Lidarr 2
Link Click S04E09 (Sonarr import 2026-10-02 14:44 UTC): media file and download both inode 162664, 2 links
```

Answer: hardlinks from downloads into the library work in all three containers through `/mnt/shared`. The sample in the export file found 371 of 400 downloads with 2 links. No import happened between the cutover and this record, so no post-cutover import was observed; the import path did not change.

The same test with Sonarr's `/tv` bind fails with `Cross-device link`. Sonarr's root folders are under `/mnt/shared/media`, not `/tv`, so imports are unaffected; nothing should be pointed at `/tv`, `/movies` or `/music`.

### Whole-fleet convergence

Commands: `ansible-playbook playbooks/base.yml --check --diff`, then `ansible-playbook playbooks/site.yml --check --diff`.

```
base.yml: blackpearl changed=0, homelab changed=0, normandy changed=0, proxmox-a changed=0, proxmox-b changed=0, public-api changed=0
site.yml: blackpearl changed=0, homelab changed=0, normandy changed=0, public-api changed=0
```

Answer: no drift on any host after the change.

## Seen in passing, not acted on

- Two releases in the download area look like malware: Sonarr's queue holds `Lanterns 2026 S01E08 ... MeGusta.exe` (blocked, "Found executable file with extension: '.exe'"), and `/mnt/shared/media/anime/One-Punch Man (2015) [tvdb-293088]/Season 3/` holds `One-Punch Man S03E12 1080p WEB H264-SENSEI.rar` and `.scr`. Neither was opened or removed.

## Rollback

Until the 1.8 TB disk is retired (step 5):

1. Stop all torrents through the API and stop the stack: `cd /home/iryzhkov/apps/torrent && docker compose stop`.
2. In the repository, change `hard` back to `soft` in `inventory/host_vars/blackpearl.yml` and restore the `/mnt/tmp:/mnt/tmp` bind in `stacks/torrent/docker-compose.yml`.
3. Run `ansible-playbook playbooks/base.yml -l blackpearl`. The mount is unmounted and mounted again with `soft`. Because of the docker.service drop-in, stopping the mount also stops Docker, and with it `diun`; start Docker again afterwards with `sudo systemctl start docker`, or remove the drop-in first if Docker should not depend on the mount.
4. Run `ansible-playbook playbooks/site.yml -l blackpearl -e only=torrent`.
5. Set `temp_path` back to `/mnt/tmp/downloads/incomplete` through `POST /api/v2/app/setPreferences`, then start all torrents.

After step 5 the old disk is gone; the rollback is then steps 1 to 4 without the `/mnt/tmp` bind, and `temp_path` stays on the NAS.

## Repository files changed

- `roles/base/tasks/nfs_mounts.yml` (new): NFS mounts in fstab, remount on change, docker.service drop-in, `mounts_absent` with a systemd reload.
- `roles/base/tasks/main.yml`: includes it when a host lists `nfs_mounts` or `mounts_absent`.
- `roles/base/defaults/main.yml`: `nfs_mounts: []`, `mounts_absent: []`, so the capability is off by default.
- `playbooks/base.yml`: header comment.
- `inventory/host_vars/blackpearl.yml`: `nfs_mounts` for `/mnt/shared`; `mounts_absent: [/mnt/tmp]` for step 5.
- `stacks/torrent/docker-compose.yml`: `/mnt/tmp` bind removed.
- `docs/transition/phase1/2026-10-02-downloads-export.md`, this file, `docs/transition/phase1/2026-10-02-local2tb-retire.md`.
