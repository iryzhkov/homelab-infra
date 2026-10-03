# Phase 3 runbook: normandy (VM 202) stop and start

Prepared 2026-10-02 by E16. Evidence and survey: `2026-10-02-normandy-prep.md`.
Hosts: proxmox-b (hp-workstation-01, `ssh proxmox-b`), normandy (VM 202,
`ssh normandy`, 192.168.70.234). Run from the laptop in `~/Work/homelab-infra`.

What the window applies:

- memory 24576 to 20480 MiB (ballooning is already off, `balloon: 0`);
- `cpuunits` 1024 (the default of 100 today, as on every guest), matching homelab;
- Docker's data-root from `/var/lib/docker` to `/mnt/data/docker`, and
  containerd's root from `/var/lib/containerd` to `/mnt/data/containerd`
  (Igor's decision, 2026-10-02 17:05). Docker uses the containerd image store,
  so the images and container layers (17 GB) live under containerd's root and
  only the volumes (7.7 GB, mostly Immich's model cache) under Docker's.
  `/mnt/data` is the 400 GB zvol on `tank` from phase 2. Both directories were
  pre-copied warm during preparation.

Not applied, because it is already true: the plan's "Frigate record path and
Immich upload location onto `/mnt/data`". Frigate recording is disabled and its
media is already on `/mnt/data`; Immich's upload location (with thumbs and
encoded video) is on NFS, `/mnt/shared/immich`; Immich's Postgres is already on
`/mnt/data`. No Compose file changes. `fstrim.timer` is already enabled and is
now kept by the role (`fstrim_timer_enabled: true`, converged with no change);
nothing to do for it in the window.

Service impact, from step 2.1 until step 6 completes: Frigate (no recording is
lost because recording is off, but object detection, events and snapshots
stop, and Home Assistant's Frigate integration goes unavailable), Immich
(`photos.ryzhkov.dev`), normandy's Gatus, and normandy's cloudflared connector
(homelab's connector keeps the tunnel up). From step 5 also the non-Docker
services on normandy: T3 Code, t3-steward (worker and coordinator), Huyang and
the steward's Gatus health service. Run the window at night.

Every step has an expected result. If the result differs, stop and go to the
rollback section named in the step.

## 0. Pre-checks (nothing changes)

0.1 Igor's explicit go for the normandy window, with the time, at night. No go,
no window. Igor also answers 0.6.

0.2 No agent work on normandy is mid-run. The VM restart kills the T3 server
and the steward worker on it.

```
ssh normandy 'export PATH=$HOME/.local/bin:$PATH; t3-steward status 2>/dev/null | head -20; pgrep -af "claude .*stream-json|codex" | head'
```

Expected: no running task on the normandy worker and no agent session process.
Stop: wait, or ask Igor.

0.3 Pending changes are empty and the VM is as surveyed.

```
ssh proxmox-b 'qm pending 202 | grep -E "^(new|del)"; qm config 202 | grep -E "^(memory|balloon|cpuunits|hostpci0|scsi1|tpmstate0)"; free -m | grep Mem'
```

Expected: no `new` or `del` line; `memory: 24576`, `balloon: 0`, no `cpuunits`
line, `hostpci0: 0000:00:02.0,pcie=1`,
`scsi1: tank:vm-202-disk-0,backup=0,discard=on,iothread=1,size=400G,ssd=1`,
`tpmstate0: local-lvm:vm-202-disk-2,...`. proxmox-b available memory about
3.4 GB (3412 MB at preparation). Stop: any pending line; find out who staged it.

0.4 Baselines, recorded in the window's evidence file.

```
ssh normandy 'docker ps --format "{{.Names}} {{.Status}}"; docker info -f "{{.DockerRootDir}}"; findmnt -n /mnt/data; df -h / /mnt/data; free -m; ls /etc/containerd 2>&1; systemctl show docker -p ActiveEnterTimestamp'
ssh normandy 'curl -s http://192.168.70.234:5000/api/stats | python3 -c "import json,sys;d=json.load(sys.stdin);print(d[\"detectors\"]);print({k:(v[\"camera_fps\"],v[\"detection_fps\"]) for k,v in d[\"cameras\"].items()})"'
```

Expected (preparation values): 7 running (gatus, cloudflared, frigate,
immich_server, immich_machine_learning, immich_postgres, immich_redis);
`/var/lib/docker`; `/dev/sdb1 ext4`; `/` 52G used of 98G, `/mnt/data` about
27G used (the warm copies); no `/etc/containerd`. Frigate: detector `ov`
inference about 8 ms; cameras `garage_outside` and `doorbell` at about 3 fps,
`garage_inside` and `back_yard` at 0 (disabled; the plan's "nine cameras" does
not match Frigate's four configured, two active).

0.5 A last warm pass, so the final copy in the window is short.

```
ssh normandy 'for d in docker containerd; do sudo rsync -aHAX --numeric-ids -x --delete /var/lib/$d/ /mnt/data/$d/; echo $d rc=$?; done; df -h /mnt/data | tail -1'
```

Expected: rc=0 twice; `/mnt/data` under 80 % (about 8 % at preparation).

0.6 Igor's choice on `no-block-boot.conf`: keep it (nothing to do) or remove
it in step 3.3. Its only content is `[Unit]` `DefaultDependencies=no`, written
by hand on 2026-09-15 with no record of why (searched in preparation). It
removes docker.service's ordering after `basic.target` and its ordered stop at
shutdown; it does not make anything non-blocking. The preparation recommends
removing it, because the managed `local-mounts.conf` is now the ordering Docker
needs and this boot is supervised anyway.

## 1. (No VM change before the outage)

The VM changes are staged in step 4, after Docker is stopped, because
`cpuunits` takes effect at once on a running VM.

## 2. Stop Docker and containerd, final copy (inside normandy)

2.1 Stop the socket first, so nothing (including the role's `docker info` in
step 3) can socket-activate Docker on the old root. containerd runs as its own
service (`containerd.service`, pulled in by docker.service; `disabled` as a
unit file) and is stopped after Docker.

```
ssh normandy 'date -Is; sudo systemctl stop docker.socket docker.service; sudo systemctl stop containerd.service; systemctl is-active docker.socket docker.service containerd.service; pgrep -a containerd-shim | head -3'
```

Expected: `inactive inactive inactive` and no shim process. Frigate has a 30 s
stop grace period, so the stop takes up to about 40 s. This is the start of the
outage. Rollback: section A.

2.2 Final copy of both roots, then a dry run that must find nothing left.

```
ssh normandy 'for d in docker containerd; do time sudo rsync -aHAX --numeric-ids -x --delete --stats /var/lib/$d/ /mnt/data/$d/ | grep -E "Number of files:|regular files transferred"; echo $d rc=${PIPESTATUS[0]}; done'
ssh normandy 'for d in docker containerd; do sudo rsync -aHAXn --numeric-ids -x --delete --itemize-changes /var/lib/$d/ /mnt/data/$d/ | wc -l; done'
```

Expected: rc=0 for both, in well under a minute (the warm second passes took
0.3 s for docker and 6.3 s for containerd); the dry runs print `0` and `0`.
`-x` is required: without it rsync descends into container overlay mounts
(none should exist with Docker stopped, but the flag keeps that true).
Stop: any non-zero rc or count; rollback section A.

The old `/var/lib/docker` and `/var/lib/containerd` stay where they are,
untouched, as the rollback copies (about 25 GB on the root disk, which has 44 GB
free). Delete them only after a week of normal running and Igor's decision.

## 3. Switch the roots (laptop)

3.1 In `inventory/host_vars/normandy.yml`, add:

```yaml
# Docker's and containerd's roots on the data disk since the phase 3 window.
docker_data_root: /mnt/data/docker
containerd_root: /mnt/data/containerd
```

3.2 Converge.

```
ansible-playbook playbooks/base.yml -l normandy --diff
```

Expected: changed=6 or 7: `/etc/docker/daemon.json` gains
`"data-root": "/mnt/data/docker"` (the file is also rewritten as sorted,
two-space JSON, with the other keys unchanged); `/etc/containerd` is created
with `config.toml` holding `version = 4` and `root = "/mnt/data/containerd"`;
`containerd.service.d/containerd-root.conf` with
`RequiresMountsFor=/mnt/data/containerd`, and a daemon-reload. The "restart
pending" messages may be absent because neither daemon is running. Both must
still be stopped afterwards:

```
ssh normandy 'systemctl is-active docker.socket docker.service containerd.service; systemctl show containerd -p RequiresMountsFor; sudo grep data-root /etc/docker/daemon.json; containerd config dump | grep -E "^root"'
```

Expected: `inactive inactive inactive`, `RequiresMountsFor=/mnt/data/containerd`,
the data-root line, `root = '/mnt/data/containerd'`. Stop: if either daemon is
active, it started on whichever root its configuration named at that moment;
go to section A.

3.3 Only if Igor chose removal in 0.6:

```
ssh normandy 'sudo mv /etc/systemd/system/docker.service.d/no-block-boot.conf /root/no-block-boot.conf.removed-phase3; sudo systemctl daemon-reload; systemctl show docker -p DropInPaths'
```

Expected: `DropInPaths=/etc/systemd/system/docker.service.d/local-mounts.conf`
only. The file is kept in `/root` for rollback.

Neither daemon is started here: the VM restart in step 5 starts them on the new
roots.

## 4. Stage the VM changes (proxmox-b)

```
ssh proxmox-b 'qm set 202 --memory 20480 --cpuunits 1024; qm pending 202 | grep -E "^(new|del)|cpuunits"; cat /sys/fs/cgroup/qemu.slice/202.scope/cpu.weight'
```

Expected: `new memory: 20480` and `cur cpuunits: 1024` (or the `cpuunits` line
already current), weight `1024`.

Why: memory cannot change on a running VM without memory hot-plug, which 202
does not have, so it stays pending until `qm shutdown` and `qm start`.
`cpuunits` is written straight to the VM's cgroup `cpu.weight` and takes effect
at once; 1024 against the default 100 gives normandy about ten times the weight
of a default guest (CT 201, the future agent-b) under contention, the same
figure as homelab.

Stop: if memory shows as applied on the running VM (`cur memory: 20480`), run
`qm set 202 --memory 24576` and report.

## 5. Stop and start the VM (proxmox-b)

```
ssh proxmox-b 'date -Is; time qm shutdown 202 --timeout 300; qm status 202'
```

Expected: `status: stopped` well under 300 s (Docker is already down). At the
last shutdown (2026-10-01 20:49) the user manager logged an ordering cycle
between `t3-steward-worker.service`, `huyang.service` and `default.target`
and broke it by itself; that is harmless but may add seconds. Stop: on timeout,
look at the console (`qm terminal 202` or the web UI) before anything else;
`qm stop 202` only with Igor's agreement.

```
ssh proxmox-b 'qm pending 202 | grep -E "^(new|del)"; qm start 202; date -Is'
```

Expected: no `new` or `del` line before the start, then the start returns in a
few seconds. Stop: if `qm start` fails with a vfio or PCI error for
`hostpci0`, go to section D.

The root is LUKS2, unlocked in the initramfs by the vTPM
(`rd.luks.options=tpm2-device=auto`). Memory and `cpuunits` do not change the
PCR values it is sealed to, so it should unlock unattended. If the guest does
not answer SSH within 3 minutes, open the console: a passphrase prompt means the
TPM unlock failed (section E).

## 6. Post-checks

6.1 VM configuration and proxmox-b memory.

```
ssh proxmox-b 'qm config 202 | grep -E "^(memory|balloon|cpuunits|hostpci0|scsi)"; qm pending 202 | grep -E "^(new|del)"; cat /sys/fs/cgroup/qemu.slice/202.scope/cpu.weight; free -m | grep Mem'
```

Expected: `memory: 20480`, `balloon: 0`, `cpuunits: 1024`, hostpci0 and both
disks unchanged; no pending lines; weight 1024. proxmox-b available memory about
7.4 GB (3.4 GB before, plus the 4 GB returned).

6.2 Boot: LUKS unlocked by the vTPM, `/mnt/data` mounted before the daemons.

```
ssh normandy 'journalctl -b -u systemd-cryptsetup@root -u cryptsetup.target --no-pager | tail -3; journalctl -b --no-pager | grep -iE "tpm2|passphrase" | head -3; findmnt -n /mnt/data; systemctl show mnt-data.mount containerd.service docker.service -p ActiveEnterTimestampMonotonic -p Id'
```

Expected: the root unlocked without a passphrase (TPM2 token); `/mnt/data` on
`/dev/sdb1 ext4`; `mnt-data.mount`'s monotonic timestamp lower than
containerd's and Docker's. Stop: a daemon started before the mount; section B.

6.3 Daemons on the new roots.

```
ssh normandy 'docker info -f "{{.DockerRootDir}} {{.Driver}}"; docker info | grep driver-type; sudo find /proc/$(systemctl show -p MainPID --value containerd)/fd -lname "*meta.db" -printf "%l\n"; docker plugin ls; docker images | wc -l; docker ps --format "{{.Names}} {{.Status}}"'
```

Expected: `/mnt/data/docker overlayfs`, `io.containerd.snapshotter.v1`,
`/mnt/data/containerd/io.containerd.metadata.v1.bolt/meta.db`, the loki plugin
enabled, the same images as before (no re-pull), and all 7 containers up,
healthy within about 3 minutes (Immich ML is the slowest). Stop: either root
still under `/var/lib`, an empty image list, or containers missing; section B.

6.4 GPU: `/dev/dri`, Frigate's OpenVINO detector, Immich ML.

```
ssh normandy 'ls /dev/dri; docker exec frigate ls /dev/dri; curl -s http://192.168.70.234:5000/api/stats | python3 -c "import json,sys;d=json.load(sys.stdin);print(d[\"detectors\"]);print({k:(v[\"camera_fps\"],v[\"detection_fps\"]) for k,v in d[\"cameras\"].items()})"; docker exec immich_machine_learning python -c "import onnxruntime as o; print(o.get_available_providers())"'
```

Expected: `card0 card1 igpu-render renderD128 renderD129 by-path` in the guest
and the Frigate container; detector `ov` with inference speed near the 8 ms
baseline (a CPU fallback would be far slower or absent); `garage_outside` and
`doorbell` at about 3 fps with detection running; Immich ML providers
`['OpenVINOExecutionProvider', 'CPUExecutionProvider']`. Stop: missing devices
or no `ov` detector; section D.

6.5 Gatus and public routes.

```
ssh normandy 'curl -s http://192.168.70.234:3002/api/v1/endpoints/statuses | python3 -c "import json,sys; d=json.load(sys.stdin); print(len(d), [e[\"key\"] for e in d if e.get(\"results\") and not e[\"results\"][-1][\"success\"]])"'
curl -s -o /dev/null -w "%{http_code}\n" https://photos.ryzhkov.dev/api/server/ping
curl -s -o /dev/null -w "%{http_code}\n" https://cctv.ryzhkov.dev/
```

Expected: 50 endpoints with an empty failure list (as at preparation) after one
or two check intervals; homelab's Gatus green too; both routes 200. Then ask
Igor to walk past a camera, or wait for an event, and confirm a new event in
Frigate (`/api/events?limit=1`). Stop: a service stays red; investigate before
rollback.

6.6 Memory inside the guest. normandy's working set was about 8.5 GB of 24 GB.

```
ssh normandy 'free -m; cat /proc/pressure/memory; zramctl'
```

Expected: available above 8 GB once Immich ML has loaded its models; `some
avg300` near zero; zram swap little used. Check daily for a week (section C if
pressure grows).

6.7 Root disk space and writes. `/` still holds the old roots (about 25 GB)
until they are deleted. Root-disk writes do not change: they come from
t3-steward and T3 Code SQLite outside Docker (about 209 GB/day measured in
preparation).

6.8 Repository convergence.

```
ansible-playbook playbooks/base.yml --check --diff
ansible-playbook playbooks/site.yml --check --diff
```

Expected: changed=0 on normandy, and no "restart pending" message for either
root. The lead commits the host_vars change from step 3.1.

## Rollback

A. Before the VM restart (daemons stopped, steps 2.1 to 4). The old roots are
intact. In host_vars set `docker_data_root: /var/lib/docker` and
`containerd_root: /var/lib/containerd`, converge `base.yml -l normandy`
(daemon.json and config.toml now name the old roots; the containerd drop-in
requires a mount that exists), then
`ssh normandy 'sudo systemctl start containerd.service docker.socket docker.service'`.
If step 3.3 ran, move the file back from `/root` and daemon-reload first.
Expected: 7 containers running, roots under `/var/lib`. If step 4 ran, either
continue with the restart for memory only or revert as in C.

B. After the restart, Docker or containerd misbehaves on the new roots.
Containers have written to volumes and layers on the new roots since the start,
so copy back first: stop `docker.socket docker.service containerd.service`,
then `sudo rsync -aHAX --numeric-ids -x --delete /mnt/data/<d>/ /var/lib/<d>/`
for `docker` and `containerd`, then as in A (set both old roots, converge,
start). If the new copy itself is suspect, skip the copy-back and accept losing
the writes since the window (Immich's model cache, Redis, Gatus history; the
Postgres data is on `/mnt/data/immich/postgres`, not in either root).

C. Memory back to 24 GB: `ssh proxmox-b 'qm set 202 --memory 24576'`, pending
until the next stop and start, which needs another go from Igor. `cpuunits`
back to the default: `qm set 202 --delete cpuunits` (applies live).

D. The iGPU does not come back. Frigate falls back to no detection (its
detector is pinned to `GPU`) and Immich ML to CPU; Immich itself works. One more
`qm shutdown 202` and `qm start 202`. If still missing, read
`lspci -nnk -s 00:02.0` and `dmesg | grep -i -E "vfio|i915|0000:00:02"` on
proxmox-b. A reboot of proxmox-b resets the device but stops CT 201 (an AdGuard
replica) too; only with Igor's go.

E. The vTPM does not unlock the root. Enter the LUKS passphrase at the console
(Igor holds it; it is not in the repository). Once booted, re-enroll with
`systemd-cryptenroll --wipe-slot=tpm2 --tpm2-device=auto /dev/sda2` and record
why the PCRs moved.

## Estimated duration

Floor from phase 0: public-api's stop and start took 32 s from shutdown to HTTPS
200. normandy adds:

| Step | Estimate |
|---|---|
| 0 pre-checks and last warm pass | 5 min, before the outage |
| 2.1 stop Docker and containerd | under 1 min (Frigate's 30 s grace) |
| 2.2 final rsync and dry runs | about 1 min (warm passes 0.3 s and 6.3 s) |
| 3 converge (and 3.3) | 1 to 2 min |
| 4 stage VM changes | under 1 min |
| 5 shutdown and start, vTPM unlock | 1 to 2 min |
| boot to all 7 containers healthy | 2 to 3 min |

Service outage: about 7 to 10 minutes. Whole window with post-checks: about
25 minutes, plus the wait for a Frigate event.

proxmox-b memory: about 3.4 GB available before (with 606 MB of host swap in
use), about 7.4 GB after.
