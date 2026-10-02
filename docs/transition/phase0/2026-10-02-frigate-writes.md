# Phase 0 check: Frigate write volume against the BX500

Measured 2026-10-02 13:00-13:05 PDT. Hosts: normandy (VM 202) and proxmox-b
(hp-workstation-01). Derived from existing counters, not a one-day delta.

Caveat: executor E1 was restoring a backup to VMID 9202 on `ssd1tb` (the BX500)
during this check. The BX500's `/proc/diskstats` write counter went from
102093904 to 127735600 sectors (about 13 GB) in 141 seconds, so the host-side
uptime average below is contaminated and the clean figures are the guest-side
one and the SMART lifetime average.

## Where Frigate writes

`docker inspect frigate` mounts on normandy:

```
/home/igor/app/frigate/config -> /config
/mnt/data/frigate/media       -> /media/frigate
/dev/dri                      -> /dev/dri
```

`/mnt/data` is `/dev/sdb1` (ext4, 885G, 1.7G used), which is VM 202's `scsi1`,
`ssd1tb:202/vm-202-disk-0.qcow2` on `/mnt/ssd1tb` = `/dev/sda1` on proxmox-b,
the Crucial BX500 (`CT1000BX500SSD1`, serial 2438E98C8AD2, firmware M6CR072).

Frigate's configuration has `record: enabled: false`; only snapshots are kept
(`retain: default: 2`). `recordings/` is empty (4.0K), `clips/` holds 280M, and
files written in the last 24 h total 136.8 MB (2138 files). Recording of the
cameras is done by UniFi Protect on the UDM, not by Frigate.

## (a) Guest counters on normandy

Guest uptime 58239 s (0.674 days; the guest rebooted about 16 h ago although the
QEMU process has run 31.6 days).

```
sdb (BX500, /mnt/data)   659248 sectors written    = 0.34 GB  -> 0.50 GB/day
sda (NVMe, LUKS root)    298161355 sectors written = 152.7 GB -> 226 GB/day
```

## (b) Direct daily recording volume

There is none to measure: recording is disabled. To estimate what recording
would add, the two enabled detect streams were copied for 20 s each inside the
frigate container (read-only, `ffmpeg -c copy` to a temp file, then deleted):

```
garage_outside (1280x720 detect stream)  1691060 bytes / 20 s = 0.68 Mbit/s = 7.3 GB/day
doorbell       (960x720 detect stream)   1319384 bytes / 20 s = 0.53 Mbit/s = 5.7 GB/day
```

The two disabled cameras use 640x360 low channels; their high streams are
2688x1512. A high-resolution stream at a typical 4-6 Mbit/s is 43-65 GB/day per
camera if recorded continuously.

## (c) Host counters on proxmox-b

`lsblk -o NAME,MODEL,SERIAL`: `sda CT1000BX500SSD1 2438E98C8AD2 931.5G`,
`nvme0n1 WD PC SN810 SDCPNRY-256G-1006 231231800792 238.5G`.

Host uptime 2730411 s (31.6 days).

```
sda (BX500)  102093904 sectors written = 52.3 GB -> 1.65 GB/day (includes part of E1's restore)
             1938345032 sectors discarded (weekly guest fstrim of the 900G volume)
nvme0n1      10555544127 sectors written = 5.40 TB -> 171 GB/day
```

`smartctl -A /dev/sda`:

```
  9 Power_On_Hours           15897
246 Total_LBAs_Written       672387944   (x 512 B = 344.3 GB)
202 Percent_Lifetime_Remain  raw 0      (Crucial reports percent used here)
  5 Reallocate_NAND_Blk_Cnt  0
194 Temperature_Celsius      33 (min/max 21/42)
```

Lifetime average: 344.3 GB / 15897 h = 0.52 GB/day.

`smartctl -A /dev/nvme0n1`: `Data Units Written: 23,782,918 [12.1 TB]`,
`Power On Hours: 1,202`, `Percentage Used: 2%`, 35 Celsius: 242 GB/day
lifetime.

## Arithmetic

- BX500 today: about 0.5 GB/day (guest sdb 0.50, SMART lifetime 0.52). At that
  rate 360 TBW is about 2000 years away.
- Frigate today adds about 0.14 GB/day (snapshots) of that.
- Continuous recording of the two current detect streams: about 13 GB/day; total
  about 13.5 GB/day, 360 TBW in about 73 years.
- Continuous recording of all four cameras' high streams at 4-6 Mbit/s: about
  170-260 GB/day, 360 TBW in about 3.8-5.8 years. Event-only retention divides
  that by the motion duty cycle.

## Recorded answer

The BX500 takes about 0.5 GB/day today (Frigate snapshots about 0.14 GB/day,
recording disabled); recording the two current detect streams would add about
13 GB/day (about 73 years to 360 TBW), and continuous high-resolution recording
of four cameras would add 170-260 GB/day (about 4-6 years).

## Implications for later phases

- Phase 2/3: the BX500's endurance is not a constraint unless Frigate is turned
  into a continuous high-resolution recorder; that decision, not the disk move,
  sets the lifetime. The 400 GB zvol sizing for Frigate retention only matters
  if recording is enabled.
- Separate finding, outside this check: normandy's NVMe root (LUKS + btrfs on
  local-lvm) writes 171-242 GB/day (host and SMART agree). The SN810 256G is at
  2 % used after 1202 h; at this rate it is roughly 200 TBW in about 2.5 years.
  The writer is the T3 Code and t3-steward SQLite state, not Frigate; see the
  next section.

## normandy root writes: who writes

Measured 2026-10-02 13:08-13:12 PDT inside normandy, read only (sudo for
`/proc/<pid>/io`). Nothing was changed. `pidstat` and `iotop` are not
installed, so the method is `write_bytes` deltas from `/proc/<pid>/io` for every
process over 120 s, against the `sda` sector counter over the same window.

Device: `sda` wrote 820320 sectors = 400 MiB in 120 s (13:08:02-13:10:03), a
rate of about 288 GB/day, consistent with the 171-242 GB/day averages above.

Top processes by `write_bytes` delta in those 120 s:

```
87.4 MiB  sshd-session pid 554690   (SSH from 192.168.90.212, gaming-pc)
53.1 MiB  t3-steward   pid 1363272  (t3-steward worker serve)
46.1 MiB  node         pid 629      (t3 serve --port 7391, T3 Code server)
35.8 MiB  btrfs-transaction
19.9 MiB  sshd-session pid 556213   (SSH from 192.168.70.223, homelab)
12.2 MiB  kworker kcryptd (LUKS)
 5.2 MiB  t3-steward   pid 1363250  (t3-steward run, coordinator)
 3.6 MiB  systemd-journal
 3.2 MiB  gatus
~13 MiB   btrfs flush/endio and kcryptd kworkers together
```

The two `sshd-session` processes had no long-lived children. Sampling their
children every 0.1 s for 25 s caught only one command:
`/home/igor/.local/bin/t3-steward coordinator-exchange --config
/home/igor/.config/t3-steward/config.yaml`, started repeatedly over SSH by the
other steward hosts. Its I/O is charged to the SSH session when it exits. In
a separate 20 s window session 554690's `write_bytes` rose by 19 MB.

Files held open by the writers:

```
t3-steward  /home/igor/.local/state/t3-steward/state.db      400723968 bytes (+ -wal 4.8 MB)
t3 serve    /home/igor/.t3/userdata/state.sqlite              1377906688 bytes (+ -wal 10.8 MB)
```

Both are SQLite databases in WAL mode on btrfs (`/@home`,
`compress=zstd:3`) inside LUKS. Neither directory has the `C` (nodatacow)
attribute (`lsattr -d` shows no flags). Every WAL checkpoint rewrites pages
inside a large copy-on-write file, so btrfs writes new extents, checksums and
metadata for each, and the device sees roughly 400 MiB for about 220 MiB of
application writes.

Suspects ruled out:

- Frigate: `docker stats` block output 341 MB since the guest booted 16 h ago;
  its cache is a 1 GB tmpfs (`/tmp/cache`); not in the 120 s top list (under
  2 MiB).
- Immich: postgres 255 MB, redis 173 MB, server 0 B written since boot.
- Docker logs: the logging driver is `loki`; `/var/lib/docker/containers` is
  196K.
- journald: 3.6 MiB in 120 s (about 2.6 GB/day, not negligible but small); 554.5M
  on disk.
- zram/swap: 0 B used, `pswpout 0`; zram is in RAM anyway.

All containers together wrote about 0.9 GB in 16 h, about 1.4 GB/day.

Recorded answer: normandy's 170-290 GB/day of root writes come from the T3
agent stack's SQLite state, mainly t3-steward's `state.db` (its local worker
and coordinator plus `coordinator-exchange` runs over SSH from gaming-pc and
homelab, together about 40 % of the attributed bytes) and T3 Code's
1.4 GB `state.sqlite` (about 12 %). btrfs copy-on-write and LUKS roughly double
those writes. Frigate, Immich, Docker logs and swap contribute under 2 %.

Implication: this is the agent tooling's write pattern, not a homelab service,
and it wears normandy's 256 GB NVMe (local-lvm on proxmox-b). Candidate fixes,
for the owner of t3-steward to decide: fewer exchange round-trips or batched
writes, a smaller `state.db` (vacuum or pruning), and `chattr +C` on the state
directories (new files only) or moving them off btrfs. The same pattern
probably exists on the other steward hosts.
