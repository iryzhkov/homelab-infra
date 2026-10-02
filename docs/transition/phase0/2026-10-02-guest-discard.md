# Phase 0 check: guest discard and thin-pool allocation

Measured 2026-10-02 12:55 PDT. Guests: homelab (101), normandy (202),
public-api (106), blackpearl (102). Host: proxmox-a for the thin pool.

## lsblk -D inside each guest

```
homelab     vda (virtio-blk)   DISC-GRAN 512B  DISC-MAX 2G
normandy    sda, sdb (scsi)    DISC-GRAN 4K    DISC-MAX 1G   (root is LUKS: dm 'root' shows 0B/0B)
public-api  sda (scsi)         DISC-GRAN 4K    DISC-MAX 1G
blackpearl  sda, sdb (scsi)    DISC-GRAN 4K    DISC-MAX 1G
```

Every guest disk advertises discard, including homelab's and public-api's,
whose running configurations have no `discard=on`. QEMU's virtio-blk and
scsi-hd devices advertise discard regardless; with the drive's discard mode
at the default `ignore`, the host silently drops the requests.

On normandy the root filesystem sits on a LUKS mapping (`/dev/mapper/root`,
btrfs) that reports `DISC-GRAN 0B`, so the crypt layer is not passing discards
through (no `allow-discards`). `/mnt/data` (sdb1, ext4) does.

## fstrim.timer

`systemctl is-enabled fstrim.timer`: enabled and active in all four guests;
last run Mon 2026-09-28, next Mon 2026-10-05.

homelab's last run, from `journalctl -u fstrim.service`:

```
2026-09-28T00:21:26-0700 homelab fstrim[1711243]: /: 60.3 GiB (64758243328 bytes) trimmed on /dev/vda1
```

public-api: `/: 724.3 MiB (759459840 bytes) trimmed on /dev/sda1`.

The guest trims weekly and reports success; the space never returns because
the host ignores it.

## Thin pool on proxmox-a

`lvs -o lv_name,lv_size,data_percent,pool_lv pve`:

```
data           337.86g 72.29
vm-101-disk-0  192.00g 99.96   (guest df: 187G size, 122G used, 69 %)
vm-102-disk-0   32.00g 27.33   (guest df: 6.9G used of 31G)
vm-106-disk-0   32.00g 22.60   (guest df: 3.8G used of 31G)
vm-103-disk-1   32.00g 46.79
vm-105-disk-0   10.00g 55.03
vm-100-disk-0    2.00g 98.89
vm-104-disk-0    2.00g 80.62
vm-107-disk-0    2.00g 97.92
vm-110-disk-0    1.00g 19.93
snap_vm-103-disk-1_pre-matter-nic 32.00g, snap_vm-105-disk-0_pre-fw-flash 10.00g, snap_vm-103-disk-0_pre-matter-nic 4.00m
```

homelab's volume is 192 GiB x 99.96 % = about 192 GiB allocated against 122 GB
used in the guest. The two snapshots on 103 and 105 pin their blocks and will
keep those volumes from shrinking after trim.

## Recorded answer

Yes, the virtio disks expose discard to every guest (homelab DISC-GRAN 512B /
DISC-MAX 2G) and fstrim already runs weekly, but on 101 and 106 the host drops
the requests because `discard=on` is only pending; homelab's thin volume is at
99.96 % for 122 GB of data.

## Implications for later phases

- Phase 3: once 101 restarts with the pending `discard=on`, one `fstrim -av`
  should release roughly 60-70 GiB, taking vm-101-disk-0 from about 192 GiB to
  about 125 GiB allocated, in line with the plan's estimate.
- 106 gains the same on its next stop/start (about 3-4 GiB).
- normandy's LUKS root does not pass discards; adding `discard` to its crypttab
  entry is a separate decision (it leaks free-space layout through the
  ciphertext).
