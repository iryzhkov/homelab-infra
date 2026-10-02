# Phase 1: survey for the NAS downloads export

Date: 2026-10-02, 13:20 to 13:40 PDT.
Hosts: black-pearl (VM 102, 192.168.60.53), homelab (VM 101), proxmox-a, the NAS at 192.168.40.40 (UniFi UNAS Pro, UniFi Drive).
Everything below was read only. No export, mount, container, torrent or setting was changed.

## Decision (2026-10-02, after this survey)

Igor chose option 1 below, relayed by the lead session. No NAS export is created. black-pearl's existing mount of the `media` share at `/mnt/shared` is brought under the base role and changed from `soft` to `hard`, ordered before docker.service; qBittorrent's incomplete folder moves to `/mnt/shared/downloads/incomplete`; the stack's `/mnt/tmp` bind is removed; the 1.8 TB disk is then retired. The cutover is recorded in `2026-10-02-downloads-cutover.md`.

Corrected premise: the plan's "92 GB of current downloads" on the 1.8 TB disk is the qcow2's allocation on proxmox-a. Inside the guest the disk holds 1.5 GB of partial files. All 1243 completed torrents (25.6 TB) were already on the NAS, inside the `media` share, before phase 1, and imports were already hardlinks.

## Verdict

Stopped before step 1. The plan assumes 92 GB of downloads sit on black-pearl's 1.8 TB disk and must be copied to a new export. They do not: every completed torrent (1243 torrents, 25.6 TB) already lives on the NAS, inside the `media` share, and is hardlinked into the library today. The 1.8 TB disk holds 1.5 GB of incomplete scratch; the 92 GB is the qcow2's allocation on the host, not guest data. A dedicated UniFi Drive `downloads` share would be a separate export outside `media`, which breaks hardlinks for the *arr containers (a new client mount, EXDEV) and would mean moving 25.6 TB. That is a layout change the plan does not foresee, so the brief's stop condition applies. Separately, no credential on the laptop can change UniFi Drive (see the last check), so step 1 would also be blocked on Igor.

## Checks

### Where qBittorrent writes

Command: `sudo grep` of `Session\\(DefaultSavePath|TempPath|TempPathEnabled)` in `/home/iryzhkov/app/qbittorrent/qBittorrent/qBittorrent.conf`; `categories.json`; WebUI API `/api/v2/app/preferences` and `/api/v2/torrents/info` (login with the credentials in `~/apps/torrent/.env`, rendered from `secrets/blackpearl-torrent.sops.yaml`).

```
save_path /mnt/shared/downloads/complete   temp_path /mnt/tmp/downloads/incomplete   temp_path_enabled true   auto_tmm_enabled true
categories: radarr-movies -> /mnt/shared/downloads/complete/movies, tv-sonarr -> .../complete/tv, xxx -> .../complete/xxx; free-leech, lidarr, manga, music -> default
torrents: 1243 (stalledUP 1241, uploading 2), total size 25633747348643 bytes; auto_tmm true=1050 false=193
save_path counts: 688 .../complete/tv, 517 .../complete/movies, 28 .../complete/xxx, 4 .../complete/free-leech, 3 .../complete/manga, 3 /mnt/shared/downloads
```

Answer: save paths are app settings (preferences and categories), not Compose. Every torrent is complete and seeds from `/mnt/shared/downloads` on the NAS. Only the incomplete directory is on the local disk.

### The 1.8 TB disk

Commands: `df -h /mnt/tmp`, `sudo du -sh /mnt/tmp/downloads/*` on black-pearl; `qemu-img info` and `du -sh /mnt/pve/local2tb/*/` on proxmox-a.

```
/dev/sdb1  1.8T  1.5G  1.7T  1% /mnt/tmp          (ext4, fstab by UUID, defaults)
1.5G /mnt/tmp/downloads/incomplete/free-leech; every other subdirectory 4.0K
vm-102-disk-0.qcow2: virtual size 1.76 TiB, disk size 91.5 GiB; local2tb used 96001128 KiB
```

Answer: the "92 GB" is qcow2 allocation that the guest has freed (discard=on, `fstrim.timer` enabled, but not yet trimmed). Guest data is 1.5 GB of partial files.

### NAS exports and filesystem

Command: `showmount -e 192.168.40.40` from homelab-reachable black-pearl (`/usr/sbin/showmount`).

```
/volume/062db88c-.../.srv/.unifi-drive/public/.data           .70.223,.70.234,.1.29,.60.53,.1.5,.90.212
/volume/062db88c-.../.srv/.unifi-drive/services_backup/.data  .1.5,.1.29,.70.234,.70.223,.60.53
/volume/062db88c-.../.srv/.unifi-drive/machine_learning/.data .70.223,.1.29,.90.212,.70.234,.1.5
/volume/062db88c-.../.srv/.unifi-drive/media/.data            .1.29,.90.212,.70.234,.70.223,.60.53,.1.5
```

Answer: each UniFi Drive share is its own directory `/.srv/.unifi-drive/<share>/.data` on one volume, exported whole (clients mount it as `/var/nfs/shared/<share>`). black-pearl is already a client of `media`, `services_backup` and `public`. A new share named `downloads` would sit beside `media`, not inside it.

### How black-pearl and homelab mount the NAS

Commands: `/etc/fstab` on black-pearl; `/proc/mounts` and `stat -f` on homelab; `docker inspect Sonarr Radarr Lidarr`.

```
black-pearl: 192.168.40.40:/var/nfs/shared/media /mnt/shared nfs defaults,nconnect=8,rsize=1048576,wsize=1048576,async,noatime,_netdev,soft,timeo=50,retrans=3,noauto,x-systemd.automount,x-systemd.idle-timeout=600
             192.168.40.40:/var/nfs/shared/services_backup /mnt/apps (same options); both hand-written, not in the repository
homelab:     192.168.40.40:/var/nfs/shared/media /mnt/shared nfs rw,noatime,vers=3,...,soft,nconnect=8,timeo=50,retrans=3
Sonarr, Radarr, Lidarr: /mnt/shared -> /mnt/shared, plus library subpaths (/tv, /movies, /music)
```

Answer: qBittorrent and the *arr containers see downloads at the same path, `/mnt/shared/downloads`, through one NFSv3 mount of the `media` share on each host. No NFS mount exists in the repository today; both hosts' mounts are hand-managed.

### *arr download clients and path mappings

Command on homelab: `/api/v3/remotepathmapping`, `/api/v3/downloadclient`, `/api/v3/config/mediamanagement`, `/api/v3/rootfolder` (v1 for Lidarr), API keys read from each `config.xml` into a shell variable and not printed.

```
sonarr-tv:    remotepathmapping []  qBittorrent 192.168.60.53:8080 category tv-sonarr      copyUsingHardlinks true
radarr-movie: remotepathmapping []  qBittorrent 192.168.60.53:8080 category radarr-movies  copyUsingHardlinks true
lidarr-music: remotepathmapping []  qBittorrent 192.168.60.53:8080 category lidarr         copyUsingHardlinks true
```

Answer: no remote path mappings; the apps rely on qBittorrent reporting `/mnt/shared/downloads/...`, which is the same path in their containers.

### Do imports hardlink today

Command on homelab: `find movies tv -type f -size +100M | head -400 | xargs stat -c %h | sort | uniq -c` in `/mnt/shared/downloads/complete`.

```
371 files with 2 links, 29 with 1
```

Answer: yes. Imports are hardlinks today because downloads and library share one filesystem and one client mount.

### Can the export be created from the laptop

Commands: `ssh -o BatchMode=yes {root,ui,admin,igor}@192.168.40.40`; `POST https://192.168.40.40/api/auth/login` as the UDM local admin `claude` (password from the keyring, `service=unifi-local`, not printed); `GET /api/system`.

```
ssh: Permission denied (publickey,keyboard-interactive) for all four users
login: 403 "Invalid username or password"
/api/system: name "Unify NAS", hardware UNASPRO
```

Answer: the NAS is its own UniFi OS console; the UDM's local admin does not exist there, and no SSH key is authorised. Creating any export needs Igor, either in the UniFi Drive UI or by giving the laptop a credential.

## Chosen layout

None adopted; the plan's layout does not fit the actual state.

- A dedicated UniFi Drive `downloads` share (the plan's step 1) is exported as its own directory beside `media`. homelab would need a second mount to reach it, and a hardlink between two NFS mounts on one client fails with EXDEV, so every import would become a copy. It would also require moving 25.6 TB of seeding data out of `media`. Rejected.
- A subfolder export (`media/.data/downloads` with its own client list and root squash) would satisfy both the isolation goal and hardlinks, but UniFi Drive configures NFS per share, and this could not be confirmed without access to the NAS.

## Options for Igor

1. Retire the disk without a new export. Point qBittorrent's incomplete directory at `/mnt/shared/downloads/incomplete` (it already exists there, 30 GB of partials), make black-pearl's media mount `hard` and repository-managed through the base role, and drop `/mnt/tmp` from the stack. No NAS change, no *arr change, hardlinks unchanged. The isolation the review asked for (a root-squashed export only black-pearl can mount) is not gained, and black-pearl keeps read-write access to the whole `media` share, as it has today.
2. Keep the isolation goal. Confirm in UniFi Drive whether a folder inside `media` can be its own NFS export. If it can, export `media/downloads` to 192.168.60.53 only and mount it on black-pearl at `/mnt/downloads`, with the same container path. homelab keeps seeing it at `/mnt/shared/downloads`, so hardlinks keep working. black-pearl would then be removed from the `media` export's client list.
3. A separate `downloads` share as written in the plan, accepting that imports become copies and 25.6 TB must move. Not recommended at 72 % NAS usage.

Either 1 or 2 makes the plan's copy and recheck steps nearly empty: at most 1.5 GB of partial files on the local disk, which qBittorrent can simply redownload.

## Step 5 (scope change received 13:40 PDT)

The lead forwarded Igor's change: step 5, detaching the 1.8 TB disk, follows as soon as steps 1 to 4 are verified, gated on clean rechecks, one confirmed hardlink import and executor E7's restore result. None of those gates could be met while steps 1 to 4 were stopped. After Igor chose option 1, the gates were met and step 5 was done; see `2026-10-02-local2tb-retire.md`.

## Implications for later phases

- Phase 1 step 5 (detaching the 1.8 TB disk) needs only the incomplete directory moved; its rollback concern about a 92 GB copy does not exist.
- Phase 2: `local2tb` holds only VM 102's qcow2 (91.5 GiB allocated, 1.5 GB used by the guest) and a 99 MB `.speedtest` file.
- black-pearl's NFS mounts are hand-written in `/etc/fstab` and outside the repository, the same as homelab's.
