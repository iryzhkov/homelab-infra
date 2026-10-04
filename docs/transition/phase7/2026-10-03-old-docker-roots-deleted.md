# Old Docker and containerd roots deleted (2026-10-03)

Igor asked on 2026-10-03 to delete the old Docker data that the serviceable
transition kept as rollback after the phase 3 data-root moves. Nothing was
restarted. The `.moved` and `compose/` rollback copies, `/srv/swarm`, everything
under `/mnt`, and homelab's live `/var/lib/containerd` were left untouched.

## Pre-deletion checks

All checks passed on both hosts.

homelab:

- `docker info -f '{{.DockerRootDir}}'` returned `/mnt/docker-data/docker`;
  `/etc/docker/daemon.json` has the same `data-root`.
- `/var/lib/docker` was a plain directory (not a symlink) on `/dev/vda1` (ext4,
  `/`); `stat %m` and `findmnt -T` both gave `/`, and `findmnt -R` listed no
  mounts under it.
- `lsof +D /var/lib/docker` and a scan of `/proc/*/{cwd,root,maps}` found no users.
- `du -sh /var/lib/docker`: 19G. `df -h /` before: 187G size, 120G used, 58G
  available (68%).
- containerd (pid 609) holds `/var/lib/containerd/.../meta.db` open, confirming
  homelab's `/var/lib/containerd` is live; it was not touched.

normandy:

- `docker info -f '{{.DockerRootDir}}'` returned `/mnt/data/docker`;
  `/etc/docker/daemon.json` has the same `data-root`.
- `/etc/containerd/config.toml` line 4 is `root = "/mnt/data/containerd"`.
  containerd (pid 564) has cwd `/`, and its open files are
  `/mnt/data/containerd`, its `meta.db` and the overlayfs `metadata.db`; none
  under `/var/lib/containerd`.
- `/var/lib/docker` and `/var/lib/containerd` were plain directories on
  `/dev/mapper/root[/@]` (btrfs, `/`), with no mounts under them and no users
  in `lsof +D` or `/proc`.
- `du -sh`: `/var/lib/docker` 7.7G, `/var/lib/containerd` 17G. `df -h /` before:
  98G size, 53G used, 44G available (55%).

Both hosts: 32 (homelab) and 18 (normandy) running containers, all three swarm
nodes Ready, Gatus 75 endpoints with none failing.

## Deletion

Each path was removed with `sudo rm -rf --one-file-system <path>`, one at a
time, behind a guard that re-checked mount point, symlink, nested mounts and
the live Docker root immediately before the `rm`.

## After

homelab:

- `df -h /`: 102G used, 77G available (57%); about 18G freed.
- `sudo fstrim -av`: 86.2 GiB trimmed on `/`, 258.5 GiB on `/mnt/docker-data`.
- proxmox-a thin pool (`lvs`): `data` 59.59% to 49.47% (Meta 2.64% to 2.39%),
  `vm-101-disk-0` 75.02% to 57.22%; about 34G returned to the pool.
- 32 containers running, Docker root unchanged, all swarm nodes Ready.

normandy:

- `df -h /`: 52G used, 44G available (55%); only about 1G freed so far.
- The space is still pinned by snapper snapshots of `@`: snapshots 4
  (2026-08-30, `post-tpm-cmdline-fix`, no cleanup algorithm) and 5
  (2026-10-01, number cleanup) both contain `var/lib/docker` and
  `var/lib/containerd`. It is released only when those snapshots are removed;
  snapshot 4 will never be removed automatically. No snapshot was touched.
- fstrim skipped: the LUKS root does not pass discard.
- 18 containers running, all swarm nodes Ready, Gatus 75 endpoints none failing.
