# Phase 3 preparation: normandy (VM 202 on proxmox-b)

Date: 2026-10-02, survey from 14:49 PDT.
Hosts: normandy (VM 202, 192.168.70.234), proxmox-b (hp-workstation-01, 192.168.1.29).
Executor: E16.

Status: preparation done (resumed 17:10 PDT after a quota pause). Runbook: `runbook-normandy.md`.

## Mismatches with the brief and plan

1. Docker uses the containerd image store (`docker info`: `Storage Driver: overlayfs`, `driver-type: io.containerd.snapshotter.v1`). Images and container writable layers live in `/var/lib/containerd` (17 GB), which belongs to containerd.service, not in Docker's data-root. Moving `data-root` moves only `/var/lib/docker` (7.7 GB, almost all of it the `immich_model-cache` volume). Moving the images too needs containerd's `root` in `/etc/containerd/config.toml` (the file does not exist today) plus `RequiresMountsFor=/mnt/data` on containerd.service. `roles/base` has no capability for either. Igor decided at 17:05 PDT to move both roots in the window.
2. The plan's "Frigate record path and Immich upload location onto `/mnt/data`" is already true or not applicable. Frigate recording is disabled and its media is already on `/mnt/data`. Immich's upload location is on NFS. Only small state remains on the root disk (see below).
3. `rsync -aHAX --numeric-ids` without `-x` descends into the overlay mounts of running containers under `/var/lib/docker/rootfs/overlayfs/<id>`. The first pass copied seven merged container root filesystems (about 13 GB of junk) into `/mnt/data/docker/rootfs/overlayfs/`. Every pass must use `-x` (`--one-file-system`). The copy was wiped and redone with `-x` (see Warm pre-copy).
4. fstrim on `/` does not reach local-lvm: the LUKS mapping has no `allow_discards` flag (`dmsetup table root` shows no optional parameters; `rd.luks.options=tpm2-device=auto` only). Only `/mnt/data` (plain ext4) trims through to `tank`. Not changed; enabling discard on the LUKS root is a separate decision (it leaks free-space layout).
5. The brief's "cameras" are four in Frigate's configuration, two of them detecting (`garage_outside`, `doorbell`); the plan's "nine cameras" is the UniFi Protect count.

## Survey on proxmox-b

Command: `qm config 202`; `qm pending 202`

```
memory: 24576   balloon: 0   cores: 4   (no cpuunits line: default, cgroup cpu.weight 100)
hostpci0: 0000:00:02.0,pcie=1   tpmstate0: local-lvm:vm-202-disk-2,size=4M,version=v2.0
scsi0: local-lvm:vm-202-disk-1,discard=on,iothread=1,size=100G,ssd=1
scsi1: tank:vm-202-disk-0,backup=0,discard=on,iothread=1,size=400G,ssd=1
qm pending: every line "cur", none pending
```

Answer: 24 GB, balloon off, cpuunits at the default (100; `/sys/fs/cgroup/qemu.slice/202.scope/cpu.weight` reads 100, CT 201 also 100), no pending changes.

Command: `free -m`; guests on the node

```
Mem: total 31871  used 28458  available 3412   Swap: 7715 total, 606 used
VM 202 24576 MB; template 9001 2048 MB (stopped); CT 201 2048 MB + 512 swap
ARC target 2.0 GiB
```

Answer: about 3.4 GB available today; shrinking normandy to 20 GB returns 4 GB, about 7.4 GB available after.

## Survey inside normandy

Command: `docker info`; `du -sh -x`

```
root=/var/lib/docker driver=overlayfs (io.containerd.snapshotter.v1) logdriver=loki version=29.7.2; containerd 2.3.5
/var/lib/docker 7.7G: volumes 7.6G, plugins 96M (loki), containers 196K; 12221 entries
/var/lib/containerd 17G: snapshotter overlayfs 13G, content 3.8G; 204387 entries
docker system df: images 16.14GB, containers 1.85GB, volumes 8.09GB (immich_model-cache 8.08GB)
```

Command: `df -h`; `free -m`; `zramctl`

```
/dev/mapper/root (btrfs in LUKS) 98G, 52G used, 44G free
/dev/sdb1 /mnt/data 393G, 1.7G used, 371G free
Mem: 23956 total, 7462 used, 16493 available; zram0 zstd 23.4G, 0 used
```

Command: `systemctl status fstrim.timer`; `systemctl is-enabled fstrim.timer`

```
enabled; active (waiting); next Mon 2026-10-05 00:10
```

Answer: fstrim.timer is already enabled and running (Arch preset, unmanaged). Setting `fstrim_timer_enabled: true` should converge with no change.

Command: `sudo cat /etc/docker/daemon.json`

```
default-address-pools 172.16.0.0/12 size 24; log-driver loki (url http://192.168.70.223:3100/...), labels host=omarchy-normandy; max-size 50m, max-file 3. No data-root.
```

Command: `systemctl cat docker`; `systemctl show docker -p ...`

```
drop-ins: local-mounts.conf (RequiresMountsFor=/mnt/data, managed), no-block-boot.conf ([Unit] DefaultDependencies=no, 30 bytes, 2026-09-15 14:04:16, unmanaged)
Requires=system.slice -.mount docker.socket mnt-data.mount; After includes mnt-data.mount
ActiveEnterTimestamp=Thu 2026-10-01 20:50:31 PDT; containerd.service ActiveEnterTimestamp 20:50:27
```

### Origin of `no-block-boot.conf`

Searched: the repository, OV memory, the feed (`--grep no-block-boot`, nothing), normandy's journal for 2026-09-15 13:40 to 14:10 (no sudo or daemon-reload entries; the file may have been written with a preserved mtime), the OS migration tarball of 2026-08-30 (not there), Claude transcripts on the laptop, normandy and homelab, and the T3 state databases on gaming-pc and normandy. No record of why it exists.

Answer and recommendation: remove it in the window. `DefaultDependencies=no` does not make boot non-blocking; it only drops docker.service's ordering after `sysinit.target`/`basic.target` and its `Conflicts=`/`Before=shutdown.target`, so Docker can start before basic system setup and is not stopped in order at shutdown. Since 2026-10-02 the managed `local-mounts.conf` is the only ordering Docker needs. Removing it in the window, where the VM boots anyway, means the next boot tests the change under supervision. It is not brought into the role because nobody can state its reason.

## Frigate and Immich paths

Command: `docker inspect` mounts; Immich `stack.env`; Frigate `config.yml`

```
frigate          /home/igor/app/frigate/config -> /config   (root disk: frigate.db 37M + wal 4M, backup.db 21M, model_cache 326M; 387M total)
                 /mnt/data/frigate/media -> /media/frigate  (already on /mnt/data)
                 cache: tmpfs /tmp/cache 1 GB; record: enabled: false; snapshots retain 2 days
immich_server    /mnt/shared/immich -> /usr/src/app/upload  (NFS: library 105G, thumbs 8.7G, encoded-video 13G, upload 3.0G, backups 2.3G)
                 anonymous volume -> /data (in /var/lib/docker/volumes)
immich_ml        immich_model-cache volume -> /cache (8.08 GB, in /var/lib/docker/volumes)
immich_postgres  /mnt/data/immich/postgres (already on /mnt/data)
immich_redis     anonymous volume (in /var/lib/docker/volumes)
gatus            gatus_gatus-data volume; cloudflared none
```

Answer: no Frigate record path or Immich upload location needs to move. Immich's thumbs and encoded video are inside the NFS upload location. Immich's model cache, Redis and Gatus data are Docker volumes and move with the data-root. Frigate's config directory and its SQLite database (about 41 MB plus a 21 MB backup) stay on the root disk with the Compose project; moving them would change the Compose file and the backup role's `config_dir`, for no write or space benefit. No change to `stacks/frigate` or `stacks/immich` is proposed.

## Root-disk writes by source

Method as in phase 0: `/proc/<pid>/io` `write_bytes` deltas over 120 s against the `sda` sector counter, 2026-10-02 about 14:58 PDT.

```
sda: 593392 sectors = 290 MiB in 120 s (about 209 GB/day)
78.9 MiB sshd-session 554690 (coordinator-exchange from gaming-pc)
36.2 MiB t3-steward 1363272 (worker serve)
24.6 MiB btrfs-transaction
23.0 MiB sshd-session 556213 (from homelab)
 2.7 MiB gatus;  2.0 MiB systemd-journal;  1.9 MiB python3 (Frigate);  kcryptd/btrfs kworkers about 2 MiB each
```

Answer: unchanged from phase 0. The writers are t3-steward and its SSH-driven exchanges, outside Docker. Moving Docker's data-root (or containerd's root) does not change the root disk's write rate in any measurable way; it frees 7.7 GB (or 24.7 GB with containerd) of root-disk space.

## Warm pre-copy

Free space: `/mnt/data` 398288945152 bytes available of 421604532224. The copy (7.7 GB, 25 GB if containerd is added) is far below 80 %.

Pass 1, 14:5x PDT, Docker running:

```
sudo mkdir -p /mnt/data/docker /mnt/data/containerd
sudo rsync -aHAX --numeric-ids --stats /var/lib/docker/ /mnt/data/docker/
  209,650 files, 21,438,065,066 bytes transferred; rc=0; 156 s
```

The count was wrong: without `-x` rsync followed the seven container overlay mounts under `/var/lib/docker/rootfs/overlayfs/` (mismatch 3).

Pass 2:

```
sudo rsync -aHAXx --numeric-ids --delete --stats /var/lib/docker/ /mnt/data/docker/
  12,221 files, 5 files (7,099,939 bytes) transferred, 0 deleted; rc=0; 0 s
```

Pass 3 (same command, `--itemize-changes`): 0 files transferred, 0 s. With `-x`, rsync neither descends into the mount points in the source nor deletes inside them in the copy, so the junk stayed: `du -sh -x /mnt/data/docker` reads 21G, 197429 entries under `rootfs`.

Estimate for the final rsync with Docker stopped: under a minute for `/var/lib/docker` (a warm pass takes 0 s; the stop adds only changed volume files). A first full copy of `/var/lib/containerd` (17 GB, 204387 entries), if the lead adds it, took no measurement yet; by the rate of pass 1 (21 GB in 156 s) it would take about 2 to 3 minutes cold and seconds warm.

## Checkpoint (paused for quota, superseded)

Kept as written at the pause; "Resumed" below records what followed.

Done:
- Survey of proxmox-b and normandy, recorded above.
- Root-disk write measurement, recorded above.
- Warm copy of `/var/lib/docker` to `/mnt/data/docker`, three passes, the last with `-aHAXx --numeric-ids --delete`.

Host state:
- New directories on normandy: `/mnt/data/docker` (21 GB: the correct copy of `/var/lib/docker` plus about 13 GB of copied container root filesystems under `/mnt/data/docker/rootfs/overlayfs/<id>/`, mode 0710 so a non-root `cd` fails) and an empty `/mnt/data/containerd`.
- An attempt to delete the junk (`cd /mnt/data/docker/rootfs/overlayfs && ... find -delete`) failed at the `cd` with Permission denied, and the `&&` chain stopped there, so nothing was deleted.
- Nothing else changed: no host_vars, no converge, daemon.json untouched, Docker `ActiveEnterTimestamp` Thu 2026-10-01 20:50:31 PDT, no `qm set`.
- The lead's go for the role arrived: `fstrim_timer_enabled` may be set and converged now; `docker_data_root` must not be set before the window and is a step inside it.

Remains:
1. Remove the junk: `sudo find /mnt/data/docker/rootfs/overlayfs -mindepth 2 -delete` (only the copy under `/mnt/data`), then repeat `rsync -aHAXx --numeric-ids --delete` and confirm `du -sh -x /mnt/data/docker` is about 7.7G. Alternatively `sudo rm -rf /mnt/data/docker` and recopy with `-x` (a few minutes).
2. Lead decision on containerd (mismatch 1). If yes, warm-copy `/var/lib/containerd` to `/mnt/data/containerd` with `-aHAXx`, and a role capability is needed for containerd's root and its mount ordering.
3. Set `fstrim_timer_enabled: true` in `inventory/host_vars/normandy.yml`, converge `base.yml -l normandy`, confirm Docker's `ActiveEnterTimestamp` is unchanged.
4. Write `docs/transition/phase3/runbook-normandy.md` (the window: `qm set 202 --memory 20480 --cpuunits <value matching homelab's>`, Docker stop, final rsync with `-x`, `docker_data_root: /mnt/data/docker` set and converged, removal of `no-block-boot.conf`, `qm shutdown`/`qm start`, post-checks, rollback).
5. `site.yml --check` and `base.yml --check` with changed=0.

## Resumed (17:10 to 17:40 PDT)

State check before resuming:

```
systemctl show docker -p ActiveEnterTimestamp       -> Thu 2026-10-01 20:50:31 PDT (unchanged)
systemctl show containerd -p ActiveEnterTimestamp   -> Thu 2026-10-01 20:50:27 PDT
sha256sum /etc/docker/daemon.json                   -> 022de6b5...8a53, mtime 2026-08-30 03:12:58 (untouched)
/mnt/data/docker 21G, /mnt/data/containerd 4.0K; /mnt/data 6 % used
```

Answer: host state matched the checkpoint.

### How containerd is configured

```
systemctl show containerd -p ExecStart     -> /usr/bin/containerd (no flags); unit file state: disabled, pulled in by docker.service's Wants=
pgrep -a containerd                        -> pid 568 /usr/bin/containerd; shims with -namespace moby
/etc/containerd                            -> does not exist
containerd config dump                     -> version = 4, root = '/var/lib/containerd', imports = ['/etc/containerd/conf.d/*.toml']
```

Answer: dockerd uses the system `containerd.service` (`--containerd=/run/containerd/containerd.sock`), not an embedded one, with built-in defaults. A test outside `/etc` (`containerd -c <tmp>/main.conf config dump`, temp files removed) showed that a config file with `version = 4` and `root = ...` is honoured with no migration warning (a `version = 3` file is migrated with a warning). The conf.d import path was not used, because it is not certain that containerd loads imports when the main `config.toml` is missing, and testing that would mean writing into `/etc/containerd`.

### Role capability `containerd_root`

Added to `roles/base`: `tasks/containerd_root.yml`, `containerd_root: ""` and `containerd_root_marker` in `defaults/main.yml`, and the include in `tasks/main.yml`. It writes `/etc/containerd/config.toml` (marker line, `version` read from `containerd config default`, `root`), refuses to overwrite a config.toml without its marker, adds `containerd.service.d/containerd-root.conf` with `RequiresMountsFor=<root>` plus a daemon-reload, never restarts anything, and reports "restart pending" by reading the root of the running daemon from its open `meta.db`.

Command: `ansible-playbook playbooks/base.yml -l normandy --check --diff -e containerd_root=/mnt/data/containerd`

```
changed=4: /etc/containerd (directory), config.toml (+version = 4, +root = "/mnt/data/containerd"), containerd.service.d, containerd-root.conf
"containerd runs on /var/lib/containerd and config.toml names /mnt/data/containerd. ... restart it in the maintenance window ..."
```

Answer: the capability works in check mode on normandy; nothing was written. Neither `docker_data_root` nor `containerd_root` is set in host_vars (the lead's rule: set in the window).

### Warm copy, redone

The junk copy was removed and both roots copied twice with `-x`:

```
sudo rm -rf --one-file-system /mnt/data/docker; sudo mkdir /mnt/data/docker
sudo rsync -aHAX --numeric-ids -x --delete --stats /var/lib/<d>/ /mnt/data/<d>/   (twice each)
docker     pass1 57.8 s, 12,221 files, 8,185,457,774 bytes; pass2 0.3 s, 4 files, 7,888,736 bytes
containerd pass1 166.5 s, 204,428 files, 17,540,120,690 bytes; pass2 6.3 s, 0 files
du -sh -x: /mnt/data/docker 7.7G, /mnt/data/containerd 17G; df /mnt/data: 27G of 393G, 8 %
```

Answer: the warm copies match the sources in size; the final copy with both daemons stopped should take well under a minute. `/mnt/data` stays far below 80 %.

### fstrim.timer

`fstrim_timer_enabled: true` set in `inventory/host_vars/normandy.yml`.

Command: `ansible-playbook playbooks/base.yml -l normandy`

```
ok=13 changed=0; Docker ActiveEnterTimestamp Thu 2026-10-01 20:50:31 PDT; containerd 20:50:27; fstrim.timer enabled, active; daemon.json sha256 unchanged; /etc/containerd absent
```

Answer: the timer was already enabled by the Arch preset; the role now keeps it, with no change and no restart.

### Baselines for the runbook

```
Frigate /api/stats: detector ov inference 8.23 ms; garage_outside 3.1/0.2 fps, doorbell 3.0/2.8, garage_inside 0/0, back_yard 0/0
Gatus 192.168.70.234:3002: 50 endpoints, none failing
immich_machine_learning: /dev/dri card0 card1 renderD128 renderD129; providers ['OpenVINOExecutionProvider', 'CPUExecutionProvider']
user services on normandy that the VM restart stops: t3code, t3-steward, t3-steward-worker, huyang, gatus-steward-health
```

### Final checks

```
ansible-playbook playbooks/base.yml --check --diff: changed=0 on homelab, normandy, proxmox-a, proxmox-b, public-api; blackpearl changed=2
ansible-playbook playbooks/site.yml --check --diff: changed=0 on homelab, normandy, public-api; blackpearl changed=1
```

The black-pearl changes are the documented pending changes of the black-pearl preparation, not this work.

## Implications for later phases

- Phase 3 window: both roots move together, and containerd must be stopped as well as `docker.socket` and `docker.service`.
- Phase 7 drain: images on normandy now live on `tank` (`backup=0`), so a restore of VM 202 brings back an empty data disk and a Docker without images; recovery re-pulls them.
- Delete `/var/lib/docker` and `/var/lib/containerd` on normandy's root (about 25 GB) after a week, on Igor's decision.
