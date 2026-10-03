# Phase 3 window: normandy (VM 202) stop and start

Date: 2026-10-02, 17:38:59 to 17:46 PDT.
Hosts: proxmox-b (hp-workstation-01), normandy (VM 202, 192.168.70.234).
Executor: E16, following `runbook-normandy.md`. Igor's go relayed by the lead
(lead session, question tool); black-pearl's window had finished at 17:33.

Result: done. Docker's and containerd's roots are on `/mnt/data`, memory is
20480 MiB, `cpuunits` 1024. Outage of Docker services from 17:39:13 to 17:42:15
PDT (3 min 2 s); the VM itself was down from 17:41:07 to about 17:41:46.
`no-block-boot.conf` was left in place (step 3.3 skipped, Igor has not decided).

One deviation, at step 2.1: see "Stop condition at 2.1".

## Timeline (PDT)

| Time | Step |
|---|---|
| 17:38:59 | 0.2/0.3 pre-checks |
| 17:39:04 to 17:39:11 | 0.4 baselines, 0.5 last warm pass (rc=0, rc=0) |
| 17:39:13 | 2.1 `systemctl stop docker.socket docker.service`, then `containerd.service`: outage starts |
| 17:39:18 | docker.service SIGKILLed by its 5 s stop timeout; containerd stopped |
| 17:39:34 to 17:40:20 | Frigate's container and the loki plugin confirmed exited |
| 17:40:32 to 17:40:43 | 2.2 final rsync and dry runs |
| 17:40:49 to 17:41:01 | 3.1/3.2 host_vars set, `base.yml -l normandy` converged |
| 17:41:03 | 4 `qm set 202 --memory 20480 --cpuunits 1024` |
| 17:41:07 to 17:41:15 | 5 `qm shutdown 202` (7.7 s) |
| 17:41:19 | 5 `qm start 202` returned |
| 17:41:35 | root LUKS unlocked (vTPM) |
| 17:41:39 / 41 / 44 | `/mnt/data` mounted / containerd active / Docker active |
| 17:41:47 | SSH answers |
| 17:42:04 to 17:42:15 | Frigate, Immich server and ML healthy: outage ends |
| 17:42:53 | first Frigate event after the restart (doorbell, car) |
| 17:43:40 | Gatus green on both instances, public routes 200 |

## 0. Pre-checks

```
pgrep -af "claude .*stream-json|codex"    -> nothing but the probe itself
t3-steward status                          -> no running session; last action 17:30:08 (archive)
qm pending 202 | grep -E "^(new|del)"      -> nothing
qm config 202: memory 24576, balloon 0, no cpuunits, hostpci0 0000:00:02.0,pcie=1, scsi1 tank:vm-202-disk-0,backup=0,...
proxmox-b Mem: available 3185 MB
```

Baselines inside normandy: 7 containers up (gatus, cloudflared, frigate,
immich_server, immich_machine_learning, immich_postgres, immich_redis), root
`/var/lib/docker`, `/mnt/data` on `/dev/sdb1` (27G used, 8 %), `/` 53G used,
no `/etc/containerd`, Docker `ActiveEnterTimestamp` Thu 2026-10-01 20:50:31,
8 images. Frigate: detector `ov` 7.99 ms; garage_outside 3.0/0.1 fps,
doorbell 3.0/3.1, garage_inside and back_yard 0 (disabled).

## Stop condition at 2.1, and what was done

```
systemctl is-active docker.socket docker.service containerd.service -> inactive failed inactive
pgrep -a containerd-shim -> 805 (-namespace plugins.moby, the loki driver), 1679601 (-namespace moby -id 28f8b87e..., Frigate)
journal: docker.service: State 'stop-sigterm' timed out. Killing. ... Failed with result 'timeout'.
systemctl show docker containerd -p TimeoutStopUSec -> 5s, 5s
/etc/systemd/system.conf: DefaultTimeoutStopSec=5s (Omarchy default)
```

Cause: Omarchy sets `DefaultTimeoutStopSec=5s` system-wide. dockerd stops
containers within their grace period (Frigate's is 30 s), so systemd SIGKILLed
dockerd after 5 s with Frigate still shutting down. Six containers had already
exited at 17:39:13; Frigate's processes and the loki driver were left under
their shims. containerd then stopped and left the two shims running.

The runbook's rollback A (restart Docker on the old roots) would have
re-attached to the same orphans and gained nothing. Instead the stop was
completed:

```
sudo kill -TERM 1679625 835     (Frigate's s6-svscan, the loki docker-driver)
  -> 1679625: No such process (Frigate's init had already exited on its own SIGTERM from dockerd)
ps --ppid 1679601; ps --ppid 805 -> no children: both containers' processes gone by 17:39:34
```

The two shims stayed, idle, with no task process (they wait for a containerd
to collect them), and Frigate's merged overlay stayed mounted under
`/var/lib/docker/rootfs/overlayfs/28f8b87e...`. Nothing could write any more,
so the final copy was consistent; the VM restart cleared both. Frigate's
SQLite (on `/home/igor/app/frigate/config`, not in either root) shut down
under its own SIGTERM.

## 2.2 Final copy

```
rsync -aHAX --numeric-ids -x --delete --stats /var/lib/docker/ /mnt/data/docker/
  rc=0, 0.3 s, 12,197 files, 23 transferred (5,217,785 bytes), 24 deleted
rsync ... /var/lib/containerd/ /mnt/data/containerd/
  rc=0, 5.5 s, 204,427 files, 41 transferred (2,098,102 bytes), 24 deleted
dry runs (-n --itemize-changes | wc -l): docker 0, containerd 0
```

The old `/var/lib/docker` and `/var/lib/containerd` stay as the rollback copy.

## 3. Switch the roots

`inventory/host_vars/normandy.yml`: `docker_data_root: /mnt/data/docker` and
`containerd_root: /mnt/data/containerd`.

```
ansible-playbook playbooks/base.yml -l normandy --diff   -> ok=30 changed=5
  daemon.json: + "data-root": "/mnt/data/docker" (rewritten as sorted two-space JSON, other keys unchanged)
  /etc/containerd, config.toml (version = 4, root = "/mnt/data/containerd"), containerd.service.d, containerd-root.conf
systemctl is-active docker.socket docker.service containerd.service -> inactive failed inactive
systemctl show containerd -p RequiresMountsFor -> /mnt/data/containerd
containerd config dump | grep ^root -> root = '/mnt/data/containerd'
```

Step 3.3 (`no-block-boot.conf`) skipped as instructed.

## 4. VM changes

```
qm set 202 --memory 20480 --cpuunits 1024
  cur cpuunits: 1024; cur memory: 24576; new memory: 20480
cpu.weight (qemu.slice/202.scope) -> 1024 (live)
```

## 5. Stop and start

```
qm shutdown 202 --timeout 300  -> real 7.7 s; status: stopped
qm pending 202 | grep -E "^(new|del)" -> nothing (memory applied to the config)
qm start 202  -> "swtpm_setup: Not overwriting existing state file." "vfio-pci ... OpRegion detected on Intel display 3e92."
```

Boot: `Startup finished in 6.911s (firmware) + 6.754s (loader) + 1.246s (kernel) + 2.523s (initrd) + 8.048s (userspace) = 25.484s`.

## 6. Post-checks

6.1 VM configuration and proxmox-b memory.

```
memory: 20480; balloon: 0; cpuunits: 1024; hostpci0, scsi0, scsi1 unchanged; no pending lines; cpu.weight 1024
proxmox-b Mem: available 7170 MB (3185 before)
```

Answer: as planned; proxmox-b gained about 4 GB.

6.2 vTPM unlock and mount ordering.

```
17:41:34 Starting Cryptography Setup for root... / 17:41:35 Finished Cryptography Setup for root.  (no passphrase prompt)
mnt-data.mount   ActiveEnterTimestampMonotonic 6891875  (17:41:39)
containerd       8554429  (17:41:41)
docker           11817884 (17:41:44)
findmnt /mnt/data -> /dev/sdb1 ext4
```

Answer: the vTPM unlocked the root unattended; `/mnt/data` was mounted before
containerd and Docker.

The kernel logged an i915 `WARNING ... intel_vga_disable` call trace at 3.5 s
("failed to retrieve link info, disabling eDP"). The previous boot logged the
same warning twice (`journalctl -b -1 -k`), so it is pre-existing and not caused
by this window.

6.3 Daemons on the new roots.

```
docker info -> /mnt/data/docker overlayfs; driver-type: io.containerd.snapshotter.v1
containerd meta.db -> /mnt/data/containerd/io.containerd.metadata.v1.bolt/meta.db
docker plugin ls -> loki:latest enabled
images: 8 (same as before, no re-pull)
7 containers up; overlays mounted under /mnt/data/docker/rootfs/overlayfs/
```

6.4 GPU, Frigate, Immich ML (17:43).

```
/dev/dri (guest and frigate container): by-path card0 card1 igpu-render renderD128 renderD129
Frigate detectors: ov inference_speed 7.91 ms
cameras: garage_outside 3.1/2.5 fps, doorbell 3.0/8.0, garage_inside 0/0, back_yard 0/0
events since 17:41:40: doorbell car 17:42:53, garage_outside car 17:43:02, 17:43:11
immich_machine_learning providers: ['OpenVINOExecutionProvider', 'CPUExecutionProvider']
```

Answer: the iGPU came back; Frigate detects on OpenVINO at the baseline speed on
both enabled cameras; Immich ML has OpenVINO.

6.5 Health, Gatus, public routes.

```
first healthy: frigate 17:42:04, immich_server 17:42:14, immich_machine_learning 17:42:14; all 7 healthy
normandy Gatus: 50 endpoints, failing []
homelab Gatus: 45 endpoints, failing []
https://photos.ryzhkov.dev/api/server/ping 200; https://cctv.ryzhkov.dev/ 200
user services: t3code, t3-steward, t3-steward-worker, huyang, gatus-steward-health active
```

6.6 Memory inside the guest.

```
Mem total 19924, used 6696, available 13228; swap 0 used; /proc/pressure/memory some avg300=0.00
```

Answer: no pressure at 20 GB; recheck daily for a week.

6.7 Disk. `/` 52G used (the old roots, about 25 GB, still there), `/mnt/data`
27G (8 %).

6.8 Repository convergence.

```
ansible-playbook playbooks/base.yml --check --diff -> changed=0 on blackpearl, homelab, normandy, proxmox-a, proxmox-b, public-api
  (both "restart pending" tasks skipped on normandy)
ansible-playbook playbooks/site.yml --check --diff -> changed=0 on blackpearl, homelab, normandy, public-api
```

## Host changes

- normandy: Docker stopped and started (via the VM restart) on `/mnt/data/docker`;
  containerd on `/mnt/data/containerd`; `/etc/docker/daemon.json` gained
  `data-root`; new `/etc/containerd/config.toml` and
  `/etc/systemd/system/containerd.service.d/containerd-root.conf`. Old roots
  untouched under `/var/lib`.
- proxmox-b: VM 202 `memory: 20480`, `cpuunits: 1024`; one `qm shutdown` and
  `qm start`.

## Implications for later phases

- Any Docker stop on normandy (and on any Omarchy host) must allow for
  `DefaultTimeoutStopSec=5s`: `systemctl stop docker` kills dockerd before
  containers with a longer grace period (Frigate 30 s) are down. Stop the
  Compose stacks first (`docker compose stop`), or stop with
  `--timeout`/a `TimeoutStopSec=` drop-in. This matters for the phase 7 drain
  and for unattended Docker updates. homelab's docker.service has
  `TimeoutStopUSec=1min 30s` (checked 17:47), so its window is not affected.
- Delete `/var/lib/docker` and `/var/lib/containerd` on normandy (about 25 GB)
  after a week, on Igor's decision.
- `no-block-boot.conf` remains; Igor decides.
