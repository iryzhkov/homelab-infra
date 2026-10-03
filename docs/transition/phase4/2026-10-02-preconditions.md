# Phase 4 preconditions carried from phase 3

Date: 2026-10-02, 20:05 to 20:12 PDT. Executor E19.
Hosts: proxmox-a, proxmox-b, homelab, blackpearl (all read only over SSH from the laptop).
Source of the preconditions: `docs/transition/phase3/2026-10-02-acceptance.md`, "Carried
forward". Nothing was changed.

## Summary

| Precondition | Status now | Can be closed |
|---|---|---|
| Tonight's 02:30 vzdump skips the `backup=0` zvols of 101 and 202 | configuration correct; run not yet happened | after 2026-10-03 ~03:00 |
| proxmox-b memory remeasured before public-api-b is added | measured, but public-api-b (VM 108) was already running | now, with the mismatch noted |
| black-pearl's copy through `noexec` `/mnt/apps` at 01:00 and 01:10 | cron entries and mount correct; run not yet happened | after 2026-10-03 01:10 |
| Monday's homelab bulk backup of black-pearl | last run `exit=0`; next run not yet happened | after 2026-10-05 06:00 |

Phase 4 preparation (VM 108, proposals) does not depend on any of the waiting items. The
VIP cutover and the failover test, which power off public-api, should wait for the first two
backup items, so that a known-good vzdump of VM 106 and of black-pearl's data exists from
after phase 3.

## 1. vzdump and the `backup=0` disks

Command: `cat /etc/pve/jobs.cfg` on both nodes; `qm config 101` on proxmox-a and
`qm config 202` on proxmox-b, disk lines; the four newest `/var/log/vzdump/qemu-*.log`.

```
vzdump: nightly-all-guests   all 1  mode snapshot  schedule 02:30  storage nas-backup
                             prune-backups keep-daily=7,keep-weekly=4
virtio1: tank:vm-101-disk-0,backup=0,discard=on,iothread=1,size=300G
scsi1:   tank:vm-202-disk-0,backup=0,discard=on,iothread=1,size=400G,ssd=1
qemu-101.log 2026-10-02 02:44 Finished Backup of VM 101 (00:14:04)
qemu-202.log 2026-10-02 02:51 Finished Backup of VM 202 (00:21:18)
```

The last run (2026-10-02 02:30) predates the phase 3 windows, so it says nothing about the
new zvols. `nas-backup` has 13.17 TB free (71.88 % used).

Recorded answer: configured correctly, unproven until tonight's run.

Note: `all 1` means VM 108 (public-api-b, 16 GB root) is backed up from tonight as well.

Check after tonight:

```
ssh proxmox-a 'grep -E "exclude disk|include disk|Finished|ERROR" /var/log/vzdump/qemu-101.log'
ssh proxmox-b 'grep -E "exclude disk|include disk|Finished|ERROR" /var/log/vzdump/qemu-202.log /var/log/vzdump/qemu-108.log'
```

Expected: `exclude disk 'virtio1' ... (backup=no)` for 101, `exclude disk 'scsi1' ...
(backup=no)` for 202, `Finished Backup` for 101, 202 and 108, and a 101 duration well under
last night's 14 minutes now that Docker's root is on the excluded zvol.

## 2. proxmox-b memory

Command: `ssh proxmox-b 'free -m; qm list'`, `/proc/spl/kstat/zfs/arcstats`, KSM.

```
               total        used        free      shared  buff/cache   available
Mem:           31871       25546        5738          60        1309        6324
Swap:           7715         479        7236
 108 public-api-b  running  2048 MB
 202 omarchy       running 20480 MB
 ARC size 2053 MiB, c_max 2048 MiB; KSM pages_sharing 0
 memory pressure: some avg10=0.00 avg60=0.00 avg300=0.00
```

Mismatch with the brief: the precondition says to remeasure before public-api-b is added,
but VM 108 was already running (created by E18) when this was measured at 20:05. Available
memory is 6324 MB with it, against 7.2 GB recorded in phase 3 without it: about 0.9 GB used
by a 2 GB VM that has `balloon: 0` and has not yet touched all of its memory. Expect
available to settle near 5.2 GB once VM 108 has run for a while. No memory pressure, 479 MB
swap in use, ARC at its 2 GB cap.

Recorded answer: 6324 MB available with public-api-b running; enough for phase 4, and the
headroom left for agent-b in phase 6 is about 5 GB, not 7 GB.

## 3. black-pearl's copy through `/mnt/apps`

Command: `crontab -l` and `findmnt -T /mnt/apps` on blackpearl; the target on homelab.

```
0  1 * * * rsync -a --no-owner --no-group /home/iryzhkov/app/ /mnt/apps/blackpearl
10 1 * * * stat /mnt/apps/blackpearl >/dev/null && findmnt -n -t nfs,nfs4 -T /mnt/apps/blackpearl >/dev/null && rsync -a --no-owner --no-group /home/iryzhkov/apps/ /mnt/apps/blackpearl/stack-definitions
/mnt/apps: rw,nosuid,nodev,noexec,noatime,vers=3,... addr=192.168.40.40
```

`rsync` runs from the root file system and only writes into `/mnt/apps`, so `noexec` should
not affect it. File times under `/mnt/backup/blackpearl` on homelab cannot prove a run,
because `rsync -a` keeps the source's times.

Recorded answer: configured as expected; unproven until 2026-10-03 01:10.

Check after 01:10 (read only, a dry run that should list nothing):

```
ssh blackpearl 'rsync -an --itemize-changes --no-owner --no-group /home/iryzhkov/app/ /mnt/apps/blackpearl | grep -v "^\." | head; rsync -an --itemize-changes --no-owner --no-group /home/iryzhkov/apps/ /mnt/apps/blackpearl/stack-definitions | grep -v "^\." | head; journalctl --since "2026-10-03 00:59" --until "2026-10-03 01:20" | grep -i "cron\|rsync"'
```

A few changed lines from files written after 01:10 (qBittorrent state) are normal; a full
listing means the copy did not run.

## 4. Monday's bulk backup

Command: `cat ~/.local/state/homelab-backup/bulk-blackpearl.last-run` on homelab.

```
2026-10-01T06:01:18-07:00 exit=0
```

Recorded answer: the last run, before phase 3, succeeded. The next one (Monday
2026-10-05, 06:00 per the phase 3 window record) is the one that proves the new layout;
it gates phase 4's acceptance record, not the cutover.

## Implications for later phases

- Phase 4: schedule the VIP cutover and the public-api power-off test after tonight's checks
  (sections 1 and 3) pass, not before.
- Phase 6: proxmox-b's headroom for agent-b is about 5 GB with public-api-b settled, not the
  7.2 GB recorded in phase 3.
