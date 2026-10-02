# Phase 1: how a restore of VM 102 treats its `backup=0` disk

Date: 2026-10-02, 13:30 to 13:55 PDT.
Hosts: proxmox-a (hp-elite-mini-01, 192.168.1.5) for the restore; proxmox-b (hp-workstation-01, 192.168.1.29) for VMID checks and housekeeping; black-pearl (VM 102) read-only for its fstab. Source archive on `nas-backup` (NFS 192.168.40.40).
Purpose: the phase 0 acceptance makes this a precondition of phase 1 step 5. Before VM 102's 1.8 TB `scsi1` (`backup=0`, on `local2tb`) is detached and deleted, establish how a restore of VM 102 treats that disk. Method follows the phase 0 restore of VM 202 (`docs/transition/phase0/2026-10-02-restore-test-vm202.md`): restore to a scratch VMID with `--unique`, make it safe, boot it, destroy it. VM 102 itself was not touched.

Result: a restore of VM 102 drops the `backup=0` disk. The restored config keeps the `scsi1` line only as a comment, with no drive and no `unused` entry. The restored guest then does not boot: its fstab mounts the missing disk at `/mnt/tmp` by UUID without `nofail`, so systemd times out on the device and stops in emergency mode, and Control-D drops back into emergency mode.

## Mismatches with the brief

- None that changed a step. VM 102's config has no `sshkeys` line and no hostpci or USB devices. VMID 250, listed on proxmox-b in phase 0, no longer appears in `qm list` there; it does not affect VMID 9102.
- The guest agent could not prove boot, because the guest never leaves emergency mode. Boot was proved instead with QEMU monitor screen dumps, which are the only console this VM has (it has no serial port).

## 1. Baseline of VM 102 and the archive

Command: `qm config 102 > /root/qm102-before-2026-10-02.txt; sha256sum`; `qm status 102 --verbose`

```
96c74250b8ede269ef2f8d25ab88aafd23f78b9eea65c18ef0bdefe2e2aa9f6d
scsi0: local-lvm:vm-102-disk-0,cache=none,discard=on,iothread=1,size=32G,ssd=1
scsi1: local2tb:102/vm-102-disk-0.qcow2,backup=0,discard=on,iothread=1,size=1800G
net0: virtio=BC:24:11:C2:13:91,bridge=vmbr1,firewall=1,tag=60
memory: 8192   onboot: 1   startup: order=5
status: running   uptime: 2231010
```

Command: `pvesm list nas-backup --vmid 102`; `zstd -dc <archive> | vma config -`; `grep -E 'include|exclude' <archive>.log`

```
nas-backup:backup/vzdump-qemu-102-2026_10_02-02_44_15.vma.zst vma.zst backup 2880162032 102
scsi1: local2tb:102/vm-102-disk-0.qcow2,backup=0,discard=on,iothread=1,size=1800G
#qmdump#map:scsi0:drive-scsi0:local-lvm:raw:
INFO: include disk 'scsi0' 'local-lvm:vm-102-disk-0' 32G
INFO: exclude disk 'scsi1' 'local2tb:102/vm-102-disk-0.qcow2' (backup=no)
```

Answer: the newest archive is last night's `vzdump-qemu-102-2026_10_02-02_44_15.vma.zst` (2.88 GB). It holds only `scsi0`. The `scsi1` line is in the archive's stored config, but `scsi1` has no `#qmdump#map` entry, so its contents are not in the backup.

## 2. Scratch VMID and space

Command: `qm list` and `pct list` on both nodes; `pvesh get /cluster/nextid --vmid 9102`; `pvesm status`; `free -m` on proxmox-a

```
used VMIDs: 100-107, 110, 201, 202
9102
local-lvm  lvmthin active 354275328 253306859 100968468 71.50%
Mem: total 63974 used 48839 available 15135
```

Answer: VMID 9102 was free cluster-wide. `local-lvm` had 96 GiB free for a 32 GB thin volume, and proxmox-a had 15 GB of memory available.

Before restoring, the restore code on proxmox-a was read (`/usr/share/perl5/PVE/QemuServer.pm`, `restore_update_config_line`): a drive line with `backup=0` is written back as `#<line>`, a comment. The owner checks in `vmconfig_register_unused_drive`, `try_deallocate_drive` and `destroy_vm` skip any volume whose owner VMID is not the VM being changed. So neither `qm set --delete` nor `qm destroy` on 9102 could free VM 102's volume.

## 3. Restore and how `scsi1` appears

Command (13:38:07 PDT): `qmrestore /mnt/pve/nas-backup/dump/vzdump-qemu-102-2026_10_02-02_44_15.vma.zst 9102 --unique 1 --storage local-lvm` (no `--start`)

```
new volume ID is 'local-lvm:vm-9102-disk-0'
total bytes read 34359738368, sparse bytes 26015387648 (75.7%)
real 0m44.281s
```

Command: `cat /etc/pve/qemu-server/9102.conf`; `qm config 9102`

```
#scsi1%3A local2tb%3A102/vm-102-disk-0.qcow2,backup=0,discard=on,iothread=1,size=1800G
net0: virtio=BC:24:11:EB:93:24,bridge=vmbr1,firewall=1,tag=60
scsi0: local-lvm:vm-9102-disk-0,cache=none,discard=on,iothread=1,size=32G,ssd=1
(no scsi1 line, no unusedN line)
description: scsi1%3A local2tb%3A102/vm-102-disk-0.qcow2,backup=0,...   (qm config's view of the comment)
```

Answer: on a restore to a new VMID, `scsi1` is absent as a drive and is not `unused`. It survives only as a config comment, which `qm config` shows as the `description`. No drive line referenced a VM 102 volume, so there was no drive key to delete. The restore took 44 s.

Command: `qm set 9102 --delete description`, so that no text in 9102's config named VM 102's volume. Then `qm config 102 | sha256sum`; `pvesm list local2tb`

```
96c74250b8ede269ef2f8d25ab88aafd23f78b9eea65c18ef0bdefe2e2aa9f6d
local2tb:102/vm-102-disk-0.qcow2 qcow2 images 1932735283200 102
```

Answer: VM 102's config was byte-identical to step 1, and its 1.8 TB volume still existed.

## 4. Making the scratch VM safe, and boot

Command:

```
qm set 9102 --net0 virtio=BC:24:11:EB:93:24,bridge=vmbr1,firewall=1,tag=60,link_down=1 \
  --onboot 0 --memory 2048 --balloon 0 --name black-pearl-restoretest --delete startup
```

```
net0: virtio=BC:24:11:EB:93:24,bridge=vmbr1,firewall=1,link_down=1,tag=60
onboot: 0   memory: 2048   balloon: 0
(no hostpci, usb or startup line)
```

Answer: the only NIC had its link down, the VM would not start on boot, it had 2048 MB, and it had no passthrough devices. `--unique` gave it a new MAC, smbios UUID and vmgenid.

Command (13:39:03 PDT): `qm start 9102`; then `qm guest cmd 9102 ping` every 6 s for 240 s

```
rc=0
(no agent ping in 240 s)
status: running
```

Command (13:45:31 PDT): `echo 'screendump /root/vm9102.ppm' | qm monitor 9102`, image viewed on the laptop

```
/dev/sda1: clean, 100048/2035824 files, 1961858/8138240 blocks
[ TIME ] Timed out waiting for device dev-disk-by\x2duuid-cc61153d\x2d1167\...d6acb.device - /dev/disk/by-uuid/cc61153d-1167-40a6-a74e-53b4b5ad6acb.
[DEPEND] Dependency failed for mnt-tmp.mount - /mnt/tmp.
[DEPEND] Dependency failed for local-fs.target - Local File Systems.
You are in emergency mode. ...
Give root password for maintenance
(or press Control-D to continue):
```

Command (13:45:36 PDT): `qm sendkey 9102 ctrl-d`, then the agent ping loop for 150 s, then a second screen dump

```
Reloading system manager configuration
Starting default.target
You are in emergency mode. ...
(or press Control-D to continue):
```

Answer: the restored guest's kernel and root filesystem are fine (`/dev/sda1: clean`), but boot stops in emergency mode. The missing data disk's fstab entry makes `local-fs.target` fail. Continuing with Control-D re-enters emergency mode, and the guest agent never starts. A restore of black-pearl as it is configured today needs console access with the root password to finish booting.

The fstab was read from the live black-pearl instead, read-only, because the restored guest's agent never ran. Command: `ssh blackpearl 'grep -v "^#" /etc/fstab; findmnt /mnt/tmp; lsblk'`

```
UUID=2929647d-8d52-4a4d-9e2b-b97f8e44a430 /        ext4  errors=remount-ro 0 1
UUID=cb58dbb7-7bd2-40c5-988a-d0f09b28d93d none     swap  sw                0 0
UUID=cc61153d-1167-40a6-a74e-53b4b5ad6acb /mnt/tmp ext4  defaults          0 2
192.168.40.40:/var/nfs/shared/media /mnt/shared nfs nconnect=8,...,_netdev,hard,timeo=50,retrans=3 0 0
192.168.40.40:/var/nfs/shared/services_backup /mnt/apps nfs ...,noauto,x-systemd.automount,... 0 0
/dev/sdb1 /mnt/tmp ext4
sdb 1.8T  sdb1 1.8T cc61153d-1167-40a6-a74e-53b4b5ad6acb /mnt/tmp
```

Answer: the line that breaks boot is `UUID=cc61153d-1167-40a6-a74e-53b4b5ad6acb /mnt/tmp ext4 defaults 0 2`, the 1.8 TB `scsi1`, with neither `nofail` nor `x-systemd.device-timeout`. It is not managed by Ansible: a search of the repository for `/mnt/tmp` and `cc61153d` finds only docs, phase 1 evidence and `stacks/torrent/docker-compose.yml`, nothing under `ansible/`. The same failure will happen on the live black-pearl at its next boot once `scsi1` is detached, unless the line is changed first.

## 5. Teardown and VM 102 check

Command (13:50:07 PDT): `qm stop 9102`; `qm destroy 9102 --purge 1 --destroy-unreferenced-disks 1`

```
stop rc=0
Logical volume "vm-9102-disk-0" successfully removed.
purging VM 9102 from related configurations..
destroy rc=0
```

Command: `qm list`; `qm status 102 --verbose`; `qm config 102 > /root/qm102-after-2026-10-02.txt; sha256sum; cmp`; `lvs pve`; `pvesm list local-lvm | grep -c 9102`; `pvesm list local2tb`

```
102 black-pearl running 8192 32.00 584028
status: running   uptime: 2231763
before 96c74250b8ede269ef2f8d25ab88aafd23f78b9eea65c18ef0bdefe2e2aa9f6d
after  96c74250b8ede269ef2f8d25ab88aafd23f78b9eea65c18ef0bdefe2e2aa9f6d
IDENTICAL
vm-102-disk-0 pve Vwi-aotz-- 32.00g data 27.37
0
local2tb:102/vm-102-disk-0.qcow2 qcow2 images 1932735283200 102
```

Answer: VM 9102 and its only volume are gone, and no `vm-9102` volume remains. VM 102 kept running with the same PID, and its uptime advanced by the elapsed time rather than resetting. Its config is byte-identical, and both of its volumes are present.

The working files were removed after their content was recorded here: `rm /root/qm102-before-2026-10-02.txt /root/qm102-after-2026-10-02.txt /root/restore9102.log /root/restore9102.start /root/vm9102.ppm` on proxmox-a, and the screen dump copies on the laptop.

## 6. Housekeeping from the phase 0 security review

Command: `ls -l /tmp/rrd-*.json /tmp/ct100.yaml /tmp/ct107.yaml`; `du -cb /tmp/rrd-*.json`; then `rm -f` of the same paths. Contents were not printed.

proxmox-a, deleted:

```
/tmp/ct100.yaml 6874   /tmp/ct107.yaml 6875   (both Sep 6 13:21)
28 files /tmp/rrd-*.json, 12023908 bytes total (Oct 2 11:40 to 11:41):
rrd-lxc-{100,104,105,107,110}-month.json and -month-max.json (496417 to 570031 each)
rrd-node-{month,week,year}.json and -max.json (71423 to 724294 each)
rrd-qemu-{101,102,103,106}-month.json, -month-max.json and -year-max.json (56020 to 604028 each)
```

proxmox-b, deleted (`/tmp/ct100.yaml` and `/tmp/ct107.yaml` do not exist there):

```
14 files /tmp/rrd-*.json, 4516901 bytes total (Oct 2 11:41):
rrd-lxc-201-month.json 581886, -month-max.json 545409
rrd-node-month.json 712170, -month-max.json 683117, -week.json 167881, -week-max.json 160659, -year.json 84796, -year-max.json 79791
rrd-qemu-202-month.json 594964, -month-max.json 575780, -year-max.json 58538
rrd-qemu-250-month.json 117834, -month-max.json 117834, -year-max.json 36242
```

Answer: all 42 RRD exports and both container YAML files are gone; a follow-up `ls` reports `No such file or directory` for every pattern on both nodes.

## Implications for later phases

- Phase 1 step 5: before `qm set 102 --delete scsi1`, change black-pearl's fstab line for `/mnt/tmp` (`UUID=cc61153d-1167-40a6-a74e-53b4b5ad6acb`). Remove it, or at least add `nofail,x-systemd.device-timeout=10s`, and unmount `/mnt/tmp` in the guest before the detach. The line is not in Ansible today, so make the change through Ansible (an `ansible.posix.mount` task with `state: absent`) rather than by hand. Confirm with `findmnt --verify` and a reboot of VM 102 before the qcow2 is deleted. Without this change, black-pearl's next reboot after the detach stops in emergency mode and needs console access.
- Rollback after step 5: a restore of VM 102 brings back only the 32 GB root disk. The 1.8 TB contents are not in any backup and are accepted as lost from backups, as they already are; the NAS copy made earlier in phase 1 is their only copy.
- Any restore of VM 102 taken before the fstab change has the same emergency-mode boot. Budget console access with the root password for that case; a blank replacement disk would not help, because the fstab entry names the old filesystem's UUID. The restore itself takes under a minute from the NAS.

Answer: a restore of VM 102 drops the `backup=0` disk (it becomes a config comment, neither a drive nor `unused`), and the restored guest stops in emergency mode on its `/mnt/tmp` fstab entry. Phase 1 step 5 must therefore remove that fstab entry, or make it `nofail`, through Ansible before the detach, and accepts the 1.8 TB contents as lost from backups.
