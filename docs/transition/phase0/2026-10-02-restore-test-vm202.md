# Phase 0: restore test of VM 202 (normandy)

Date: 2026-10-02, 12:56 to 13:21 PDT.
Host: proxmox-b (hp-workstation-01, 192.168.1.29). Source archive on `nas-backup` (NFS 192.168.40.40).
Purpose: plan phase 0 item 2. Restore normandy's backup to a scratch VMID with `--unique`, confirm it boots without the GPU, then destroy it. Review item 16 asked how a `backup=0` disk behaves on restore.

Result: the newest nightly backup of VM 202 restored in 19 min 27 s. It booted without the UHD 630 and the guest agent answered 23 s after `qm start`. The guest reached multi-user with Docker running Immich and cloudflared. The scratch VM was destroyed, and VM 202's config and disks were unchanged throughout.

## Mismatches with the brief and plan

- VM 202's Proxmox name is `omarchy`. The guest hostname is `omarchy-normandy`. "normandy" in the plan is the same VM.
- VM 202 has no `backup=0` disk. `scsi1: ssd1tb:202/vm-202-disk-0.qcow2,discard=on,iothread=1,size=900G,ssd=1` is included in every nightly backup, and the archive's log says `include disk 'scsi1' 'ssd1tb:202/vm-202-disk-0.qcow2' 900G`. The only `backup=0` disk in the cluster is VM 102's `scsi1` on proxmox-a. So this test could not observe how a `backup=0` disk behaves on restore; see the last section. The difference did not make any step here unsafe, so the test went ahead.

## 1. Archive

Command: `pvesm list nas-backup --vmid 202`

```
... ten archives, 2026-09-06 to 2026-10-02 ...
nas-backup:backup/vzdump-qemu-202-2026_10_02-02_30_08.vma.zst vma.zst backup 83612778446 202
```

Command: `zstd -dc <archive> | vma config -`; `grep include <archive>.log`

```
hostpci0: 0000:00:02.0,pcie=1
scsi1: ssd1tb:202/vm-202-disk-0.qcow2,discard=on,iothread=1,size=900G,ssd=1
#qmdump#map:efidisk0:drive-efidisk0:local-lvm:raw:
#qmdump#map:scsi0:drive-scsi0:local-lvm:raw:
#qmdump#map:scsi1:drive-scsi1:ssd1tb:qcow2:
#qmdump#map:tpmstate0:drive-tpmstate0-backup:local-lvm:raw:
INFO: backup is sparse: 920.36 GiB (92%) total zero data
INFO: Finished Backup of VM 202 (00:21:18)
```

Answer: the archive used was `/mnt/pve/nas-backup/dump/vzdump-qemu-202-2026_10_02-02_30_08.vma.zst` (83.6 GB, last night's job). It holds all four drives, including the 900 GB `scsi1` and the vTPM state. No new backup was needed.

## 2. Scratch VMID, space and memory

Command: `qm list` on both nodes; `pvesh get /cluster/nextid --vmid 9202`

```
used VMIDs: 100-107, 110, 201, 202, 250
9202
```

Command: `pvesm status` and `free -m` on proxmox-b

```
ssd1tb     dir     active  960302096  3253820  908193852  0.34%
local-lvm  lvmthin active  148443136 82786736   65656399 55.77%
Mem: total 31871 used 27002 free 491 available 4869
```

Answer: VMID 9202 was free. `ssd1tb` had 866 GiB free, so the restore went there. proxmox-b had 4.8 GB of memory available, so the scratch VM got 2048 MB.

Baseline of VM 202: `qm config 202 > /root/qm202-before-2026-10-02.txt`, sha256 `96fe45489424be8212edd09b5188072c95ebecde7bf1b816414db9c9cfe76d9f`.

## 3. Restore

Command (12:57:31 PDT): `qmrestore /mnt/pve/nas-backup/dump/vzdump-qemu-202-2026_10_02-02_30_08.vma.zst 9202 --unique 1 --storage ssd1tb` (no `--start`)

```
new volume ID is 'ssd1tb:9202/vm-9202-disk-0.raw'
new volume ID is 'ssd1tb:9202/vm-9202-disk-1.raw'
new volume ID is 'ssd1tb:9202/vm-9202-disk-2.qcow2'
new volume ID is 'ssd1tb:9202/vm-9202-disk-3.raw'
total bytes read 1073746608128, sparse bytes 988232437760 (92%)
VM 9202 (efidisk0): size of disk 'ssd1tb:9202/vm-9202-disk-0.raw' updated from 4M to 528K
real 19m27.142s
```

Answer: the restore took 19 min 27 s (log closed 13:16:58). It read about 80 GiB of real data from the NAS, which with the NAS mount on proxmox-b's 1 GbE path is the floor for any full restore of normandy. All four volumes were created as new `vm-9202-*` volumes on `ssd1tb`; the 900 GB `scsi1` was restored as a sparse qcow2. `--unique` gave new MAC addresses (`BC:24:11:D7:5B:21`, `BC:24:11:CC:B3:B4`), a new smbios UUID and a new vmgenid. The EFI vars disk was resized from 4M to 528K, which is normal for a raw efidisk on a directory storage.

## 4. Making the scratch VM safe before start

Command: `qm config 9202` (after restore)

```
hostpci0: 0000:00:02.0,pcie=1
memory: 24576
onboot: 1
net0: virtio=BC:24:11:D7:5B:21,bridge=vmbr1,tag=60
net1: virtio=BC:24:11:CC:B3:B4,bridge=vmbr1,tag=70
efidisk0: ssd1tb:9202/vm-9202-disk-0.raw,...
scsi0: ssd1tb:9202/vm-9202-disk-1.raw,...
scsi1: ssd1tb:9202/vm-9202-disk-2.qcow2,...
tpmstate0: ssd1tb:9202/vm-9202-disk-3.raw,size=4M,version=v2.0
```

Answer: every disk line referenced a `vm-9202-*` volume. None pointed at VM 202's volumes, and there were no `unused` entries, so no disk line had to be removed. The passthrough line and the production NIC settings came through from the backup and had to be changed.

Command:

```
qm set 9202 --delete hostpci0
qm set 9202 --net0 virtio=BC:24:11:D7:5B:21,bridge=vmbr1,tag=60,link_down=1 \
  --net1 virtio=BC:24:11:CC:B3:B4,bridge=vmbr1,tag=70,link_down=1 \
  --onboot 0 --memory 2048 --balloon 0 --name normandy-restoretest
```

```
balloon: 0
memory: 2048
name: normandy-restoretest
net0: virtio=BC:24:11:D7:5B:21,bridge=vmbr1,link_down=1,tag=60
net1: virtio=BC:24:11:CC:B3:B4,bridge=vmbr1,link_down=1,tag=70
onboot: 0
(no hostpci line)
tpmstate0: ssd1tb:9202/vm-9202-disk-3.raw,size=4M,version=v2.0
```

Answer: the scratch VM had no PCI passthrough, both NICs had their link down, and it was set not to start on boot. It had 2048 MB of memory with no balloon, and the vTPM was kept. `sha256sum` of `qm config 202` was still `96fe4548...76d9f`.

## 5. Boot without the GPU

Command (13:19:35 PDT): `qm start 9202`; then `qm guest cmd 9202 ping` every 6 s

```
swtpm_setup: Not overwriting existing state file.
rc=0
agent ping OK at 2026-10-02T13:20:00-07:00
qmpstatus: running   running-machine: pc-q35-11.0+pve0
```

Command: `qm guest cmd 9202 get-host-name`, `get-osinfo`; `qm guest exec 9202 -- ...`

```
"host-name" : "omarchy-normandy"
"pretty-name" : "Omarchy", "kernel-release" : "7.2.5-3-omarchy"
00:01.0 VGA compatible controller: Red Hat, Inc. Virtio 1.0 GPU (rev 01)
/mnt/data /dev/sdb1 ext4 rw,relatime
ens18 DOWN NO-CARRIER   ens19 DOWN NO-CARRIER
/dev/tpm0 /dev/tpmrm0
```

About 85 s after start: `systemctl is-system-running; systemctl --failed; docker ps`

```
degraded
mnt-backup.mount   failed
mnt-nas-ml.mount   failed
mnt-shared.mount   failed
cloudflared              Up 22 seconds
immich_machine_learning  Up 55 seconds (healthy)
immich_postgres          Up 55 seconds (healthy)
immich_redis             Up 57 seconds (healthy)
/dev/mapper/root  98G  52G  44G  55% /
/dev/sdb1        885G 1.7G 884G   1% /mnt/data
```

Answer: the restored guest boots without the UHD 630, using only the virtio GPU. The proof is the guest agent answering 23 s after `qm start` and `systemctl` reaching `degraded`. The only failed units are the three NFS mounts, which fail because the NICs are down, as expected. The root filesystem (`/dev/mapper/root`, so the guest's LUKS or LVM root unlocked with the restored vTPM), `/mnt/data` with its 1.7 GB, and the Immich containers all came back. Frigate was not in `docker ps` at 85 s; with 2 GB of memory, no network and no GPU that is expected and was not pursued.

The restored guest starts `cloudflared` with production tunnel credentials. With the link up it would have joined the production tunnel as a second connector. If the guest sets fixed addresses itself (the Proxmox config only says `ip=dhcp`; the guest's network configuration was not inspected), they would also have collided with normandy's on VLAN 60 and 70. `link_down=1` is therefore a required step of any restore test of this VM, not an optional one.

## 6. Teardown and VM 202 check

Command (13:21:08 PDT): `qm stop 9202`; `qm destroy 9202 --purge 1 --destroy-unreferenced-disks 1`

```
stop rc=0
purging VM 9202 from related configurations..
destroy rc=0
```

Command: `qm list`; `ls /mnt/ssd1tb/images/`; `pvesm status --storage ssd1tb`

```
202 omarchy running
/mnt/ssd1tb/images/: 202
ssd1tb  dir  active  960302096  3253884  908193788  0.34%
```

Command: `qm config 202 > /root/qm202-after-2026-10-02.txt; sha256sum; diff`; `qm status 202 --verbose`; `lvs pve`; `ls -l /mnt/ssd1tb/images/202/`

```
before 96fe45489424be8212edd09b5188072c95ebecde7bf1b816414db9c9cfe76d9f
after  96fe45489424be8212edd09b5188072c95ebecde7bf1b816414db9c9cfe76d9f
IDENTICAL
status: running  uptime: 2731540
vm-202-disk-0 4.00m  vm-202-disk-1 100.00g  vm-202-disk-2 4.00m
vm-202-disk-0.qcow2 966515359744
```

Answer: VM 9202 and all its volumes are gone, and `ssd1tb` is back to 3253884 KiB used. VM 202 kept running throughout: its uptime of 2731540 s, about 31.6 days, was not reset. Its config is byte-identical before and after, and its volumes are unchanged.

The full `qm config 202` both before and after (byte-identical, sha256 above; the `sshkeys` value is the laptop's public key, shortened here):

```
agent: enabled=1
balloon: 0
bios: ovmf
boot: order=scsi0
ciupgrade: 0
ciuser: igor
cores: 4
cpu: host
efidisk0: local-lvm:vm-202-disk-0,efitype=4m,pre-enrolled-keys=0,size=4M
hostpci0: 0000:00:02.0,pcie=1
ipconfig0: ip=dhcp
ipconfig1: ip=dhcp
machine: q35
memory: 24576
meta: creation-qemu=9.2.0,ctime=1788081705
name: omarchy
net0: virtio=BC:24:11:E2:A5:8E,bridge=vmbr1,tag=60
net1: virtio=BC:24:11:7F:D2:9F,bridge=vmbr1,tag=70
onboot: 1
ostype: l26
scsi0: local-lvm:vm-202-disk-1,discard=on,iothread=1,size=100G,ssd=1
scsi1: ssd1tb:202/vm-202-disk-0.qcow2,discard=on,iothread=1,size=900G,ssd=1
scsihw: virtio-scsi-single
smbios1: uuid=f82002cd-473f-49a4-bbce-94010784daa2
sockets: 1
sshkeys: ssh-ed25519 AAAA...M20i1 igor@omarchy-macbook-air-igor
tablet: 1
tpmstate0: local-lvm:vm-202-disk-2,size=4M,version=v2.0
vga: virtio,memory=256
vmgenid: c594abca-6ffc-4d70-9436-83862ec41908
```

The working files on proxmox-b were removed after their content was recorded here (the restore start time 12:57:31 and the decisive `qmrestore` log lines are in section 3): `rm /root/qm202-before-2026-10-02.txt /root/qm202-after-2026-10-02.txt /root/restore9202.log /root/restore9202.start`.

## Timings

| Step | Time |
|---|---|
| `qmrestore` of 83.6 GB archive (1000 GiB logical, about 80 GiB real) from NAS to `ssd1tb` | 19 min 27 s |
| `qm start` to guest agent ping | 23 s (start at 13:19:37, ping at 13:20:00) |
| `qm start` to Immich containers healthy | under 85 s |
| `qm stop` plus `qm destroy` | 3 s |

## `backup=0` restore behaviour (review item 16)

This test could not observe it, because VM 202 has no `backup=0` disk (see the mismatches above). What was observed: every drive in the archive's `#qmdump#map` lines was recreated as a new volume of the scratch VMID, and nothing referred to the source VM's volumes. Proxmox's documented behaviour is that a drive marked `backup=0` is left out of the archive's map. On restore to a new VMID that drive's line then has no volume. On restore over the same VMID, `qmrestore` keeps the existing volume as an `unused` disk. That is not measured here and remains unverified. It matters for VM 102, whose 1.8 TB `scsi1` is `backup=0`.

## Implications for later phases

- Restore time for normandy from the NAS is about 20 minutes, set by reading about 80 GiB over proxmox-b's link to the NAS. Use 20 to 25 minutes in any rollback budget that involves restoring VM 202.
- Phase 2, step 3: normandy's `/mnt/data` disk is in today's backups, not `backup=0` as the plan assumed. Before the point of no return there are therefore two copies of its content, the planned NAS copy and last night's vzdump.
- Any later restore test of VM 202 must set `link_down=1` on both NICs before start, because the guest brings up `cloudflared` with production credentials and fixed addresses.
- The `backup=0` restore question moves to VM 102. If it matters for phase 2's rollback, test it there with the same safety steps.
