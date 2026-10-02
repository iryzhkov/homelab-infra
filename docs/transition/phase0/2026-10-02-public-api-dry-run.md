# Phase 0 item 3: public-api stop/start dry run

Result in one line: shutdown 14.3 s, boot-to-SSH 14 s and boot-to-HTTPS 16 s
after `qm start` returned, 32 s from the shutdown command to HTTPS 200; the
pending discard/ssd change applied and a trim released 2.67 GiB to the thin
pool.

Host: proxmox-a (hp-elite-mini-01), VM 106 public-api (192.168.20.33).
First checked 2026-10-02 12:52 and 13:05 PDT; run after the lead's decision
at 13:06 PDT.

## Pending change: found, then accepted

The brief required `qm pending 106` to show no pending changes, because a stop
and start applies them. It showed one, so the run was first held back:

```
cur scsi0: local-lvm:vm-106-disk-0,iothread=1,size=32G
new scsi0: local-lvm:vm-106-disk-0,discard=on,iothread=1,size=32G,ssd=1
```

Lead decision (coordinator, 2026-10-02): accept the change and run the dry run.
Reason: `docs/storage.md` lines 44-53 record that `discard=on` and `cache=none`
were staged on purpose on 2026-09-06 for homelab, public-api and Black Pearl's
system disk and are waiting for a stop/start, which is what phase 3 intends for
106.

Differences between the pending line and the repository record:

- The pending line also carries `ssd=1`, which `docs/storage.md` does not
  mention.
- It carries `iothread=1`, unchanged from the running value.
- It carries no explicit `cache=` option. The running disk has none either, so
  it already uses the Proxmox default (no host cache, equivalent to
  `cache=none`); the `cache=none` change in `docs/storage.md` is real only for
  homelab (101), whose running disk is `cache=writethrough`.
- It pairs with `scsihw: virtio-scsi-single` (unchanged, not pending): one
  virtio-scsi controller per disk, which is what makes `iothread=1` effective.
- The `[PENDING]` section's file mtime reads 02:47 PDT today, not 2026-09-06.
  The config file was presumably rewritten later; the staged content matches
  the record.

VM 101's pending change was not touched.

## Before the run

`qm config 106` (current config with pending values applied in the view):

```
agent: 1
boot: order=scsi0;ide2;net0
cores: 2
cpu: x86-64-v2-AES
ide2: local:iso/deb_12.iso,media=cdrom,size=631M
memory: 4096
meta: creation-qemu=9.0.0,ctime=1766035756
name: public-api
net0: virtio=BC:24:11:7A:F3:A4,bridge=vmbr1,firewall=1,tag=20
numa: 0
onboot: 1
ostype: l26
scsi0: local-lvm:vm-106-disk-0,discard=on,iothread=1,size=32G,ssd=1
scsihw: virtio-scsi-single
smbios1: uuid=518b3dca-c6d3-428c-b6be-2de6744fabcd
sockets: 1
vmgenid: 56a913e0-0524-4b60-a483-fcc1bda55c06
```

`qm pending 106`: every key `cur` with the same value as above, except

```
cur scsi0: local-lvm:vm-106-disk-0,iothread=1,size=32G
new scsi0: local-lvm:vm-106-disk-0,discard=on,iothread=1,size=32G,ssd=1
```

`lvs -o lv_name,lv_size,data_percent pve/vm-106-disk-0 pve/data`:

```
data           337.86g 72.29
vm-106-disk-0   32.00g 22.60
```

Baseline HTTPS (laptop resolves the names to 192.168.20.33 through AdGuard):
`https://uptime.ryzhkov.dev/` 200, `https://igor.ryzhkov.dev/` 200.

## Run

From the laptop. SSH polled with `ssh -o ConnectTimeout=3 -o ControlPath=none
-o BatchMode=yes public-api true`, HTTPS with `curl -sk -o /dev/null -m 4 -w
'%{http_code}' https://uptime.ryzhkov.dev/` (Gatus, routed by public-api's
Traefik), both every 2 s.

```
13:06:35  date -Is before shutdown (2026-10-02T13:06:35-07:00)
          ssh proxmox-a 'time qm shutdown 106 --timeout 180'   real 0m14.349s, exit 0
13:06:49  shutdown returned; qm status 106: stopped
          ssh proxmox-a 'time qm start 106'                    real 0m1.309s, exit 0
13:06:51  start returned
13:07:05  ssh public-api true succeeded                        +14 s after start
13:07:07  https://uptime.ryzhkov.dev/ returned 200             +16 s after start
```

Shutdown duration 14.3 s; boot-to-SSH 14 s; boot-to-HTTPS 16 s; total outage
from the shutdown command to HTTPS 200 about 32 s.

## After the run

- `qm pending 106 | grep -E '^(new|del)'`: nothing; the change is applied.
  `qm status 106 --verbose`: running, `pc-i440fx-11.0+pve0`.
- `docker ps` on public-api at 13:07:14: `traefik Up 9 seconds`,
  `diun Up 9 seconds`.
- Guest: `lsblk -D /dev/sda` shows DISC-GRAN 4K, DISC-MAX 1G (as before);
  `/sys/block/sda/queue/rotational` is now `0` (the `ssd=1` effect).

## Trim

`sudo fstrim -av` inside public-api, 13:07:14-13:07:22:

```
/: 26.6 GiB (28567957504 bytes) trimmed on /dev/sda1
```

`lvs` on proxmox-a, before the run, right after the start, and 5 s after the
trim:

```
                 before   after start   after trim
vm-106-disk-0    22.60    22.60         14.26        (of 32.00g)
data             72.29    -             71.50        (of 337.86g)
```

vm-106-disk-0 fell from 7.23 GiB to 4.56 GiB allocated, 2.67 GiB released, and
the pool fell by the same amount (0.79 % of 337.86 GiB). The weekly trim of
2026-09-28 (724 MiB reported) had released nothing because discard was being
dropped.

## Recorded answer

Shutdown 14.3 s, boot-to-SSH 14 s, boot-to-HTTPS 16 s (about 32 s outage in
total); discard now reaches the thin pool: 22.60 % to 14.26 % for vm-106 after
one trim.

## Implications for later phases

- Phase 3 budget: the floor is well under a minute for a small Debian VM with
  Docker. homelab will be longer (28 GiB RAM, iGPU passthrough, many
  containers), but the 5-10 minute assumption looks pessimistic.
- The same mechanism (pending `discard=on` on an LVM-thin volume, then `fstrim`)
  is proven on this pool, so the phase 3 homelab trim should release its
  roughly 60-70 GiB once 101 restarts with its pending change. Note 101's disk is
  virtio-blk, not SCSI; the path differs at the device, not at the pool.
