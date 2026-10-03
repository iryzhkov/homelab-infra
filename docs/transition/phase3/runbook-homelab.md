# Phase 3 runbook: homelab (VM 101) stop and start

Prepared 2026-10-02 by E15. Evidence and survey: `2026-10-02-homelab-prep.md`.
Hosts: proxmox-a (hp-elite-mini-01, `ssh proxmox-a`), homelab (VM 101,
`ssh homelab`, 192.168.70.223). Run from the laptop in `~/Work/homelab-infra`.

What the window applies:

- the staged root-disk change: `discard=on`, and `cache` from writethrough to
  none. Igor confirmed both parts on 2026-10-02 (lead session); this is not an
  open decision.
- memory 28672 to 20480 MiB, ballooning off (`balloon: 0`), `cpuunits` raised;
- Docker's data-root moved from `/var/lib/docker` (on the thin LV) to
  `/mnt/docker-data/docker` (virtio1, a 300 GB zvol on `tank`, hot-added and
  pre-copied during preparation), with docker.service requiring the mount;
- `fstrim -av` afterwards, so the thin pool gets the root disk's free blocks back.

Not applied: `ssd=1`. The root disk is virtio-blk, which has no such option.

Containerd: Igor decided normandy moves both `/var/lib/docker` and
`/var/lib/containerd`. On homelab, Docker uses the overlay2 graph driver, not the
containerd image store, and `/var/lib/containerd` holds 2.7 MB, so there is
nothing there to move. Following the normandy decision literally would add a
containerd restart for no gain. This runbook leaves containerd's root in place;
Igor sees this at the go.

Service impact: every homelab service (42 containers, including Emby, Authentik,
Vaultwarden, Forgejo, Loki and InfluxDB), plus the T3 server and t3-steward
on homelab, from step 2.1 until step 4 completes.

Every step has an expected result. If the result differs, stop and go to the
rollback section named in the step.

## 0. Pre-checks (nothing changes)

0.1 The T3 thread has finished.

```
ssh homelab 'export PATH=$HOME/.local/bin:$PATH; t3-steward status | grep 32e69299 | head -3; pgrep -af "claude .*stream-json"'
```

Expected: no claude session process for thread
32e69299-2938-4a2b-9880-c58f5ebd98bc. At preparation (14:49) it was still running
as PID 3383329 ("Review Steward Usage Feedback"). Stop: if it is still running,
do not start the window; the VM shutdown would kill it.

0.2 Emby has no active playback. Load the Emby API key as in
`docs/transition/phase2/2026-10-02-fake-media-cleanup.md` (row `Id=34` of
`Tokens_2` in `~/app/emby/data/authentication.db`) into `EMBY_KEY` on homelab
without printing it, then:

```
curl -s "http://127.0.0.1:8096/emby/Sessions?api_key=$EMBY_KEY" \
  | python3 -c 'import json,sys; print(sum(1 for s in json.load(sys.stdin) if s.get("NowPlayingItem")))'
```

Expected: `0`. Stop: anything else; wait or ask Igor.

0.3 Pending changes match the survey.

```
ssh proxmox-a 'qm pending 101 | grep -E "^(new|del)|virtio1"'
```

Expected, exactly:

```
new virtio0: local-lvm:vm-101-disk-0,cache=none,discard=on,iothread=1,size=192G
cur virtio1: tank:vm-101-disk-0,backup=0,discard=on,iothread=1,size=300G
```

Stop: any other `new` or `del` line; find out who staged it.

0.4 Baselines, recorded in the window's evidence file.

```
ssh proxmox-a 'lvs -o lv_name,lv_size,data_percent pve/data pve/vm-101-disk-0'
ssh homelab 'docker ps -q | wc -l; docker info -f "{{.DockerRootDir}}"; findmnt -n /mnt/docker-data; df -h /; free -m'
```

Expected (preparation values): data 71.78 %, vm-101-disk-0 99.96 %; 42 running;
`/var/lib/docker`; `/dev/vdb1 ext4`; `/` 111G used; swap 974 of 974 MB used.

0.5 Igor's explicit go for the homelab window, with the time. No go, no window.

## 1. Stage the VM changes (proxmox-a)

```
ssh proxmox-a 'qm set 101 --memory 20480 --balloon 0 --cpuunits 1024; qm pending 101 | grep -E "^(new|del)"'
```

Expected:

```
new balloon: 0
new memory: 20480
new virtio0: local-lvm:vm-101-disk-0,cache=none,discard=on,iothread=1,size=192G
```

and `cpuunits: 1024` as a `cur` line.

Why: memory cannot change on a running VM without memory hot-plug, which 101
does not have (`hotplug` is the default `network,disk,usb`), and `balloon: 0`
removes the balloon device, which also needs a new QEMU process. Both therefore
stay pending until `qm shutdown` and `qm start`. `cpuunits` is different:
Proxmox writes it straight to the VM's cgroup `cpu.weight` (100 today, the
default for every guest on proxmox-a), so it takes effect at once and harms
nothing. 1024 gives homelab about ten times the weight of a default guest under
contention; the plan names no figure, so the lead may pick another.

Stop: if memory shows as applied (`cur memory: 20480`) on the running VM, run
`qm set 101 --memory 28672` and report; the guest would be squeezed live.

## 2. Move Docker's data (inside homelab)

2.1 Stop Docker and mask it for the copy.

Stopping `docker.socket` is not enough. The 2026-10-02 window found that
Docker came back twice while it was meant to be down
(`2026-10-02-homelab-window.md`, deviation 1), because `authentik-watchdog.timer`,
and `forgejo-webhooks.service` and `jocasta-container.service` with
`Restart=on-failure`, start units that have `Requires=docker.service`. A
runtime mask (a `/dev/null` link under `/run/systemd/system`, gone at the next
boot) makes every such start fail instead.

First check the stop timeout. homelab's is 90 s; if it is ever much shorter,
stop the containers first (`docker stop -t 30 $(docker ps -q)`), so dockerd is
not killed in the middle of their grace periods.

```
ssh homelab 'systemctl show docker -p TimeoutStopUSec'
ssh homelab 'date -Is; sudo systemctl stop docker.socket docker.service && sudo systemctl mask --runtime docker.service docker.socket; systemctl is-active docker.socket docker.service; systemctl is-enabled docker.service docker.socket; docker ps 2>&1 | head -1'
```

Expected: `TimeoutStopUSec=1min 30s`; then `inactive inactive`,
`masked-runtime masked-runtime`, and `docker ps` cannot connect. This is the
start of the outage. containerd keeps running; it holds no containers now.
Rollback: section A.

2.2 Final copy, and a dry run that must find nothing left to copy.

```
ssh homelab 'time sudo rsync -aHAX -x --numeric-ids --delete --stats /var/lib/docker/ /mnt/docker-data/docker/; echo rc=$?'
ssh homelab 'sudo rsync -aHAXn -x --numeric-ids --delete --itemize-changes /var/lib/docker/ /mnt/docker-data/docker/ | wc -l'
```

Expected: rc=0 in about 1 minute (the second warm pass took 61 s); the dry run
prints `0`. Stop: any non-zero rc or a non-zero count; rollback section A.

The old `/var/lib/docker` stays where it is, untouched, as the rollback copy.
Delete it only after a week of normal running and Igor's decision (about 20 GB
on the thin LV).

2.3 Switch the data-root and require the mount (laptop). In
`inventory/host_vars/homelab.yml`, uncomment `docker_data_root:
/mnt/docker-data/docker`, and in the `local_mounts` item uncomment
`before_docker: true`. Then:

```
ansible-playbook playbooks/base.yml -l homelab --diff
```

The plain command works with homelab's Docker stopped: the playbook's first play
(InfluxDB buckets and tokens) sees InfluxDB down, prints that it is skipped and
ends, and the base role still runs. `--start-at-task` is no longer needed.

The converge runs while Docker is still masked, on purpose. The role's
`docker info` then fails to connect, which the role tolerates (`failed_when:
false`, and the pending-restart report needs rc 0), so nothing starts Docker in
the middle of the converge. The drop-in is written to
`/etc/systemd/system/docker.service.d/`, which the runtime mask does not touch.

Expected: changed=3 or 4: `/etc/docker/daemon.json` gains `"data-root":
"/mnt/docker-data/docker"` (the file is also reformatted as sorted, two-space
JSON; the other keys are unchanged), `docker.service.d/local-mounts.conf` with
`RequiresMountsFor=/mnt/docker-data`, and a daemon-reload. No "restart
pending" message, because `docker info` cannot reach the stopped daemon.

Then unmask, so the VM restart can start Docker. From here a timer may start
Docker early, which is harmless now: daemon.json names the new root and the copy
is complete.

```
ssh homelab 'sudo systemctl unmask --runtime docker.service docker.socket; systemctl is-enabled docker.service docker.socket; systemctl show docker -p RequiresMountsFor; sudo grep data-root /etc/docker/daemon.json'
```

Expected: `enabled enabled`, `RequiresMountsFor=/mnt/docker-data`, the
data-root line. Stop: if `RequiresMountsFor` is empty, run `sudo systemctl
daemon-reload` and check again before step 3.

Docker is not started here; the VM restart in step 3 starts it on the new root.

## 3. Stop and start the VM (proxmox-a)

```
ssh proxmox-a 'date -Is; time qm shutdown 101 --timeout 300; qm status 101'
```

Expected: `status: stopped`, in well under 300 s (public-api took 14 s; homelab
has no Docker left to stop). Stop: on timeout, look at the console
(`qm terminal` or the web UI) before anything else; `qm stop 101` only with
Igor's agreement.

```
ssh proxmox-a 'qm pending 101 | grep -E "^(new|del)"; qm start 101; date -Is'
```

Expected: no `new` or `del` line before the start (all changes are now
configuration), then the start returns in a few seconds. Stop: if `qm start`
fails with a vfio or PCI error for `hostpci0`, go to section D.

## 4. Post-checks

4.1 VM configuration.

```
ssh proxmox-a 'qm config 101 | grep -E "^(memory|balloon|cpuunits|virtio)"; qm pending 101 | grep -E "^(new|del)"; cat /sys/fs/cgroup/qemu.slice/101.scope/cpu.weight'
```

Expected: `memory: 20480`, `balloon: 0`, `cpuunits: 1024`,
`virtio0: ...cache=none,discard=on,iothread=1,size=192G`, virtio1 unchanged;
no pending lines; weight 1024.

4.2 The iGPU came back (Emby transcodes on it).

```
ssh homelab 'ls /dev/dri; sudo vainfo --display drm --device /dev/dri/renderD128 2>&1 | grep -E "Driver version|VAProfileH264|VAProfileHEVC" | head -4; docker exec Emby ls /dev/dri'
```

Expected: `by-path card0 card1 renderD128` in the guest and in the `Emby`
container, and vainfo listing the iHD driver with H.264 and HEVC profiles.
Stop: missing devices; section D.

4.3 Docker on the new root.

```
ssh homelab 'docker info -f "{{.DockerRootDir}} {{.Driver}}"; systemctl show docker -p RequiresMountsFor; docker plugin ls; docker ps -q | wc -l; docker ps -a --filter status=exited --filter status=restarting --format "{{.Names}} {{.Status}}"'
```

Expected: `/mnt/docker-data/docker overlay2`, `RequiresMountsFor=/mnt/docker-data`,
the loki plugin enabled, 42 running, nothing exited or restarting (give it
2 minutes after boot). Stop: data-root still `/var/lib/docker`, or containers
missing; section B.

4.4 Gatus is green.

```
ssh homelab 'curl -s http://192.168.70.223:3002/api/v1/endpoints/statuses | python3 -c "import json,sys; d=json.load(sys.stdin); bad=[e[\"key\"] for e in d if e.get(\"results\") and not e[\"results\"][-1][\"success\"]]; print(len(d), bad)"'
```

Expected: every endpoint's latest result succeeded (`[]`) after one or two check
intervals; also `https://uptime.ryzhkov.dev/` 200. Stop: a homelab service
stays red; investigate it before rollback.

4.5 Trim and the thin pool.

```
ssh proxmox-a 'lvs -o lv_name,lv_size,data_percent pve/data pve/vm-101-disk-0'
ssh homelab 'sudo fstrim -av'
sleep 10; ssh proxmox-a 'lvs -o lv_name,lv_size,data_percent pve/data pve/vm-101-disk-0'
```

Expected: `/` reports about 75 to 80 GiB trimmed (191 GiB partition, 111 GiB
used). vm-101-disk-0 falls from 99.96 % (192 GiB) to about 60 % (about
115 GiB), and `data` from about 243 GiB (71.8 % of 337.86 GiB) to about
165 GiB (about 49 %), in line with the plan's 243 to 170 GB. The old
`/var/lib/docker` (20 GB) still occupies blocks until it is deleted. Stop: if the
LV does not fall, discard is not reaching the pool; find out why before anything
else depends on it (the plan's instruction).

4.6 Memory. homelab's working set was about 12 GB of 28 GB, and its 974 MB swap
was already full at 28 GB, so swap is not a sign of pressure on its own; watch
that it does not start thrashing at 20 GB. The 20 GB figure holds against the
measured 11 GB working set.

```
ssh homelab 'free -m; cat /proc/pressure/memory'
```

Expected: available above 5 GB; `some avg300` near zero. Check again daily for a
week; if swap-in activity or memory pressure grows, the figure goes back up
(section C).

4.7 Repository convergence.

```
ansible-playbook playbooks/base.yml --check --diff
ansible-playbook playbooks/site.yml --check --diff
```

Expected: changed=0 on homelab (and every other host). The lead commits the
host_vars change from step 2.3.

## Rollback

A. Before the VM restart (Docker stopped, step 2.1 to 2.3). The old root is intact.
If Docker is not masked yet (the window was after 2.3), mask it first:
`sudo systemctl mask --runtime docker.service docker.socket`. In host_vars set
`docker_data_root: /var/lib/docker` and comment `before_docker` again, then
`ansible-playbook playbooks/base.yml -l homelab --diff` (the InfluxDB play
skips itself while Docker is down; daemon.json now names the old root; the
drop-in file stays but only requires a mount that exists), then
`ssh homelab 'sudo systemctl unmask --runtime docker.service docker.socket && sudo systemctl start docker.socket docker.service'`.
Expected: 42 containers running, `docker info` root `/var/lib/docker`. The VM
can still be restarted for the memory and disk changes, or those reverted as in C.

B. After the restart, Docker misbehaves on the new root. Containers have written
to their volumes on the new root since the start, so copy back first:
`sudo systemctl stop docker.socket docker.service && sudo systemctl mask --runtime docker.service docker.socket`,
then
`sudo rsync -aHAX -x --numeric-ids --delete /mnt/docker-data/docker/ /var/lib/docker/`,
then as in A (set `/var/lib/docker`, converge with the plain
`ansible-playbook playbooks/base.yml -l homelab --diff`, unmask, start). If the
new root itself is suspect, skip the copy-back and accept losing the writes
since the window.

C. Memory back to 28 GB:
`ssh proxmox-a 'qm set 101 --memory 28672 --delete balloon'` (the original
configuration had no balloon line), pending until the next stop and start, which
needs another go from Igor. `cpuunits` back to the default:
`qm set 101 --delete cpuunits` (applies live). Disk cache back, if ever needed:
`qm set 101 --virtio0 local-lvm:vm-101-disk-0,cache=writethrough,discard=on,iothread=1,size=192G`
(pending).

D. The iGPU does not come back. Emby falls back to software transcoding, which
works but loads the CPU; everything else is unaffected. One more
`qm shutdown 101` and `qm start 101`. If still missing, read
`lspci -nnk -s 00:02.0` and `dmesg | grep -i -E "vfio|i915|0000:00:02"` on
proxmox-a. A reboot of proxmox-a resets the device but stops every guest on it;
only with Igor's go.

## Estimated duration

Floor from phase 0: public-api's stop and start took 32 s from shutdown to HTTPS
200 (shutdown 14 s, boot to HTTPS 16 s). homelab adds:

| Step | Estimate |
|---|---|
| 0 pre-checks | 5 min, before the outage |
| 1 stage VM changes | under 1 min, before the outage |
| 2.1 stop Docker (42 containers) | 0.5 to 1 min |
| 2.2 final rsync and dry run | about 2 min (61 s measured for a warm pass) |
| 2.3 converge | 1 to 2 min |
| 3 shutdown and start | about 1 min |
| boot to all 42 containers healthy | 2 to 3 min |

Service outage: about 7 to 10 minutes. Whole window with post-checks and trim:
about 25 minutes.
