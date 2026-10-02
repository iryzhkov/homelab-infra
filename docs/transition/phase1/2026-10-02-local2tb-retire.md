# Phase 1 step 5: black-pearl's 1.8 TB disk retired

Date: 2026-10-02, 13:51 to 13:55 PDT.
Hosts: black-pearl (VM 102), proxmox-a (hp-elite-mini-01).
Scope: Igor moved step 5 forward (relayed by the lead session) once steps 1 to 4 were verified. Gates: complete rechecks with no missing or errored torrents, one import confirmed as a hardlink, and executor E7's restore result. The first two are recorded in `2026-10-02-downloads-cutover.md` (1239 torrents complete, 0 missing, 0 errored; Link Click S04E09 import at inode 162664 with 2 links, plus the in-container hardlink test). E7 (`2026-10-02-vm102-backup0-restore.md`) found that a restore drops the `backup=0` disk and the restored guest stops in emergency mode on the `/mnt/tmp` fstab line, which has no `nofail`; the lead cleared the gate on condition that the line is removed through Ansible before the detach.

## Contents before the unmount

Command on black-pearl: `sudo du -sh /mnt/tmp/downloads /mnt/tmp/lost+found`, `sudo du -sh /mnt/tmp/downloads/*/`, `sudo find /mnt/tmp -type f`.

```
1.5G  /mnt/tmp/downloads
16K   /mnt/tmp/lost+found
8.0K  /mnt/tmp/downloads/complete/
1.5G  /mnt/tmp/downloads/incomplete/
4.0K  /mnt/tmp/downloads/tv-sonarr/
2 files: /mnt/tmp/downloads/.DS_Store (6148 B, 2025-12-28) and
         incomplete/free-leech/Rewrite.2019.S01E11.MULTi.1080p.WEB.x264-HEADER/...mkv.!qB (1602820127 B, 2026-01-07)
```

Answer: one stale partial file from January that no current torrent references, and a macOS `.DS_Store`. Both were discarded with the disk, with Igor's acceptance.

## Fstab line removed through Ansible

Change: `mounts_absent: [/mnt/tmp]` in `inventory/host_vars/blackpearl.yml`, applied by `roles/base/tasks/nfs_mounts.yml` with `ansible.posix.mount state: absent`, followed by a systemd reload.

Commands: `ansible-playbook playbooks/base.yml -l blackpearl --check --diff` (13:52:16, `changed=1`), then the same without `--check` (13:52:23, `changed=1`, item `/mnt/tmp`).

Verification without a reboot, on black-pearl:

| Command | Decisive output | Answer |
|---|---|---|
| `grep -n mnt /etc/fstab` | only the `/mnt/shared` and `/mnt/apps` NFS lines | the `UUID=cc61153d-... /mnt/tmp ext4 defaults 0 2` line is gone |
| `findmnt /mnt/tmp`; `ls -ld /mnt/tmp` | no output; `No such file or directory` | unmounted, and the module removed the empty mount point |
| `sudo findmnt --verify` | `/media/cdrom0 [E] udf,iso9660 does not match with on-disk iso9660`; `0 parse errors, 1 error, 0 warnings` | clean for every disk mount. The one error is the installer's `noauto` CD-ROM line, which lists two filesystem types; it predates this change and cannot block a boot |
| `sudo systemctl daemon-reload` | `rc=0` | succeeds |
| `systemctl list-units --all \| grep mnt-tmp`; `systemctl --failed` | no output; no output | no leftover or failed unit |
| `systemctl list-dependencies --reverse mnt-tmp.mount` | `mnt-tmp.mount` and nothing beneath it | nothing requires the mount |
| `docker inspect` of every running container, sources grepped for `/mnt/tmp` | `0` | no bind mount references it |

The first real boot without the disk happens at the planned phase 3 stop and start of VM 102; it was not rebooted here.

## Detach on proxmox-a

| Time (PDT) | Command | Decisive output |
|---|---|---|
| 13:52:31 | `qm set 102 --delete scsi1` | `update VM 102: -delete scsi1`, rc 0; config now has `unused0: local2tb:102/vm-102-disk-0.qcow2` and no `scsi1`; `qm pending` lists only `cur unused0` |
| 13:52:31 | `lsblk` in the guest | `sda` and `sr0` only; `sdb` gone |
| 13:52:42 | `qm set 102 --delete unused0` | `update VM 102: -delete unused0`, rc 0; no `unused` or `scsi1` entry left in the config or in `qm pending` |

Hot-unplug worked without stopping the VM. The guest's kernel logged the removal at 13:52:54 with one warning: `Synchronize Cache(10) failed: Result: hostbyte=DID_BAD_TARGET`, then `WARNING: ... at kernel/workqueue.c:635 __cancel_work_timer` in `kacpi_hotplug acpi_hotplug_work_fn` (Debian 6.1.0-52-amd64). With `virtio-scsi-single`, each disk has its own controller, so the unplug removed a whole virtio-scsi controller, and this warning is the 6.1 kernel tearing that controller down. Nothing failed afterwards: no failed units, load 0.06, all containers up.

`qm set --delete unused0` deleted the volume itself, not only the reference: `pvesm list local2tb` returned only its header, and `images/102/` was removed. `pvesm free` was not needed.

## What remains on local2tb

Commands on proxmox-a: `pvesm list local2tb`, `pvesm status`, `ls -la /mnt/pve/local2tb`, `find /mnt/pve/local2tb -mindepth 1 -not -path '*/lost+found*'`, `du -sh` of each entry.

```
pvesm list local2tb: no volumes
pvesm status: local2tb dir active 1921724676 KiB total, 101436 KiB used, 0.01 %
f 103809024 /mnt/pve/local2tb/.speedtest     (2026-09-06, from the throughput test in docs/storage.md)
d dump/ images/ private/ snippets/ template/ template/iso/ template/cache/   (all empty)
lost+found/ 16K
```

Answer: no guest volumes remain. The storage holds the 99 MiB `.speedtest` file and the empty directory tree Proxmox creates. The storage definition and the drive were left untouched for phase 2.

## Health afterwards

- qBittorrent: 1238 torrents, `stalledUP 1232, uploading 6`, none below 100 %, `connection_status connected`. The one torrent fewer is again the share limit (`Torrent reached the seeding time limit. Removing torrent and deleting its content.` at 13:52:42 for "[DKB] Seihantai na Kimi to Boku - S02E12").
- Sonarr, Radarr, Lidarr: `/ping` 200. Sonarr and Radarr health is empty. Lidarr reports `IndexerRssCheck` and `IndexerSearchCheck` ("No indexers available..."), a configuration state unrelated to downloads.
- `ansible-playbook playbooks/base.yml --check --diff`: changed=0 on blackpearl, homelab, normandy, proxmox-a, proxmox-b, public-api.
- `ansible-playbook playbooks/site.yml --check --diff`: changed=0 on blackpearl, homelab, normandy, public-api.

## Rollback

None for the disk: the qcow2 is deleted, and the NAS copy is the only copy of downloads, as the plan intends. The partial file it held is lost. The NFS rollback in `2026-10-02-downloads-cutover.md` still applies, without the `/mnt/tmp` bind.

## Implications for later phases

- Phase 2: `local2tb` is empty apart from `.speedtest`; the storage and the CT2000P3 can be repurposed.
- Phase 3: VM 102's planned stop and start is the first boot without the disk, with `/mnt/shared` mounted `hard` at boot and docker.service waiting for it. If the NAS is unreachable at that boot, Docker does not start on black-pearl.
- The acceptance precondition about how a restore treats a `backup=0` disk no longer applies to VM 102, which now has no such disk.
