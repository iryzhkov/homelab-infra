# VM templates

Each Proxmox node has its own Debian 13 cloud-init template, because the nodes have no shared storage and `qm clone --target` cannot copy across nodes.

| Node | VMID | Name |
|---|---|---|
| proxmox-a (hp-elite-mini-01) | 9000 | debian13-cloud |
| proxmox-b (hp-workstation-01) | 9001 | debian13-cloud |

Both templates use OVMF without pre-enrolled secure boot keys, q35, virtio-scsi-single, 2 cores and 2 GiB, a serial console, a NIC on vmbr1, DHCP and a cloud-init drive, with a 3 GiB root disk on `local-lvm`. They contain no SSH keys, passwords, tokens or Nebula certificates. The Debian genericcloud image does not include qemu-guest-agent, so Ansible installs it on first converge.

Build record: `docs/transition/phase2/2026-10-02-templates.md`.

## Clone

Run the clone on the node that will host the VM, from that node's template:

```
qm clone <tpl> <newid> --full 1 --name <name> --storage local-lvm
qm set <newid> --cores <n> --memory <MiB> --net0 virtio,bridge=vmbr1,tag=<vlan> \
  --ipconfig0 ip=<addr>/<cidr>,gw=<gw> --nameserver <dns> \
  --ciuser <user> --sshkeys <public key file> --ciupgrade 0 --onboot 1
qm resize <newid> scsi0 <size>G
qm start <newid>
```

Then converge the VM with Ansible. Only public keys go through cloud-init; secrets come from Ansible.

## Rebuild

On each node, with ID 9000 on proxmox-a and 9001 on proxmox-b:

1. Download the image and its checksums into `/var/lib/vz/template/cache`, verify them, and rename the image with its build id. The build id is the newest dated directory under `https://cloud.debian.org/images/cloud/trixie/`. Download the image once and scp it to the other node, so both templates use identical bytes.

   ```
   curl -fsSLO https://cloud.debian.org/images/cloud/trixie/latest/SHA512SUMS
   curl -fsSLO https://cloud.debian.org/images/cloud/trixie/latest/debian-13-genericcloud-amd64.qcow2
   grep ' debian-13-genericcloud-amd64.qcow2$' SHA512SUMS; sha512sum debian-13-genericcloud-amd64.qcow2
   ```

2. Destroy the old template with `qm destroy $ID --purge 1`. Existing full clones do not depend on it.

3. Build the new template:

   ```
   qm create $ID --name debian13-cloud --memory 2048 --cores 2 --cpu host --machine q35 --bios ovmf \
     --efidisk0 local-lvm:1,efitype=4m,pre-enrolled-keys=0 --scsihw virtio-scsi-single \
     --net0 virtio,bridge=vmbr1 --agent enabled=1 --serial0 socket --vga serial0 --ostype l26
   qm importdisk $ID <image> local-lvm          # reports vm-$ID-disk-1
   qm set $ID --scsi0 local-lvm:vm-$ID-disk-1,discard=on,ssd=1,iothread=1
   qm set $ID --ide2 local-lvm:cloudinit --boot order=scsi0 --ipconfig0 ip=dhcp --onboot 0 --tags template
   qm template $ID
   ```

4. Test the template with a scratch full clone. Give the clone `--net0 virtio,bridge=vmbr1,link_down=1` and resize its disk to 10G. Start the clone and read its serial console with `qm terminal <id>`, or with `socat -u UNIX-CONNECT:/var/run/qemu-server/<id>.serial0 -`. Wait for `Cloud-init v. ... finished ... DataSourceNoCloud`. Then destroy the clone with `qm destroy <id> --purge 1`.

The nightly vzdump job (`all 1`) includes the templates.
