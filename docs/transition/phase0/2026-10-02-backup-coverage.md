# Phase 0: vzdump backup coverage

Date: 2026-10-02, 12:56 to 13:00 PDT.
Hosts: proxmox-a (hp-elite-mini-01), proxmox-b (hp-workstation-01); storage `nas-backup` (NFS 192.168.40.40:/var/nfs/shared/services_backup).
Purpose: plan phase 0 item 2, "confirm a vzdump job covers every guest on both nodes to `nas-backup`; take a manual full backup of each guest that is not covered".

## Backup jobs

Command: `cat /etc/pve/jobs.cfg` (cluster-wide); `grep -v '^#' /etc/pve/vzdump.cron`

```
vzdump: nightly-all-guests
	comment All guests to NAS nightly; created 2026-08-31 after the PVE 9 upgrade
	schedule 02:30
	all 1
	compress zstd
	enabled 1
	mode snapshot
	notes-template {{guestname}} - {{node}} - {{vmid}}
	prune-backups keep-daily=7,keep-weekly=4
	storage nas-backup
```

`vzdump.cron` holds only the `PATH=` line, so there are no legacy cron jobs.

Answer: there is one job. It runs at 02:30 daily, covers every guest on both nodes (`all 1`, no `exclude`, no `node` restriction), and writes to `nas-backup` in snapshot mode with zstd. It keeps 7 daily and 4 weekly backups, the same as the storage's own `prune-backups`.

Command: `pvesh get /cluster/backup-info/not-backed-up`

```
[]
```

Answer: Proxmox reports no guest that is not covered by a backup job.

Command: `pvesh get /nodes/<node>/tasks --typefilter vzdump --limit 3`

```
hp-elite-mini-01  last three runs (2026-09-30, 10-01, 10-02)  status OK, about 18 to 19 minutes each
hp-workstation-01 last three runs (2026-09-30, 10-01, 10-02)  status OK, about 21 to 23 minutes each
```

Answer: the job has succeeded on both nodes on each of the last three nights.

## Per-guest coverage and latest backup

Command: `pvesm list nas-backup` (grouped by VMID: count, newest archive, its size)

| VMID | Node | Name | Type | Archives on nas-backup | Newest backup | Size (bytes) |
|---|---|---|---|---|---|---|
| 100 | a | adguard | LXC | 10 | 2026-10-02 02:30:02 | 310415057 |
| 101 | a | homelab | VM | 10 | 2026-10-02 02:30:11 | 93512989627 |
| 102 | a | black-pearl | VM | 10 | 2026-10-02 02:44:15 | 2880162032 |
| 103 | a | homeassistant | VM | 10 | 2026-10-02 02:45:06 | 5360049949 |
| 104 | a | mqtt | LXC | 10 | 2026-10-02 02:46:09 | 401561999 |
| 105 | a | zigbee2mqtt | LXC | 10 | 2026-10-02 02:46:22 | 947299256 |
| 106 | a | public-api | VM | 10 | 2026-10-02 02:46:49 | 2686534789 |
| 107 | a | adguard | LXC | 10 | 2026-10-02 02:47:38 | 308099507 |
| 110 | a | healthcheck | LXC | 10 | 2026-10-02 02:47:48 | 42673797 |
| 201 | b | alpine-adguard | LXC | 10 | 2026-10-02 02:30:06 | 79508497 |
| 202 | b | omarchy (normandy) | VM | 10 | 2026-10-02 02:30:08 | 83612778446 |
| 250 | b | hyprperch-test | VM | 10 | 2026-10-02 02:51:26 | 1086217731 |

Answer: every guest on both nodes is covered by `nightly-all-guests` and has a backup from 2026-10-02 02:30 to 02:52. No manual backup was needed, and none was taken.

VM 250 has since been destroyed (see `2026-10-02-vm250-destroy.md`); its ten archives remain.

## Disks excluded from backup

Command: `grep -E '^(scsi|virtio|sata|ide|efidisk|tpmstate|rootfs|mp)[0-9]*:' /etc/pve/nodes/*/{qemu-server,lxc}/*.conf`; `grep -rl backup=0 /etc/pve/nodes`

```
102.conf: scsi1: local2tb:102/vm-102-disk-0.qcow2,backup=0,discard=on,iothread=1,size=1800G
```

Answer: the only disk with `backup=0` is black-pearl's (VM 102) 1.8 TB download disk on `local2tb`. No LXC has extra mount points.

Mismatch with the plan: the plan (phase 0 item 2) and review item 16 describe normandy (VM 202) as having a `backup=0` disk. It does not. `scsi1: ssd1tb:202/vm-202-disk-0.qcow2,discard=on,iothread=1,size=900G,ssd=1` has no `backup=0`, and the 2026-10-02 vzdump log for VM 202 shows `include disk 'scsi1' 'ssd1tb:202/vm-202-disk-0.qcow2' 900G`. The nightly backup reads all 1000 GiB ("transferred 1000.00 GiB in 1269 seconds", "backup is sparse: 920.36 GiB (92%) total zero data") and takes 21 minutes.

## Implications for later phases

- Phase 2: wiping `local2tb` destroys VM 102's `scsi1`, which has never been in any backup. Only its 32 GB root disk is recoverable from `nas-backup`.
- Phase 2, step 3: normandy's `/mnt/data` disk is in the nightly vzdump today. After it is rebuilt as a zvol, decide explicitly whether the new `scsi1` gets `backup=0`, as the target design in the audit proposes. That would end today's nightly whole-disk copy of `/mnt/data`, so per-service backups must cover Immich and Frigate first.
- Pruning happens per guest when that guest is backed up, so archives of a destroyed guest such as VM 250 are never pruned by the job. They stay on the NAS until they are removed by hand.

## VM 250's orphaned backups

VM 250's ten archives on `nas-backup` (2026-09-06 to 2026-10-02, about 1.09 GB each, 10.9 GB in total, listed in `2026-10-02-vm250-destroy.md`) are kept on purpose, as the brief required. The nightly job will never prune them automatically, because vzdump prunes only for guests it backs up and VM 250 no longer exists. They stay until someone removes them.

If Igor decides to delete them, remove each volume with `pvesm free`, from either node, one volume at a time:

```
pvesm list nas-backup --vmid 250
pvesm free nas-backup:backup/vzdump-qemu-250-2026_09_06-02_41_51.vma.zst
(repeat for each volid listed)
```

Not run. `pvesm prune-backups nas-backup --vmid 250 --keep-last 1 --dry-run 1` previews a prune that keeps only the newest archive.
