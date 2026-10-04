# Phase 7: drain, power-off and return of proxmox-b

Date: 2026-10-03, 14:45 to 15:20 PDT. Executor E28, on the lead's go. Branch
`transition/phase-7` (from main 2b141ff). Hosts: proxmox-b (hp-workstation-01) drained and powered
off; proxmox-a, homelab, the edges, OCI and Home Assistant read. Runbook:
`runbook-host-maintenance.md`.

Presence: Igor and Lisa were home. Igor decided at about 13:35 to run phase 7 now, overriding the
presence check; the drain ran with `-e allow_home=true` for that reason. Emby showed no playback.

## Preconditions (14:45)

- Unlock key: `sops -d --extract '["swarm_unlock_key"]' secrets/swarm.sops.yaml` decrypts on the
  laptop.
- `docker node ls`: OCI Drain/Reachable, homelab Active/Leader, normandy Active/Reachable; both
  workers `LocalNodeState active`. `ping 10.42.0.1` answers from homelab and normandy.
- proxmox-a `pvecm status`: Quorate, Total votes 3, QDevice `State: Connected`.
- Gatus: no failing endpoint on either instance. VIP on public-api-b, keepalived active on both.
- Hourly snapshots run on both workers (14:45:39 to 14:46:41, `Result=success`). Last vzdump on
  proxmox-b: OK, 02:30.

## Drain and power-off

`ansible-playbook playbooks/host-drain.yml -e host=proxmox-b -e allow_home=true -e power_off=true`,
14:47:03 to 14:51:59, rc 0, `ok=46 changed=6`.

- Plan printed: worker normandy to homelab, edge public-api-b to public-api, 11795 MiB available on
  homelab against 3872 MiB that moves there; no Swarm move (nothing is pinned to normandy); Compose
  move immich.
- VIP: keepalived stopped on public-api-b; public-api took 192.168.20.3.
- Immich moved to homelab by `compose-move.yml` (live check equal: assets 21340, users 6, albums 44).
- normandy drained; frigate, gatus and cloudflared stopped.
- Every household route answered through the VIP except cctv (Frigate, the accepted exception);
  `vault.ryzhkov.dev` resolved to the VIP on homelab; no unexpected Gatus failure.
- Guests shut down: agent-b, normandy (VM 202), public-api-b, CT 201. proxmox-b left the network;
  proxmox-a stayed quorate.

## Host down (14:52 to 15:02)

- proxmox-a: `Expected votes 3`, `Total votes 2`, `Flags: Quorate Qdevice` at 14:52 and 15:01.
- Swarm: normandy `Down Drain Unreachable`; homelab Leader; OCI Reachable.
- homelab's Gatus: only normandy-side checks failing (adguard-ct201, edge public-api-b, gatus on
  normandy, the steward coordinator and workers). The public Gatus (uptime.ryzhkov.dev): none failing.
- The steward coordinator checks failed: the coordinator runs on normandy, and it is agent
  infrastructure, not household (recorded as expected, lead's ruling).
- **Finding: voice.ryzhkov.dev** failed in about one round of two (92 of 391 samples, 000) for the
  whole outage: its route had `health_check: false`, so Traefik kept sending half the requests to
  normandy. Fixed after the return (below).

## Return

- Power: neither node had been woken from power-off before. Wake-on-LAN works on proxmox-b: a magic
  packet to `9c:7b:ef:a9:e3:d4` (MAC from the UDM's ARP table), sent with Python from proxmox-a to
  192.168.1.255 ports 9 and 7 at 15:01:58; ping answered at 15:02:40.
- `host-return.yml -e host=proxmox-b`, first run 15:02:43: refused at the mount-order check,
  `['/mnt/data', '/mnt/data', '7029347', '0']`: Docker had not finished starting (its
  `ActiveEnterTimestampMonotonic` read 0). A few seconds later: mnt-data.mount 7.03 s, containerd
  8.13 s, docker 10.18 s after boot. The playbook now waits for Docker before comparing. Second run
  15:03:20 to 15:06:55, rc 0, `ok=26 changed=4`.
- tank: `zfs-import@tank` "Finished" at 15:02:29; `pool 'tank' is healthy`, mounted. **Boot import
  proven** on proxmox-b.
- `/mnt/data` mounted before containerd and Docker (above).
- Guests: 108, 202, 211 running, CT 201 running. Quorate, 3 votes.
- Swarm: normandy came back locked (dockerd: "Swarm is encrypted and needs to be unlocked"); the
  nodes play unlocked it; `Ready Active Reachable`.
- Compose: frigate, gatus, cloudflared up; Immich moved home by `compose-move.yml` (placement file
  removed), its four containers healthy.
- keepalived active on both edges; the VIP stays on public-api (`nopreempt`).
- Both Gatus instances (75 and 80 endpoints) and the public one (16): none failing.

## Per-service unavailability

From a probe of 36 routes through the VIP every 2 s (391 rounds, 14:46:53 to 15:07:13), and a
probe of photos.ryzhkov.dev through Cloudflare every 0.5 s. A failure is any answer but 2xx, 3xx or
401.

| Service | Unavailable | Cause |
|---|---|---|
| cctv (Frigate) | 14:50:29 to 15:04:18, 13 min 49 s | accepted exception: stopped by the drain, back with normandy |
| photos (Immich) | 14:49:31 to 14:49:47 (16 s) and 15:05:51 to 15:06:13 (22 s) | the move to homelab and back; through Cloudflare the same windows, plus 4 isolated failures |
| voice | 92 failed samples between 14:51:06 and 15:03:34 | no health check on its route (fixed) |
| speed | 14:50:15 to 14:50:25, 10 s | drain rescheduling, image health check |
| wiki | 14:50:15 to 14:50:23, 8 s | drain rescheduling |
| draw, uptime, dns-sync | 3 to 5 s each, 14:50:15 to 14:50:23 | drain rescheduling |
| 16 routes (ai, autobrr, bazarr, budget, garage, igor, irc, logs, obsidian, ov, prowlarr, radarr, rss, sonarr, speed, vault) | one sample each at 14:51:00 or 14:51:06 (000) | a single round failing on every route at once while public-api-b and normandy shut down; not reproduced |
| every other route (auth, blog, emby, git, home, jocasta, lidarr, manga, music, request, torrent, zigbee) | none | |

Agents: agent-b was down 14:51 to about 15:04; the steward coordinator and normandy's steward worker
with normandy.

## Fixes made

- `playbooks/host-return.yml`: the mount-order check waits up to five minutes for Docker to be
  active.
- `inventory/group_vars/swarm/stacks-s2.yml`: voice's route checks `/` (200 without a login on both
  workers). It was the only Swarm route without a health check (every service in the edges'
  `generated.yml` checked). `traefik.yml` 15:13:52, `changed=1` on each edge (file-provider reload);
  Traefik's API then shows `http://192.168.70.223:8097` and `http://192.168.70.234:8097` both `UP` on
  both edges, and no backend of any service other than UP. `site.yml -l homelab,normandy -e
  only=gatus` converged the matching Gatus change.

## Pause and check (15:08 to 15:16)

Both Gatus instances and the public one: none failing. `site.yml --check`: changed=0 on all five
Docker hosts; `swarm.yml --check`: changed=0 on homelab, normandy, oci; `backup.yml --check`:
changed=0 everywhere. Clean; continued to proxmox-a.
