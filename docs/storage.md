# Where the disks are, and the discard problem

Written 2026-09-06, after a question about whether Black Pearl's second disk was
being used well. It was, and the storage around it was not.

## The physical picture

| Node | Device | Size | Proxmox storage | Holds |
|---|---|---|---|---|
| proxmox-a | nvme0n1, CT500P3 | 466 GB | `local`, `local-lvm` (LVM-thin) | every VM's system disk, all the LXC volumes |
| proxmox-a | nvme1n1, CT2000P3 | 1.8 TB | `local2tb` (dir, qcow2) | Black Pearl's download scratch |
| proxmox-b | nvme0n1, WD SN810 | 239 GB | `local`, `local-lvm` | normandy's system disk |
| proxmox-b | sda, CT1000BX500 | 932 GB | `ssd1tb` (dir, qcow2) | normandy's 900 GB data disk |
| NAS | UniFi Drive | 44 TB | `nas-backup` (NFS) | media, backups, machine learning |

`ssd1tb` appears as **disabled** in `pvesm status` on proxmox-a. That is not a
fault: the storage is declared with `nodes hp-workstation-01`, so it exists only
on proxmox-b. The same is true in reverse for `local2tb`, which shows disabled
on proxmox-b. Each node sees the other's local storage listed and unavailable.

## Black Pearl's scratch disk is the right idea

qBittorrent writes incomplete torrents to `/mnt/tmp` on the 1.8 TB NVMe and
moves completed files to `/mnt/shared` on the NAS. Torrenting is small random
writes with preallocation; doing that on flash rather than across NFS to
spinning disks is correct and worth keeping.

What was wrong was everything around it.

## Discard, and 176 GB of blocks nobody was using

A thin-provisioned disk only returns space when the guest tells the host that
blocks are free, and the guest can only do that if the VM's disk has
`discard=on`. Without it, a guest's `fstrim.timer` runs every week, reports
success, and achieves nothing.

Measured on 2026-09-06:

| Disk | Guest says | Host had allocated |
|---|---|---|
| Black Pearl `/mnt/tmp` | 1.5 GB | 58 GB in the qcow2 |
| homelab root | 67 GB of 187 GB | 189 GB of a 192 GB thin volume, 98.5% |

Black Pearl's disk already had `discard=on`, so a manual `fstrim -v /mnt/tmp`
returned the space immediately: the qcow2 went from 58 GB to 1.5 GB, and it
reported 1.7 TiB trimmed.

homelab, public-api and Black Pearl's system disk had no `discard=on` at all.
They have it now, along with `cache=none` in place of `cache=writethrough`,
which is both faster and equally safe. These are pending changes: a disk option
applies when the VM next stops and starts, not on a guest reboot. Black Pearl
was restarted the same day and has them; homelab and Home Assistant are
waiting for a convenient moment.

normandy's disks on proxmox-b were already `discard=on,ssd=1`, which is why its
900 GB data disk occupies 3.9 GB.

## Rebalancing, attempted and abandoned

The 500 GB NVMe was 73% full while the 1.8 TB NVMe held 1.5 GB, so homelab's
192 GB root — the largest single consumer — was moved to `local2tb` online.
The move was cancelled at 17% after measuring what it was actually doing.

**The CT2000P3 cannot sustain writes.** With nothing else competing the mirror
managed 14 MB/s, decaying from 104 MB/s at the start, which put the remaining
160 GB at over three hours. The obvious explanations were all ruled out:

| Suspect | Measurement |
|---|---|
| Thermal throttling | 66 °C against an 85 °C warning threshold, and `Warning Comp. Temperature Time: 0` |
| PCIe link | negotiated x4 at 8 GT/s, full width and speed |
| Drive health | 6% used, spare 100%, zero media errors |
| qcow2 overhead | a plain `dd` with `O_DIRECT` to the same filesystem was also under 10 MB/s |

What is left is the drive: the P3 is DRAM-less QLC. It is fine for the bursty
writes of a torrent scratch, which is what it already does, and poor at bulk
sequential ingest. That is a characteristic to design around rather than a
fault to fix.

So homelab's root stays on `local-lvm`, and the capacity problem is solved by
discard instead: reclaiming its stranded blocks takes the pool from 73% to
roughly 37% without moving anything. Cancelling a drive-mirror is safe — the VM
keeps running on its original disk throughout, which it did, and Proxmox
removed the partial destination file itself.

## What to check when a disk looks full

1. What the guest reports (`df -h`) against what the host has allocated
   (`lvs -o lv_name,data_percent` for LVM-thin, `du -sh` on the qcow2 for a
   directory storage). A large gap means discard is not working.
2. Whether the VM config has `discard=on` for that disk.
3. Whether the guest has `fstrim.timer` enabled. Both halves are needed, and
   each is useless alone.
