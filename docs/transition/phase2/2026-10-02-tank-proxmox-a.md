# Phase 2 step 2: ZFS pool `tank` on proxmox-a

Date: 2026-10-02, measurements from 14:05 PDT (-07:00).
Host: proxmox-a (hp-elite-mini-01, 192.168.1.5), pve-manager 9.2.11.
Executor: E10. Scope: the `local2tb` storage and the CT2000P3SSD8 drive only.

## 1. Preconditions (read-only)

### Storage content

Command: `pvesm list local2tb`

Output: header line only (`Volid Format  Type      Size VMID`), exit 0.

Answer: the storage holds no volumes.

### Files on the filesystem

Command: `find /mnt/pve/local2tb -type f`

Output: `-rw-r--r-- 1 root root 103809024 Sep  6 18:02 /mnt/pve/local2tb/.speedtest`. Directories: `lost+found`, `snippets`, `private`, `images`, `template/iso`, `template/cache`, `dump`, all empty. `du -sh` 100M.

Answer: only `.speedtest` (103,809,024 bytes, 99.0 MiB), a disposable speed-test artefact, plus empty directories.

### References under /etc/pve

Command: `grep -rn local2tb /etc/pve`

Output:

```
/etc/pve/.rrd:10:pve-storage-9.0/hp-elite-mini-01/local2tb:...
/etc/pve/storage.cfg:10:dir: local2tb
/etc/pve/storage.cfg:11:	path /mnt/pve/local2tb
```

Answer: only `storage.cfg` and a harmless rrd entry.

### Guest configurations

Command: `grep -ln local2tb /etc/pve/nodes/*/qemu-server/*.conf /etc/pve/nodes/*/lxc/*.conf`

Output: nothing, exit 1. Files checked: hp-elite-mini-01 LXC 100, 104, 105 (and `105.conf.bak-20260609`), 107, 110, VM 101, 102, 103, 106; hp-workstation-01 LXC 201, VM 202.

Answer: no guest references the storage.

### Open files

Command: `lsof +D /mnt/pve/local2tb`

Output: nothing, exit 1.

Answer: no process holds a file on it.

### Drive identity

Commands: `ls -l /dev/disk/by-id/`, `lsblk -o NAME,MODEL,SERIAL,SIZE,MOUNTPOINTS`

Output (trimmed):

```
nvme-CT2000P3SSD8_2420E8AFBCE0 -> ../../nvme1n1
nvme-CT500P3SSD8_24244958383E -> ../../nvme0n1
nvme0n1  CT500P3SSD8   24244958383E  465.8G  (pve VG: root, swap, data thin pool)
nvme1n1  CT2000P3SSD8  2420E8AFBCE0    1.8T
  nvme1n1p1                            1.8T  /mnt/pve/local2tb
```

Answer: the target is CT2000P3SSD8 serial 2420E8AFBCE0, by-id path `/dev/disk/by-id/nvme-CT2000P3SSD8_2420E8AFBCE0`, currently `nvme1n1` as expected. The boot disk is CT500P3SSD8 serial 24244958383E (`nvme0n1`) and is not touched.

All preconditions matched the brief.

## 2. How the filesystem was mounted

Command: `cat /etc/systemd/system/mnt-pve-local2tb.mount`

```
[Install]
WantedBy=multi-user.target

[Mount]
Options=defaults
Type=ext4
What=/dev/disk/by-uuid/3ab4fa0b-26d3-4454-9935-d2e8339784ab
Where=/mnt/pve/local2tb

[Unit]
Description=Mount storage 'local2tb' under /mnt/pve
```

The unit was enabled and active, wanted by `multi-user.target` through the symlink `/etc/systemd/system/multi-user.target.wants/mnt-pve-local2tb.mount` (dated Dec 28 2025). `blkid /dev/nvme1n1p1` confirmed `UUID="3ab4fa0b-26d3-4454-9935-d2e8339784ab" TYPE="ext4" PARTUUID="6c7a4c65-1f43-4054-8e9c-8e9b64a021c8"`.

Command: `cat /etc/fstab`

Output: entries for `/dev/pve/root`, `/boot/efi` (UUID=7144-2A6D), `/dev/pve/swap`, `proc`, and the NFS mount `192.168.40.40:/var/nfs/shared/services_backup /mnt/backup`. No `local2tb` line.

Answer: the mount came only from the hand-written systemd unit (the form the Proxmox GUI "Directory" storage creates); there was no fstab line to remove, so the plan's "remove its fstab line" does not apply on proxmox-a.

## 3. Retiring `local2tb`

Stanza recorded from `/etc/pve/storage.cfg` before removal:

```
dir: local2tb
	path /mnt/pve/local2tb
	content backup,snippets,vztmpl,images,iso,rootdir
	is_mountpoint 1
	nodes hp-elite-mini-01
```

Commands run, in order, with results:

- `pvesm remove local2tb`: exit 0; `grep -c local2tb /etc/pve/storage.cfg` then 0.
- `systemctl disable --now mnt-pve-local2tb.mount`: `Removed '/etc/systemd/system/multi-user.target.wants/mnt-pve-local2tb.mount'.`, exit 0.
- `rm /etc/systemd/system/mnt-pve-local2tb.mount`
- `systemctl daemon-reload`
- `findmnt /mnt/pve/local2tb`: no output, exit 1. `systemctl status mnt-pve-local2tb.mount`: `Unit mnt-pve-local2tb.mount could not be found.`
- The underlying mountpoint directory `/mnt/pve/local2tb` on the root filesystem was empty after the unmount (`find` listed only the directory itself), so it was removed with `rmdir /mnt/pve/local2tb`, exit 0.

The `.speedtest` file (103,809,024 bytes) and the empty Proxmox directory tree lived on the ext4 filesystem on `nvme1n1p1`, not on the root filesystem. They are no longer mounted anywhere and are destroyed by the `wipefs` and `zpool create` in step 4.

Answer: `local2tb` is retired: no storage definition, no mount unit, nothing mounted, no mountpoint directory.

Rollback until step 4: re-add the stanza above with `pvesm add dir local2tb --path /mnt/pve/local2tb --content backup,snippets,vztmpl,images,iso,rootdir --is_mountpoint 1 --nodes hp-elite-mini-01`, recreate the unit from the content above and `systemctl enable --now mnt-pve-local2tb.mount`. After step 4 there is nothing to roll back to; the filesystem held no data.

## 4. ZFS pool creation

State before the gate: `cat /sys/module/zfs/parameters/zfs_arc_max` returned 0 (no cap yet; E9's Ansible change pending). `zpool list`: `no pools available`. `systemctl is-enabled zfs-import-cache.service zfs-import-scan.service zfs-mount.service zfs-import.target zfs.target`: all `enabled`.

The lead released the gate at about 14:09 after E9 reported the cap live and in the initramfs (`docs/transition/phase2/2026-10-02-zfs-arc-cap.md`).

### ARC cap

Command: `cat /sys/module/zfs/parameters/zfs_arc_max` (14:09:45)

Output: `4294967296`

Answer: the 4 GiB ARC cap is in effect. `readlink -f` on the by-id path still gave `/dev/nvme1n1`, nothing from it was mounted, and `zpool list` said `no pools available`.

### Wipe

Command: `wipefs -a /dev/disk/by-id/nvme-CT2000P3SSD8_2420E8AFBCE0`

Output:

```
...: 8 bytes were erased at offset 0x00000200 (gpt): 45 46 49 20 50 41 52 54
...: 8 bytes were erased at offset 0x1d1c1115e00 (gpt): 45 46 49 20 50 41 52 54
...: 2 bytes were erased at offset 0x000001fe (PMBR): 55 aa
...: calling ioctl to re-read partition table: Success
```

Answer: the primary GPT, the backup GPT and the protective MBR were erased, exit 0. The ext4 filesystem holding `.speedtest` was destroyed with them.

### Pool creation

Command: `zpool create -f -o ashift=12 -O compression=lz4 -O atime=off -o autotrim=on tank /dev/disk/by-id/nvme-CT2000P3SSD8_2420E8AFBCE0`

Output: exit 0. The vdev appears in `zpool status` as `nvme-CT2000P3SSD8_2420E8AFBCE0`, ONLINE, with 0 errors. ZFS wrote a new GPT: `nvme1n1p1` is a 1.8T `zfs_member` labelled `tank` (pool GUID 9770480044549924427), and `nvme1n1p9` is the 8M reserved partition. No stale ext4 signature remains.

Answer: `tank` exists on the 2 TB drive, addressed by id.

### Import at boot

Commands: `ls -l /etc/zfs/zpool.cache`, `zdb -C -U /etc/zfs/zpool.cache`, `zpool get cachefile tank`, `systemctl is-enabled zfs-import-cache.service zfs-import-scan.service zfs-mount.service zfs-import.target zfs.target zfs-import@tank.service`

Output: `/etc/zfs/zpool.cache` was written at 14:09 (1752 bytes). It contains pool `tank`, hostname `hp-elite-mini-01`, path `/dev/disk/by-id/nvme-CT2000P3SSD8_2420E8AFBCE0-part1`. `cachefile` is the default (`-`). `zfs-import-cache`, `zfs-import-scan`, `zfs-mount`, `zfs-import.target` and `zfs.target` are all `enabled`. `zfs-import@tank.service` is `disabled`.

Follow-up on the lead's instruction: `systemctl enable zfs-import@tank.service` printed `Created symlink '/etc/systemd/system/zfs-import.target.wants/zfs-import@tank.service' → '/usr/lib/systemd/system/zfs-import@.service'.`, exit 0. `systemctl is-enabled zfs-import@tank.service` now returns `enabled`. This is what Proxmox's own pool creation does.

Answer: `tank` imports at boot through `zfs-import-cache.service`, which uses the by-id path recorded in the cache file, and through the per-pool `zfs-import@tank.service`. `zfs-mount` then mounts it. proxmox-a was not rebooted, so the boot import stays unproven until the phase 7 host-down test.

## 5. Proxmox storage and quota

Command: `pvesm add zfspool tank --pool tank --sparse 1 --content images,rootdir --nodes hp-elite-mini-01`

Output: exit 0. Stanza now in `/etc/pve/storage.cfg`:

```
zfspool: tank
	pool tank
	content images,rootdir
	mountpoint /tank
	nodes hp-elite-mini-01
	sparse 1
```

Proxmox added `mountpoint /tank` by itself. E11 extends `nodes` to hp-workstation-01 once proxmox-b has its own `tank`.

### Quota

Formula: `quota = floor(0.9 * (used + available))`, with `used` and `available` taken from `zfs get -Hp -o value used,available tank`. The quota was removed first with `zfs set quota=none tank` (exit 0, `zfs get -Hp -o value quota tank` then read 0), so that `available` was not capped by an earlier quota. The read then gave used = 626688 and available = 1931124043776, about raw minus the 1/32 slop. The quota is therefore floor(0.9 * 1931124670464) = 1738012203417 bytes (1.58 TiB, 87.2 % of raw).

Command: `zfs set quota=1738012203417 tank`, exit 0. Afterwards `zfs get -Hp -o value quota,available tank` reported quota 1738012203417 and available 1738011564441.

First correction: the first quota, 1793578342809 bytes, was floor(0.9 * raw size from `zpool list`). The lead had it recomputed from usable space so that about 10 % of the pool stays free.

Second correction: the second value, 1614220508528 bytes, was computed while the first quota still capped `available`, so it came out as 0.9 of the first quota (about 83.6 % of usable space). It was replaced by the value above, measured with no quota set.

### Final state

`pvesm status` (trimmed):

```
Name   Type     Status   Total (KiB)  Used (KiB)  Available (KiB)  %
tank   zfspool  active    1697277542         624      1697276918  0.00%
```

`local`, `local-lvm` and `nas-backup` are unchanged and active. `ssd1tb` shows `disabled` on this node because it is restricted to hp-workstation-01, as before.

`zpool status tank`: `state: ONLINE`, the single vdev `nvme-CT2000P3SSD8_2420E8AFBCE0` ONLINE, 0 read, write and checksum errors, `errors: No known data errors`.

`zfs get compression,atime,quota tank`: compression `lz4` local, atime `off` local, quota `1738012203417` local.

`zpool get autotrim,ashift tank`: autotrim `on` local, ashift `12` local.

Answer: `tank` is active as Proxmox storage on hp-elite-mini-01 with every requested property set.

## 6. Drive temperature

Command: `smartctl -A /dev/disk/by-id/nvme-CT2000P3SSD8_2420E8AFBCE0`

Before (14:05, idle ext4 mounted): `Temperature: 59 Celsius`, `Temperature Sensor 1: 59 Celsius`, `Temperature Sensor 2: 75 Celsius`, `Temperature Sensor 8: 59 Celsius`, `Warning  Comp. Temperature Time: 0`, `Critical Comp. Temperature Time: 0`, `Percentage Used: 7%`, `Data Units Written: 68,664,556 [35.1 TB]`, `Power On Hours: 18,746`.

Answer: sensor 2 reads 75 C against about 79 C in phase 0; no warning or critical temperature time recorded.

After (14:10:01, pool idle): `Temperature: 59 Celsius`, `Temperature Sensor 1: 59 Celsius`, `Temperature Sensor 2: 69 Celsius`, `Temperature Sensor 8: 59 Celsius`, warning and critical temperature time both 0.

Answer: no thermal change from creating the pool. Sensor 2 is the hot one, and it should be watched when phase 3 puts homelab's data zvol on `tank`.

## Implications for later phases

- Phase 2 step 3 (E11): extend `tank`'s `nodes` to include hp-workstation-01 after proxmox-b's pool exists. The pool name must be exactly `tank`.
- Phase 7: the host-down test is the first real proof that `tank` imports at boot, through `zpool.cache` and `zfs-import@tank.service`.
- Capacity planning on `tank` should use 1.58 TiB, the quota, rather than 1.8T.
- The plan's "remove its fstab line" does not apply on proxmox-a, because the mount was a systemd unit. `docs/storage-pools.md` should describe it that way.
