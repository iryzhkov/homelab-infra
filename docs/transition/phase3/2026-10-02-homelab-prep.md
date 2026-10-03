# Phase 3 preparation: homelab (VM 101)

Date: 2026-10-02, 14:49 to 14:58 PDT.
Hosts: proxmox-a (hp-elite-mini-01), homelab (VM 101, 192.168.70.223).
Executor: E15. Preparation only: no VM stop or start, no Docker restart.

## Survey on proxmox-a

Command: `qm config 101`, `qm pending 101` (14:49:22)

```
boot: order=virtio0;ide2;net0   cores: 4   cpu: host   numa: 0   onboot: 1
hostpci0: 0000:00:02
memory: 28672        (no balloon line, so ballooning is on with target = memory)
(no cpuunits line)   (no hotplug line, so the default network,disk,usb)
scsihw: virtio-scsi-single (no SCSI disk)
net0: virtio=BC:24:11:AC:1B:D0,bridge=vmbr1,firewall=1,tag=70
cur virtio0: local-lvm:vm-101-disk-0,cache=writethrough,iothread=1,size=192G
new virtio0: local-lvm:vm-101-disk-0,cache=none,discard=on,iothread=1,size=192G
```

Answer: exactly one pending change, on the root disk: `discard=on` and `cache` from writethrough to none, as phase 0 recorded (101.conf mtime 2026-10-02 02:44:15 PDT). Igor confirmed both parts on 2026-10-02 in the lead session.

Mismatch with the plan: the plan asks for `discard=on,ssd=1` on virtio0, but virtio0 is virtio-blk, which has no `ssd` option. `ssd=1` is not staged and is not applicable without moving the disk to SCSI. Recorded, not changed.

Command: `qm status 101 --verbose`

```
balloon: 30064771072   maxmem: 30064771072   mem: 28155543552
running-machine: pc-i440fx-11.0+pve0   uptime: 2733852
```

Command: `cat /sys/fs/cgroup/qemu.slice/*.scope/cpu.weight`

```
101 100   102 100   103 100   106 100
```

Answer: no guest on proxmox-a sets `cpuunits`; all run at the cgroup v2 default weight 100.

Command: `lvs -o lv_name,lv_size,data_percent pve/vm-101-disk-0 pve/data`

```
data           337.86g 71.78
vm-101-disk-0  192.00g 99.96
```

`tank` before the hot-add: 624K used, 1.58T available, ONLINE.

## Survey inside homelab

Debian 12, kernel 6.1.0-52-amd64.

Command: `docker info` and `sudo du -sx /var/lib/docker`

```
root=/var/lib/docker driver=overlay2 version=29.7.2 containers=42 running=42 cgroup=systemd
/var/lib/docker: 20G (20566388736 bytes), 513268 entries
plugin: loki:latest (c08a170c5aec), enabled
```

Answer: Docker's data is about 20 GB, not the plan's 30 GB. The Loki log driver is a managed plugin stored under the data-root, so it moves with it.

Command: `df -hT`, `free -m`, `/proc/swaps`

```
/dev/vda1  ext4  187G  111G  67G  63%  /
Mem: total 28064  used 12193  free 2174  buff/cache 14729  available 15870
Swap: 974 total, 974 used (vda5, full)
```

Answer: swap is full at 28 GB; watch it after the shrink to 20 GB, as the plan says.

Command: `systemctl is-enabled fstrim.timer`, `systemctl list-timers fstrim.timer`

```
enabled, active; last Mon 2026-09-28 00:21:25, next Mon 2026-10-05 01:24:57
```

Command: `cat /etc/docker/daemon.json`, `ls /etc/systemd/system/docker.service.d`

```
{"default-address-pools": [{"base": "172.16.0.0/12", "size": 24}],
 "log-driver": "loki",
 "log-opts": {"loki-url": "http://localhost:3100/loki/api/v1/push", "loki-batch-size": "400",
              "loki-retries": "3", "max-size": "50m", "max-file": "3"}}
(272 bytes, 2026-03-18; no docker.service.d directory; DropInPaths empty)
docker.service ActiveEnterTimestamp=Wed 2026-09-09 16:41:42 PDT
```

Command: `dpkg -l | grep docker`, `ls /etc/apt/sources.list.d`

```
docker-ce 5:29.7.2-1~debian.12~bookworm, containerd.io 2.3.4-1, compose plugin 5.5.0-1; docker.list
```

Answer: Docker was installed by hand from Docker's apt repository, and `daemon.json` is hand-written; the repository manages neither (no match for `daemon.json` or `data-root` in roles).

## T3 thread 32e69299-2938-4a2b-9880-c58f5ebd98bc

- `pgrep -af 't3|steward|claude'` on homelab: `t3 serve` (port 7391), `t3-steward run`, `worker serve`, `worker bridge`, and one `claude ... --model claude-opus-5-5[1m]` process, PID 3383329, started 11:34:01.
- `t3-steward status` on homelab, 14:49:20: `warn claudeAgent/claude/five_hour 32e69299-... Review Steward Usage Feedback ... at 85%`; the steward attributes a running thread to it.
- `feed recent --since 1d --grep 32e69299`: nothing.

Answer: the thread ("Review Steward Usage Feedback") was still running at 14:49. Nothing was stopped.

## Base role capability (repository)

- `roles/base/defaults/main.yml`: `fstrim_timer_enabled: false`, `docker_data_root: ""`, documented.
- `roles/base/tasks/main.yml`: enables and starts `fstrim.timer` when `fstrim_timer_enabled`; includes `docker_data_root.yml` when `docker_data_root` is set.
- `roles/base/tasks/docker_data_root.yml`: merges `data-root` into the existing `daemon.json`, writes only when the parsed content differs, never restarts or reloads Docker, and reports "restart pending" while `docker info` shows a different root. No package install.
- Check-mode test on homelab with `-e docker_data_root=/mnt/docker-data/docker`: only the `daemon.json` diff (the file would be reformatted as sorted 2-space JSON), the pending-restart message, nothing applied. The lead passed the capability to E16.
- `inventory/host_vars/homelab.yml`: `fstrim_timer_enabled: true`; `local_mounts` for `/mnt/docker-data` without `before_docker`; `docker_data_root` present only as a comment for the window.

## Hot preparation

Command (proxmox-a, 14:51:03): `qm set 101 --virtio1 tank:300,discard=on,iothread=1,backup=0`

```
virtio1: successfully created disk 'tank:vm-101-disk-0,backup=0,discard=on,iothread=1,size=300G'
qm pending 101: cur virtio1: ... (applied); the only new line is still virtio0
zfs: tank/vm-101-disk-0 volsize 322122547200, refreservation 0 (sparse)
guest: vdb 300G appeared, no reboot
```

virtio1, not scsi1, to match the root disk's bus as briefed; therefore no `ssd=1`. Note: the zvol is named `vm-101-disk-0` on `tank`, the same name as the root LV on `local-lvm`; storage IDs tell them apart.

Inside homelab: `sfdisk` GPT with one Linux partition, `mkfs.ext4 -L docker-data /dev/vdb1`, UUID `d8d9a50f-7121-48d8-8ad3-ee3f3f5c3caa`.

Command: `ansible-playbook playbooks/base.yml -l homelab --diff` (14:51): changed=2 (fstab line, mount). `findmnt /mnt/docker-data` shows `/dev/vdb1 ext4`. docker.service unchanged (ActiveEnterTimestamp 2026-09-09 16:41:42, no drop-in). `fstrim.timer` was already enabled, so that task reported ok.

Warm copy, Docker running:

```
sudo rsync -aHAX -x --numeric-ids --delete --stats /var/lib/docker/ /mnt/docker-data/docker/
pass 1 (nice/ionice idle) 14:51:36-14:56:10: 274 s, rc=0, 513268 entries, 400345 files, 19.38 GB sent
pass 2 14:56:54: 61 s, rc=0, 26 files, 324 MB transferred, 0 deleted
```

`-x` keeps rsync out of the running containers' overlay `merged` mounts. Answer: a final pass with Docker stopped should take about 1 minute, mostly the scan of 513k entries. The zvol used 13.3 GB after pass 2 (lz4).

## Checkpoint (paused for quota)

Paused at about 14:58 PDT on the lead's quota-drain instruction. No host change after that.

Done:
- Survey above.
- Base role capability written and check-tested; lead informed.
- virtio1 (300 GB, `tank`) hot-added and applied, not pending.
- `/mnt/docker-data` formatted and mounted by UUID through the base role; no `before_docker` drop-in.
- `fstrim.timer`: enabled and active (it was already; now declared in host_vars).
- Two warm copies to `/mnt/docker-data/docker/`.

Host state: `/etc/docker/daemon.json` untouched; Docker not restarted; `qm pending 101` still holds only the virtio0 change; memory, balloon and cpuunits unchanged.

Remaining at the pause: the runbook and the check-mode runs (done below).

## Resumed 17:15 PDT

Read-only re-check of the checkpoint state on homelab:

```
docker.service ActiveEnterTimestamp=Wed 2026-09-09 16:41:42 PDT   (unchanged)
/etc/docker/daemon.json 272 bytes, mtime 2026-03-18 19:18:40, md5 95294c52fec73e37153a95f4bbc1773f   (untouched)
findmnt /mnt/docker-data: /dev/vdb1 ext4 rw,relatime
qm pending 101: new virtio0 (unchanged); cur virtio1
```

`roles/base` passed to E16 at this point; E15 made no further edits to it.

## Containerd store

Igor decided that normandy moves both `/var/lib/docker` and `/var/lib/containerd`. Checked on homelab:

```
docker info: Storage Driver: overlay2 (graph driver, not the containerd image store)
sudo du -sxh /var/lib/containerd: 2.7M
/etc/containerd/config.toml: disabled_plugins = ["cri"], no root override
```

Answer: homelab does not use the containerd image store, so there is nothing to move and no warm copy was made. The runbook records this against the normandy decision so Igor sees it at the go.

## Runbook and convergence

- `docs/transition/phase3/runbook-homelab.md` written: pre-checks (T3 thread finished, no Emby playback, pending matches, Igor's go), staged VM changes, Docker stop and final copy, data-root converge, VM stop and start, post-checks, rollback, duration estimate. It records the confirmed cache change and the full swap.
- `ansible-playbook playbooks/base.yml -l homelab --check --diff`: changed=0.
- `ansible-playbook playbooks/site.yml -l homelab --check --diff`: changed=0.

## Implications for later phases

- The window converge runs `docker info` from the role while Docker is stopped. If only docker.service were stopped and docker.socket left listening, that call would socket-activate Docker. The runbook stops both; E16's normandy runbook should do the same.
- Old `/var/lib/docker` (about 20 GB) stays on the thin LV as the rollback copy until Igor decides to delete it.
