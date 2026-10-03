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

## 8. Pruned local tags and mesh pushes to Loki (2026-10-03)

Background in `2026-10-03-F5.md`: the weekly docker-update sweep pruned homelab's local tags `dsh-dsh:latest`, `jocasta:37ce...`, `cv-site-app`, `openviking-openviking` and `whisper-cpp-dispatch:local`; the images remain under `git.ryzhkov.dev/igor/...`.

Repository changes:

- `inventory/group_vars/swarm/main.yml`: every `swarm_registry_images` source is the registry name itself; a comment records the rebuild path (build under the registry name, then `--tags registry`, which pushes because a rebuilt image has no registry digest yet). `swarm.yml --tags registry --check`: changed=0 on both workers.
- `inventory/host_vars/homelab.yml`: `jocasta_image: git.ryzhkov.dev/igor/jocasta:37ce657515092be80c5d49b4a3b30f482d86ed63`, matching the hand-edited `~/app/jocasta/.env`; `site.yml --check -e only=jocasta` shows the `.env` unchanged. `docs/jocasta.md`: load CI builds under the registry name.
- Compose files: `image: git.ryzhkov.dev/igor/<name>:latest` beside `build:` for dsh, cv-site and openviking; whisper's `image:` changed from `whisper-cpp-dispatch:local`. cv-site, openviking and whisper are Swarm stacks now, so their Compose files are not deployed.

Not converged, waiting for the lead: both remaining Compose projects would be recreated, with the same image ID.

- jocasta: the running container was created from `jocasta:37ce...`; any `docker compose up` (`site.yml`, or `jocasta-container.service` at boot) now recreates it because the image name in its configuration changed with the hand edit. `site.yml --check -l homelab -e only=jocasta`: `Bring the stack up` changed.
- dsh: the deployed Compose file still has no `image:`, so `site.yml --check` fails at dsh (`No such image: dsh-dsh:latest`, Compose's dry run on the old file). A real run installs the new file and recreates dsh (also picking up the `compose_logging_override` another executor added to `roles/compose_stack`).

Mesh pushes to Loki: toolfeedback, which UpKeeper ships to every fleet host in homelab-cli (found on gaming-pc and in a t3-steward workspace on homelab), pushes to `http://10.42.0.2:3100`. homelab's `SWARM-INGRESS` dropped `-i nebula1 -o docker_gwbridge`, which covers Loki's host-mode port. The role now has `swarm_mesh_published_ports` (3100/tcp) and `swarm_mesh_cidr` (10.42.0.0/24): on homelab `-A SWARM-INGRESS -s 10.42.0.0/24 -i nebula1 -o docker_gwbridge -p tcp --dport 3100 -j RETURN` ahead of the drop; on normandy `ufw route insert 1 allow in on nebula1 out on docker_gwbridge from 10.42.0.0/24 to any port 3100 proto tcp`. Applied with `swarm.yml --tags nodes -l homelab,normandy`. From the laptop (10.42.0.5): `curl http://10.42.0.2:3100/ready` 000 before, 200 after; `10.42.0.2:5090` still no answer (mesh block intact); `10.42.0.7:3100` no answer because Loki runs on homelab only.

Checks: `base.yml --check` changed=0 on all seven hosts. `swarm.yml --check`: normandy and oci changed=0; homelab reports five stacks (vaultwarden, navidrome, sonarr-tv, radarr-movie and one more) whose `deployed.yml` would change, all from another executor's uncommitted `swarm_stack_logging` addition to `roles/swarm/tasks/stack.yml`, not from this section. `site.yml --check`: changed=0 on blackpearl, normandy, public-api and public-api-b; homelab fails at dsh as above until the dsh and jocasta recreates are approved.

## 9. Review fixes (Ansible review, 2026-10-03)

1. Failed deploys are retried: `roles/swarm/tasks/stack.yml` compares the new rendering with `deployed.yml` (read with `slurp`), writes it to `deployed.yml.pending`, deploys from that file and renames it over `deployed.yml` only after `docker stack deploy` succeeded. A failed deploy leaves the old `deployed.yml`, so the next converge deploys again.
2. The rebuild leave path no longer restarts Swarm tasks: `roles/swarm/tasks/main.yml` lists Compose-labelled containers with their `com.docker.swarm.service.id` label and keeps only those without one (images built with `docker compose build`, such as openviking and cv-site, carry the Compose label into their Swarm tasks).
3. `scripts/stack-lint.py` refuses any `$` in `command`, `entrypoint` or `healthcheck.test` (`deployed.yml` is interpolated a second time at deploy, so even `$$` ends up as an empty variable, the cv-site lesson) and warns on `$` in the values of an env_file it can read beside the stack file. All 31 stack files pass (one warning: hugo's two replicas share a mount, by design); a test file with `$$HOME` in `command` and `$URL` in `healthcheck.test` is refused.
4. `playbooks/site.yml` is two plays sharing their vars and tasks through YAML anchors: `docker_hosts:!edge_proxy` at once, then `edge_proxy:&docker_hosts` with `serial: 1` (public-api, then public-api-b), so a Traefik static-configuration change never restarts both edges together.

Checks after the fixes: `swarm.yml --check` changed=0 on homelab, normandy and oci (the new pending/rename logic sees every deployed stack as unchanged); `site.yml --check` changed=0 on blackpearl, homelab, normandy, public-api and public-api-b. Nothing was converged for these fixes.

Recorded for the runbook:

- A cold rebuild of the Swarm (both workers lost, or `--force-new-cluster` with no stacks left) deploys `forgejo` first: it holds the registry, including every `mirror/*` image the other stacks name by digest. Until it answers, only nodes that already hold the images can start tasks.
- hugo's webhook handler (`roles/forgejo_webhooks/files/handlers/igor/blog.sh`) copies the built site to `normandy:/srv/swarm/hugo/public`. The `normandy` alias is already in the repository: homelab's `fleet_ssh_peers` includes normandy, so `roles/fleet_ssh` writes `Host normandy` (192.168.70.234, user igor) into homelab's `~/.ssh/config` block `ANSIBLE MANAGED: fleet peers`, and normandy's `fleet_ssh_authorized: [homelab]` authorises the key with `from="192.168.70.223"`. An older hand-written `Host normandy` entry higher in the same file is identical (plus the default `IdentityFile ~/.ssh/id_ed25519`); ssh uses the first match, so removing the hand-written one changes nothing.

## 10. Security review items (2026-10-03): survey only, nothing changed yet

### `/srv/swarm` permissions

Read-only survey on homelab and normandy (`stat`, `find -printf`, `docker inspect` of every Swarm task: configured user, PID 1 UID, bind mounts under `/srv/swarm`). Today `<pool>/swarm` and every stack directory are 0755 and most files 0644: other local users can read, among others, `vaultwarden/data/db.sqlite3` and `rsa_key.pem`, the *arr databases and `config.xml` files (API keys), forgejo's data, openviking's data, influxdb's and grafana's data.

Who writes each directory, and the proposed mode of the stack directory (the root `<pool>/swarm` becomes 0711 root:root on both workers):

| Stack directory | Writer (process UID) | Proposed |
|---|---|---|
| vaultwarden, actual, cv-site, gatus-public, lubelogger, openviking, komga, autobrr | root (PID 1 UID 0; komga's and autobrr's older files belong to 1000, which root still reads) | root:root 0700 |
| forgejo | the Forgejo server runs as `git`, UID 1000 (files owned by 1000); sshd as root | 1000:1000 0700 |
| bazarr, lidarr-music, ombi, prowlarr, radarr-movie, sonarr-tv | linuxserver `abc`, UID 911 (files 911:911) | 911:911 0700 |
| navidrome | 1000:988 (`user:` in the stack) | 1000:988 0700 |
| thelounge | `node`, UID 1000 | 1000:1000 0700 |
| obsidian | CouchDB, UID 5984 | 5984:5984 0700 |
| adguard (normandy) | UID 1001; its one file is already 0400 1001 | 1001:root 0700 |
| miniflux | Postgres, UID 70 | 70:70 0700 |
| authentik | Postgres 70 (`postgres/`), Redis 999 (`redis/`), server and worker 1000 (`media/`, `templates/`, `certs/`) | stack directory root:root 0711; each subdirectory owned by its writer, 0700 |
| logging | Loki 10001 (`loki/`), Grafana 472 (`grafana/`), InfluxDB 1000 (`influxdb/`); `config/loki-config.yml` read by 10001 | stack directory root:root 0711; data subdirectories owned by their writer, 0700; `config/` 0755 (no secrets) |
| hugo, voice, whisper, wyoming-piper | public content (the site, a static page, model files, voices) written by the deploying user and read by nginx (101) or caddy | unchanged, 0755 |

Root readers are unaffected: the hourly snapshot, the peer copy and the move playbook run as root. Plan: a per-stack map in `roles/swarm` defaults applied by `state_root.yml` on whichever node holds the directory, applied one stack at a time with the service's health check and a write test after each (a vaultwarden login, an *arr config save, a Forgejo push, a Grafana dashboard save). Waiting for the lead's go.

### Built images in Forgejo pulled anonymously

Anonymous `GET /v2/igor/<image>/manifests/<tag>` with an anonymous token answers 200 for cv-site, dsh, openviking, whisper-server and jocasta (and for the `mirror/*` images). `git.ryzhkov.dev` is published through the Cloudflare tunnel, so this is anonymous from the Internet.

Secrets check, read-only (`docker image inspect` env and labels; `docker save` of each image with every layer's file list searched for `.env`, `*.pem`, `*.key`, SSH keys, `credentials`, `tokens.json`, `ov.conf`, `*secret*`, `.netrc`, `.npmrc`, `.docker/config.json`, outside library and certificate paths; `docker history --no-trunc` for ARG and secret-looking build steps):

- dsh, openviking, whisper-server, jocasta: no secret found. Environment keys are PATH and runtime versions only (openviking's `GPG_KEY` is the Python base image's public signing-key fingerprint); labels are Compose or OCI metadata; no key or credential files; no secret build arguments.
- cv-site: no key or token (`app/data/tokens.json` is 2 bytes, an empty JSON value), but the image bakes the site's whole `app/data` directory as it was at build time (2026-09-09): `analytics.jsonl` (79.8 MB of visitor analytics), `contacts.jsonl` (750 bytes, contact-form submissions) and `contacts.json`, `projects.json`. That is personal data, publicly pullable. The running service bind-mounts `/srv/swarm/cv-site/data` over that directory, so the baked copy is not needed.

Making the packages private: Forgejo package visibility follows the owner, and `igor` is a public user that also owns the public repositories, so it means a private organisation, re-pushing five images (about 2.7 GB) under it, changing the references (`swarm_registry_images`, `jocasta_image`, the cv-site, openviking and whisper stack files, the dsh Compose file), a pull token valid for the organisation on normandy, and redeploying cv-site, openviking and whisper and recreating dsh and jocasta, each a service restart needing the lead's go. That is more than an hour with its restarts, so per the brief it is carried, not done. Recommended first step regardless, and cheap: rebuild cv-site with `data/` in `.dockerignore` and delete the old cv-site package versions from Forgejo, which removes the public copy of the personal data.

#### Privacy finding: cv-site's image carried the site's data (fixed 2026-10-03, 10:40 to 10:55 PDT)

From 2026-09-09 until 2026-10-03 the cv-site image in Forgejo (`git.ryzhkov.dev/igor/cv-site:latest`, pullable without authentication from the Internet through the Cloudflare tunnel since phase 5 pushed it on 2026-10-02 20:20) contained `/app/data` as it was at build time: `analytics.jsonl` (79.8 MB of visitor analytics), `contacts.jsonl` (750 bytes of contact-form submissions), `contacts.json`, `projects.json` and an empty `tokens.json`. The cause is the last line of the checkout's Dockerfile, `COPY --from=builder /app/data ./data`. Forgejo keeps no access log of package pulls that would show whether anyone fetched it.

Fix, on the lead's go:

1. `stacks/cv-site/docker-compose.yml` (the repository's build path for the image) now builds with an inline Dockerfile identical to the checkout's except that the final stage runs `RUN mkdir data` instead of copying it; the service bind-mounts `/srv/swarm/cv-site/data` at run time. The checkout (`~/cv-site`, GitHub `iryzhkov/cv-site`, HEAD 4b88ec2, the commit the old image was built from 20 s after it was made) is unchanged; its own Dockerfile still copies `data/` and should be fixed upstream.
2. Built on homelab with `docker compose --project-directory ~/cv-site -f <repository file> build app` (image `sha256:dc5fed07...`, `/app/data` empty), pushed and pre-pulled with `swarm.yml --tags registry` (manifest `sha256:22709eb93e941a822c7b1403b64da018a6bd5ef2d25202bd010e5c6040f4351d`; homelab changed=23 because the sweep's prune had also removed homelab's local `mirror/*` tags, which the play re-created and found already in the registry; a second run changed=0).
3. `stacks/cv-site/stack.yml` pinned to that digest; `swarm.yml --tags stacks -e only=cv-site` rolled the service once at 10:44:05 PDT. https://igor.ryzhkov.dev answered 502 from 10:44:15 to 10:44:20 (about 5 s); then `/`, `/photos` and `/admin` answered 200 directly on 192.168.70.223:8091 and through Traefik from the LAN; `analytics.jsonl` on `/srv/swarm/cv-site/data` kept growing (112130456 bytes at 10:45). The task runs `git.ryzhkov.dev/igor/cv-site:latest@sha256:22709eb9...`.
4. Old versions: Forgejo lists one cv-site version (`latest`, created 10:43:06), because a container tag is replaced in place; the old manifest `sha256:f696f11b...` answers 404, and the old data layer (blob `sha256:558a314f23adaccd8e9c4a95593a6eb894655641aa28c4b9a79761aaaef59aa5`, from homelab's distribution metadata) answers 404 to an anonymous request. Forgejo's package cleanup job removes the unreferenced blob file from disk later; it is no longer served.
5. Anonymous check of what is served now: for cv-site:latest and for dsh:latest, openviking:latest, whisper-server:latest and jocasta:37ce6575..., every layer of the current manifest was fetched with an anonymous token and its file list searched for `analytics.jsonl`, `contacts.jsonl`, any file under `data/` or `app/data/`, `.env`, `projects.json` and `tokens.json`: no hit in any image (7, 9, 6, 5 and 3 layers).

Carried: the move of the five built images to a private organisation (above). Until then the built images stay anonymously pullable, now without baked data or secrets.

#### cv-site's Immich address (2026-10-03, 11:25 PDT)

`IMMICH_URL` in `inventory/group_vars/swarm/stacks-f4.yml` was the literal `http://192.168.70.234:2283` and would break when Immich moves to homelab. It is now `https://photos.ryzhkov.dev` (through the edge proxy; from inside the running task `wget https://photos.ryzhkov.dev/api/server/ping` answered `{"res":"pong"}` first). Rolled with `swarm.yml --tags stacks -e only=cv-site` at 11:25:01: two failed probes of https://igor.ryzhkov.dev (11:25:18 to 11:25:19). The task's environment shows the new value; `/photos` answers 200 directly and through Traefik, and a thumbnail it lists (`/photos/thumb/c0f858a2-...`) is served as `image/webp`, 17706 bytes. The image roll above and this one were two separate rolls, because the item arrived after the first.

### `/srv/swarm` permissions applied (2026-10-03, 10:47 to 11:23 PDT)

`roles/swarm/tasks/state_root.yml` now closes every private stack directory (`root:root 0700`, not recursive) and leaves `swarm_state_public_stacks` (hugo, voice, whisper, wyoming-piper) as they are. Per-stack ownership by the service's UID turned out unnecessary: a container never traverses the host directories above its bind mounts (dockerd resolves the source as root when it creates the task), and the writer-owned subdirectories keep their owners and modes. Everything on the host that reads the state runs as root.

Applied one stack at a time with `ansible-playbook playbooks/swarm.yml --tags nodes -l <node> -e swarm_state_only=<stack>`, each followed by the Traefik route (before and after) and a write test: `docker exec -u <owner of the mount source>` touching and removing a file in each writable `/srv/swarm` bind of each running task. On homelab, in this order, all 200 (or 302 for logging and authentik) before and after and every write ok: vaultwarden, actual, autobrr, bazarr, cv-site, forgejo, komga, lidarr-music, lubelogger, navidrome (2 mounts), obsidian, ombi, openviking (no route; write ok), prowlarr, radarr-movie, sonarr-tv, thelounge, miniflux, logging, authentik (7 mounts). logging's Loki mount reported a failed write test only because the Loki image has no `sh`; Loki's directory showed 10 files written in the following two minutes and `/ready` answered 200. On normandy: adguard and gatus-public (single-file mounts; route 200 before and after, tasks not restarted). homelab also held old copies of adguard's and gatus-public's directories from the S2 staging (adguard's holds the credentials file), now closed too; their `swarm-prepare.yml` files now create the directory with 0700, so the next deploy does not reopen it. No task restarted during the work.

Reverted: the state directory itself (`<pool>/swarm`) was set to 0711 at 10:47 as the brief asked, and went back to 0755 at 11:23. With 0711, rsync 3.5.0 on normandy (Arch) refused to receive into `/srv/swarm/hugo/public/` as igor (`change_dir#1 "/srv/swarm/hugo/public/" failed: Permission denied (13)`), because it opens each directory of the receiving path for reading; homelab's rsync 3.2.7 was unaffected. That broke the copies the deploying user makes into the public stacks: hugo's site from the Forgejo webhook, and voice, whisper and wyoming-piper on a redeploy. No blog build ran in that window (webhook log empty), and after the revert `swarm.yml --tags stacks -e only=hugo` synced normandy's copy again. The stack names a 0755 directory lists are no secret; the 0700 stack directories are what protects the data. My own write test had also touched `/srv/swarm/hugo/public` on both workers, which the same run's rsync put back.

Checks afterwards: `swarm.yml --check` changed=0 on homelab, normandy and oci; `base.yml --check` changed=0 everywhere; `site.yml --check` changed=0 on blackpearl, public-api and public-api-b, and changed=2 on homelab and normandy, both the `gatus` stack's rendered configuration and its restart, from another executor's inventory changes in the working tree (`inventory/group_vars/all.yml`, `inventory/host_vars/*.yml`), present before this work started.

## Autolock and OCI rules

The migration plan carries the operating rules (unlock after a manager restart, no drain while OCI is unreachable). With three managers one may be down or locked and the cluster keeps quorum. Because OCI is also the Proxmox QDevice and the Nebula lighthouse, and the control plane now runs over Nebula, an OCI outage removes one Swarm vote and the QDevice vote together; the two workers still hold 2 of 3.

## Implications for later phases

- Phase 5 batches: Traefik reaches normandy only on 2283, 7391 and 8971 until Igor adds each Swarm-published port to UniFi's `traefik-normandy-ports` (and phase 4's second edge address to the source). Until then a service on normandy is reachable by Traefik only through the routing mesh on homelab.
- Phase 7: the drain runbook starts with the two-manager rule; a drain of proxmox-a demotes homelab first and needs the unlock key from `secrets/swarm.sops.yaml` on return.
- Forgejo's registry lives on homelab's root disk inside the twice-weekly B2 copy of `~/app`; excluding `forgejo/gitea/packages` from `rclone_exclude.txt` belongs to the backup batch.
- homelab's stale `rules.v4` raw table is restored at every boot before Docker; it should be removed or emptied in a maintenance window.
