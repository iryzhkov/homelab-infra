# Phase 2 step 4: Debian 13 cloud-init templates

Date: 2026-10-02, 14:05 to 14:10 PDT (21:05 to 21:10 UTC).
Hosts: proxmox-a (hp-elite-mini-01, 192.168.1.5) and proxmox-b (hp-workstation-01, 192.168.1.29), both pve-manager 9.2.11.
Executor: E12. Run from the laptop over root SSH.

Result: template 9000 on proxmox-a and template 9001 on proxmox-b, both named `debian13-cloud`, built from the same verified Debian 13 genericcloud image (build 20261001-2618). A full clone of each booted under OVMF without secure boot, cloud-init ran from the NoCloud drive, and the root filesystem grew to the resized 10G disk. The scratch clones are destroyed.

## 1. VMIDs and starting state

Command: `qm list` on each node, and `pvesh get /cluster/resources --type vm --output-format json` on proxmox-a.

- proxmox-a VMs: 101, 102, 103, 106. proxmox-b VMs: 202.
- Cluster-wide guest IDs: `[100, 101, 102, 103, 104, 105, 106, 107, 110, 201, 202]`.

Answer: 9000, 9001 and the scratch IDs 9900 and 9901 were free cluster-wide.

Command: `pvesm status` on each node (before).

- proxmox-a `local-lvm`: lvmthin, total 354275328 KiB, used 253306859 KiB, available 100968468 KiB, 71.50 %. `local2tb` dir active at 0.01 %, `ssd1tb` disabled, `nas-backup` NFS active at 71.82 %.
- proxmox-b `local-lvm`: lvmthin, total 148443136 KiB, used 81376527 KiB, available 67066608 KiB, 54.82 %. `ssd1tb` dir active at 0.34 %, `local2tb` disabled.

Answer: proxmox-b had about 64 GiB free in its thin pool, enough for a 3 GiB template and a 10 GiB scratch clone.

Command: `ip -br link | grep vmbr` on each node.

Answer: `vmbr1` exists and is up on both nodes. proxmox-a also has `vmbr0` and `vmbr_virt`; proxmox-b has `vmbr0`.

## 2. Image

Command, on proxmox-a in `/var/lib/vz/template/cache`:

```
curl -fsSLO https://cloud.debian.org/images/cloud/trixie/latest/SHA512SUMS
curl -fsSL -o debian-13-genericcloud-amd64.qcow2 https://cloud.debian.org/images/cloud/trixie/latest/debian-13-genericcloud-amd64.qcow2
grep ' debian-13-genericcloud-amd64.qcow2$' SHA512SUMS
sha512sum debian-13-genericcloud-amd64.qcow2
```

- Image Last-Modified: Thu, 01 Oct 2026 19:04:07 GMT; Content-Length 341508096.
- SHA512SUMS entry and computed sum are identical:
  `f46f0671a6e5bdec5291ab8972bae2f10e5408c2f64a74078f11efc2f06a436a9d0313ed50e0472542eeabf780e9f7c792ac0a314c6c20507fcd9fd81b468c3d`
- The newest dated directory under `https://cloud.debian.org/images/cloud/trixie/` is `20261001-2618/`, and its own SHA512SUMS lists the same hash for `debian-13-genericcloud-amd64-20261001-2618.qcow2`, so `latest` is build 20261001-2618.
- `qemu-img info`: qcow2, virtual size 3 GiB (3221225472 bytes), 326 MiB on disk.

The files were renamed to carry the build id and copied to proxmox-b with scp:

- `/var/lib/vz/template/cache/debian-13-genericcloud-amd64-20261001-2618.qcow2`
- `/var/lib/vz/template/cache/SHA512SUMS-debian13-20261001-2618`

Command on proxmox-b: `sha512sum /var/lib/vz/template/cache/debian-13-genericcloud-amd64-20261001-2618.qcow2`.

Answer: proxmox-b computed the same `f46f0671...1b468c3d` hash, so both templates come from identical bytes.

## 3. Template build

The same commands ran on each node, with ID=9000 on proxmox-a and ID=9001 on proxmox-b:

```
qm create $ID --name debian13-cloud --memory 2048 --cores 2 --cpu host --machine q35 --bios ovmf \
  --efidisk0 local-lvm:1,efitype=4m,pre-enrolled-keys=0 --scsihw virtio-scsi-single \
  --net0 virtio,bridge=vmbr1 --agent enabled=1 --serial0 socket --vga serial0 --ostype l26
qm importdisk $ID /var/lib/vz/template/cache/debian-13-genericcloud-amd64-20261001-2618.qcow2 local-lvm
qm set $ID --scsi0 local-lvm:vm-$ID-disk-1,discard=on,ssd=1,iothread=1
qm set $ID --ide2 local-lvm:cloudinit --boot order=scsi0 --ipconfig0 ip=dhcp --onboot 0 --tags template
qm template $ID
```

- `efidisk0: successfully created disk 'local-lvm:vm-$ID-disk-0,efitype=4m,pre-enrolled-keys=0,size=4M'`
- `unused0: successfully imported disk 'local-lvm:vm-$ID-disk-1'`, so the import was disk-1, as the brief assumed.
- `qm template` renamed the disks to `base-$ID-disk-0` (EFI vars) and `base-$ID-disk-1` (root, 3G, 31.05 % allocated). The cloud-init drive is `vm-$ID-cloudinit` (4M).
- proxmox-a printed `WARNING: Sum of all thin volume sizes (350.01 GiB) exceeds the size of thin pool pve/data and the amount of free space in volume group (16.00 GiB).` The pool was already overcommitted before this step (337.86 GiB pool); the template adds 3 GiB of virtual size and about 1 GiB of real data. No warning appeared on proxmox-b.

Final `qm config 9000` (9001 is identical apart from the MAC, uuid and vmgenid):

```
agent: enabled=1
bios: ovmf
boot: order=scsi0
cores: 2
cpu: host
efidisk0: local-lvm:base-9000-disk-0,efitype=4m,pre-enrolled-keys=0,size=4M
ide2: local-lvm:vm-9000-cloudinit,media=cdrom
ipconfig0: ip=dhcp
machine: q35
memory: 2048
name: debian13-cloud
net0: virtio=BC:24:11:B3:66:03,bridge=vmbr1
onboot: 0
ostype: l26
scsi0: local-lvm:base-9000-disk-1,discard=on,iothread=1,size=3G,ssd=1
scsihw: virtio-scsi-single
serial0: socket
tags: template
template: 1
vga: serial0
```

Answer: both templates exist, with no SSH keys, users, passwords, tokens or Nebula certificates set. `qm cloudinit dump` shows only Proxmox's defaults: `users: [default]`, `chpasswd: expire: False`, `package_upgrade: true`, and the hostname. OVMF without secure boot worked, so SeaBIOS was not needed.

## 4. Clone test

The same commands ran on each node, with 9000 to 9900 on proxmox-a and 9001 to 9901 on proxmox-b:

```
qm clone 9000 9900 --full 1 --name tpltest
qm set 9900 --net0 virtio,bridge=vmbr1,link_down=1
qm resize 9900 scsi0 10G
qm start 9900
timeout 300 socat -u UNIX-CONNECT:/var/run/qemu-server/9900.serial0 - > /root/tpltest-9900-serial.log &
qm guest cmd 9900 ping
qm cloudinit dump 9900 user
qm stop 9900 && qm destroy 9900 --purge 1
```

The NIC had `link_down=1` throughout, so the clones never reached any network or received an address. The serial log on each node showed the following (proxmox-a times; proxmox-b was within a few seconds):

- `BdsDxe: starting Boot0002 "UEFI QEMU QEMU HARDDISK "`, then `Booting 'Debian GNU/Linux'` and `efi: EFI v2.7 by Proxmox distribution of EDK II`. The image boots under OVMF with `pre-enrolled-keys=0`.
- `Welcome to Debian GNU/Linux 13 (trixie)!`
- `EXT4-fs (sda1): resized filesystem to 2588667` (4 KiB blocks, about 9.9 GiB). growpart extended the root filesystem to the 10G disk.
- `Cloud-init v. 25.1.4 finished at Fri, 02 Oct 2026 21:08:46 +0000. Datasource DataSourceNoCloud [seed=/dev/sr0].  Up 131.72 seconds`
- `Debian GNU/Linux 13 tpltest ttyS0` / `tpltest login:`. The hostname from the cloud-init drive was applied.
- `ci-info: no authorized SSH keys fingerprints found for user debian.` This confirms that no keys are baked into the image or template.
- Expected with the link down: `FAILED ... Wait for Network to be Configured` (a 120 s timeout, which is why cloud-init finished at about 130 s), and apt `Temporary failure resolving 'deb.debian.org'` from `package_upgrade: true`.
- Harmless: `error: no suitable video mode found.` (GRUB on a serial-only VGA), `GPT: Use GNU Parted to correct GPT errors.` (the GPT backup header was not at the end after the resize; growpart fixes it), and on proxmox-a only `unchecked MSR access error: WRMSR to 0x38f` (a PMU MSR under `cpu: host`).
- `qm guest cmd <id> ping` returned `QEMU guest agent is not running` on both nodes. The build manifest `debian-13-genericcloud-amd64-20261001-2618.json` mentions `cloud-init` and `openssh-server` but not `qemu-guest-agent`.

Cleanup: `qm destroy --purge 1` removed both clones. `qm status` reports that the configuration file does not exist, and `lvs` shows no leftover `vm-9900` or `vm-9901` volumes. The serial logs in `/root` were deleted.

Answer: the serial console proved the boot and the cloud-init run on both nodes. genericcloud does not ship qemu-guest-agent, so Ansible must install and start `qemu-guest-agent` on first converge. Until then, `qm guest` commands and agent-reported IPs do not work, and Ansible must find the address from DHCP or set a static `--ipconfig0`.

## 5. Backups

Command: `cat /etc/pve/jobs.cfg` and `pvesh get /cluster/backup` on proxmox-a.

```
vzdump: nightly-all-guests
	all 1
	mode snapshot
	schedule 02:30
	storage nas-backup
	prune-backups keep-daily=7,keep-weekly=4
```

`exclude` is unset.

Answer: the templates are included in the nightly job, because `all 1` covers every guest and vzdump backs up templates. Each one adds roughly 1 GiB of real data and is unchanged night to night, so they were left included. To exclude them later, add `--exclude 9000,9001` to the job.

## 6. End state

Command: `pvesm status | grep local-lvm` on each node (after).

- proxmox-a `local-lvm`: used 254298830 KiB, 71.78 % (was 71.50 %).
- proxmox-b `local-lvm`: used 82356251 KiB, 55.48 % (was 54.82 %).

The cached image and its SHA512SUMS remain in `/var/lib/vz/template/cache` on both nodes for rebuilds.

## Clone procedure for phases 4 and 6

Clone each VM on its own node, from that node's template: 9000 on proxmox-a and 9001 on proxmox-b. `--target` does not work across nodes without shared storage.

```
qm clone <tpl> <newid> --full 1 --name <name> --storage local-lvm
qm set <newid> --cores <n> --memory <MiB> --net0 virtio,bridge=vmbr1,tag=<vlan> \
  --ipconfig0 ip=<addr>/<cidr>,gw=<gw> --nameserver <dns> --searchdomain <domain> \
  --ciuser <admin user> --sshkeys <public key file> --ciupgrade 0 --onboot 1 --tags <tags>
qm resize <newid> scsi0 <size>G
qm set <newid> --scsi1 tank:<size>,discard=on,ssd=1,iothread=1,backup=0   # data zvol where the plan calls for one
qm start <newid>
```

Then run Ansible against the new address. Its first tasks install `qemu-guest-agent`, then join Nebula and lay down secrets. Only public SSH keys go into cloud-init; tokens and Nebula certificates come from Ansible, never from `qm set`.

## Implications for later phases

- The guest agent is absent from the image. The phase 4 and phase 6 base role must install `qemu-guest-agent` before anything relies on `qm guest` or on Proxmox showing guest IPs.
- Proxmox's default cloud-init user-data sets `package_upgrade: true`. On a VLAN without internet egress this delays first boot by a failed apt run. Pass `--ciupgrade 0` on clones whose network cannot reach the Debian mirrors, and let Ansible upgrade them.
- proxmox-a's local-lvm thin pool is overcommitted: 360 GiB of virtual volumes during the test against a 337.86 GiB pool, at 71.78 % real use. The `agent-a` root disk on this pool adds to the overcommit, so watch the pool's real use before phase 6.
- `machine: q35` is unpinned, so clones follow the newest QEMU machine version after host upgrades. This is harmless for Linux guests.
