# Phase 2 acceptance record

Date: 2026-10-02. Implementation commit `92dea29`, review fixes `03a0c94`, documentation fix `fdb76d3`, all on `transition/phase-2`.

## Verdict

Accepted. Plan phase 2 steps 1 to 4 are met:

1. `zfs_arc_max` is 4 GiB on proxmox-a and 2 GiB on proxmox-b, live, in `/etc/modprobe.d/zfs.conf` and in every installed initramfs, set before either pool existed.
2. proxmox-a's `local2tb` is retired and the 2 TB P3 (serial 2420E8AFBCE0) is pool `tank` with a quota of 90 % of usable space.
3. normandy's `/mnt/data` moved from a qcow2 on `ssd1tb` to a 400 GB zvol (`backup=0`) on proxmox-b's new `tank` (BX500, serial 2438E98C8AD2), mounted by UUID through the base role with Docker ordered after it. The `tank` storage definition spans both nodes.
4. Debian 13 cloud-init templates 9000 and 9001 exist on each node, carry no secrets, and were tested by clone.

The only interruption was Immich and Frigate, stopped from 14:12:18 to 14:17:55 PDT on Igor's approval. normandy's Docker daemon, VM 202 and homelab were not restarted. `site.yml`, `base.yml` and `backup.yml` in check mode report changed=0 on every host.

## Review history

Four Sonnet 5.5 reviewers: storage and Proxmox FIX REQUIRED (the proxmox-a quota had been computed while an earlier quota capped available space); Ansible ACCEPT with three should-fix; service availability ACCEPT; security and data protection FIX REQUIRED (the NAS rollback copy sat under a path shipped to B2 on Monday 2026-10-05). Executors fixed the quota, moved the copy to `/mnt/backup/normandy-migration/`, guarded the ARC tasks against an unloaded module and rebuilt every initramfs. The Ansible reviewer's claim that a hand-written drop-in already ordered Docker after `/mnt/data` was shown wrong: `no-block-boot.conf` holds only `DefaultDependencies=no`. The Fable 5.1 reviewer re-checked every fix on the hosts, required one documentation correction, and accepted at `fdb76d3`.

## Decisions recorded

- Igor, 2026-10-02: stop Frigate together with Immich for the disk swap, because Frigate also bind-mounts `/mnt/data`.
- Igor, 2026-10-02: because the new data disk is outside vzdump, normandy's Immich database dump runs daily under the backup role and is shipped to B2 daily. The offsite recovery point for that database went from about four days to about one.
- Igor, 2026-10-02: the fake executables in the media library were deleted (`2026-10-02-fake-media-cleanup.md`).
- Lead, accepted by the Fable reviewer: the template image is verified by TLS from cloud.debian.org and matching checksums from two Debian hosts, because Debian publishes no signature for current cloud images. `docs/templates.md` says to check for a signature on every rebuild.

## Carried to phase 3 and later

- Boot import of `tank` is unproven on both nodes until a host reboot (phase 7).
- A restore of VM 202 drops `scsi1`; recovery recreates the zvol and restores from the daily dump. Frigate media has no backup.
- proxmox-b has about 3.2 GB of memory available with 606 MB of swap in use; proxmox-a's `local-lvm` is 71.8 % used and overcommitted.
- normandy's hand-written `docker.service.d/no-block-boot.conf` (`DefaultDependencies=no`) is unmanaged; bring it into the repository or remove it.
- black-pearl should leave the `services_backup` and `public` export client lists (from phase 1); the rollback copy on `services_backup` is deleted by about 2026-10-09, Igor decides.
- The cloud image lacks `qemu-guest-agent`; the first converge of a clone installs it, and clones on a VLAN without egress use `--ciupgrade 0`.
- The 2 TB P3's second temperature sensor reads 69 to 79 C; watch it when homelab's data zvol lands on `tank`.
- B2 storage under `apps/normandy/postgres` grows without bound because `rclone copy` never deletes; give it a retention in phase 5.
