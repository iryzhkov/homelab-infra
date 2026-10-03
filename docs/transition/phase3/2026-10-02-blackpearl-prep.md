# Phase 3 preparation: black-pearl (VM 102 on proxmox-a)

Date: 2026-10-02, 14:49 to 15:00 PDT.
Hosts: proxmox-a, black-pearl (VM 102), homelab (VM 101, read-only), normandy (read-only).
Scope: read-only survey and repository changes for black-pearl's stop-and-start window. Nothing was converged and no host was changed.

## Checkpoint (paused for quota)

Done:

- Survey complete (results below).
- Repository changes made, not converged: `inventory/host_vars/blackpearl.yml` (`nosuid,nodev,noexec` on `/mnt/shared`; `/mnt/apps` taken under `nfs_mounts` with its existing options plus `nosuid,nodev,noexec`, instead of being retired) and `stacks/torrent/docker-compose.yml` (`FIREWALL_OUTBOUND_SUBNETS` removed).
- Check runs at 14:54 to 14:58 PDT. `site.yml --check`: blackpearl changed=1 (the torrent Compose file); homelab, normandy and public-api changed=0. `base.yml --check`: blackpearl changed=2 (the fstab write and the mount task, items `/mnt/shared` and `/mnt/apps`); homelab, normandy, proxmox-a, proxmox-b and public-api changed=0.

Resumed after the pause: `docs/transition/phase3/runbook-blackpearl.md` is written. It records the following decisions:
  - qBittorrent is v5.1.4, so pause and resume use `/api/v2/torrents/stop` and `/api/v2/torrents/start`.
  - The *arr test is `POST /api/v3/downloadclient/testall` (`/api/v1` for Lidarr).
  - The base converge stops docker.service and does not start it again, so `systemctl start docker` has to run before the site converge.
  - Planned order: `qm set 102 --memory 2048 --balloon 0 --cores 2` as a pending change, stop torrents, base, start docker, site, `qm shutdown 102`, `qm start 102`, post-checks, start torrents.
- Optionally, read gluetun's dropped-packet counters further.

## Survey

| Check | Command | Decisive output | Answer |
|---|---|---|---|
| VM config | `qm config 102` | `memory: 8192`, `cores: 3`, no `balloon` key, only `scsi0` (32G, `discard=on,ssd=1`), `ide2` ISO, `net0` tag 60 | no balloon key means the balloon device is present at its default (minimum equal to memory, so no ballooning); `--balloon 0` removes the device |
| Pending | `qm pending 102` | every line `cur`, no `new` or `del` | nothing pending |
| Guest memory | `free -m` | total 7940, used 1184, buff/cache 2592, available 6756; swap 974, 76 used; memory PSI 0.00 | 1.2 GB used, which matches the audit's 1.0 GB; 2 GB leaves about 800 MB plus swap |
| RRD peak | `pvesh get .../qemu/102/rrddata --cf MAX` | month and year maxima of 23.3 GiB (impossible on 8 GiB, the audit's artefact); plausible maxima 7.6 to 7.7 GiB; `qm status`: mem 3.65 GB, memhost 7.54 GB | the RRD `mem` series tracks host-side allocation, not guest use; the guest's own `free -m` is the measure |
| fstab and mounts | `cat /etc/fstab`, `findmnt` | `/` and swap by UUID, the CD-ROM line, `/mnt/shared` hard NFS (no nosuid/nodev/noexec), `/mnt/apps` hand-written soft automount of `services_backup`; `findmnt --verify` gives only the old CD-ROM error | safe to boot without the old disk; no `/mnt/tmp` line remains |
| Docker order | drop-in `nfs-mounts.conf` | `RequiresMountsFor=/mnt/shared` | Docker waits for `/mnt/shared` |
| Users of `/mnt/apps` | crontab, `/etc/cron*`, systemd units, container binds, `lsof` | user crontab: `0 1 * * * rsync -a ... /home/iryzhkov/app/ /mnt/apps/blackpearl` (hand-written, no mount guard) and `10 1 * * *` Ansible job (`playbooks/helper-maintenance.yml:127`) syncing `~/apps/` to `/mnt/apps/blackpearl/stack-definitions`; no container bind; no unit; `lsof` empty | used nightly by both cron jobs |
| How backups reach `/mnt/backup/blackpearl` | homelab: `findmnt -T /mnt/backup/blackpearl`; crontab; state files | `/mnt/backup` is `192.168.40.40:/var/nfs/shared/services_backup`; `0 6 * * 1,4 homelab-bulk-backup blackpearl`; last success 2026-10-01T06:01:18 | black-pearl pushes its own data into the export through `/mnt/apps`, and homelab ships it to B2. Retiring `/mnt/apps` would end black-pearl's backups, so it was not retired |
| Port manager target | `/app/main.py` in the port manager | `QBITTORRENT_SERVER` defaults to `localhost:8080`, not set; log: `successfully updated qBittorrent to port 46561 (confirmed)` | loopback inside gluetun's namespace, which gluetun always allows |
| *arr download client | Sonarr, Radarr, Lidarr `GET .../downloadclient` | all `192.168.60.53:8080`, no SSL | inbound to the WebUI |
| gluetun firewall | `iptables-save` in `torrent-gluetun-1` | INPUT: `lo`, `RELATED,ESTABLISHED`, `-d 172.16.2.0/24 -i eth0` ACCEPT; OUTPUT: `RELATED,ESTABLISHED` ACCEPT and one rule per subnet; `ip rule 100: from 172.16.2.2 lookup 200` (default via eth0) | inbound connections are accepted by the eth0 rule, and replies leave through the ESTABLISHED rule and table 200, with no subnet entry needed |
| Proof by traffic | `iptables -L -v` and connections on `:8080` | OUTPUT subnet rules: 0 packets each since 20:46Z; INPUT eth0 rule: 919 packets; established WebUI sessions from 192.168.70.223 and 192.168.60.97 (normandy's VLAN 60 NIC), neither of which is in the list | the outbound subnets are unused |
| Origin of the subnets | `git log -p -S FIREWALL_OUTBOUND_SUBNETS -- stacks/torrent` | added only in `36c94c5` (2026-09-06, Portainer adoption, copied verbatim); no rationale anywhere in the repository | 192.168.1.0/24 is the untagged Internal LAN, which UniFi blocks from VLAN 60 anyway; 192.168.20.0/24 is Public API; 10.0.0.0/16 and 172.21.0.0/16 match no current network. The VPN tunnel address is 10.2.0.2, outside 10.0.0.0/16. Remove all four |
| VPN state | gluetun control server | public IP 188.214.122.90 (WAN 104.8.3.194 per phase 1 review); port forward 46561 | baseline for post-checks |
| Torrents | qBittorrent API | v5.1.4, 1237 torrents: 1224 stalledUP, 13 uploading | baseline |

## Findings for the lead

- qBittorrent's `WebUI\AuthSubnetWhitelist` includes `192.168.20.0/24`, and the `torrent.ryzhkov.dev` Traefik route has no middleware. Requests through Traefik on public-api therefore skip the WebUI login. I could not confirm whether any public hostname points at the route, because DoH from the laptop timed out.
- The phase 1 follow-up to remove black-pearl from the `services_backup` export conflicts with the backup path above. Before that, the backup needs another route, for example homelab pulling over SSH.
- The OUTPUT policy dropped 38089 packets in about 1 hour. Their destination was not identified.
- Possible role change for E15: posix.mount prints no diff, and the nfs_mounts change leaves docker.service stopped.
