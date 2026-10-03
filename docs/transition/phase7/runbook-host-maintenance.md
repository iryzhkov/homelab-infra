# Runbook: taking a Proxmox host down for maintenance

Plan phase 7. Written 2026-10-03 from the phase 5 state (Swarm across homelab and normandy,
OCI as the third manager) and the phase 7 preparation in `2026-10-03-prep.md`, which holds
the measurements this runbook relies on. The playbooks are `playbooks/host-drain.yml` and
`playbooks/host-return.yml`; what lives on each host, the move order and the expected
failures are in `playbooks/vars/host-maintenance.yml`. Later this becomes the automated
drain the requirements document asks for.

## What lives where

| Host | Guest | What it carries | How it leaves |
|---|---|---|---|
| proxmox-a | VM 101 homelab | Swarm worker and manager (Leader today); 20 stateful stacks pinned here; Compose: Emby, dsh, Jocasta, Gatus, cloudflared; `feedd` | stateful stacks move to normandy one at a time (`swarm-move.yml`); stateless tasks move by the drain; `logging` and the Compose stacks stop |
| proxmox-a | VM 102 black-pearl | torrent stack, diun (Compose) | stops; no peer by design |
| proxmox-a | VM 103 homeassistant | Home Assistant, ZBT-2 radio by USB | stops (accepted exception) |
| proxmox-a | CT 105 zigbee2mqtt, CT 104 mqtt | Zigbee coordinator (CP210x by USB), MQTT broker | stop (accepted exception) |
| proxmox-a | VM 106 public-api | edge Traefik, keepalived member (priority 150) | stops; the VIP must be on public-api-b first |
| proxmox-a | CT 100 adguard (origin), CT 107 adguard (replica) | DNS 192.168.20.154 and .106 | stop; CT 201 on proxmox-b (.83) answers alone |
| proxmox-a | CT 110 healthcheck | external checks | stops |
| proxmox-a | VM 111 agent-a | steward agent runs | stops |
| proxmox-b | VM 202 normandy (`omarchy`) | Swarm worker and manager; stateless tasks; Compose: Frigate, Immich (server, ML, Postgres, Redis), Gatus, cloudflared | stateless tasks move by the drain; Compose stacks stop |
| proxmox-b | VM 108 public-api-b | edge Traefik, keepalived member | stops; the VIP must be on public-api first |
| proxmox-b | CT 201 alpine-adguard | DNS replica 192.168.20.83 | stops; CT 100 and CT 107 answer |
| proxmox-b | VM 211 agent-b | steward agent runs | stops |

Classes:

- **Swarm floating** (`gpu=intel` placement): excalidraw, librespeed, kiwix, flaresolverr,
  adguardhome-sync, gatus-public, voice, wyoming-openai, wyoming-piper, whisper, hugo. They
  reschedule by themselves when their node is drained.
- **Swarm pinned stateful** (`pin.<stack>` label): actual, authentik, autobrr, bazarr,
  cv-site, forgejo, komga, lidarr-music, lubelogger, miniflux, navidrome, obsidian, ombi,
  openviking, prowlarr, radarr-movie, sonarr-tv, thelounge, vaultwarden, all on homelab today.
  They do not move on a drain: their tasks would sit pending. Each moves by
  `playbooks/swarm-move.yml` (stop, cold copy, restore, pin flip, start).
- **Swarm, never moves**: `logging` (Grafana, InfluxDB, Loki). Loki publishes 3100 in host mode
  on homelab and every log client names homelab's address. `swarm-move.yml` refuses it.
- **Compose pinned**: Emby, dsh, Jocasta, Gatus and cloudflared on homelab; Frigate, Immich,
  Gatus and cloudflared on normandy; torrent and diun on black-pearl. They stop with their host.
- **LXC**: the AdGuard origin (CT 100) and its replicas (CT 107 on proxmox-a, CT 201 on
  proxmox-b); MQTT, Zigbee2MQTT and the healthcheck on proxmox-a.

## Accepted exceptions

From the plan, confirmed by Igor 2026-10-02:

- proxmox-a down: Home Assistant automations, Zigbee and Thread (USB radios), MQTT.
- proxmox-b down: Frigate (no camera recording or detection).
- Emby and Immich without hardware acceleration while the other host's iGPU is unavailable.
  The plan means them to keep running on the other host. **Today neither move is scripted**
  (see "Compose-pinned moves, not yet scripted" below), so Emby is down while proxmox-a is,
  and Immich is down while proxmox-b is, until that work is done.
- black-pearl (torrents) has no peer and is down while proxmox-a is.

Recorded on 2026-10-03, to be put to Igor as exceptions for today:

- `logging` (Grafana at logs.ryzhkov.dev, InfluxDB, Loki) is down while proxmox-a is. Containers
  log in non-blocking mode, so their log lines are dropped, not queued, and no container stops
  waiting on Loki.
- Jocasta (the agents' working-document store), dsh (`ai.ryzhkov.dev`) and the fleet feed
  (`feedd`) are down while proxmox-a is. dsh is tied to homelab by design (it runs
  `docker compose` on homelab's own projects); Jocasta and the feed are not in a movable form
  yet (prep record).
- OpenViking moves to normandy with the other stateful stacks, but its consumers (dsh, the
  fleet's `ov-memory` clients) name `192.168.70.223:1933`, which only answers while homelab is
  up. Agent memory is therefore unreachable while proxmox-a is down, unless the consumers use
  a name or both worker addresses.
- Literal homelab addresses: the *arr apps call flaresolverr at `192.168.70.223:8191`; Home
  Assistant names Wyoming on `192.168.70.223:10200` and `:10300` and InfluxDB on `:8087`;
  OCI's Caddy proxies `emby.ryzhkov.dev` to `10.42.0.2:8096`. All fail while homelab is down.
- While homelab is down, normandy's hourly snapshot job cannot copy to its peer and reports a
  failure every hour (the feed is down too; the phone gets it). normandy's snapshots stay local.
- `swarm.yml --tags stacks` runs from the bootstrap manager, homelab, and cannot deploy while
  homelab is down. Nothing in the drain needs it.

## Rules

1. **No drain while OCI is unreachable.** OCI is the third Swarm manager, the Proxmox QDevice
   and the Nebula lighthouse. With one host down, the Swarm keeps quorum only with OCI
   (two of three managers), and so does the Proxmox cluster (one node plus the QDevice). The
   drain refuses unless `docker node ls` shows all three managers `Ready` and `Leader` or
   `Reachable`, `ping 10.42.0.1` answers from both workers, and `pvecm status` shows
   `Quorate`, three votes, and the QDevice `Connected`.
2. **Unlock after every manager restart.** Autolock is on. homelab or normandy comes back from
   a power cycle `locked`: its Compose containers start, its Swarm tasks do not, and it holds
   no raft vote. `host-return.yml` runs `ansible-playbook playbooks/swarm.yml --tags nodes -l
   <worker>`, which unlocks it with `swarm_unlock_key` from `secrets/swarm.sops.yaml`. By
   hand, if the laptop is unavailable: `sops -d --extract '["swarm_unlock_key"]'
   secrets/swarm.sops.yaml` on a machine with the age key, then `docker swarm unlock` on the
   worker. Do this before anything else touches the cluster.
3. **One manager at a time.** Never take homelab, normandy and OCI down, upgrade them or
   change their Nebula configuration together. One host per maintenance window; return it
   fully (unlocked, undrained, stacks home) before the other.
4. **One service-affecting step at a time, fleet-wide.** The lead serialises; the playbooks
   move one stack at a time.
5. **Nobody home.** The plan powers a host off when nobody is home. `host-drain.yml` refuses
   while Home Assistant shows a person at home or Emby shows playback (see "Presence
   pre-check"). `-e allow_home=true` and `-e allow_playing=true` override, only on Igor's word.
6. **No `swarm.yml --tags stacks` converge while a stateful stack sits away from its inventory
   pin**, until the prepare-file fix is confirmed (2026-10-03: miniflux's and Authentik's
   `swarm-prepare.yml` read the node from the inventory pin rather than the node label, so a
   converge would recreate an empty state directory on the old node and abort; E26 is
   fixing them). The drain and return playbooks never run that converge while stacks are
   away, except `-e shed=` restores after every stack is home.

## Presence pre-check

There is no Home Assistant API token, and the SSH add-on's supervisor token gets 401 from the
core API. The reliable read is Home Assistant's own state file over its SSH host:
`/config/.storage/core.restore_state`, which Home Assistant rewrites every 15 minutes and which
holds every `person.*` entity. The drain reads it with `jq` over `ssh home-assistant` and
refuses when any person is `home`, or when the file is older than 30 minutes or unreadable
(presence unknown).

Media players (the Sony TV, Android TV) are not restorable entities and are not in that
file; the recorder database (`home-assistant_v2.db`, 1.6 GB) cannot be read there (no
`sqlite3` or Python on the busybox host). This is the gap: a TV playing something without
Emby is not seen. What is seen instead is Emby's own activity log
(`/home/iryzhkov/app/emby/data/activitylog.db`, read-only on homelab): a user whose last event
is `playback.start` within four hours counts as playing. That also covers remote Emby users,
who are affected by a proxmox-a drain whether or not anyone is home.

In check mode both conditions are reported but do not stop the dry run.

## Drain: `ansible-playbook playbooks/host-drain.yml -e host=<proxmox-a|proxmox-b>`

Run it with `--check` first; every read-only check runs and each stack move runs
`swarm-move.yml --check`. Without `-e power_off=true` the playbook stops after step 7: that
is the drain-only rehearsal (plan phase 7 step 1); undo it with `host-return.yml`.

| Step | What happens | Rollback if it fails or is stopped here |
|---|---|---|
| 1. Pre-checks | Host known; only listed guests run; managers and OCI; Proxmox quorum and QDevice; both Gatus instances green; last vzdump OK and under 26 h; presence and Emby; reservations of what moves fit on the surviving worker with 2 GiB to spare | Nothing changed. Fix the cause; never override the OCI or quorum checks. |
| 2. VIP | If the leaving host's edge holds 192.168.20.3, `keepalived` is stopped there and the playbook waits for the survivor to take it (phase 4 measured 1.34 s). `nopreempt` keeps it there afterwards. | `systemctl start keepalived` on the stopped edge (`host-return.yml` does it); the VIP stays where it is. |
| 3. Shed | Only with `-e shed=<stack,...>`: those stacks scaled to 0. Not needed today: normandy has 11.6 GiB available against 4.7 GiB reserved by everything that moves there. Candidates, cheapest first: whisper (1.5 GiB limit), komga, flaresolverr, wyoming-piper, librespeed, kiwix. | `ansible-playbook playbooks/swarm.yml --tags stacks -e only=<stack>` per shed stack (the return does it with the same `-e shed=`). |
| 4. Moves | `swarm-move.yml -e stack=<s> -e to=<survivor worker>` per stateful stack pinned to the leaving worker: leaf stacks in name order, then authentik, miniflux, vaultwarden, forgejo last (the image mirror). Each is a stop, a cold copy, a restore, a pin flip, a start, probes, and a tidy of obsolete copies. About 40 s each, of which about 20 s is the route failing; 19 stacks take about 13 minutes. | A move that fails before its pin flip leaves the stack stopped on the old node: `docker service scale <svc>=<n>` on it restores it (the counts are in `/srv/swarm/<stack>/.swarm-replicas`). After the flip: move it back with `swarm-move.yml -e to=<old node>`. Moves already done stay valid; continue or run `host-return.yml`. |
| 5. Drain | `docker node update --availability drain <worker>`; wait until it runs no Swarm task and every service except `logging` and the shed has a running task. hugo runs one of two replicas (one per node). | `docker node update --availability active <worker>`. |
| 6. Compose | `docker stop -t 60` on every container of the host's Compose projects (normandy's Omarchy `DefaultTimeoutStopSec=5s` would otherwise kill them at shutdown). | `docker start` on them (`host-return.yml` step 7). |
| 7. Verify | Every household route through the VIP answers 2xx, 3xx or 401, except those that go down with this host by design; `vault.ryzhkov.dev` resolves to the VIP from the surviving worker (a VLAN 70 client); Gatus on the survivor fails only where `host_maintenance_gatus_expected` allows. | Investigate before powering off; `host-return.yml` undoes steps 2 to 6. |
| 8. Power off (`-e power_off=true`) | Guests shut down in the listed order (`qm shutdown --timeout 300`, `pct shutdown --timeout 120`); refuse if any still runs; `systemd-run --on-active=10 /sbin/shutdown -h now`; wait until the host leaves the network; check the survivor is still quorate. | Power the host on; `host-return.yml`. |

DNS during a proxmox-a outage: DHCP advertises 192.168.20.154, .106 and .83 on every VLAN
except 60. With CT 100 and CT 107 down, clients fall back to .83 after their first query to
.154 times out; expect slow first lookups on some clients.

## Host down

Record, for each service, when it stopped answering and when it answered again (the drain's
step 7 lists the planned ones). With one node and the QDevice, the cluster stays quorate.

**Emergency, the survivor loses quorum** (OCI or Nebula fails while one host is down):
Proxmox makes `/etc/pve` read-only and no guest can be started or changed. On the survivor:
`pvecm expected 1`. This is an emergency step only: it lets one node act alone, and must not be
left in place; the value returns to normal when the other node rejoins. The Swarm
counterpart, if the survivor is the only manager left: `docker swarm init --force-new-cluster
--advertise-addr <its mesh address> --data-path-addr <its VLAN 70 address>`, and the other
managers rejoin later through the nodes play with `-e swarm_rebuild=true` after
`docker swarm leave --force` (`docs/backups.md`, crash path). Neither is routine.

## Return: `ansible-playbook playbooks/host-return.yml -e host=<proxmox-a|proxmox-b>`

Power the host on by hand (neither node wakes from power-off over the network). Then:

| Step | What happens | If it fails |
|---|---|---|
| 1. Host and quorum | wait for SSH; `pvecm status` quorate with three votes, QDevice connected | `pvecm status` and `journalctl -u corosync` on both nodes; never `pvecm expected 1` while both nodes are up |
| 2. tank | `zpool status -x tank` healthy, mounted, `pvesm` active, ARC cap 4 GiB on proxmox-a and 2 GiB on proxmox-b, the three ZFS units enabled. The first boot after phase 2 is the first proof that `tank` imports at boot. | `zpool import tank` by hand, then `zfs mount -a`; guests whose disks are on `tank` (homelab's data disk, normandy's `/mnt/data`) do not start without it |
| 3. Guests | every listed guest running (onboot) within 10 minutes | `qm start <id>` / `pct start <id>`; `qm config` and the phase 3 window records hold the settings |
| 4. Mount order | the worker's pool disk (`/mnt/docker-data` on homelab, `/mnt/data` on normandy) mounted, `RequiresMountsFor` on Docker, and the mount active before Docker started | stop Docker, mount, start Docker; on normandy also mind the unmanaged `no-block-boot.conf` (`DefaultDependencies=no`) |
| 5. Unlock | `swarm.yml --tags nodes -l <worker>`, then all three managers Ready and Reachable | the by-hand unlock above |
| 6. Undrain | `docker node update --availability active` | - |
| 7. Compose | `docker start` every stopped container of the projects the drain stopped; wait until none is exited, starting or unhealthy | `ansible-playbook playbooks/site.yml -l <host> -e only=<project>` |
| 8. Stacks home | the survivor's hourly snapshot job runs once (the move needs a peer copy under two hours old), then `swarm-move.yml -e to=<worker>` for every stack whose inventory `pin` names the worker, same order as the drain; then `-e shed=` stacks are redeployed | a stack left on the survivor keeps working there; move it later |
| 9. Verify | both Gatus instances without failures, every route through the VIP, keepalived active on both edges and the VIP on exactly one | - |

Swarm does not rebalance stateless tasks when a node returns; they come back when their
service is next updated, or with `docker service update --force <service>` (start-first, no
gap).

## Compose-pinned moves, not yet scripted

The plan wants Emby to run on normandy while proxmox-a is down, and Immich on homelab while
proxmox-b is down. Neither is a one-variable move today: each host's Compose stacks are a
literal `stacks:` list in its host_vars, read by about a dozen consumers (site.yml, the
Traefik generator, Gatus, the drift manifest, the backup role), and the Compose files carry
host literals. The design proposed for the lead:

1. An inventory variable `compose_placement: {emby: homelab, immich: normandy}` and one
   definition of each movable stack, from which each host's `stacks:` list is built; the
   drain overrides it for the window.
2. Per-host values out of the Compose files: Immich `HOST_IP`, `DB_DATA_LOCATION` (on the
   worker's pool disk), the `/dev/dri/igpu-render` udev link (normandy only); Emby's absolute
   `/home/iryzhkov/app/emby` and `GIDLIST=1000,105,44` (normandy's render and video groups are
   987 and 983).
3. Data: a warm copy before the window through the existing `swarmsync` transport (Emby
   config 16 GB in 105,259 files; Immich Postgres 1.5 GB; its 7.6 GB model cache can be
   re-downloaded), the final copy while stopped, images pre-pulled on the target.
4. Outside the repository: OCI's Caddy (`emby.ryzhkov.dev` to `10.42.0.2:8096`) and Home
   Assistant's Emby integration name homelab.

Estimated downtime per move once built: Immich 3 to 5 minutes, Emby 2 to 3 minutes. Until
then they are on the exception list above. Jocasta (mesh-only listener, its firewall chain,
three systemd units and its backup unit) and the feed (`feedd`, a Python script and user unit
not in this repository, named by every fleet host) need more than this and are not planned for
today.

## Measured

From the 2026-10-03 rehearsals (`2026-10-03-prep.md`): a move of autobrr (SQLite) took 50 s
with 34 s of route failures; miniflux (Postgres) about 40 s each way with 21 s and 22 s of
failures and identical row counts; the S-stack drain cost the stateless services 0 to 34 s
each. phase 4: the VIP moves in 1.34 s.
