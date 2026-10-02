# Phase 2 step 3: normandy's data disk and `tank` on proxmox-b

Date: 2026-10-02, survey from 14:05 PDT, window 14:12:18 to 14:18:25 PDT.
Hosts: normandy (VM 202, 192.168.70.234), proxmox-b (hp-workstation-01, 192.168.1.29).
Executor: E11.

Status: done. The survey found that Frigate also uses `/mnt/data` (mismatch 1). The lead relayed Igor's authorisation to stop Frigate together with Immich in one window. The pre-copy ran while both were up. The window then ran without a pause: stop, final copy, release of the old disk, `tank` on the BX500, a new 400 GB zvol, restore, start. Immich and Frigate were down from 14:12:18 to 14:17:55 PDT (healthy at 14:18:25).

## Mismatches with the brief and plan

1. Frigate bind-mounts `/mnt/data/frigate/media` to `/media/frigate` (snapshots and clips, 281 MB). The plan and the brief treat the disk as Immich's only. Unmounting `/mnt/data` on the host does not detach it from Frigate's mount namespace (Docker binds are `rprivate`), so the host unmount succeeds while Frigate keeps writing to the old filesystem. Hot-unplugging `scsi1` would then remove a device Frigate is still writing to, and Frigate would keep pointing at the detached filesystem after the new disk is mounted. Either way Frigate needs a stop and start around the swap. That is a second service-affecting step, which only Igor or the lead can authorise. This was already recorded in phase 0 (`docs/transition/phase0/2026-10-02-frigate-writes.md`), but the brief did not take it into account.
2. `/mnt/data` is not on LUKS. It is plain ext4 on `/dev/sdb1`. Only the root (`sda2`) is LUKS2, unlocked by the vTPM (`rd.luks.options=tpm2-device=auto`). The new disk can therefore be plain ext4, the same protection as today; no LUKS design is needed. If Igor wants the data disk encrypted like the root, that is a change of policy, not a reproduction of today's setup.
3. The base role has no capability for local-disk mounts. `roles/base/tasks/nfs_mounts.yml` writes `fstype: nfs` only, and installs `nfs-common` with `apt`, while normandy runs Omarchy (Arch). `mounts_absent` is generic (`ansible.posix.mount state: absent`) and would work for retiring the old line. Mounting the new disk by UUID through Ansible needs a new base-role capability (a local mount list, plus a `RequiresMountsFor` drop-in on docker.service), and `roles/base/**` belongs to E9 until E9 reports.
4. VM 202's `scsi1` has no `backup=0`, so last night's vzdump also holds the disk's contents (as phase 0 found).
5. `zfs_arc_max` on proxmox-b read `0` (no cap) at 14:05. Before the window E11 re-read it as `2147483648` live, with `options zfs zfs_arc_max=2147483648` in `/etc/modprobe.d/zfs.conf` and that file inside the current initramfs, and the lead's go confirmed E9's cap.
6. The NFS export squashes ownership, so `rsync -aHAX --numeric-ids` (the briefed flags) failed every `chown` (exit 23) on the first pre-copy pass. Later passes used `-rlptHAX --numeric-ids`, and ownership and modes were carried in a separate listing (see below). No file has ACLs or extended attributes (`getfattr -R`, `getfacl -R -s` print nothing).

## Survey on normandy

Command: `lsblk -f`

```
sda
|-sda1   vfat        FAT32       791A-2838                              /boot
`-sda2   crypto_LUKS 2           d1d3d0b8-c896-4ffd-9ad1-108369d002fd
  `-root btrfs                   0b58d867-d2ba-4637-8764-828f25841442   / /home /var/log /var/cache/pacman/pkg
sdb
`-sdb1   ext4        1.0   data  b4b307a6-3326-4fff-9da2-8fd80784a269   883.1G free  /mnt/data
```

Command: `cat /etc/fstab` (the data-disk line; the rest is the btrfs root, `/boot` and three NFS mounts)

```
UUID=b4b307a6-3326-4fff-9da2-8fd80784a269  /mnt/data  ext4  defaults,nofail,x-systemd.device-timeout=10  0 2
```

Command: `sudo cat /etc/crypttab`; `cat /proc/cmdline`

```
(crypttab: comments only)
rd.luks.name=d1d3d0b8-c896-4ffd-9ad1-108369d002fd=root rd.luks.options=tpm2-device=auto root=/dev/mapper/root ...
```

Answer: `/mnt/data` is not encrypted. The root is LUKS2, unlocked in the initramfs by systemd-cryptenroll's TPM2 token on the vTPM.

Command: `findmnt /mnt/data`; `du -sh /mnt/data/*`; `find /mnt/data -xdev | wc -l`

```
/mnt/data /dev/sdb1 ext4 rw,relatime
281M  /mnt/data/frigate
1.5G  /mnt/data/immich
16K   /mnt/data/lost+found
7586 entries
```

Ownership: `/mnt/data`, `frigate`, `immich` are `igor:igor`; `immich/postgres` is `999:root`, mode 0700.

Command: `docker inspect` mounts of every container, filtered on `/mnt/data`

```
/frigate          running  /mnt/data/frigate/media -> /media/frigate
/immich_postgres  running  /mnt/data/immich/postgres -> /var/lib/postgresql/data
```

Immich's `stack.env`: `DB_DATA_LOCATION=/mnt/data/immich/postgres`, `UPLOAD_LOCATION=/mnt/shared/immich` (the library is on NFS).

Command: `docker info -f '{{.DockerRootDir}}'`

```
/var/lib/docker
```

Answer: Docker's data-root is on the LUKS root, not on `/mnt/data`.

Command: `fuser -vm /mnt/data`; `systemctl show docker -p ActiveEnterTimestamp -p RequiresMountsFor`; `ls /etc/systemd/system/docker.service.d/`

```
/mnt/data: kernel mount; postgres processes (immich_postgres)
ActiveEnterTimestamp=Thu 2026-10-01 20:50:31 PDT
RequiresMountsFor=
no-block-boot.conf
```

Answer: before the window, docker.service had no requirement or ordering on `/mnt/data`. The survey also read `systemctl show docker -p Requires` as `Requires=system.slice docker.socket`, with no `mnt-data.mount`.

The phase 2 Ansible review said that `no-block-boot.conf` already had `Requires=... mnt-data.mount`. E11 re-read the file read-only at about 14:35 PDT and found that it does not:

```
-rw-r--r-- 1 root root 30 2026-09-15 14:04 /etc/systemd/system/docker.service.d/no-block-boot.conf
[Unit]
DefaultDependencies=no
```

That is the whole file, 30 bytes. `mnt-data.mount` appears in `systemctl show docker -p Requires -p After` only since 14:14. It comes from the `RequiresMountsFor=/mnt/data` in the new `local-mounts.conf`, which systemd expands into `Requires=` and `After=mnt-data.mount`. `DropInPaths` lists both files. They set different keys, so neither overrides the other. The new drop-in is therefore the only thing that makes Docker wait for `/mnt/data`; it duplicates nothing. `no-block-boot.conf` is hand-written and not in the repository. With `DefaultDependencies=no`, docker.service no longer gets the implicit `After=` and `Requires=` on `sysinit.target` and `basic.target`, nor the implicit `Conflicts=` and `Before=` on `shutdown.target`. It is unmanaged and unrelated to `/mnt/data`.

Running containers: gatus, cloudflared, frigate, immich_server, immich_machine_learning, immich_postgres, immich_redis; Compose projects cloudflare, frigate, gatus, immich (`/home/igor/app/immich-stack`).

## Is `/mnt/data` managed by Ansible?

Search of the repository for `/mnt/data` and the disk's UUID: only `stacks/frigate/docker-compose.yml` (the bind) and phase 0 evidence. `inventory/host_vars/normandy.yml` has no `nfs_mounts` or `mounts_absent`.

Answer: no. The fstab line was written by hand on 2026-08-30. The NFS lines on normandy are not managed either.

## Survey on proxmox-b

Command: `qm config 202` (disks)

```
scsi0: local-lvm:vm-202-disk-1,discard=on,iothread=1,size=100G,ssd=1
scsi1: ssd1tb:202/vm-202-disk-0.qcow2,discard=on,iothread=1,size=900G,ssd=1
efidisk0: local-lvm:vm-202-disk-0   tpmstate0: local-lvm:vm-202-disk-2
```

`qm pending 202` shows no pending changes.

Command: `pvesm status`

```
local       dir      active  37.90%
local-lvm   lvmthin  active  55.48%
nas-backup  nfs      active  71.82%
ssd1tb      dir      active   0.34%
```

Command: `lsblk -o NAME,MODEL,SERIAL,SIZE`; `ls -l /dev/disk/by-id | grep CT1000`

```
sda      CT1000BX500SSD1  2438E98C8AD2  931.5G   sda1 ext4 a48c3122-6c65-48a9-ac40-bdaf86b03609 /mnt/ssd1tb
nvme0n1  WD PC SN810 SDCPNRY-256G-1006  231231800792  238.5G
ata-CT1000BX500SSD1_2438E98C8AD2 -> ../../sda
```

Command: `cat /etc/fstab`; `ls /etc/systemd/system/*.mount`; `systemctl cat mnt-ssd1tb.mount`

```
/dev/sda1 /mnt/ssd1tb ext4 defaults 0 2
no hand-written mount units; mnt-ssd1tb.mount is generated by systemd-fstab-generator
```

Answer: `ssd1tb` is mounted by an fstab line naming `/dev/sda1` (not by UUID).

Command: `grep -rn ssd1tb /etc/pve`

```
/etc/pve/storage.cfg: dir: ssd1tb / path /mnt/ssd1tb / content rootdir,snippets,backup,iso,images,vztmpl / nodes hp-workstation-01
/etc/pve/nodes/hp-workstation-01/qemu-server/202.conf: scsi1: ssd1tb:202/vm-202-disk-0.qcow2,...
/etc/pve/.rrd: metrics only
```

Command: `find /mnt/ssd1tb -maxdepth 3`; `qemu-img info`

```
dump/ (empty)  private/ (empty)  snippets/ (empty)  template/cache/ (empty)  template/iso/ (empty)  lost+found/
images/202/vm-202-disk-0.qcow2   virtual 900 GiB, allocated 3.1 GiB
```

Answer: the qcow2 of VM 202 is the only thing on `ssd1tb`.

Command: `zpool list`; `cat /sys/module/zfs/parameters/zfs_arc_max`

```
no pools available
0
```

## NAS destination chosen

normandy already mounts the `services_backup` export at `/mnt/backup` (the same export proxmox-b mounts as `nas-backup`), 13 TB free. The copy was written to `/mnt/backup/normandy/data-2026-10-02/` (agreed with the lead) and moved after the window to `/mnt/backup/normandy-migration/data-2026-10-02/`, a path that is not shipped to B2 (see Rollback). It holds: `data/` (the tree), `owners.txt` (`find -printf '%U:%G %m %y %P'` of the source, 7589 lines), `sha-src.txt` (sha256 of every regular file in the source, 7537 lines, itself sha256 `3865c417f97efb307d0f452321e24c50c6e4d42715fddc6eab41dd98400ca897`). The export squashes ownership on write (files land as `977:988`), so the restore took ownership from `owners.txt`, not from the copy.

## Design for the new disk

- Plain ext4, no LUKS, matching the old `/mnt/data` (agreed with the lead).
- fstab by UUID at `/mnt/data`, with `nofail`. A docker.service drop-in with `RequiresMountsFor=/mnt/data` keeps Docker from starting if the disk is missing, which is the intent. The phase 1 restore test of VM 102 showed that a `backup=0` disk is dropped on restore and that an fstab line without `nofail` sends the guest to emergency mode. So `nofail` stays on the fstab line and the docker.service requirement does the gating.
- The drop-in is applied with `daemon-reload` only. docker.service is never restarted.
- New base-role capability `local_mounts` (`roles/base/tasks/local_mounts.yml`, default `[]` in `roles/base/defaults/main.yml`, included from `roles/base/tasks/main.yml` only when the list is non-empty). It uses `ansible.posix.mount` with `src: UUID=...` and installs no package, so it works on Arch. Its drop-in is `docker.service.d/local-mounts.conf`, separate from the NFS tasks' `nfs-mounts.conf`. Added after the lead confirmed that E9 was done with `roles/base`.

## The window

Pre-checks at 14:10 PDT, both services still running: the pre-copy was complete, the role change was written, `base.yml --check -l normandy` gave changed=0, and `qm pending 202` was empty. The lead confirmed that no other executor was in a service-affecting step.

Pre-copy (14:08:17 to about 14:10 PDT, services running):

```
sudo rsync -aHAX --numeric-ids --stats /mnt/data/ /mnt/backup/normandy/data-2026-10-02/data/
  7537 files, 1,807,652,647 bytes; rc=23 (chown "Operation not permitted" only)
sudo rsync -rlptHAX --numeric-ids --delete --stats /mnt/data/ .../data/
  rc=0
```

Stop (WINDOW START 14:12:18 PDT):

```
cd /home/igor/app/frigate && docker compose --env-file .env stop
cd /home/igor/app/immich-stack && docker compose --env-file stack.env stop
```

Both were stopped by 14:12:29. gatus and cloudflared stayed up. `fuser -vm /mnt/data` then showed only the kernel mount.

Final copy and checksums (14:12:30 to 14:13:02):

```
sudo rsync -rlptHAX --numeric-ids --delete --stats /mnt/data/ .../data/   rc=0, 19 files transferred, 22 deleted
sha256 of every regular file, source and copy, sorted by path: 7537 lines each
cmp sha-src.txt sha-nas.txt -> MANIFEST_MATCH
```

Release of the old mount (Ansible): `inventory/host_vars/normandy.yml` got `mounts_absent: [/mnt/data]`, then `ansible-playbook playbooks/base.yml -l normandy` ran: changed=1 ("Unmount and forget retired mounts"). The fstab line for `b4b307a6-...` was gone, `/dev/sdb1` was unmounted, and docker.service's `ActiveEnterTimestamp` was unchanged.

Hot-unplug on proxmox-b (14:13:17):

```
qm set 202 --delete scsi1      -> unused0: ssd1tb:202/vm-202-disk-0.qcow2
guest lsblk: sdb gone; dmesg: "[sdb] Synchronize Cache(10) failed: Result: hostbyte=DID_BAD_TARGET" (already unmounted, harmless)
qm set 202 --delete unused0
ls /mnt/ssd1tb/images/202/     -> No such file or directory
pvesm list ssd1tb              -> empty
```

Answer: `--delete unused0` destroyed the qcow2 and its directory. Afterwards `qm config 202` had no `ssd1tb` reference and `qm pending 202` was empty.

Point of no return (14:13:33), with the gate confirmed (ARC cap 2 GiB live and in the initramfs):

```
pvesm remove ssd1tb
umount /mnt/ssd1tb
cp /etc/fstab /root/fstab-before-tank-2026-10-02; removed "/dev/sda1 /mnt/ssd1tb ext4 defaults 0 2"
systemctl daemon-reload            (no ssd1tb unit left)
wipefs -a /dev/disk/by-id/ata-CT1000BX500SSD1_2438E98C8AD2-part1   (ext4 magic)
wipefs -a /dev/disk/by-id/ata-CT1000BX500SSD1_2438E98C8AD2         (both GPT headers, PMBR)
zpool create -f -o ashift=12 -O compression=lz4 -O atime=off -o autotrim=on tank /dev/disk/by-id/ata-CT1000BX500SSD1_2438E98C8AD2
systemctl enable zfs-import@tank.service
```

Before the wipe, the by-id path was checked to resolve to `sda`, model `CT1000BX500SSD1`, serial `2438E98C8AD2`, with nothing mounted.

Quota and storage. The quota is 90 % of usable space, floor(0.9 x (used + available)), from `zfs get -Hp -o value used,available tank`: 417792 + 965561917440, giving 869006101708.

```
zfs set quota=869006101708 tank
pvesm set tank --nodes hp-elite-mini-01,hp-workstation-01
```

E10 had already created the `tank` definition for `hp-elite-mini-01`; E11 only extended its node list. `storage.cfg` now reads `nodes hp-workstation-01,hp-elite-mini-01`, `sparse 1`, `content rootdir,images`, `mountpoint /tank`.

New data disk (14:13:45):

```
qm set 202 --scsi1 tank:400,discard=on,ssd=1,iothread=1,backup=0
  -> scsi1: tank:vm-202-disk-0,backup=0,discard=on,iothread=1,size=400G,ssd=1
  zfs: volsize 429496729600, refreservation 0 (sparse)
guest: sdb 400G appeared (scsi-0QEMU_QEMU_HARDDISK_drive-scsi1); no reboot
sfdisk: GPT, one Linux partition; mkfs.ext4 -L data /dev/sdb1
  -> UUID=0c01def4-9341-4497-aa2f-678c2b0e8531
```

`backup=0`: the per-service backups have to cover the disk's contents. The phase 1 restore test of VM 102 showed that a restore drops such a disk: no drive, no `unused` entry. The old `scsi1` was in vzdump; the new one is not.

Mount (Ansible): in `inventory/host_vars/normandy.yml`, `mounts_absent` was replaced by `local_mounts` (`/mnt/data`, the UUID above, `ext4`, `defaults,nofail,x-systemd.device-timeout=10`, `before_docker: true`). `ansible-playbook playbooks/base.yml -l normandy` gave changed=3: the fstab line, the mount, and `docker.service.d/local-mounts.conf` with `RequiresMountsFor=/mnt/data`.

```
systemctl show docker -p RequiresMountsFor -p ActiveEnterTimestamp -p NeedDaemonReload
  RequiresMountsFor=/mnt/data
  ActiveEnterTimestamp=Thu 2026-10-01 20:50:31 PDT     (unchanged)
  NeedDaemonReload=no
```

Restore (to 14:17:48):

```
sudo rsync -rlptHAX --numeric-ids .../data-2026-10-02/data/ /mnt/data/   7537 files
chown -h / chmod every entry from owners.txt
find -printf listing of the new disk vs owners.txt -> OWNERS_MATCH
sha256 manifest of the new disk vs sha-src.txt     -> MANIFEST_MATCH (7537)
```

Start (14:17:55):

```
cd /home/igor/app/immich-stack && docker compose --env-file stack.env up -d
cd /home/igor/app/frigate && docker compose --env-file .env up -d
```

Compose started the existing containers (no recreate). Bind sources resolve at start, as the checks below show.

## Checks after the window

```
14:18:25 frigate, immich_server, immich_postgres, immich_redis healthy; immich_machine_learning starting
findmnt -N <frigate pid> /media/frigate                     -> /dev/sdb1[/frigate/media] ext4
findmnt -N <immich_postgres pid> /var/lib/postgresql/data  -> /dev/sdb1[/immich/postgres] ext4
http://192.168.70.234:2283/ 200; /api/server/ping {"res":"pong"}; Frigate :5000/api/version 200
https://photos.ryzhkov.dev/api/server/ping 200, https://cctv.ryzhkov.dev/ 200 (14:18:27)
docker.service ActiveEnterTimestamp=Thu 2026-10-01 20:50:31 PDT (never restarted)
```

Igor saw a 503 from photos.ryzhkov.dev at 14:17:56. That fell inside the window, one second after the start and before the containers were healthy.

```
zpool status tank: ONLINE, ata-CT1000BX500SSD1_2438E98C8AD2, no errors
zfs list: tank 1.55G used, 808G avail; tank/vm-202-disk-0 refer 1.55G, volsize 400G
pvesm status proxmox-b: tank zfspool active 848638771 KiB total, 0.19 %
pvesm status proxmox-a: tank zfspool active 1576387215 KiB total, 0.00 %
ansible-playbook playbooks/base.yml --check --diff: changed=0 on blackpearl, homelab, normandy, proxmox-a, proxmox-b, public-api
ansible-playbook playbooks/site.yml --check --diff: changed=0 on blackpearl, homelab, normandy, public-api
```

## Downtime

Immich and Frigate were stopped from 14:12:18 to 14:17:55 PDT (5 min 37 s), and healthy again at 14:18:25 (6 min 7 s in total). Nothing else on normandy stopped.

## Backup coverage of the new disk (read-only check)

- Immich's Postgres: Immich's own nightly dump (`/mnt/shared/immich/backups/immich-db-backup-*`, newest 2026-10-02 02:00) and a hand-written `normandy-backup.timer` on normandy (Mon and Thu 01:30, `/mnt/backup/normandy/postgres/immich_*.sql.gz`, newest 2026-10-01). Neither is managed by the backup role; `roles/backup` only excludes the Immich dumps from rclone and ships `/mnt/backup/normandy` to B2. Gap: the dump job is unmanaged. (Superseded later the same day: E13 brought the job under `roles/backup` as a daily timer, see `2026-10-02-immich-db-backup.md`.)
- Frigate's media (snapshots and clips, 281 MB, 2-day retention): not covered by anything. Gap, low value.
- No coverage was added, as the lead instructed.

## Rollback

Before the point of no return, the NAS copy and the untouched `ssd1tb` were both available. Now only the NAS copy remains, with `owners.txt` and `sha-src.txt`, plus the vzdumps of VM 202 taken before the window, which still include the old `scsi1`. proxmox-b's previous fstab is at `/root/fstab-before-tank-2026-10-02`.

The NAS copy moved after the window. It now lives at `/mnt/backup/normandy-migration/data-2026-10-02/`.

- Why: the phase 2 security review found that the copy sat under `/mnt/backup/normandy`. homelab's cron job `40 6 * * 1,4 homelab-bulk-backup normandy` ships that tree to `b2:igor-offsite-backup/apps/normandy` with `rclone copy`, which never prunes. The next run, Monday 2026-10-05 06:40, would have uploaded the raw Immich Postgres data directory and Frigate's media to B2 permanently.
- The move: `sudo mv /mnt/backup/normandy/data-2026-10-02 /mnt/backup/normandy-migration/` on normandy at about 14:25 PDT. Both paths are on the same NFS mount (`192.168.40.40:/var/nfs/shared/services_backup`), so this was a rename that took 0.018 s.
- Checks after the move: `owners.txt` has 7589 lines and `sha-src.txt` still hashes to `3865c417...a897`. `/mnt/backup/normandy` again holds only `config`, `os-migration-*` and `postgres`.
- Shipped paths: `roles/backup/files/homelab-bulk-backup` ships only `$HOME/app`, `$HOME/apps`, `$HOME/cv-site`, `/mnt/shared/immich`, `/mnt/backup/blackpearl` and `/mnt/backup/normandy`, and homelab's crontab runs exactly those six jobs. The offsite bundle stages under `/mnt/backup/offsite`. vzdump on `nas-backup` prunes only its `dump/` directory. `/mnt/backup/normandy-migration` is outside every shipped path. It already held phase 0's `immich-pgdata.tgz`.
- Retention: keep the copy until the Postgres dump of 2026-10-05 01:30 (`normandy-backup.timer`) has been verified. Delete it by about 2026-10-09, when the vzdumps that still hold the old `scsi1` roll over. Igor decides the deletion.

## Implications for later phases

- Phase 3: `/mnt/data` is now Ansible-managed (`local_mounts`) and docker.service requires it. Moving Docker's data-root, the Frigate record path or Immich's upload location onto it needs no further ordering work.
- Phase 5: the per-service backups must cover Immich's Postgres and Frigate's media, because the new disk is `backup=0`. Today the Postgres dump job on normandy is hand-written.
- `docs/storage-pools.md` (or `docs/storage.md`) should record that `ssd1tb` is gone and that `tank` exists on proxmox-b. That file is not E11's.
