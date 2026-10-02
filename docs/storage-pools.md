# Storage pools on the Proxmox nodes

The durable reference for the local storage on proxmox-a and proxmox-b since
phase 2 of the serviceable transition (2026-10-02). The dated evidence, with
every measurement, is in `docs/transition/phase2/`:

- `2026-10-02-zfs-arc-cap.md` - the ARC cap through Ansible
- `2026-10-02-tank-proxmox-a.md` - `tank` on proxmox-a
- `2026-10-02-normandy-data-and-tank-b.md` - `tank` on proxmox-b and normandy's data disk
- `2026-10-02-templates.md` - the cloud-init templates on `local-lvm`

Each node has two drives: a boot NVMe holding the Proxmox root, swap and the
`local-lvm` thin pool, and a second drive given whole to a single-disk ZFS pool
named `tank`. Every drive is addressed by `/dev/disk/by-id/` path, never by
`nvmeXn1` or `sdX`, which can change between boots.

## proxmox-a (hp-elite-mini-01, 192.168.1.5)

| Drive | Serial | by-id path | Holds |
|---|---|---|---|
| Crucial CT500P3SSD8, 500 GB NVMe | 24244958383E | `nvme-CT500P3SSD8_24244958383E` | boot disk: VG `pve` with root, swap and the `data` thin pool (`local-lvm`); template 9000 |
| Crucial CT2000P3SSD8, 2 TB NVMe | 2420E8AFBCE0 | `nvme-CT2000P3SSD8_2420E8AFBCE0` | the ZFS pool `tank` |

- `tank`: ashift 12, compression lz4, atime off, autotrim on. Raw size
  1992864825344 bytes.
- Quota: 1738012203417 bytes (1.58 TiB), set on the pool's root dataset. An
  earlier value, 1614220508528, had been computed while a first quota still
  capped `available`, and was replaced.
- ARC cap: `zfs_arc_max = 4294967296` (4 GiB).
- The 2 TB P3 is DRAM-less QLC and sustains bulk writes poorly (see
  `docs/storage.md`). Its second temperature sensor read 69 to 79 C in phase 0
  and phase 2, without any recorded warning time.

The drive was previously the `dir` storage `local2tb`, an ext4 filesystem
(UUID `3ab4fa0b-26d3-4454-9935-d2e8339784ab`) mounted by a hand-written unit
`/etc/systemd/system/mnt-pve-local2tb.mount`, not by fstab. Phase 1 moved Black
Pearl's download scratch off it, and phase 2 retired it.

## proxmox-b (hp-workstation-01, 192.168.1.29)

| Drive | Serial | by-id path | Holds |
|---|---|---|---|
| WD PC SN810 SDCPNRY-256G-1006, 256 GB NVMe | 231231800792 | (boot disk) | Proxmox root, swap and `local-lvm`; normandy's system, EFI and TPM disks; template 9001 |
| Crucial CT1000BX500SSD1, 1 TB SATA | 2438E98C8AD2 | `ata-CT1000BX500SSD1_2438E98C8AD2` | the ZFS pool `tank` |

- `tank`: ashift 12, compression lz4, atime off, autotrim on. Raw size
  996432412672 bytes.
- Quota: 869006101708 bytes (809 GiB).
- ARC cap: `zfs_arc_max = 2147483648` (2 GiB).

The drive was previously the `dir` storage `ssd1tb`, mounted from the fstab
line `/dev/sda1 /mnt/ssd1tb ext4 defaults 0 2`, and held only normandy's 900 GB
qcow2 data disk. The previous fstab is kept at
`/root/fstab-before-tank-2026-10-02` on proxmox-b.

## ARC cap

ZFS's default ARC limit on these kernels is almost all of RAM, which the guests
need. The cap is set by Ansible, in the base role:

- `inventory/host_vars/proxmox-a.yml`: `zfs_arc_max_bytes: 4294967296`
- `inventory/host_vars/proxmox-b.yml`: `zfs_arc_max_bytes: 2147483648`
- `roles/base/tasks/zfs_arc.yml`, included only on hosts that define
  `zfs_arc_max_bytes`. It writes `options zfs zfs_arc_max=<bytes>` in
  `/etc/modprobe.d/zfs.conf`, runs `update-initramfs -u` when that line
  changes, and writes `/sys/module/zfs/parameters/zfs_arc_max` when the live
  value differs.

Apply with `ansible-playbook playbooks/base.yml -l proxmox-a,proxmox-b`. Both
nodes boot through GRUB on EFI from `/boot` on the ext4 root, so
`update-initramfs` is enough and `proxmox-boot-tool refresh` is not needed.

## The shared `tank` storage definition

One Proxmox storage definition covers both pools. The pool name is node-local,
so each node resolves `tank` to its own drive. In `/etc/pve/storage.cfg`:

```
zfspool: tank
	pool tank
	content rootdir,images
	mountpoint /tank
	nodes hp-workstation-01,hp-elite-mini-01
	sparse 1
```

`sparse 1` makes zvols thin (no `refreservation`), so the per-pool quota is what
stops the pools filling. The storage is not shared: a disk on `tank` lives on
one node, and moving a guest between nodes copies it.

## normandy's `/mnt/data`

VM 202 (normandy) on proxmox-b has `scsi1` as a 400 GB zvol on `tank`:

```
scsi1: tank:vm-202-disk-0,backup=0,discard=on,iothread=1,size=400G,ssd=1
```

In the guest it is one GPT partition with ext4, label `data`, UUID
`0c01def4-9341-4497-aa2f-678c2b0e8531`, mounted at `/mnt/data`. It holds Immich's
Postgres (`/mnt/data/immich/postgres`) and Frigate's snapshots and clips
(`/mnt/data/frigate/media`). The Immich library is on NFS, not on this disk.

The mount is managed by the base role's `local_mounts` capability
(`roles/base/tasks/local_mounts.yml`), declared in
`inventory/host_vars/normandy.yml`:

```yaml
local_mounts:
  - path: /mnt/data
    uuid: 0c01def4-9341-4497-aa2f-678c2b0e8531
    fstype: ext4
    opts: defaults,nofail,x-systemd.device-timeout=10
    before_docker: true
```

`before_docker: true` writes `docker.service.d/local-mounts.conf` with
`RequiresMountsFor=/mnt/data`, so Docker does not start on an empty mount point.
`nofail` stays on the fstab line, because a restore of VM 202 drops a
`backup=0` disk and without `nofail` the guest would boot to emergency mode.

`backup=0` means the nightly vzdump of VM 202 does not include this disk. Its
contents are protected only by the services' own backups (see Known gaps).

## Commands used

proxmox-a, retiring `local2tb` and creating `tank`:

```
pvesm remove local2tb
systemctl disable --now mnt-pve-local2tb.mount
rm /etc/systemd/system/mnt-pve-local2tb.mount
systemctl daemon-reload
rmdir /mnt/pve/local2tb
wipefs -a /dev/disk/by-id/nvme-CT2000P3SSD8_2420E8AFBCE0
zpool create -f -o ashift=12 -O compression=lz4 -O atime=off -o autotrim=on tank /dev/disk/by-id/nvme-CT2000P3SSD8_2420E8AFBCE0
systemctl enable zfs-import@tank.service
pvesm add zfspool tank --pool tank --sparse 1 --content images,rootdir --nodes hp-elite-mini-01
zfs set quota=none tank                    # measure usable space with no quota in place
zfs get -Hp -o value used,available tank   # 626688, 1931124043776
zfs set quota=1738012203417 tank
```

proxmox-b, after normandy's data was copied to the NAS and `scsi1` released:

```
qm set 202 --delete scsi1
qm set 202 --delete unused0
pvesm remove ssd1tb
umount /mnt/ssd1tb
# fstab line "/dev/sda1 /mnt/ssd1tb ext4 defaults 0 2" removed
systemctl daemon-reload
wipefs -a /dev/disk/by-id/ata-CT1000BX500SSD1_2438E98C8AD2-part1
wipefs -a /dev/disk/by-id/ata-CT1000BX500SSD1_2438E98C8AD2
zpool create -f -o ashift=12 -O compression=lz4 -O atime=off -o autotrim=on tank /dev/disk/by-id/ata-CT1000BX500SSD1_2438E98C8AD2
systemctl enable zfs-import@tank.service
zfs set quota=869006101708 tank
pvesm set tank --nodes hp-elite-mini-01,hp-workstation-01
qm set 202 --scsi1 tank:400,discard=on,ssd=1,iothread=1,backup=0
```

In normandy, the new disk was partitioned with `sfdisk` (GPT, one Linux
partition) and formatted with `mkfs.ext4 -L data /dev/sdb1`, then mounted by
`ansible-playbook playbooks/base.yml -l normandy`.

The quota on each pool is 90 % of usable space, computed after the pool is
created:

```
zfs get -Hp -o value used,available tank
# quota = floor(0.9 * (used + available))
```

Measure with no quota set (`zfs set quota=none tank` first), because an
existing quota caps `available` and the result would shrink each time.
Usable space is the raw `zpool list` size minus ZFS's slop reservation (about
3 %, 1/32 of the pool).

## Attaching a mirror later

Both pools are single-disk, so a drive failure loses the pool. To turn one into
a mirror, fit a drive of at least the same size and attach it by id:

```
ls -l /dev/disk/by-id/                     # find the new drive's by-id path by model and serial
wipefs -a /dev/disk/by-id/<new by-id>
zpool attach tank <existing by-id> <new by-id>
zpool status tank                          # wait for the resilver to finish
```

The existing by-id names are `nvme-CT2000P3SSD8_2420E8AFBCE0` on proxmox-a and
`ata-CT1000BX500SSD1_2438E98C8AD2` on proxmox-b, as `zpool status tank` lists
them. Use `zpool attach`, not `zpool add`: `add` would stripe the new drive
into the pool rather than mirror it, and that cannot be undone. Quota and
Proxmox storage need no change.

## After a host reboot

On each node:

1. `zpool status tank` shows ONLINE with the by-id vdev, and `zfs list tank`
   shows it mounted at `/tank`.
2. `systemctl is-enabled zfs-import@tank.service zfs-import-cache.service zfs-mount.service`
   reports `enabled` for all three.
3. `cat /sys/module/zfs/parameters/zfs_arc_max` shows the cap (4294967296 on
   proxmox-a, 2147483648 on proxmox-b).
4. `pvesm status` lists `tank` as active.

On normandy, after proxmox-b or VM 202 restarts:

1. `findmnt /mnt/data` shows the ext4 filesystem with UUID
   `0c01def4-9341-4497-aa2f-678c2b0e8531`.
2. `systemctl show docker -p RequiresMountsFor` shows `/mnt/data`.
3. `/mnt/data` was mounted before Docker started: compare
   `systemctl show mnt-data.mount -p ActiveEnterTimestamp` with
   `systemctl show docker -p ActiveEnterTimestamp`.
4. Immich and Frigate are healthy.

## Rollback copy of normandy's old data disk

The content of the old `/mnt/data` (the 900 GB qcow2 on `ssd1tb`, destroyed
on 2026-10-02) is kept on the NAS at
`/mnt/backup/normandy-migration/data-2026-10-02/`: `data/` (the tree),
`owners.txt` (numeric owner, group and mode of every entry, because the export
squashes ownership) and `sha-src.txt` (sha256 of every file). It was first
written under `/mnt/backup/normandy` and moved out the same day. homelab's
`homelab-bulk-backup normandy` job (Mon and Thu 06:40) copies that tree to B2
with `rclone copy`, which never prunes. That would have put the raw Immich
Postgres data directory and Frigate's media in B2 permanently.
`normandy-migration` is not shipped anywhere.

Keep the copy until the Postgres dump of 2026-10-05 has been verified. Delete
it by about 2026-10-09, when the vzdumps of VM 202 that still hold the old
`scsi1` roll over. Igor decides the deletion.

## Known gaps

- normandy has a second, unmanaged docker.service drop-in,
  `/etc/systemd/system/docker.service.d/no-block-boot.conf` (written by hand on
  2026-09-15, not in this repository). Its entire content is `[Unit]` and
  `DefaultDependencies=no`. It has nothing to do with `/mnt/data`: the
  requirement on `/mnt/data` comes only from the managed `local-mounts.conf`.
  `DefaultDependencies=no` drops docker.service's implicit ordering after
  `sysinit.target` and `basic.target` and its implicit stop at
  `shutdown.target`. Follow-up, not done: either bring it into this repository
  with the reason it exists, or remove it once the boot behaviour it was
  written for is understood.

- Frigate's media (`/mnt/data/frigate/media`, snapshots and clips, about 281
  MB with 2-day retention) is not backed up by anything. Low value, but
  recorded.
- Immich's Postgres is dumped by Immich's own nightly job and by a hand-written
  `normandy-backup.timer` on normandy (Mon and Thu 01:30, to
  `/mnt/backup/normandy/postgres/`). Neither is managed by the backup role,
  which only ships `/mnt/backup/normandy` to B2.
- Boot import of `tank` is unproven on both nodes. Neither node has been
  rebooted since its pool was created. Phase 7's host-down test is the first
  proof.
