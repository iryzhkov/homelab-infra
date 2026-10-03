# Phase 3 window: homelab (VM 101) stop and start

Date: 2026-10-02, 19:18 to 19:40 PDT.
Hosts: proxmox-a (hp-elite-mini-01), homelab (VM 101, 192.168.70.223).
Executor: E15, following `runbook-homelab.md` with the lead's changes.

Result in one line: homelab now runs with 20 GiB, `balloon: 0`, `cpuunits: 1024`, `discard=on,cache=none` on its root disk and Docker's data-root on `/mnt/docker-data/docker`. All 42 containers came back, Gatus is green and the iGPU is back. The trim released 69 GiB from the thin pool (71.80 % to 51.35 %). Docker was socket-activated twice during the window; that is recorded as a deviation below.

## Go and waiver

- Igor gave the go at about 17:50 PDT on 2026-10-02, in the lead session, to restart homelab now.
- Igor waived the pre-check that T3 thread 32e69299-2938-4a2b-9880-c58f5ebd98bc had finished. At 19:18 the steward's last entry for it was `10-02 17:07:00 resume ... Review Steward Usage Feedback`, and a `claude ... stream-json` process (PID 3911965) was running on homelab. The VM shutdown ended that process.
- No other service-affecting step was running (the lead confirmed that the black-pearl and normandy windows were complete).

## Pre-checks (19:18)

```
Emby /emby/Sessions (key from Tokens_2 Id=34, not printed): sessions 7, playing 0
qm pending 101: new virtio0: local-lvm:vm-101-disk-0,cache=none,discard=on,iothread=1,size=192G
                cur virtio1: tank:vm-101-disk-0,backup=0,discard=on,iothread=1,size=300G
docker.service TimeoutStopUSec=1min 30s (DefaultTimeoutStopSec at the default of 90s)
42 containers running; / 115G used; swap 974 of 974 MB
lvs: data 337.86g 71.80, vm-101-disk-0 192.00g 99.96
```

The stop timeout was 90 s, so the containers were not stopped separately before Docker.

## Timeline

```
19:18:19  sudo systemctl stop docker.socket docker.service      (15.5 s; both inactive)
19:18:35  final rsync -aHAX -x --numeric-ids --delete           9 s, rc=0, 342 files, 198 deleted
          dry run with --itemize-changes                        0 lines
19:19     host_vars: docker_data_root and before_docker uncommented
          ansible-playbook playbooks/base.yml -l homelab --diff  failed in play 1 (InfluxDB on homelab is down while Docker is stopped)
          rerun with --start-at-task "Load the metric tokens"   changed=3: daemon.json, local-mounts.conf, daemon-reload
19:19:10  Docker socket-activated on the OLD root (deviation 1)
19:20     qm set 101 --memory 20480 --balloon 0 --cpuunits 1024
19:20:08  stop docker.socket docker.service again
          rsync 8 s, rc=0, 1219 files, 599 deleted
19:20:34  Docker socket-activated on the NEW root during the verifying dry run (deviation 1)
19:21:04  qm shutdown 101 --timeout 300                        70.2 s, stopped
19:22:15  qm pending 101: no new/del lines; qm start 101       rc=0, 3 s
19:22:31  SSH up (+16 s)
19:22:41  docker.service active on /mnt/docker-data/docker
19:23:31  42 containers running, vaultwarden health starting
```

homelab's services were down from 19:18:19 to about 19:23:30, about 5 minutes, apart from the two unplanned Docker starts. The pending changes were staged at 19:20, after the converge; the lead's order put them after it.

## Deviation 1: Docker started twice by a timer

The runbook assumed that stopping `docker.socket` and `docker.service` kept Docker down. It did not. Twice, `docker.socket` and `docker.service` were started by systemd while the copy was in progress:

- 19:19:10, during the converge, Docker started on `/var/lib/docker`, before the converge wrote `daemon.json`. It ran on the old root until 19:20:08. Its writes landed in the old root, and the second final rsync (19:20:08) copied them.
- 19:20:34, about 7 s after the second rsync ended (rc=0), Docker started on the new root, because `daemon.json` named it by then. The verifying dry run, still running, then listed 18 lines. These were only Docker's own container-start writes on the new root (`config.v2.json`, `hostconfig.json`, `hosts`, `resolv.conf` and buildkit metadata, all newer on the new root). The old root was not written after 19:20:08, so the new root started from a complete copy.

Cause: `authentik-watchdog.timer` (and, with `Restart=on-failure`, `forgejo-webhooks.service` and `jocasta-container.service`) activates units with `Requires=docker.service`. At 19:20:34 `authentik-watchdog.timer` fired and `docker.socket` started in the same second. Starting a unit that requires docker.service starts Docker. The 19:19:10 start coincided with the base role's `homelab-gpu` task. That task does not depend on Docker, so this start too is attributed to a dependent unit; the exact one was not proven.

Effect: none on data, because each start followed a completed copy, as the evidence above shows. The cost was two extra short container starts and stops during the outage.

Fix for later windows: also stop the timers and units that require Docker (`authentik-watchdog.timer`, `forgejo-webhooks.service`, `jocasta-container.service`, `jocasta-firewall.service`), or `systemctl mask --runtime docker.service docker.socket` for the copy, then unmask before the restart. The runbook should say so. Also, `base.yml -l homelab` cannot run while homelab's Docker is stopped, because its first play talks to InfluxDB on homelab; use `--start-at-task "Load the metric tokens"`.

## Post-checks

VM configuration (proxmox-a):

```
balloon: 0   cpuunits: 1024   memory: 20480
virtio0: local-lvm:vm-101-disk-0,cache=none,discard=on,iothread=1,size=192G
virtio1: tank:vm-101-disk-0,backup=0,discard=on,iothread=1,size=300G
qm pending 101: no new/del lines; cgroup cpu.weight 1024
qm start output: "vfio-pci,host=0000:00:02.0 ... OpRegion detected on Intel display 4690"
```

iGPU:

```
guest /dev/dri: by-path card0 card1 renderD128
vainfo: Intel iHD driver for Intel(R) Gen Graphics - 23.1.1; VAProfileH264High VLD and EncSlice
docker exec Emby ls /dev/dri: card0 card1 renderD128
```

Answer: the iGPU re-initialised on the first `qm start`.

Docker:

```
docker info: /mnt/docker-data/docker overlay2
RequiresMountsFor=/mnt/docker-data
loki:latest true
42 running; none exited, restarting or unhealthy (19:40)
```

Gatus (`/api/v1/endpoints/statuses` on homelab's internal instance, port 3002): 45 endpoints, none failing on its latest result. `https://uptime.ryzhkov.dev/` 200, `https://emby.ryzhkov.dev/` 302.

Trim (`sudo fstrim -av`, 19:23:40, took 16 min 0 s):

```
/mnt/docker-data: 275 GiB (295296933888 bytes) trimmed on /dev/vdb1
/: 73.9 GiB (79350407168 bytes) trimmed on /dev/vda1

                 before            after
data             71.80 % (242.6 GiB)  51.35 % (173.5 GiB)   of 337.86g
vm-101-disk-0    99.96 % (191.9 GiB)  63.97 % (122.8 GiB)   of 192.00g
```

Answer: discard now reaches the pool. 69.1 GiB was released, matching the plan's 243 to 170 GB. The old `/var/lib/docker` (about 20 GB) still holds blocks on the root LV. The trim was slow: about 4.6 GiB a minute through virtio-blk with a 2 GiB DISC-MAX. The weekly `fstrim.timer` runs are much smaller, so this is a one-time cost.

Memory at 19:40: 20000 MiB total, 9296 used, 10704 available, swap 0 of 974 used, `/proc/pressure/memory` all zero. Swap was full (974 of 974 MB) at 28 GB before the window, so it is a figure to watch for a week at 20 GB.

Repository:

```
ansible-playbook playbooks/base.yml --check --diff: changed=0 on blackpearl, homelab, normandy, proxmox-a, proxmox-b, public-api
ansible-playbook playbooks/site.yml --check --diff: changed=0 on blackpearl, homelab, normandy, public-api
```

`inventory/host_vars/homelab.yml` now sets `docker_data_root: /mnt/docker-data/docker` and `before_docker: true`.

## Rollback state

- The old `/var/lib/docker` is untouched since 19:20:08 and stays as the rollback copy. Delete it after a week of normal running, on Igor's decision. It frees about 20 GB in the guest, and the next weekly trim returns it to the pool.
- The original values for C in the runbook are `memory 28672`, no `balloon` line, no `cpuunits` line, and `cache=writethrough` without discard.

## Implications for later phases

- Phase 6 may count on homelab at 20 GiB, after the week of watching swap and memory pressure.
- Any later Docker data move, on any host, must stop or mask the units that `Requires=docker.service` (see deviation 1). Stopping `docker.socket` is not enough.
