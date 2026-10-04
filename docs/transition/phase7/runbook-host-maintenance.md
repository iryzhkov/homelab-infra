# Runbook: taking a Proxmox host down for maintenance

Plan phase 7. Written 2026-10-03 from the phase 5 state (Swarm across homelab and normandy,
OCI as the third manager) and the phase 7 preparation in `2026-10-03-prep.md`, which holds
the measurements this runbook relies on. The playbooks are `playbooks/host-drain.yml` and
`playbooks/host-return.yml`; what lives on each host, the move order and the expected
failures are in `playbooks/vars/host-maintenance.yml`. Later this becomes the automated
drain the requirements document asks for.

This is the plan's `homelab/runbooks/host-maintenance.md`. It was proved on 2026-10-03: both
hosts drained, powered off for about ten minutes and returned with the playbooks, with the
results in `2026-10-03-drain-proxmox-b.md` and `2026-10-03-drain-proxmox-a.md`.

In short, per host:

```
ansible-playbook playbooks/host-drain.yml  -e host=<h> --check        # every check, nothing changed
ansible-playbook playbooks/host-drain.yml  -e host=<h>                # drain only; verify by hand
ansible-playbook playbooks/host-drain.yml  -e host=<h> -e power_off=true
# hold; then wake it (below)
ansible-playbook playbooks/host-return.yml -e host=<h>
```

proxmox-b first, then proxmox-a, never both, and the second only after the first is fully back
and `site.yml`, `swarm.yml` and `backup.yml` check clean. Measured: proxmox-b drains in about 5
minutes and returns in about 4; proxmox-a drains in about 18 (19 moves) and returns in about 17.

## What lives where

| Host | Guest | What it carries | How it leaves |
|---|---|---|---|
| proxmox-a | VM 101 homelab | Swarm worker and manager (Leader today); 20 stateful stacks pinned here; Compose: Emby, dsh, Jocasta, Gatus, cloudflared; `feedd` | stateful stacks move to normandy one at a time (`swarm-move.yml`); Emby moves to normandy (`compose-move.yml`); stateless tasks move by the drain; `logging` and the other Compose stacks stop |
| proxmox-a | VM 102 black-pearl | torrent stack, diun (Compose) | stops; no peer by design |
| proxmox-a | VM 103 homeassistant | Home Assistant, ZBT-2 radio by USB | stops (accepted exception) |
| proxmox-a | CT 105 zigbee2mqtt, CT 104 mqtt | Zigbee coordinator (CP210x by USB), MQTT broker | stop (accepted exception) |
| proxmox-a | VM 106 public-api | edge Traefik, keepalived member (priority 150) | stops; the VIP must be on public-api-b first |
| proxmox-a | CT 100 adguard (origin), CT 107 adguard (replica) | DNS 192.168.20.154 and .106 | stop; CT 201 on proxmox-b (.83) answers alone |
| proxmox-a | CT 110 healthcheck | external checks | stops |
| proxmox-a | VM 111 agent-a | steward agent runs | stops |
| proxmox-b | VM 202 normandy (`omarchy`) | Swarm worker and manager; stateless tasks; Compose: Frigate, Immich (server, ML, Postgres, Redis), Gatus, cloudflared | stateless tasks move by the drain; Immich moves to homelab (`compose-move.yml`); the other Compose stacks stop |
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
- **Compose movable**: Emby (home homelab) and Immich (home normandy). Each moves to the other
  worker with `playbooks/compose-move.yml` (below), using that worker's iGPU.
- **Compose pinned**: dsh, Jocasta, Gatus and cloudflared on homelab; Frigate, Gatus and
  cloudflared on normandy; torrent and diun on black-pearl. They stop with their host.
- **LXC**: the AdGuard origin (CT 100) and its replicas (CT 107 on proxmox-a, CT 201 on
  proxmox-b); MQTT, Zigbee2MQTT and the healthcheck on proxmox-a.

## Accepted exceptions

From the plan, confirmed by Igor 2026-10-02:

- proxmox-a down: Home Assistant automations, Zigbee and Thread (USB radios), MQTT.
- proxmox-b down: Frigate (no camera recording or detection).
- Emby and Immich keep running on the other worker, with that worker's iGPU (homelab has a
  UHD 770, normandy a UHD 630), moved by `compose-move.yml`.
- black-pearl (torrents) has no peer and is down while proxmox-a is.

Igor, 2026-10-03 about 10:30: Emby and Immich move; the remaining exceptions are, for
proxmox-a, Home Assistant, Zigbee and MQTT, Jocasta, dsh, the feed, logging and torrents, and
for proxmox-b, Frigate. In detail:

- `logging` (Grafana at logs.ryzhkov.dev, InfluxDB, Loki) is down while proxmox-a is. Containers
  log in non-blocking mode, so their log lines are dropped, not queued, and no container stops
  waiting on Loki.
- Jocasta (the agents' working-document store), dsh (`ai.ryzhkov.dev`) and the fleet feed
  (`feedd`) are down while proxmox-a is. dsh is tied to homelab by design (it runs
  `docker compose` on homelab's own projects); Jocasta (mesh-only listener, its firewall chain,
  three systemd units and its backup unit) and the feed (a Python script and user unit not in
  this repository, named by every fleet host) are not in a movable form.

Consequences that follow from those, not separate exceptions:

- Agent infrastructure, not household: the agent VM on the host (agent-a or agent-b) stops; the
  steward coordinator runs on normandy and is down while proxmox-b is (its Gatus checks fail then).

- OpenViking moves to normandy with the other stateful stacks and is reachable as
  `https://ov.ryzhkov.dev` through the edges (live since 2026-10-03 11:15). Clients that name `192.168.70.223:1933` lose it while homelab is
  down until they switch to that name.
- Literal homelab addresses: the *arr apps call flaresolverr at `192.168.70.223:8191` (the
  routing mesh keeps it answering only while homelab is up); Home Assistant names Wyoming and
  InfluxDB on homelab (HA is down with proxmox-a anyway).
- cv-site's `IMMICH_URL` is the literal `http://192.168.70.234:2283`
  (`inventory/group_vars/swarm/stacks-f4.yml`), so its photo feature fails while Immich runs on
  homelab; `https://photos.ryzhkov.dev` would follow it.
- Backups that name a host keep naming the home host: Emby's twice-weekly SQLite copy runs on
  homelab, Immich's nightly dump on normandy. While a stack is away (hours), the job on the
  home host is down with it; no new backup of that stack is taken until it returns.
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
6. **Converge from the laptop only while a Compose stack is away.** The temporary placement of
   Emby and Immich lives in `~/.local/state/homelab-infra/compose-placement.yml` on the laptop
   (`compose_placement_override_file`), written by `compose-move.yml` and removed when both are
   home. A `site.yml` run from another machine would not see it, would deploy the stack on its
   home host as well, and the prepare check would refuse there only if that host's copy were
   missing. Never edit the file by hand; move the stack instead.
7. **A `swarm.yml --tags stacks` converge is safe while a stateful stack sits away from its
   inventory pin.** Checked 2026-10-03: every one of the 20 `stacks/*/swarm-prepare.yml` that
   names a pin finds the node by `docker node ls --filter node.label=pin.<stack>=true` and
   falls back to the inventory `pin` only when no node carries the label (before the first
   cutover); the six stateless ones name no pin. (Until E26's fix that day, miniflux's and
   Authentik's read the inventory pin, and a converge after a move would have recreated an
   empty state directory on the old node.)
8. **The maintenance marker.** `host-drain.yml` sets the node label `maintenance=<proxmox
   host>` on the surviving worker before it changes anything, and `host-return.yml` removes it
   as its last step. While it is set, `site.yml`, the keepalived role and the nodes play's
   availability reset (`roles/swarm/tasks/labels.yml`) refuse to run (`roles/maintenance_guard`),
   so no converge restarts keepalived on the edge that is going down, undrains the worker, or
   deploys onto the host that is off. `-e maintenance_override=true` runs them anyway; the
   maintenance playbooks pass it to the converges they run themselves. A drain refuses while
   another host's marker is set (one host at a time). To see it: `docker node ls -q --filter
   node.label=maintenance` on a manager. To clear it by hand after an abandoned maintenance:
   `docker node update --label-rm maintenance <node>`.

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
| 1. Pre-checks | Host known; only listed guests run; managers and OCI; Proxmox quorum and QDevice; both Gatus instances green (on a re-run after a drain-only run, the survivor's only, expected failures allowed); last vzdump OK and under 26 h; presence and Emby; reservations of what moves fit on the surviving worker with 2 GiB to spare; every stack that will move has a peer copy on the survivor under two hours old (run `systemctl --user start homelab-backup-hourly.service` on the leaving worker first); no other host's maintenance marker. Then the marker is set. | Nothing changed but the marker; `host-return.yml` removes it, or `docker node update --label-rm maintenance <node>`. Never override the OCI or quorum checks. |
| 2 to 8 as a unit | If any step from 2 on fails, a rescue starts keepalived again on the edge the run stopped, redeploys the shed tier, and fails the drain. Moves already made stay valid; the marker stays set. | `host-return.yml -e host=<h>` undoes the rest. |
| 2. VIP | If the leaving host's edge holds 192.168.20.3, `keepalived` is stopped there and the playbook waits for the survivor to take it (phase 4 measured 1.34 s). `nopreempt` keeps it there afterwards. | `systemctl start keepalived` on the stopped edge (`host-return.yml` does it); the VIP stays where it is. |
| 3. Shed | Only with `-e shed=<stack,...>`: those stacks scaled to 0. Not needed today: normandy has 11.6 GiB available against 4.7 GiB reserved by everything that moves there. Candidates, cheapest first: whisper (1.5 GiB limit), komga, flaresolverr, wyoming-piper, librespeed, kiwix. | `ansible-playbook playbooks/swarm.yml --tags stacks -e only=<stack>` per shed stack (the return does it with the same `-e shed=`). |
| 4. Moves | `swarm-move.yml -e stack=<s> -e to=<survivor worker>` per stateful stack pinned to the leaving worker: leaf stacks in name order, then authentik, miniflux, vaultwarden, forgejo last (the image mirror). Each is a stop, a cold copy, a restore, a pin flip, a start, probes, and a tidy of obsolete copies. About 40 s each, of which about 20 s is the route failing; 19 stacks take about 13 minutes. | A move that fails before its pin flip leaves the stack stopped on the old node: `docker service scale <svc>=<n>` on it restores it (the counts are in `/srv/swarm/<stack>/.swarm-replicas`). After the flip: move it back with `swarm-move.yml -e to=<old node>`. Moves already done stay valid; continue or run `host-return.yml`. |
| 4b. Compose moves | `compose-move.yml -e stack=<s> -e to=<survivor worker>` for the movable Compose stack placed on the leaving worker: Emby for proxmox-a, Immich for proxmox-b (section "Compose moves"). | Before the placement flip: `site.yml -l <worker> -e only=<s>`. After it: `compose-move.yml -e to=<worker>`. |
| 5. Drain | `docker node update --availability drain <worker>`; wait until it runs no Swarm task and every service except `logging` and the shed has a running task. hugo runs one of two replicas (one per node). | `docker node update --availability active <worker>`. |
| 6. Compose | `docker stop -t 60` on every container of the host's Compose projects (normandy's Omarchy `DefaultTimeoutStopSec=5s` would otherwise kill them at shutdown). | `docker start` on them (`host-return.yml` step 7). |
| 7. Verify | Every route Traefik serves (its router list on the surviving edge, 43 hosts on 2026-10-03) is probed through the VIP. The household routes in `host_maintenance_routes` must answer 2xx, 3xx or 401, except those that go down with this host by design; any other router (T3 hosts, the test car, Proxmox) is probed at `/` and reported, without refusing; `vault.ryzhkov.dev` resolves to the VIP from the surviving worker (a VLAN 70 client); Gatus on the survivor fails only where `host_maintenance_gatus_expected` allows. | Investigate before powering off; `host-return.yml` undoes steps 2 to 6. |
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

Power the host on. Both nodes wake from power-off by Wake-on-LAN (proved 2026-10-03); send the
magic packet from the other node, which is on the same LAN:

```
# MAC: proxmox-a 7c:4d:8f:99:6b:56, proxmox-b 9c:7b:ef:a9:e3:d4 (UDM ARP table, /proc/net/arp)
ssh <other node> "python3 -c \"import socket; m=bytes.fromhex('<mac without colons>'); p=b'\\xff'*6+m*16; s=socket.socket(socket.AF_INET,socket.SOCK_DGRAM); s.setsockopt(socket.SOL_SOCKET,socket.SO_BROADCAST,1); [s.sendto(p,('192.168.1.255',x)) for x in (9,7)]\""
```

The node answers ping about 40 s (proxmox-b) to 2 minutes (proxmox-a) later. Otherwise press the
power button. Then:

| Step | What happens | If it fails |
|---|---|---|
| 1. Host and quorum | wait for SSH; `pvecm status` quorate with three votes, QDevice connected | `pvecm status` and `journalctl -u corosync` on both nodes; never `pvecm expected 1` while both nodes are up |
| 2. tank | `zpool status -x tank` healthy, mounted, `pvesm` active, ARC cap 4 GiB on proxmox-a and 2 GiB on proxmox-b, the three ZFS units enabled. The first boot after phase 2 is the first proof that `tank` imports at boot. | `zpool import tank` by hand, then `zfs mount -a`; guests whose disks are on `tank` (homelab's data disk, normandy's `/mnt/data`) do not start without it |
| 3. Guests | every listed guest running (onboot) within 10 minutes | `qm start <id>` / `pct start <id>`; `qm config` and the phase 3 window records hold the settings |
| 4. Mount order | the worker's pool disk (`/mnt/docker-data` on homelab, `/mnt/data` on normandy) mounted, `RequiresMountsFor` on Docker, and the mount active before Docker started | stop Docker, mount, start Docker; on normandy also mind the unmanaged `no-block-boot.conf` (`DefaultDependencies=no`) |
| 5. Unlock | `swarm.yml --tags nodes -l <worker>`, then all three managers Ready and Reachable | the by-hand unlock above |
| 6. Undrain | `docker node update --availability active` | - |
| 7. Compose | `docker start` every stopped container of the projects the drain stopped; wait until none is exited, starting or unhealthy | `ansible-playbook playbooks/site.yml -l <host> -e only=<project>` |
| 8. Stacks home | the survivor's hourly snapshot job runs once (the move needs a peer copy under two hours old), then `swarm-move.yml -e to=<worker>` for every stack whose inventory `pin` names the worker, same order as the drain; then `compose-move.yml -e to=<worker>` for the movable Compose stack whose home is the worker (the override file is removed once both are home); then `-e shed=` stacks are redeployed | a stack left on the survivor keeps working there; move it later |
| 9. Verify | both Gatus instances without failures, every route through the VIP, keepalived active on both edges and the VIP on exactly one | - |
| 10. Loki (proxmox-a only, required) | after homelab returns, Loki's task runs but `/ready` stays at "Ingester not ready: waiting for 15s after being ready" (2026-10-03: still so 17 minutes after the return); run `docker service update --force logging_loki` on a manager, and `curl http://127.0.0.1:3100/ready` on homelab answers `ready` within half a minute. Root cause open: it looks like the single-node ring keeping the old instance's entry after an unclean stop (the same symptom as the 23:50 ring failure in phase 5, `docs/transition/phase5/2026-10-03-F5.md`). | logs are dropped meanwhile, nothing else waits on it |
| 11. Clear the maintenance | an hourly snapshot on the returned worker (so the next drain's pre-pass finds fresh peer copies of the stacks that just came home), then the maintenance marker removed | `docker node update --label-rm maintenance <node>` |

What the return proves on each host, checked on 2026-10-03: `tank` imported at boot
(`journalctl -b -u zfs-import@tank`), the pool disk mounted before containerd and Docker
(monotonic activation times: normandy 7.0, 8.1, 10.2 s; homelab 2.7, 2.9, 5.7 s), the worker came
back locked and the nodes play unlocked it. Zigbee2MQTT needs a few minutes for the mesh to
re-form after proxmox-a returns (`NWK_NO_ROUTE` pings, then none).

Swarm does not rebalance stateless tasks when a node returns; they come back when their
service is next updated, or with `docker service update --force <service>` (start-first, no
gap).

## Compose moves: `ansible-playbook playbooks/compose-move.yml -e stack=<emby|immich> -e to=<worker>`

Emby (home homelab) and Immich (home normandy) are defined once, in
`inventory/group_vars/all.yml` (`compose_movable_stacks`), and placed by `compose_placement`:
the home placement (`compose_placement_home`) overlaid with the laptop's override file (rule
6). Every host's `stacks` is its own `host_stacks` plus the movable stacks placed on it, so
site.yml, the Traefik generator, the stacks filters, Gatus and the drift manifest all follow
a move with no other change. Per-host values come from the host the stack lands on: `HOST_IP`
(Immich publishes on it), `stack_root`, the state directory (`compose_data_dirs`: on the guest
worker, its pool disk), the iGPU's render and video group IDs for Emby's `GIDLIST`, and the
iGPU's udev path, from which `stacks/immich/prepare.yml` writes the `/dev/dri/igpu-render` rule.
Both prepare files refuse to start the stack on an empty state directory.

The move, in order: preconditions (the stack runs on its current host and not on the target;
room for two copies on the target's pool disk); images made present on the target with the
same image IDs (`docker save | docker load` from the source for tags, a pull for digests); a
warm copy while running, through the restricted `swarmsync` key into
`peer-snapshots/<from>/compose/<stack>/` and then locally into the state directory; the live
data check (Immich: assets, users, albums from its database); stop and remove the containers
on the source (removed, so `restart: always` cannot start a second copy when the source boots
again); the cold copy, the same two steps, then an rsync dry run in both steps that must list
nothing and the cold data check (Emby: MediaItems in `library.db` on both copies); the
placement written to the laptop's override file (removed once every stack is home); then
`site.yml -l <to> -e only=<stack>`, `site.yml -l <from> -e only=<stack>` (drops it from the old
host's drift manifest), `traefik.yml`, `site.yml -e only=gatus` on both workers; then the
containers healthy on the target, the route through the VIP, and the live check equal to
before. `host-drain.yml` runs it after the Swarm moves; `host-return.yml` runs it back.

Rollback before the placement flip: `site.yml -l <from> -e only=<stack>` starts it on the source
again (its directory is untouched). After the flip: move it back; the copy on the source is the
cold copy, so the warm copy back is short.

Sizes (2026-10-03): Emby's state 15.3 GiB, 105,259 files; Immich's Postgres 1.4 GiB, and its
images 7.2 GB to stream to homelab the first time. Immich's machine-learning model cache
(7.6 GB, a named volume) is not copied; homelab downloads the models again on first use.

The design, as it was proposed before it was built:

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

## Outside the repository

- **OCI's Caddy** is now in the repository (`playbooks/oci-caddy.yml`): `emby.ryzhkov.dev`
  proxies to both edges over the mesh (10.42.0.3 first, 10.42.0.10 second) with TLS to
  `emby.ryzhkov.dev` and an active health check on `/System/Info/Public`, so it follows Emby
  through Traefik wherever it runs and survives the loss of either edge. Emby still sees OCI's
  mesh address for outside clients (Traefik does not trust OCI's `X-Forwarded-For`). Apply
  with `ansible-playbook playbooks/oci-caddy.yml` (a `caddy reload`, no dropped connections).
- **UniFi, for Igor**: the port group `traefik-homelab-ports` (id `6a966cc55afbaf142ea88874`),
  used by the policy `public-api -> homelab (Traefik backends)` towards both workers, lacks
  2283 (Immich on homelab). (1933 is not in it either, but another policy already lets the
  edges reach it: `ov.ryzhkov.dev/health` answers 200 through the VIP.) Measured from both edges:
  `192.168.70.223:2283` times out (curl exit 28) while `:8096` answers 302. Without 2283,
  `photos.ryzhkov.dev` fails while Immich runs on homelab.
- **Home Assistant, for Igor**: `configuration.yaml` (`media_player: platform: emby, host:
  192.168.70.223`) and `packages/requests.yaml` (four `rest_command`s on
  `http://192.168.70.223:8096`) name homelab. While Emby runs on normandy (a proxmox-a drain)
  Home Assistant is down too, so it matters only for a drain-only rehearsal; the lasting fix is
  `https://emby.ryzhkov.dev`.
- **OpenViking clients** that name `192.168.70.223:1933` (found 2026-10-03):
  `~/.config/ov-mcp/config.toml` on the laptop, normandy and gaming-pc (`base_url`), homelab's
  `~/.config/ov-mcp/config.toml` and `~/.codex/config.toml`, the laptop's
  `~/.claude/settings.json`, and the `ov-memory` skill's text on every host. homelab's own
  `~/.claude.json` uses `127.0.0.1:1933`, which stops answering when OpenViking moves to
  normandy. They belong to the agent tooling (UpKeeper); the failover-capable address is
  `https://ov.ryzhkov.dev`, live since 2026-10-03 11:15.

## Measured

From the 2026-10-03 rehearsals (`2026-10-03-prep.md`): a move of autobrr (SQLite) took 50 s
with 34 s of route failures; miniflux (Postgres) about 40 s each way with 21 s and 22 s of
failures and identical row counts; the S-stack drain cost the stateless services 0 to 34 s
each. phase 4: the VIP moves in 1.34 s.

From the host-down runs (per-service tables in the two drain records): each stateful stack is
unreachable for 6 to 50 s at its move out and again at its move home (Forgejo 67 s and 88 s,
the slowest); Immich 16 s and 22 s; Emby 24 s and 28 s; Authentik and forward auth about 50 s and
37 s. While a host is down, only the accepted exceptions fail: Frigate for proxmox-b; Home
Assistant, Zigbee, MQTT, Jocasta, dsh, the feed, logging and torrents for proxmox-a.

## Copies left behind, by design

- `/srv/swarm/.moved/<stack>-<stamp>` on the node a stack left: the rollback copy of that move,
  as it was at the stop. It is overwritten (removed and replaced) the next time the stack leaves
  that node, and removed by the tidy when the stack moves back onto it. On 2026-10-03, after
  both drains, normandy holds 19 of them, 9.7 GB (from the moves home). If space is needed, prune them by
  hand: `sudo rm -rf /srv/swarm/.moved/<stack>-*` on that node, never the live
  `/srv/swarm/<stack>`.
- `<pool>/compose/<stack>` on the worker that is not the stack's home (normandy
  `/mnt/data/compose/emby`, homelab `/mnt/docker-data/compose/immich/postgres`): the cold copy
  from the last time the stack ran there. It is overwritten by the next move (`rsync --delete`)
  and makes that move's warm copy short. It is stale data, including Emby's config and Immich's
  database; the root is root:root 0700. Prune by hand if space is needed: `sudo rm -rf
  <pool>/compose/<stack>`.
- The staging copies under `peer-snapshots/<peer>/compose/` are removed by every move and every
  rollback; none should exist between maintenances.

## Retest at the next drain

- voice: its route now has a health check; confirm it does not fail while its other worker is
  down (it failed every other request during the proxmox-b drain).
- irc and rss: both failed in short bursts while other stacks moved (6 to 39 s of 503, from
  15:28 to 15:31 and 16:02 to 16:06 on 2026-10-03), not only at their own moves. Recorded, not
  fixed: their Traefik health checks (5 s interval, 3 s timeout) probably time out while the
  worker is busy with move copies. A longer timeout on those two routes is the candidate fix.

## Lessons built into the playbooks (2026-10-03)

Added after the review of the host-down runs:

- The maintenance marker and its guard (rule 8).
- The drain's read-only pre-pass on peer-copy age, before anything changes; `host-return.yml`
  ends with an hourly snapshot on the returned worker.
- The drain's rescue (keepalived and the shed tier restored on any failure after step 2).
- `swarm-move.yml` resumes a move interrupted after the old copy was set aside and before the
  pin flip (no owner on the old node, a copy in `.moved/<stack>-*`, the target already the
  owner): it skips the copies and goes on with the pin flip and the start.
- The verify step probes Traefik's own router list.

From the host-down runs themselves:

- Every Swarm route needs a Traefik health check, or Traefik keeps sending part of the traffic to
  a stopped worker (voice failed every other request while proxmox-b was down; its route now
  checks `/`).
- `host-return.yml` waits for Docker to be active before comparing the mount and Docker start
  times.
- A second `host-drain.yml` run (the power-off after a drain-only run) reads only the survivor's
  Gatus and tolerates the expected failures.
- `compose-move.yml` rolls back to the source automatically when anything after the stop fails,
  retries copies while the hourly job holds rrsync's lock, removes its staging copy (which would
  otherwise be shipped to B2 from homelab), and keeps `<pool>/compose` root-only.
