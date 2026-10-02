# Phase 0: copy of /etc/pve and host configuration to the NAS

Date: 2026-10-02, 12:57 to 13:00 PDT.
Hosts: proxmox-a (hp-elite-mini-01, 192.168.1.5), proxmox-b (hp-workstation-01, 192.168.1.29), NAS 192.168.40.40.
Purpose: plan phase 0 item 1, "Copy `/etc/pve` from both nodes to the NAS before any storage change". Taken before the VM 202 restore test and before any storage definition was touched.

## Destination

Command: `cat /etc/pve/storage.cfg`; `findmnt -T /mnt/pve/nas-backup`

```
nfs: nas-backup
	export /var/nfs/shared/services_backup
	path /mnt/pve/nas-backup
	server 192.168.40.40
/mnt/pve/nas-backup 192.168.40.40:/var/nfs/shared/services_backup
```

Answer: `nas-backup` is mounted at `/mnt/pve/nas-backup` on both nodes. The copies are in `/mnt/pve/nas-backup/pve-config/`.

That directory already existed. It holds the nightly encrypted config backups written by `/usr/local/bin/pve-config-backup.sh` (`/etc/cron.d/pve-config-backup`, 03:30 on proxmox-a and 03:45 on proxmox-b, `DEST_DIR=/mnt/backup/pve-config`, which is the same NFS export mounted a second time at `/mnt/backup`, `KEEP=14`). Those are age-encrypted (`pve-config_<node>_<stamp>.tar.zst.age`), the newest from 2026-10-02 03:30 and 03:45. The manual copy below is in addition to them, taken at a known point immediately before phase 0 changes.

## Archives taken

Command, on each node:

```
tar czf /mnt/pve/nas-backup/pve-config/<node>-etc-pve-2026-10-02.tar.gz -C / etc/pve
tar czf /mnt/pve/nas-backup/pve-config/<node>-host-etc-2026-10-02.tar.gz -C / \
    etc/fstab etc/modprobe.d etc/network/interfaces etc/hosts etc/hostname etc/default/grub etc/kernel/cmdline
```

`etc/kernel/cmdline` does not exist on either node (both boot with GRUB), so the second tar exited non-zero for that one missing path only; every other path is in the archive.

The `/etc/pve` archive contains `priv/` (`authkey.key`, `pve-root-ca.key`, `shadow.cfg`, `token.cfg`). Writing that in clear text to the NAS would have been weaker than the existing nightly practice, so each archive was encrypted with age to the same recipient the nightly job uses (`AGE_RECIPIENT` in `/etc/cron.d/pve-config-backup`, public key `age1sqykeq5dxn8td4g634hkm5026qcjnjrdpkw7rsq7uu88vw62pqsq84fz5a`; the private key is `~/.config/age/pve-config-backup.key` on the laptop), mode 0600. Each encrypted file was decrypted on the laptop and its sha256 compared with the clear-text archive's; all four matched. The clear-text archives were then deleted.

Command: `age -r <recipient> -o <archive>.age <archive>`; on the laptop `ssh proxmox-a cat <archive>.age | age -d -i ~/.config/age/pve-config-backup.key | sha256sum`

| File (in `/mnt/pve/nas-backup/pve-config/`) | Size (bytes) | sha256 of the .age file | sha256 of the tar.gz inside |
|---|---|---|---|
| `hp-elite-mini-01-etc-pve-2026-10-02.tar.gz.age` | 24394 | `1a39abd77faead36aeccd6a1281d9d7905ae307016ff453c86ca00c27438e76c` | `10b8a623a71ba3199856550c3665f48aa1c600fcdea658a6b422bc11c9975158` |
| `hp-elite-mini-01-host-etc-2026-10-02.tar.gz.age` | 2686 | `db280b1f9a4aa52da12364100e07e5436bd804a501587e402570ea98e4783eb2` | `e889d48b1f6cfa323977a3d78f1ffa8f0dcea223b6bac6792c9edb8ad83aea0f` |
| `hp-workstation-01-etc-pve-2026-10-02.tar.gz.age` | 24486 | `075186fec26202651564c701a241a6313151c38cee51658685eb5c4e9b0fe3a1` | `a078b526f33aca3c940a49a55c0536473a041113629e928b17001e918ff413ed` |
| `hp-workstation-01-host-etc-2026-10-02.tar.gz.age` | 2560 | `75de2507980c7d52dfcd01abc7e2c27ddea68ee3cdd30280f74583cd1b581ea2` | `ff0289cd90a2ab32d68d549f03a63abced2c357f037b608c2cbf0c6fc4205c4a` |

## Sanity check

Command: `tar tzf <node>-etc-pve-2026-10-02.tar.gz | wc -l; ... | head` (run on the clear-text archive before encryption)

```
69 entries per node
etc/pve/
etc/pve/.debug
etc/pve/.vmlist
etc/pve/.members
etc/pve/lxc
etc/pve/local
...
etc/pve/storage.cfg
etc/pve/user.cfg
etc/pve/priv/authkey.key
etc/pve/nodes/hp-workstation-01/qemu-server/202.conf
etc/pve/nodes/hp-workstation-01/qemu-server/250.conf
etc/pve/nodes/hp-elite-mini-01/qemu-server/101.conf
```

Answer: both `/etc/pve` archives hold `storage.cfg`, `jobs.cfg`, `user.cfg`, `priv/`, and every guest config on both nodes (VM 250 included, taken before it was destroyed).

Command: `tar tzf <node>-host-etc-2026-10-02.tar.gz`

```
proxmox-a: etc/fstab etc/modprobe.d/{intel-microcode-blacklist,blacklist-bluetooth,pve-blacklist}.conf etc/network/interfaces etc/hosts etc/hostname etc/default/grub
proxmox-b: etc/fstab etc/modprobe.d/{intel-microcode-blacklist,pve-blacklist,vfio,blacklist-i915}.conf etc/network/interfaces etc/hosts etc/hostname etc/default/grub
```

Answer: the host archives hold fstab, modprobe.d (including proxmox-b's `vfio.conf` and `blacklist-i915.conf` for the UHD 630 passthrough), network interfaces and GRUB defaults.

## Side effect on the directory mode

Change made: `chmod 700 /mnt/pve/nas-backup/pve-config`, then `chmod 770` on the same directory (from proxmox-a, about 12:58 PDT). Reason: I meant to keep the clear-text archives, which held `/etc/pve/priv`, from being readable by others while they existed. The directory was the wrong target; restricting the files alone (`chmod 600`) was enough. I then set the directory to 770 to restore group access, matching its parent `/mnt/pve/nas-backup` (`drwxrwx---`, owner 988, group 988).

Previous mode: cannot be determined. It was not recorded before the change, and nothing that would have set it was found:

- `/usr/local/bin/pve-config-backup.sh` does not create the directory. It fails if `DEST_DIR` does not exist, and only writes files with `install -m 0600`.
- `/etc/cron.d/pve-config-backup` sets only `DEST_DIR`, `KEEP` and `AGE_RECIPIENT`.
- Root's shell history on both nodes has no `mkdir` or `chmod` of the directory.
- homelab-infra has no reference to it.
- The OpenViking note `viking://resources/homelab/proxmox-config-backup.md` gives the script's mode but not the directory's.
- Sibling directories on the share are inconsistent, so they do not settle it: 770 (`grafana`, `influxdb`, `jocasta`, `loki`, `normandy`, `offsite`), 775 (`dump`, `normandy-migration`, `t3-sessions`, `unifi`) and 777 (`blackpearl`). The most likely previous modes are therefore 770 or 775.

Effect: the nightly job writes as the squashed owner (uid 977), which every one of those modes allows, so the nightly config backup is unaffected; its archives are 0660 anyway. If the previous mode was 775, the only access lost is "other" read and list on the directory. Any reader on the NAS that is neither uid 977 nor in group 988 should be checked; none is known.

## Restoring

`ssh proxmox-a cat /mnt/pve/nas-backup/pve-config/<file>.age | age -d -i ~/.config/age/pve-config-backup.key | tar xzf - -C /tmp/restore` on the laptop. Copy individual files back; never untar over a live `/etc/pve`.
