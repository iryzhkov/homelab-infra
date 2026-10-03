# Phase 3 window: black-pearl (VM 102) stop and start

Date: 2026-10-02, 17:28 to 17:38 PDT.
Hosts: proxmox-a, black-pearl (VM 102); homelab used only for the download-client test.
Authority: Igor's go for this window, given 2026-10-02 in the lead session through the question tool.
Procedure: `runbook-blackpearl.md`, run from commit `e825323` with a clean working tree.

## Outage

| Window | Start | End | Length |
|---|---|---|---|
| Torrent service, from stop to start | 17:29:09 | 17:33:01 | 3 min 52 s |
| Interruptions inside it | | | |
| Docker stopped during the base converge | about 17:29:25 | 17:29:43 | |
| gluetun recreated by the site converge | about 17:31:34 | 17:31:36 | |
| VM shutdown, boot and Docker start | 17:31:39 | 17:32:12 | |
| gluetun healthy and port forward synced | | 17:32:55 | |

## Decision recorded

Igor accepted the qBittorrent WebUI authentication bypass for `192.168.20.0/24` (`WebUI\AuthSubnetWhitelist`), which `2026-10-02-blackpearl-prep.md` reported. It stays as it is, as an accepted risk. Requests that reach the WebUI through Traefik on public-api need no login.

## Before starting

No other service-affecting work was running at 17:28:35:

- No `ansible-playbook` process ran on the laptop.
- The last cluster tasks were finished `push_file`/`pull_file` tasks on CT 105, and the VM 9900 destroy at 14:09.
- Normandy's window had not started; the lead confirmed this.

## Steps

| Time | Step | Command | Decisive output | Result |
|---|---|---|---|---|
| 17:28:47 | 1.2 pending | `qm pending 102`, lines other than `cur` | none | pass |
| 17:28:47 | 1.3 config | `qm config 102`, grep for memory, cores, balloon and hotplug | `cores: 3`, `memory: 8192`, no `balloon` line, no `hotplug` line | as expected; rollback value is no balloon key |
| 17:28:47 | 1.4 and 1.5 | `findmnt`, `systemctl is-active docker`, `docker ps`, `stat -f` | NAS mounted, Docker active, gluetun healthy, all four containers up, `nas-ok` | pass |
| 17:28:49 | 1.7 stopped torrents | `torrents/info?filter=stopped` | `[]`; 1261 torrents: 1246 stalledUP, 13 uploading, 2 downloading | none stopped beforehand; VPN 188.214.122.90, port 46561 |
| 17:28:51 to 17:29:02 | 1.6 dry runs | `base.yml -l blackpearl --check --diff`; `site.yml -l blackpearl --check --diff` | changed=2 (`/mnt/shared`, `/mnt/apps`); changed=1 (Compose diff, the subnets line only) | exactly as expected |
| 17:29:04 | 2 pending change | `qm set 102 --memory 2048 --balloon 0 --cores 2 && qm pending 102` | `new balloon: 0`, `new cores: 2`, `new memory: 2048`; current values unchanged | pending, not live |
| 17:29:09 | 3 stop torrents | `POST torrents/stop hashes=all` | 1261 torrents: 1259 stoppedUP, 2 stoppedDL | pass |
| 17:29:17 to 17:29:38 | 4 base converge | `ansible-playbook playbooks/base.yml -l blackpearl --diff` | changed=3, failed=0; Docker `inactive` afterwards | as expected |
| 17:29:41 | 4 mount check | `findmnt --mountpoint` for each path (see deviation 1) | both mounts `rw,nosuid,nodev,noexec,...`, `/mnt/shared` `hard`, `/mnt/apps` `soft`; both fstab lines carry the new options | pass |
| 17:29:43 to 17:30:06 | 4 start Docker | `sudo systemctl start docker` | four containers up, gluetun healthy | pass |
| 17:30:08 to 17:31:36 | 4 site converge | `ansible-playbook playbooks/site.yml -l blackpearl --diff` | changed=2 (Compose file, then `Bring the stack up`), failed=0; gluetun, gt-qb and the port manager recreated, diun not recreated | as expected |
| 17:31:36 | 4 firewall | `iptables -S OUTPUT` in gluetun | only `lo`, `RELATED,ESTABLISHED`, `172.16.2.2 -> 172.16.2.0/24`, WireGuard endpoint `149.50.211.169 udp 51820`, `-o tun0` | all four subnets gone |
| 17:31:39 to 17:31:56 | 5 shutdown | `time qm shutdown 102 --timeout 180` | real 17.3 s; `status: stopped` | pass |
| 17:31:56 to 17:31:59 | 5 start | `qm start 102`; `qm pending` lines starting `new` or `del`; `qm config 102 --current` | start took 1.3 s; nothing pending; `balloon: 0`, `cores: 2`, `memory: 2048` | applied |
| 17:32:15 | 6.1 boot | SSH, `systemctl is-system-running`, `--failed`, `lsblk`, `journalctl -b -p err` (also with sudo) | SSH up 16 s after start; `running`; no failed units; only `sda` and `sr0`; no error entries | clean boot without the old disk, no emergency mode |
| 17:32:15 | 6.2 resources | `free -m`, `nproc` | total 1966, used 658, available 1308; swap 0 used; 2 CPUs | pass |
| 17:32:17 | 6.3 mounts | `findmnt --mountpoint /mnt/shared`; `stat /mnt/apps/blackpearl` then `findmnt` | `/mnt/shared` nfs `hard`, `nosuid,nodev,noexec`; `/mnt/apps` mounts on access, `soft`, `nosuid,nodev,noexec` | pass |
| 17:32:17 | 6.4 Docker after mount | `systemctl show -p ActiveEnterTimestamp` | `mnt-shared.mount` 17:32:10, `docker.service` 17:32:12 | Docker started after the mount |
| 17:32:55 | 6.4 health | `docker ps` | `torrent-gluetun-1 Up 43 seconds (healthy)` | pass |
| 17:32:55 | 6.5 VPN IP | `/v1/publicip/ip`; `wget ipinfo.io/ip` in gluetun | both `159.26.99.136` (California) | not the WAN IP 104.8.3.194; pass |
| 17:32:56 | 6.6 port forward | `/v1/portforward`; port manager log; qBittorrent `listen_port` | `{"port":46268}`; `successfully updated qBittorrent to port 46268 (confirmed)`; `listen_port 46268` | renewed (new port); pass |
| 17:32:59 | 6.7 *arr test | `POST /api/v3/downloadclient/testall` (Sonarr, Radarr), `/api/v1/...` (Lidarr), from homelab | all three `[{"id":1,"isValid":true,"validationFailures":[]}]` | WebUI reachable inbound without the subnets; pass |
| 17:33:01 | 6.8 start torrents | `POST torrents/start hashes=all` | sent | step 1.7 had recorded none stopped beforehand, so `all` was correct |
| 17:33:13 to 17:35:25 | 6.9 and acceptance | `site.yml --check` and `base.yml --check` on all hosts | changed=0 and failed=0 on blackpearl, homelab, normandy and public-api (site), and on blackpearl, homelab, normandy, proxmox-a, proxmox-b and public-api (base) | pass |
| 17:38:05 | 6.8 after 5 minutes | `torrents/info`; `transfer/info` | 1261 torrents: 1252 stalledUP, 7 uploading, 2 downloading; none in error or missingFiles; `connected` | torrents resumed; pass |

No stop condition triggered and no rollback was needed.

## Deviations from the runbook

1. The step 4 mount check in the runbook read `findmnt -o ... /mnt/shared /mnt/apps`. findmnt reads two arguments as a source and target pair, so it printed nothing. I checked each mount with `findmnt --mountpoint` instead, and corrected the command in `runbook-blackpearl.md`. The result did not depend on this.
2. The torrent count was 1261 rather than the survey's 1237, because new torrents arrived during the afternoon. Two were downloading, so step 3 left 2 in `stoppedDL`, which the runbook allowed for.
3. To wait for gluetun's health, I polled until a health state was reported rather than waiting a fixed minute. It turned healthy 43 s after the boot.
4. The VPN server changed at the restart. The endpoint was 188.214.122.82 before and is 149.50.211.169 now, and the exit IP moved from 188.214.122.90 (Bucharest) to 159.26.99.136 (California). The forwarded port moved from 46561 to 46268. This is the expected ProtonVPN behaviour on reconnect, and both the port manager and qBittorrent followed it.

## Watch afterwards

- 2026-10-03 at 01:00 and 01:10: the cron copies through `/mnt/apps`, now mounted `noexec`. Check the files under `/mnt/backup/blackpearl` on homelab.
- Next homelab bulk backup of black-pearl (Monday 2026-10-05, 06:00): `bulk-blackpearl.last-run` must show `exit=0`.
- For a week: `free -m` and swap on black-pearl at 2 GB. At boot, 658 MB was used, with no swap in use.
