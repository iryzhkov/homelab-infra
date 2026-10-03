# Phase 5 foundation: survey, Swarm init, registry

Date: 2026-10-02, 19:55 to 20:30 PDT (2026-10-03 UTC). Executor E20.
Hosts: homelab (VM 101, 192.168.70.223), normandy (VM 202, 192.168.70.234), oci (150.230.40.9, mesh 10.42.0.1); read-only probes from public-api and gaming-pc.
Plan: phase 5 steps 1 to 3 of `homelab/plans/serviceable-transition-2026-10-02.md` revision 4. The stack-by-stack plan is `migration-plan.md` beside this file.

## Verdict

The Swarm has three managers: homelab (leader), normandy and OCI (`availability drain`), autolocked, overlay pool 10.200.0.0/16. Raft and gossip run on the Nebula addresses (10.42.0.2, 10.42.0.7, 10.42.0.1); the workers' VXLAN data path is on VLAN 70 (192.168.70.223, .234). Workers are labelled `gpu=intel`, `host=<name>`, `pool=<path>`; OCI `host=oci`. No Compose container on any of the three hosts restarted. The five locally built images are in Forgejo's registry and pre-pulled on normandy. `site.yml`, `base.yml` and `swarm.yml` in check mode report changed=0 on every host.

History: the first build (20:17 to 20:19 PDT) put everything on VLAN 70 and left OCI out, because OCI cannot reach VLAN 70. The lead rejected the resulting two-manager cluster (either host down loses quorum) and resolved the plan's contradiction: control plane over Nebula, data path on VLAN 70, OCI drained. The cluster was re-created that way at 20:34 (section 4). Sections 2 and 3 describe the first build and stay as the record of it.

## 1. Survey

### Docker versions

Command: `docker version --format '{{.Server.Version}} api {{.Server.APIVersion}}'`, `docker info` on each host.

| Host | Engine | Storage | Data root | Logging | Firewall backend |
|---|---|---|---|---|---|
| homelab | 29.7.2, API 1.55 | overlay2 | `/mnt/docker-data/docker` | loki plugin, `loki-url` `http://localhost:3100/...` | iptables (nf_tables) |
| normandy | 29.7.2, API 1.55 | overlayfs (containerd image store) | `/mnt/data/docker` | loki plugin, `loki-url` `http://192.168.70.223:3100/...` | iptables (nf_tables) |
| oci | 29.1.3 (Ubuntu package), API 1.52 | overlay2 | `/var/lib/docker` | default | iptables |

Answer: the two workers match exactly; OCI is six minor versions behind and comes from Ubuntu's archive, not Docker's.

### Address pools and overlap

Command: `ip -4 -br addr`, `ip route`, `/etc/docker/daemon.json`, `docker network ls`.

- homelab: `default-address-pools` `172.16.0.0/12` size 24; 41 bridges, all in 172.16.0.0/12 (172.16.x.0/24 plus older 172.18-31.0.0/16). Nebula `nebula1` 10.42.0.2/24.
- normandy: the same `default-address-pools` `172.16.0.0/12` size 24; bridges 172.16.0-4.0/24. NICs: `ens18` 192.168.60.97/24 (VLAN 60, High Seas) and `ens19` 192.168.70.234/24 (VLAN 70, default route). Nebula 10.42.0.7/24.
- oci: VCN subnet `ens3` 10.0.0.236/24, `docker0` 172.17.0.0/16, one bridge 172.18.0.0/16, Nebula 10.42.0.1/24. No `daemon.json`.
- The mesh is 10.42.0.0/24 on every interface (the route is a /24, not the /16 the brief names).

Answer: Swarm's default ingress 10.0.0.0/24 collides with OCI's VCN subnet, and its default pool 10.0.0.0/8 contains both that subnet and the Nebula mesh. The cluster was initialised with `--default-addr-pool 10.200.0.0/16 --default-addr-pool-mask-length 24`; `ingress` got 10.200.0.0/24. `docker_gwbridge` (a local bridge) came from each host's own pool: 172.16.2.0/24 on homelab, 172.16.5.0/24 on normandy. Nothing overlaps.

### Nebula

Command: the `firewall:` and `lighthouse:` sections of `/etc/nebula/config.yml` (keys not printed); `inventory/hosts.yml` group `nebula_managed`.

- `nebula_managed` holds public-api, proxmox-a and proxmox-b only. homelab, normandy and oci carry hand-written configs.
- All three have `inbound: - port: any, proto: any, host: any`, so Nebula itself would pass 2377/tcp, 7946/tcp+udp and 4789/udp in every direction. No `unsafe_routes` anywhere, and OCI's interface is only 10.42.0.1/24.

Answer: no Nebula change is needed for Swarm, so no hand edit was required or made. But Nebula carries only mesh addresses: OCI cannot reach 192.168.70.223 or .234.

### Why OCI did not join

A Swarm manager dials every other manager at its advertised address, and gossip uses the same address. The plan fixes the workers' advertise addresses to VLAN 70, and puts OCI on Nebula. Those cannot both hold:

- OCI has no route to VLAN 70 (no Nebula `unsafe_routes`, which would also need the workers' certificates re-signed with a `subnets` entry).
- Advertising the workers' mesh addresses instead would put raft and gossip on Nebula (MTU 1300, and a Nebula restart partitions the managers), which the plan rejects.
- OCI's host firewall (iptables-persistent, hand-written, not in the repository) ends INPUT with `REJECT --reject-with icmp-host-prohibited` and opens only 80, 443, 4242/udp, 22 and 5403 on `nebula1`. Opening 2377 and 7946 there would be a hand edit on a host this repository does not converge.
- Memory would have fitted: 560 MB available of 954 MB now (523 MB in phase 0); dockerd 43 MB and containerd 19 MB resident.
- OCI's Engine is 29.1.3 against the workers' 29.7.2.

Recorded answer at the time: the plan's fallback applies. Superseded by the lead's decision, section 4: managers advertise their mesh addresses, so OCI's only dependency is Nebula, and its iptables rule is now under Ansible.

### Host firewalls

- homelab: no host firewall. `iptables -S INPUT` is `-P INPUT ACCEPT` with no rules. `netfilter-persistent` is enabled and restores `/etc/iptables/rules.v4`, an `iptables-save` of 2026-03-16 whose `*raw` table holds about 40 stale Docker `PREROUTING ... -j DROP` lines for bridges that no longer exist. Not changed; recorded because every boot restores them before Docker starts.
- normandy: ufw active (Omarchy), default deny incoming, deny routed, with the ufw-docker `DOCKER-USER` chain. Allowed in: SSH from 70 and 90, Immich 2283 and Frigate 8971 from 70 and 20, Frigate 5000 from 70, T3 Code 7391 from 20, steward health 3003, LocalSend 53317, everything on `nebula1`, 4242/udp.
- oci: iptables-persistent as above.

### UniFi path from the edge proxy (read from `docs/transition/phase0/2026-10-02-unifi-vlans.md`, not re-read)

Traefik (192.168.20.33) may reach homelab on port group `traefik-homelab-ports` and normandy only on `traefik-normandy-ports` (2283, 7391, 8971). A Swarm service published through the routing mesh is reachable on both workers, but Traefik can use the normandy backend only after its port joins `traefik-normandy-ports`. That is a UniFi change, which executors may not make. VLAN 60 (High Seas, where normandy's `ens18` sits) reaches VLAN 70 only for the NAS and Emby.

### Forgejo container registry

Command: `app.ini` sections (`[packages]` absent, so the default `ENABLED = true`), `curl -s -D- https://git.ryzhkov.dev/v2/` from homelab, `forgejo admin user list`.

- Forgejo 12.0.4 answers `401` with `www-authenticate: Bearer realm="https://git.ryzhkov.dev/v2/token",service="container_registry"`. Both workers resolve `git.ryzhkov.dev` to 192.168.20.33 (AdGuard rewrite to the edge Traefik).
- Users: `igor_admin` (admin) and `igor`. Images are pushed under `igor`.
- Authentication: a Forgejo access token as the password of `docker login git.ryzhkov.dev -u igor`. Two tokens were created by `forgejo admin user generate-access-token` inside the container (through the play): `swarm-registry-push` (`write:package`), logged in on homelab, the only builder; `swarm-registry-pull` (`read:package`), logged in on normandy. Both are in `secrets/swarm.sops.yaml`. Docker keeps them base64 in each user's `~/.docker/config.json` (no credential helper).
- Packages before: 4 KB. After the pushes: 852 MB under `~/app/forgejo/gitea/packages` on homelab's root disk, which the twice-weekly `homelab-bulk-backup app` job copies to B2.

### Stacks per host

Command: `docker inspect` of every container on homelab (42), normandy (7), blackpearl (4) and public-api (2), reduced to image, mounts, published ports, devices, privileged, network mode, container name, depends_on, capabilities and shm size; `inventory/host_vars/*.yml` for routes, secrets and env; the generated Traefik file provider for backends (`roles/traefik/templates/generated.yml.j2`: every route's backend is `http://<ansible_host of the host listing the stack>:<port>`, health check `/` unless overridden). The per-stack table, with class and placement, is in `migration-plan.md`; the facts that change the plan:

- Locally built images: cv-site (`cv-site-app`), dsh (`dsh-dsh`), openviking (`openviking-openviking`), whisper (`whisper-cpp-dispatch:local`), jocasta (`jocasta:<commit>`, built from Jocasta CI). hugo is not built: it is the public `nginx:latest` serving `~/app/hugo/blog/public`, which the Forgejo webhook builds on homelab.
- Device use: Emby `devices: /dev/dri`; immich-machine-learning `devices: /dev/dri` plus a `/dev/bus/usb` bind; immich-server and Frigate bind-mount `/dev/dri`; gluetun `/dev/net/tun`. No container is privileged.
- Address-bound ports: gatus and gatus-public (`192.168.70.223:3002/3001`), navidrome (`:4533`), whisper (`:8080`), wyoming-openai (`:10300`), wyoming-piper (`:10200`), jocasta (`10.42.0.2:1935`, behind its own `JOCASTA` chain), and on normandy Frigate, Immich and Gatus. Swarm cannot bind a published port to an address.
- Docker socket: authentik-worker, dsh (read-write, plus `/home/iryzhkov/app` at the same path for in-container `docker compose`), diun on blackpearl and public-api, Traefik.
- State the plan classes wrongly: obsidian-livesync is CouchDB, not SQLite; lubelogger's `cartracker.db` is LiteDB (`file` says `data`); thelounge keeps chat logs in `logs/igor.sqlite3` (1.9 GB directory); cv-site appends to `data/analytics.jsonl` (107 MB); Grafana's database is on NFS (`/mnt/backup/grafana`); openviking is a vector store plus files, not SQLite.
- The fleet feed is `feedd.service`, a systemd user service on homelab, not a container; notify-relay likewise.
- `flaresolverr` runs on the default `bridge` network; `torrent` uses `network_mode: service:gluetun`.

## 2. Changes made

All through Ansible from this worktree; nothing by hand on a host.

| Time (PDT) | Command | Host effect |
|---|---|---|
| 20:17 | `ansible-playbook playbooks/swarm.yml --tags nodes -l homelab` | `swarm-firewall.service` (chains `SWARM-INPUT` in INPUT, `SWARM-INGRESS` in DOCKER-USER), `/mnt/docker-data/swarm` and `/srv/swarm`, `docker swarm init --advertise-addr 192.168.70.223 --listen-addr 192.168.70.223:2377 --data-path-addr 192.168.70.223 --default-addr-pool 10.200.0.0/16 --default-addr-pool-mask-length 24 --autolock`, unlock key written to `secrets/swarm.sops.yaml`, labels |
| 20:19 | `ansible-playbook playbooks/swarm.yml --tags nodes -l normandy` | eight ufw rules (below), `/mnt/data/swarm` and `/srv/swarm`, `docker swarm join` as manager with the same three address flags at 192.168.70.234, labels |
| 20:20 to 20:23 | `ansible-playbook playbooks/swarm.yml --tags registry` | two Forgejo tokens, `docker login` on both, five images tagged and pushed from homelab, pulled on normandy |

homelab's `SWARM-INPUT`: accept 2377/tcp, 7946/tcp, 7946/udp, 4789/udp from 192.168.70.234 on `ens18`, then drop those four ports from anywhere; jumped to first in INPUT. `SWARM-INGRESS`: drop `-i nebula1 -o docker_gwbridge`; jumped to first in DOCKER-USER, ahead of the existing `JOCASTA` rule. normandy's ufw: `allow in on ens19 from 192.168.70.223` to the same four ports (comment `swarm peer homelab`), and `route deny in on ens18 out on docker_gwbridge`, `route deny in on nebula1 out on docker_gwbridge` (comment `swarm ingress off ...`), which ufw places in `ufw-user-forward`, the first jump of normandy's DOCKER-USER.

## 3. Checks after the change

- No Compose container restarted. Command: `docker inspect -f '{{.State.StartedAt}} {{.RestartCount}}'` for every running container, captured at 20:17 before init and again after each step, then `diff`. homelab: 42 containers, identical before init, after init and after normandy joined (all still started 2026-10-03T02:22Z, the phase 3 window). normandy: 7, identical (started 2026-10-03T00:41Z).
- `docker node ls` on homelab:

  ```
  ID                            HOSTNAME           STATUS  AVAILABILITY  MANAGER STATUS  ENGINE VERSION
  kn56ip2asambhnhly6k6kvme0 *   homelab            Ready   Active        Leader          29.7.2
  h8chzdwyaineymz02cjgrs0rx     omarchy-normandy   Ready   Active        Reachable       29.7.2
  ```

- `docker info` Swarm section on homelab: `active 192.168.70.223 ['10.200.0.0/16'] 24 {'AutoLockManagers': True} 4789`; on normandy `active 192.168.70.234 managers=2 nodes=2`. `ingress` 10.200.0.0/24. Listening: `192.168.70.223:2377/tcp`, `192.168.70.223:7946/tcp+udp`, `0.0.0.0:4789/udp` (the kernel VXLAN socket, filtered by `SWARM-INPUT`).
- Labels (`docker node inspect -f '{{json .Spec.Labels}}'`): homelab `{"gpu":"intel","host":"homelab","pool":"/mnt/docker-data"}`, normandy `{"gpu":"intel","host":"normandy","pool":"/mnt/data"}`. normandy's Swarm hostname is `omarchy-normandy`; stacks address nodes by the `host` label, never by hostname.
- Port 2377 on homelab: open from normandy; `closed/filtered` from public-api (VLAN 20) and gaming-pc (VLAN 90, which UniFi lets reach VLAN 70), while Vaultwarden's 8099 stays open from both. Port 2377 on normandy: open from homelab, closed from public-api. The ingress filter has nothing to filter until a service publishes a port; batch S1 tests it (`migration-plan.md`).
- Registry: on normandy `git.ryzhkov.dev/igor/{cv-site:latest 122MB, dsh:latest 1.76GB, openviking:latest 1.18GB, whisper-server:latest 796MB, jocasta:37ce6575... 27.1MB}`, digests equal to those homelab pushed (cv-site `sha256:f696f11b...`, jocasta `sha256:7863602b...`).
- Idempotence: a second `--tags registry` run, then `ansible-playbook playbooks/swarm.yml --check`, report changed=0 on both hosts. The first check run of `--tags nodes` after the init reported changed=0 as well.
- `ansible-playbook playbooks/site.yml --check`: changed=0 on blackpearl, homelab (ok=386), normandy, public-api. `playbooks/base.yml --check`: changed=0 on all six hosts.
- `scripts/stack-lint.py stacks/emby/docker-compose.yml stacks/gatus/docker-compose.yml` exits 1 with `devices: Swarm services cannot map devices`, `no deploy.placement.constraints`, `no deploy.resources.limits.memory` and `port ${HOST_IP}:3002:8080 is bound to an address`; a minimal stack with constraints, a memory limit and `stop-first` passes.
- `docker compose config | docker stack deploy -c -` (plan step 4) fails on Docker 29: `services.web.ports.0.published must be a integer`. `docker stack config -c stack.yml`, with the variables in its environment, reads `env_file` and resolves relative paths itself; `roles/swarm/tasks/stack.yml` uses that.

## 4. Rebuild with three managers (lead decision, 20:30 PDT)

Nothing had been deployed to the first cluster, so it was re-created through the role.

Role changes: `swarm_advertise_addr` is the mesh address (`mesh_host`; normandy has none in the inventory, so `10.42.0.7`, its `nebula1` address, is set on the host), `swarm_data_path_addr` is `ansible_host` (VLAN 70) on the workers and 10.42.0.1 on OCI; group `swarm` became children `swarm_workers` (homelab, normandy) and `swarm_drain_managers` (oci, `swarm_availability: drain`). A node in a cluster on other addresses leaves it only with `-e swarm_rebuild=true`. The registry and stack plays run on `swarm_workers` only.

Firewall after the rebuild:

- homelab `SWARM-INPUT`: 2377/tcp, 7946/tcp, 7946/udp accepted on `nebula1` from 10.42.0.1 and 10.42.0.7; 4789/udp accepted on `ens18` from 192.168.70.234 only; all four dropped from anywhere else. `SWARM-INGRESS` unchanged.
- normandy ufw: the three VLAN 70 control-port allows of the first build deleted; inserted at the top, above the existing `Anywhere on nebula1 ALLOW IN`, allows for 2377/tcp and 7946/tcp+udp from 10.42.0.1 and 10.42.0.2 on `nebula1`, then denies for 2377, 7946 and 4789 on `nebula1` from anywhere; 4789/udp on `ens19` from 192.168.70.223 kept; the two route denies kept. ufw is kept rather than a unit because `ufw-init` flushes the built-in chains on restart.
- oci: OCI persists its firewall with iptables-persistent: `netfilter-persistent` (enabled) restores `/etc/iptables/rules.v4` (a hand-written `iptables-save`, 2026-09-07) at boot. `roles/swarm/tasks/oci_firewall.yml` writes six lines into that file before `-A INPUT -j REJECT` (2377/tcp, 7946/tcp, 7946/udp from 10.42.0.2/32 and 10.42.0.7/32 on `nebula1`, comment `swarm manager <host>`) and `-A DOCKER-USER -o docker_gwbridge -m comment --comment "swarm ingress off" -j DROP` before `-A FORWARD -j DOCKER-USER`, each validated with `iptables-restore --test`, and inserts the same rules into the live tables with `ansible.builtin.iptables`. 4789 is not opened: it stays behind the final REJECT. The DOCKER-USER drop keeps the routing mesh off OCI's public address and keeps OCI from forwarding mesh traffic over Nebula.

| Time (PDT) | Command | Effect |
|---|---|---|
| 20:34 to 20:35 | `ansible-playbook playbooks/swarm.yml --tags nodes -e swarm_rebuild=true` | homelab: firewall script rewritten, `docker swarm leave --force`, `docker swarm init --advertise-addr 10.42.0.2 --listen-addr 10.42.0.2:2377 --data-path-addr 192.168.70.223 --default-addr-pool 10.200.0.0/16 --default-addr-pool-mask-length 24 --autolock`, new unlock key written to `secrets/swarm.sops.yaml` (rotated: the old cluster's key is gone with it), labels. normandy: ufw as above, leave, join as manager with `10.42.0.7` / `192.168.70.234`. oci: rules.v4 and live rules, join as manager with `10.42.0.1` / `10.42.0.1`, labels, `availability drain` |
| 20:37 | `ansible-playbook playbooks/swarm.yml --tags nodes -l homelab` | the firewall script re-rendered with its peers sorted (a group built from child groups has no stable order, which made the check run report a change); rules identical |

Checks:

- No container restarted: `StartedAt` and `RestartCount` of every running container captured at 20:34 and after the rebuild, identical on homelab (42), normandy (7) and oci (1, `gatus-oci`).
- `docker node ls`:

  ```
  ID                            HOSTNAME                STATUS  AVAILABILITY  MANAGER STATUS  ENGINE VERSION
  mylk5ehwxx9w0ko3yjsbhbgea     free-tier-vm-instance   Ready   Drain         Reachable       29.1.3
  riag6q8hi2ntp6eyyhyk735xa *   homelab                 Ready   Active        Leader          29.7.2
  w265fymvy8amx3gepcizfzk0i     omarchy-normandy        Ready   Active        Reachable       29.7.2
  ```

- Node addresses (`docker node inspect`): oci 10.42.0.1, homelab 10.42.0.2, normandy 10.42.0.7, each also its manager address on 2377. `docker info` on homelab: `active 10.42.0.2`, 3 managers, pool `10.200.0.0/16`/24, `AutoLockManagers: True`. Listening on each: `<mesh>:2377/tcp`, `<mesh>:7946/tcp+udp`, `0.0.0.0:4789/udp` (kernel VXLAN socket, filtered).
- Reachability of 2377: from the laptop over the mesh (10.42.0.5) closed on all three; normandy to homelab and oci to normandy open.
- Nebula path: homelab to normandy 0.5 ms average over `nebula1` (direct on the LAN, not relayed through OCI); homelab to OCI 59 ms. Raft between the workers does not depend on OCI's relay.
- The data-path address cannot be read back from the CLI (`docker network inspect ingress --verbose` lists gossip peers by their mesh addresses). It is set by the role's init and join flags; batch S1 confirms it with `tcpdump -ni ens18 udp port 4789` on homelab while a service on normandy is reached through homelab. If VXLAN went to the mesh addresses instead, the firewalls drop 4789 on `nebula1` and S1 fails closed.
- OCI memory: before the join `free -m` available 528 MB, dockerd RSS 44 MB, containerd 19 MB; after the join available 507 MB, dockerd 74 MB (+30 MB for swarmkit and raft), containerd 19 MB. Above the 250 MB floor.
- OCI runs Engine 29.1.3 against 29.7.2 on the workers. Swarm accepts mixed versions; OCI runs no task, so only the raft and gossip protocol must agree. Recorded as a risk: upgrade OCI's Docker when Ubuntu's package catches up, never the workers past a version OCI cannot follow.
- `ansible-playbook playbooks/swarm.yml --check`, run twice: changed=0 on homelab, normandy and oci. `site.yml --check`: changed=0 on all four Docker hosts. `base.yml --check`: changed=0 on all six hosts.

## 5. Fix to the stack deploy tasks (2026-10-02, about 22:00 PDT)

Stack batch executors found two defects in `roles/swarm/tasks/stack.yml`; only that file changed.

1. `prepare_tasks` ran after the render, so an env_file, rendered configuration or `/srv/swarm/<name>` directory that a stack's `swarm-prepare.yml` creates did not exist when `docker stack config` needed it. New order: lint, deployment directory, stack file, secrets, environment; look for the Compose project and refuse (real run) or report (check run) when it exists and the run is not `-e swarm_cutover=<name>`; pin label; `prepare_tasks`; render; install `deployed.yml`; cutover only, `docker compose down`; deploy. Because the refusal now comes before `prepare_tasks`, a prepare file never runs in a real run while a Compose project exists and its cutover was not authorised. The role sets `swarm_stack_cutover` (true only for the authorised cutover) and states the contract in the file's header: outside the cutover a prepare file may only write the stack's own `/srv/swarm/<name>` files and deployment directory, and anything touching the Compose project or a running service is gated on `swarm_stack_cutover`. The batch prepare files read so far (vaultwarden) gate on `swarm_cutover` already.
2. Check mode failed for a stack never deployed: the render ran with `chdir` into a deployment directory that a check run does not create, and the deploy task's `chdir` failed the same way although the command itself is skipped. Check mode now renders in a scratch directory (`tempfile`, holding the stack file, the secrets and whatever already exists in the deployment directory; removed afterwards; not reported as a change), rewrites the scratch path to the deployment directory, shows the rendered stack with every service's `environment` removed (the install of `deployed.yml` is `no_log`, which hides its diff), compares it with `deployed.yml`, and reports "a real run would deploy" instead of running the deploy or `compose down`. A render that fails in check mode (for a file only a real run's prepare creates) is reported, not fatal. `delegate_to` of the Compose and pin lookups now defaults to the current host, because `delegate_to` is templated before `when` and failed for a stack without `compose` or `pin`.

Tests, check mode only, nothing deployed:

- a throwaway pending stack (`e20-throwaway`, source excalidraw, with a pin and a non-existent Compose project) through `-e @file`: rendered, "would deploy (not deployed yet)", changed=4 (all previews), failed=0;
- the S1 entry for excalidraw passed as active through `-e @file`: the cutover a real run would refuse reported, rendered with `published: 5090, mode: ingress`, "would deploy", failed=0;
- the F1 entry for vaultwarden (secrets, pin, `swarm-prepare.yml`) passed as active: its prepare ran in check mode (owner-marker lookup, seed block skipped without the cutover), rendered with its versioned secrets, failed=0;
- afterwards on homelab: no `~/swarm`, no `/srv/swarm/vaultwarden`, no `/tmp/swarm-check-*`, no `pin.*` label on any node, `docker stack ls` empty, the vaultwarden Compose container still `Up 2 hours (healthy)`;
- `ansible-playbook playbooks/swarm.yml --check` without extra vars: changed=0 on homelab, normandy and oci.

Note for executors: an entry passed through `-e @file` cannot use `{{ hostvars[...] }}` (extra vars are templated without `hostvars`; the vaultwarden entry failed until its `compose.dir` was written literally). The same entry in `inventory/group_vars/swarm/` is fine.

## 6. Image mirror and Forgejo lookup: checkpoint (2026-10-02, about 22:40 PDT, stopped for quota)

Done, in `roles/swarm` only:

- `registry_mirror*.yml` and `filter_plugins/swarm_images.py`: every external image named by a stack file of any `swarm_stacks_*` or `pending_swarm_stacks_*` entry is tagged on homelab and pushed to `git.ryzhkov.dev/igor/mirror/<registry>/<repo>:<tag>`; normandy pulls it and tags it under its original name only where it has none. Hooked into `registry_push.yml` and `registry_pull.yml` (no playbook change).
- `forgejo_container.yml`: `docker exec` target resolved from the Swarm service `forgejo_server` (node via its `host` label), falling back to the Compose container `forgejo`.
- `swarm.yml --tags registry --check`: changed=0 reported, failed=0. Not yet run for real.

Finding: a push from homelab's classic store does not keep a multi-platform pinned digest. Test push of excalidraw: pinned `sha256:f7ee194a...`, mirror `sha256:8f46c86c...` (same image ID). A digest-pinned Docker Hub reference can therefore not be satisfied from the mirror; such stacks must switch to the mirror path and mirror digest. One test image exists: `git.ryzhkov.dev/igor/mirror/docker.io/excalidraw/excalidraw:latest`.

Remaining: run `--tags registry` for real; Docker Hub images not on homelab (Hub refused homelab at 22:30) wait for the limit; tell executors which pinned Hub images need mirror references; confirm `swarm.yml --check` changed=0.

## 7. Image mirror run and Docker Hub references (2026-10-02, after the quota pause)

Lead decision: stacks using a Docker Hub image reference the Forgejo mirror, pinned to the mirror's own digest; E20 owns the change in every stack file.

- Docker Hub quota before the run (`HEAD /v2/ratelimitpreview/test/manifests/latest` from homelab): `ratelimit-remaining: 100;w=3600`, source 104.8.3.194; after the mirror run still 100: every image was already on homelab, nothing was pulled from Docker Hub.
- `ansible-playbook playbooks/swarm.yml --tags registry -l homelab`: 33 external images (16 Docker Hub, 17 ghcr.io, lscr.io and codeberg.org) tagged and pushed to `git.ryzhkov.dev/igor/mirror/<registry>/<repository>:<tag>` (changed=78, failed=0). The locally built images were untouched.
- Mirror changes made to the role on the way: references already rewritten to the mirror are listed for pre-pull by their exact digest; a node that already holds a stack's exact reference is left alone; the original name is tagged only when the node lacks that name (the first version re-tagged a pinned reference on every run).
- normandy: every mirrored image pre-pulled from Forgejo (changed=36, then 13 after the fix); all 16 Docker Hub mirror references resolve by digest on normandy. Second run of `--tags registry`: changed=0 on both workers.

Docker Hub images, upstream reference (and the upstream digest homelab holds for tag-only references) and mirror digest. Every mirror digest differs from upstream, because homelab's classic store pushes a single-platform manifest; the image IDs are identical.

| Stack | Upstream | Upstream digest | Mirror digest |
|---|---|---|---|
| vaultwarden | vaultwarden/server:1.37.3 | sha256:1587c45feaa4... | sha256:39894b6d6148d0b2583e4da429a61065f2b08dc3afbbc00f7265b5676e04f9b9 |
| actual | actualbudget/actual-server:26.9.0 | sha256:552beab3dec8... | sha256:b84e08e4e94a225f39a7e001fc2c364500a976a40be2cefc18e45561b4056fba |
| navidrome | deluan/navidrome:0.64.0 | sha256:a384948b81bd... | sha256:9a847e6f168cbde48358cd2069ffaf45f5a487d71a8ad081c501cbd28f382575 |
| komga | gotson/komga (digest only) | sha256:9cf102f5fb78... | sha256:3e2770daf09a67679f20e7c7f7aeb1c6bb9eec08d8b260b7b5cd024faa752135 |
| obsidian | couchdb:3.5.0 | sha256:1c0a30a54535... | sha256:33cade1ff68a3f191e2639a339957400cf825aba948eec57f696d28ae851a88d |
| logging loki | grafana/loki:3.7.8 | sha256:1107dd5274e0... | sha256:e664166bca56031661b40a0a0b1c77dfeffa4b03053cc887836dd4f51cb212ab |
| logging grafana | grafana/grafana:13.2.2 | sha256:ac461fb352ab... | sha256:9924c7fe0effe4a5fea08b3451905de54f09fde9d1589463a5f2d02d9ec158bc |
| logging influxdb | influxdb:2.9.1 | sha256:05d6fb735bc2... | sha256:cdf0f56ff18bc55c73c297d6a3092d461cb58009187e764def2ad89d6f3e0ac5 |
| miniflux db | postgres:17.11-alpine | sha256:b0f9560a2de0... | sha256:be3e03840bb22a93ac0c3b0003d88887e015d4d1707b06913bbf437d564a0e73 |
| miniflux | miniflux/miniflux:2.3.3 | sha256:49d7b6098761... | sha256:bb06d6abea8c2a8bd7b78d7b02d8ddc24425aab4d1afec8e3b87655242977a54 |
| authentik postgresql | postgres:16.15-alpine | sha256:721873c34ceb... | sha256:49c0be96b8c1cc13508a297288dee13b874298bf2ae34a81c392acd9c0f052b7 |
| authentik redis | redis:8.10.2-alpine | sha256:3811787313eb... | sha256:9edcda8e020021e916e9bd26acc90c61103cc93b6dedbb757cc206ddb320df6f |
| hugo | nginx:latest | sha256:abe47724e466... | sha256:4e4863e5f038304761ced85e0511553c88ea268650ee4bf85b759a3e7dc1c325 |
| excalidraw (active) | excalidraw/excalidraw:latest | sha256:f7ee194addd6... | sha256:8f46c86c80e6d2d3e166bef9f9bdc09da12ec0533d548d4de7317a15fa9e6675 |
| voice (active) | caddy:2-alpine | sha256:6aeddd44c307... | sha256:1bbeef4aae2a0815e51ef2e1675fb8db4e60fa3a2d009fd2127cedbc5f80cd42 |
| wyoming-piper (active) | rhasspy/wyoming-piper:latest | sha256:7d39aafac409... | sha256:1a32035b5a9c2efe19eae63aade8b07a57209d96da7e04bbc1176a71d912c7dd |

The full upstream digests are in the comment above each rewritten `image:` line.

- Rewritten (pending stacks, no service affected): vaultwarden, actual, navidrome, komga, obsidian, logging (3), miniflux (2), authentik (postgresql, redis), hugo. Each reads `image: git.ryzhkov.dev/igor/mirror/docker.io/<repo>@<mirror digest>` under a comment naming the upstream tag and digest. `inventory/group_vars/swarm/stacks-*.yml` holds no image keys, so no inventory change.
- Not rewritten: ghcr.io, lscr.io and codeberg.org images (not Docker Hub; mirrored and pre-pulled all the same), the locally built `git.ryzhkov.dev/igor/*` images.
- Active Docker Hub stacks excalidraw, voice and wyoming-piper: rewritten on the lead's go and redeployed one at a time with `ansible-playbook playbooks/swarm.yml --tags stacks -e only=<name>`. The active ghcr.io stacks (librespeed, kiwix, flaresolverr, wyoming-openai) need no change.

  | Stack | Deploy (PDT) | Old task shutdown, new task running (UTC) | Task gap | Seen by users | Runs on, image |
  |---|---|---|---|---|---|
  | excalidraw | 22:25:05 to 22:25:51 | 05:25:13.9, 05:25:45.9 | 32 s | https://draw.ryzhkov.dev probed every 0.5 s: 502 and timeouts 22:25:12 to 22:25:26, then 503 until 22:25:49 while Traefik's 30 s health check still marked both backends down; about 37 s in all | homelab, `git.ryzhkov.dev/igor/mirror/docker.io/excalidraw/excalidraw@sha256:8f46c86c...` |
  | voice | 22:26:53 to 22:27:09 | 05:27:04.1, 05:27:04.5 | 0.4 s | one failed probe at 22:27:05 (no Traefik health check on this route) | normandy, `.../library/caddy@sha256:1bbeef4a...` |
  | wyoming-piper | 22:28:03 to 22:28:26 | 05:28:12.9, 05:28:20.8 | 8 s | no Traefik route; Home Assistant's text-to-speech at 192.168.70.223:10200 unavailable for about 8 s plus the voice load. My TCP probe (`nc`) was faulty and failed before the deploy too; a Wyoming `describe` afterwards answered `{"type": "info", "version": "1.10.2", ...}` on both 192.168.70.223 and .234 | homelab, `.../rhasspy/wyoming-piper@sha256:1a32035b...` |

  `docker inspect` of each running task shows `Spec.ContainerSpec.Image` equal to the mirror reference above. Gatus (normandy's instance): `draw.ryzhkov.dev` and `voice.ryzhkov.dev` green on both probes for the last four results after the switch; Gatus has no check for wyoming-piper. The `nc` probe's closed connections left `ConnectionResetError` tracebacks in wyoming-piper's log; harmless.

  The excalidraw outage was longer than the task swap because Traefik keeps a backend down until its next health check (30 s interval). Stateless stacks would avoid the gap with `update_config.order: start-first` (allowed by the lint where there is no mount); that is the batch owners' choice.
- After the switches: `swarm.yml --check` changed=0 on homelab, normandy and oci; `stack-lint.py stacks/*/stack.yml` exit 0.
- `scripts/stack-lint.py stacks/*/stack.yml`: exit 0. `ansible-playbook playbooks/swarm.yml --check`: changed=0 on homelab, normandy and oci.

## Autolock and OCI rules

The migration plan carries the operating rules (unlock after a manager restart, no drain while OCI is unreachable). With three managers one may be down or locked and the cluster keeps quorum. Because OCI is also the Proxmox QDevice and the Nebula lighthouse, and the control plane now runs over Nebula, an OCI outage removes one Swarm vote and the QDevice vote together; the two workers still hold 2 of 3.

## Implications for later phases

- Phase 5 batches: Traefik reaches normandy only on 2283, 7391 and 8971 until Igor adds each Swarm-published port to UniFi's `traefik-normandy-ports` (and phase 4's second edge address to the source). Until then a service on normandy is reachable by Traefik only through the routing mesh on homelab.
- Phase 7: the drain runbook starts with the two-manager rule; a drain of proxmox-a demotes homelab first and needs the unlock key from `secrets/swarm.sops.yaml` on return.
- Forgejo's registry lives on homelab's root disk inside the twice-weekly B2 copy of `~/app`; excluding `forgejo/gitea/packages` from `rclone_exclude.txt` belongs to the backup batch.
- homelab's stale `rules.v4` raw table is restored at every boot before Docker; it should be removed or emptied in a maintenance window.
