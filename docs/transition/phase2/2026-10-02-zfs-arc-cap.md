# Phase 2 step 1: ZFS ARC cap on the hypervisors

Date: 2026-10-02, 14:04 to 14:12 PDT; review fixes 14:20 to 14:35 PDT.
Hosts: proxmox-a (hp-elite-mini-01, 64 GB), proxmox-b (hp-workstation-01, 32 GB).
Changed by: Ansible, `playbooks/base.yml` through `roles/base/tasks/zfs_arc.yml`.

ARC is now capped on both nodes (4 GiB on proxmox-a, 2 GiB on proxmox-b), at
boot and in the running kernel, before any ZFS pool exists.

## Change

- `inventory/host_vars/proxmox-a.yml`: `zfs_arc_max_bytes: 4294967296` (4 GiB).
- `inventory/host_vars/proxmox-b.yml`: `zfs_arc_max_bytes: 2147483648` (2 GiB).
- `roles/base/tasks/zfs_arc.yml`, included from `roles/base/tasks/main.yml`
  only when `zfs_arc_max_bytes` is defined, so homelab, normandy, blackpearl
  and public-api are untouched. It:
  - manages one line, `options zfs zfs_arc_max=<bytes>`, in
    `/etc/modprobe.d/zfs.conf` with `lineinfile` (other zfs options in that
    file would be kept);
  - runs `update-initramfs -u -k all` only when that line changed (registered
    result and `when`, no handlers), so every installed kernel's initrd gets
    the file (the first run used `-u`; changed after review, see below);
  - checks with `stat` that `/sys/module/zfs/parameters/zfs_arc_max` exists,
    and only then reads it and writes it when it differs from the target
    (`changed_when: true` on the write, skipped otherwise). With the zfs
    module not loaded the live read and write are skipped and the cap applies
    at the next module load. In check mode the write and the initramfs
    rebuild are skipped.
- Carried phase 0 fix, `roles/base/tasks/smart.yml`: the task that enables
  `homelab-smart.timer` now uses `state: restarted` when the
  `homelab-smart.timer` item of the registered unit loop changed, and
  `started` otherwise, as `gpu.yml` does for its service. The calendar value
  (`smart_on_calendar: "*:0/15"`) is unchanged. The expression was tested
  locally against a fake loop result: timer unchanged gives `started`, timer
  changed gives `restarted`.

## Checks before the change (14:04)

Command: `ls /etc/modprobe.d/; grep -rn zfs /etc/modprobe.d/ /lib/modprobe.d/ /usr/lib/modprobe.d/`

- proxmox-a: blacklist-bluetooth.conf, intel-microcode-blacklist.conf,
  pve-blacklist.conf; no zfs line anywhere.
- proxmox-b: blacklist-i915.conf, intel-microcode-blacklist.conf,
  pve-blacklist.conf, vfio.conf; no zfs line anywhere.

Answer: no existing zfs module options, so nothing to preserve.

Command: `cat /sys/module/zfs/parameters/zfs_arc_max; awk '/^(c_min|c_max|size) /' /proc/spl/kstat/zfs/arcstats; zpool list`

| | zfs_arc_max | c_max | size | pools |
|---|---|---|---|---|
| proxmox-a | 0 (default) | 66008481792 (61.5 GiB) | 2880 | no pools available |
| proxmox-b | 0 (default) | 32346017792 (30.1 GiB) | 1440 | no pools available |

Answer: uncapped; the default c_max on these kernels was almost all of RAM.

Command: `free -m` (Mem line: total / used / free / buff-cache / available)

- proxmox-a: 63974 / 48089 / 8751 / 7876 / 15885
- proxmox-b: 31871 / 26922 / 3349 / 2113 / 4949

Command: `proxmox-boot-tool status; efibootmgr; dpkg -l grub-efi-amd64; findmnt -no SOURCE,FSTYPE /`

- Both: `E: /etc/kernel/proxmox-boot-uuids does not exist.`; EFI boot entry
  `\EFI\proxmox\shimx64.efi`; grub-efi-amd64 2.12-9+pmx2; root
  `/dev/mapper/pve-root ext4`.

Answer: GRUB on EFI loads the initramfs from `/boot` on the ext4 root;
proxmox-boot-tool does not manage the ESPs, so no `proxmox-boot-tool refresh`
is needed and none was run. `update-initramfs` alone updates what GRUB boots.

## Converge

1. `ansible-playbook playbooks/base.yml -l proxmox-a,proxmox-b --check --diff`:
   diff `+options zfs zfs_arc_max=4294967296` (proxmox-a) and
   `+options zfs zfs_arc_max=2147483648` (proxmox-b); changed=1 on each, no
   failures.
2. `ansible-playbook playbooks/base.yml -l proxmox-a,proxmox-b --diff`:
   changed=3 on each (zfs.conf, initramfs rebuild, live value); SMART tasks
   unchanged.

## Checks after the change (14:06)

Command: `cat /etc/modprobe.d/zfs.conf; cat /sys/module/zfs/parameters/zfs_arc_max; awk '/^(c_max|size) /' /proc/spl/kstat/zfs/arcstats`

| | zfs.conf | zfs_arc_max | c_max | size |
|---|---|---|---|---|
| proxmox-a | `options zfs zfs_arc_max=4294967296` | 4294967296 | 4294967296 | 2880 |
| proxmox-b | `options zfs zfs_arc_max=2147483648` | 2147483648 | 2147483648 | 1440 |

Answer: the running ARC limit equals the target on both nodes. size is
unchanged because there is no pool yet, so the ARC holds nothing.

Command: `lsinitramfs /boot/initrd.img-$(uname -r) | grep zfs.conf; ls -l /boot/initrd.img-*`

- Both: `etc/modprobe.d/zfs.conf` present in initrd.img-7.0.14-14-pve
  (the running and newest kernel), rebuilt 14:05:53 (proxmox-a) and 14:05:54
  (proxmox-b).
- The older 6.8.x initrds were not rebuilt by this first run
  (`update-initramfs -u` rebuilds only the newest kernel's); they were
  rebuilt after review, below.

Answer: the next boot of the default kernel applies the cap.

## Review fixes (14:20 to 14:35)

The phase 2 Ansible review asked for two changes to
`roles/base/tasks/zfs_arc.yml`: guard the sysfs read with a `stat` so an
unloaded zfs module does not fail the play, and rebuild every initrd with
`update-initramfs -u -k all`. Both are in the task file as described above.

- `ansible-playbook playbooks/base.yml -l proxmox-a,proxmox-b --check --diff`,
  then the same without `--check`: changed=0 on both nodes (zfs.conf was
  already in place, so the rebuild task does not fire).
- One-off host change, run by hand at 14:27 to 14:28 because the role's
  rebuild only runs when zfs.conf changes:
  `ssh proxmox-a update-initramfs -u -k all` and
  `ssh proxmox-b update-initramfs -u -k all`. rc=0 on both; it regenerated
  five initrds on proxmox-a (7.0.14-14, 6.8.12-43, 6.8.12-20, 6.8.8-4,
  6.8.4-2) and four on proxmox-b (7.0.14-14, 6.8.12-43, 6.8.12-20, 6.8.12-4).
- Verification: `for i in /boot/initrd.img-*; do lsinitramfs $i | grep -c etc/modprobe.d/zfs.conf; done`
  returned 1 for every initrd on both nodes, including the older ones
  (for example initrd.img-6.8.4-2-pve on proxmox-a and
  initrd.img-6.8.12-4-pve on proxmox-b). Root filesystem use after the
  rebuild: 17% on proxmox-a, 40% on proxmox-b.

Answer: every installed kernel now boots with the cap.

Command: `free -m` (Mem line: total / used / free / buff-cache / available)

- proxmox-a: 63974 / 48650 / 7472 / 8594 / 15324
- proxmox-b: 31871 / 27177 / 2319 / 2892 / 4694

Answer: no memory recovered and none expected, since the ARC was empty; the
small drift comes from guests and the concurrent template builds (E12). The
cap protects memory once the pools exist.

No host was rebooted.

## Idempotence and repository checks

Final runs, after the review fixes and the one-off initramfs rebuild:

- `ansible-playbook playbooks/base.yml -l proxmox-a,proxmox-b`: proxmox-a
  changed=0, proxmox-b changed=0. `homelab-smart.timer` active on both;
  zfs_arc_max still 4294967296 and 2147483648.
- `ansible-playbook playbooks/base.yml --check --diff` (all hosts): changed=0
  on blackpearl, homelab, normandy, proxmox-a, proxmox-b, public-api.
- `ansible-playbook playbooks/site.yml --check --diff`: rc=0, changed=0 on
  blackpearl, homelab, normandy, public-api.

## Implications for later phases

- The pool-creation steps can proceed: the ARC limit is in force before the
  first pool is imported.
- A newly installed kernel gets the setting in its initramfs automatically,
  because its initramfs is built after `/etc/modprobe.d/zfs.conf` exists.
