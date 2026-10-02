# Phase 0: hard-coded address inventory

Date: 2026-10-02, 12:58 to 13:10 PDT.
Run from: the laptop. Everything below was read only; no reference was changed.
Addresses searched: 192.168.70.223 (homelab), 192.168.70.234 (normandy), 192.168.20.33 (public-api), their mesh addresses 10.42.0.2 (homelab) and 10.42.0.3 (public-api), and host names used in a way that pins a service to one host. normandy has no mesh address in the inventory.

## How each source was read

| Source | Command or method | Recorded answer |
|---|---|---|
| homelab-infra | Huyang regex search over the whole repository for the five addresses, then for `ansible_host`, `mesh_host`, `hostvars['<host>']`, `_host: <host>`, `hosts: <host>`, `HOST_IP`, `edge_proxy_ip`, Loki and dnsmasq; `docs/transition` excluded | 19 literal hits: 5 are the inventory definitions, 4 are live configuration, 2 are AdGuard reference copies, 2 are comments and 6 are in docs |
| Home Assistant | `ssh home-assistant`, `tar` of `/config` without the database, logs, `deps`, `custom_components` and `secrets.yaml`, unpacked in `/tmp` on the laptop, searched there, then deleted; `secrets.yaml` searched on the host with `grep -n` for the addresses only | 8 references to homelab, 1 to public-api; none to normandy. No Frigate or Immich integration is configured in HA (no config entry, no custom component); MQTT points at 192.168.70.241, which is not one of these hosts |
| cloudflared | `./scripts/snapshot-cloudflare.sh` (read-only API token), `jq` over the tunnel configuration; `docker inspect cloudflared` and `getent hosts` on homelab and normandy, where the two connectors run | No origin names an address. All 17 origins are `https://<name>.ryzhkov.dev`, which both connector hosts resolve through AdGuard's wildcard rewrite to 192.168.20.33. Cloudflare DNS holds no private address; `emby` points at OCI |
| Loki push targets | `/etc/docker/daemon.json`, `docker plugin ls` and each running container's `LogConfig` on homelab, normandy, public-api, blackpearl, gaming-pc, oci; promtail on both Proxmox nodes and inside every LXC | Every shipper names 192.168.70.223:3100 literally, except homelab itself (`localhost:3100`). gaming-pc uses `json-file`, oci ships nothing |
| Gatus and CT 110 | `stacks/gatus/templates/config.yaml.j2`; deployed `~/app/gatus/config.yaml` on homelab and normandy; `ssh proxmox-a pct exec 110 -- cat /etc/init.d/healthcheck` | Gatus is generated from the inventory. CT 110 hard-codes five targets on two hosts |
| NFS clients | `showmount -e 192.168.40.40` from normandy (homelab has no `showmount`) | homelab and normandy are listed literally on all four exports; public-api on none. The UniFi Drive API and UI were not reached read-only, so the list was not cross-checked against UniFi Drive's own settings, and squash/permission options are not visible |

## Inventory

Columns: address; location (system, file:line or object); purpose; how it is set today; proposed replacement; phase that must change it. "Variable" means the value comes from `inventory/hosts.yml` through `ansible_host`, `mesh_host` or a derived variable; a variable that names a host by inventory name is still listed, because it pins the service to that host.

### homelab-infra: the inventory itself (source of truth, stays)

| Address | Location | Purpose | Set today | Proposed replacement | Phase |
|---|---|---|---|---|---|
| 192.168.70.223 | `inventory/hosts.yml:22` | homelab `ansible_host` | literal, the definition | stays; it is the host's identity | none |
| 10.42.0.2 | `inventory/hosts.yml:23` | homelab `mesh_host` | literal, the definition | stays | none |
| 192.168.20.33 | `inventory/hosts.yml:28` | public-api `ansible_host` | literal, the definition | stays; add `edge_vip` beside it | 4 |
| 10.42.0.3 | `inventory/hosts.yml:29` | public-api `mesh_host` | literal, the definition | stays | none |
| 192.168.70.234 | `inventory/hosts.yml:34` | normandy `ansible_host` | literal, the definition | stays | none |

### homelab-infra: literals in live configuration

| Address | Location | Purpose | Set today | Proposed replacement | Phase |
|---|---|---|---|---|---|
| 192.168.70.223 | `inventory/group_vars/all.yml:122` | `from=` restriction on homelab's fleet SSH key | literal | `{{ hostvars['homelab'].ansible_host }}` (plus `mesh_host`) | before 5 |
| 10.42.0.2 | `inventory/group_vars/all.yml:147` | `nebula_probe_hosts`, peer pinged after a Nebula restart | literal | `{{ hostvars['homelab'].mesh_host }}` | before 5 |
| 192.168.70.223 | `stacks/traefik/dynamic/t3.yml:95` | Traefik backend `t3-homelab` (T3 server :7391) | literal | template from `hostvars['homelab'].ansible_host`, like `roles/traefik/templates/*.j2` | before 5; target may move in 6 |
| 192.168.70.234 | `stacks/traefik/dynamic/t3.yml:105` | Traefik backend `t3-normandy` (:7391) | literal | template from `hostvars['normandy'].ansible_host` | before 5; target may move in 6 |
| 192.168.20.33 | `roles/adguard/files/reference/ct107.yaml:230`, `ct201.yaml:230` | reference copies of the live AdGuard config (wildcard rewrite) | literal, reference only; the deployed value comes from `edge_proxy_ip` in `roles/adguard/templates/AdGuardHome.yaml.j2:232` | regenerate the references after the change | 4 |
| 192.168.20.33, 10.42.0.3 | `stacks/traefik/dynamic/t3.yml:12-13` | comment | prose | update with the VIP | 4 |

### homelab-infra: variables that pin a service to one host

| Address | Location | Purpose | Set today | Proposed replacement | Phase |
|---|---|---|---|---|---|
| 192.168.20.33 | `inventory/group_vars/all.yml:113` `edge_proxy_ip` = `groups['edge_proxy'][0]` | AdGuard wildcard answer, Gatus "Edge proxy TCP" | variable, first edge member | `edge_vip` (plan phase 4 step 3) | 4 |
| 10.42.0.3 | `stacks/jocasta/templates/jocasta-firewall.service.j2:11-12` `groups['edge_proxy'][0].mesh_host` | only source allowed into Jocasta's backend (deployed as `-A JOCASTA -s 10.42.0.3/32 -j RETURN` on homelab) | variable, first edge member | every `edge_proxy` member's `mesh_host` | 4 |
| 192.168.70.223 | `inventory/group_vars/all.yml:127` `forgejo_host: homelab`, used by `roles/forgejo_client/tasks/main.yml:52,63-64` | Forgejo SSH `HostName` and pinned `known_hosts` entry on clients | variable naming a host | `stack_host_ip('forgejo')`, or the edge VIP name once SSH is proxied | 5 |
| 192.168.70.223 | `inventory/group_vars/all.yml:140-141` `feed_host: homelab`, `feed_url` | fleet feed URL used by the drift check | variable naming a host | derive from wherever the feed is placed | 5 |
| 192.168.70.223 | `inventory/group_vars/all.yml:151` `notify_relay_host: homelab`, used by `roles/notify/tasks/main.yml:44-45` | notify-relay URL | variable naming a host | same pattern as the feed | 5 |
| 192.168.70.234 | `stacks/gatus/templates/config.yaml.j2:113,123` `hostvars['normandy']` | steward coordinator and worker health probes (:3003) | variable naming a host | `steward_host` variable | 6 |
| 192.168.70.223, .234 | `stacks/gatus/templates/config.yaml.j2:150,165` | each LAN Gatus probes the other's `/health` (:3002) | variable naming a host | Gatus probe group in inventory | 5 |
| (normandy) | `stacks/gatus/templates/config.yaml.j2:25,47,66,81,98,109,119,129,138,146` `inventory_hostname == 'normandy'` | only normandy's Gatus alerts | host name | `gatus_alerting: true` host variable | 5 |
| 192.168.70.223, .234 | `playbooks/gatus-influx.yml:9,11` | InfluxDB scrapers for the homelab and normandy Gatus metrics | variable naming a host | Gatus probe group | 5 |
| (homelab, normandy) | `hosts:` lines in `playbooks/adopt-flaresolverr.yml:16`, `gatus-influx.yml:5`, `gatus-oci.yml:5`, `helper-maintenance.yml:4`, `gatus-steward-health.yml:3`, `upkeeper-drift.yml:4` | where these playbooks run | host name | inventory groups | 5 (6 for the steward ones) |
| 192.168.70.223 / .234 | `roles/traefik/templates/generated.yml.j2:34`, `whisper.yml.j2:62`, `stacks/gatus/templates/config.yaml.j2:30,35`, `roles/compose_stack/tasks/main.yml:86` (`HOST_IP`), `filter_plugins/stacks.py:29` (`stack_host_ip`), `inventory/host_vars/homelab.yml:116,123` | every Traefik backend, internal Gatus check and `HOST_IP` bind follows the host the stack is listed under | variable, follows the stack | plan phase 5 step 5: both worker addresses per service | 5 |

### homelab-infra: documentation only

`docs/current-state.md:172`, `docs/gatus-trial.md:5`, `docs/traefik-routing.md:24,130`, `docs/offsite-bundle.md:61`, and comments in `stacks/diun-*/docker-compose.yml:14` (`homelab:8090`, a retired endpoint). Prose; updated when the values change, no phase gate.

### Host configuration outside the repository

| Address | Location | Purpose | Set today | Proposed replacement | Phase |
|---|---|---|---|---|---|
| 192.168.70.223 | normandy, public-api, blackpearl `/etc/docker/daemon.json` `log-opts.loki-url` = `http://192.168.70.223:3100/loki/api/v1/push` (13 containers) | Docker Loki driver default for every container | literal, hand-managed | base role writes `loki_push_url` (plan phase 5 step 4) | 5 |
| (homelab) | homelab `/etc/docker/daemon.json` `loki-url` = `http://localhost:3100/...` (36 containers) | same, homelab ships to itself | literal loopback; pins Loki to homelab | `loki_push_url` | 5 |
| 192.168.70.223 | proxmox-a and proxmox-b `/etc/promtail/config.yml:9` | journal and PVE task logs | literal, hand-managed | `loki_push_url` | 5 |
| 192.168.70.223 | `/etc/promtail/config.yml:7` in CT 100, 104, 107, 110 (proxmox-a) and CT 201 (proxmox-b) | LXC logs | literal, hand-managed | `loki_push_url` | 5 |
| 192.168.20.33 | CT 110 `/etc/init.d/healthcheck` `command_args` `traefik=https://192.168.20.33:443` | healthcheck target | literal | `edge_vip` | 4 |
| 192.168.70.223 | CT 110 `/etc/init.d/healthcheck` `authentik=:4080, vaultwarden=:8099, blog=:4580, cv-site=:8091` | healthcheck targets | literal | `https://<name>.ryzhkov.dev` through the edge VIP, or retire CT 110 in favour of Gatus | before 5 |
| 192.168.20.33 | AdGuard CT 100, CT 107 (proxmox-a), CT 201 (proxmox-b) `/opt/AdGuardHome/AdGuardHome.yaml:231` rewrite `*.ryzhkov.dev` | LAN name resolution for every service, including both cloudflared connectors | variable (`edge_proxy_ip`, converged by `roles/adguard`) | `edge_vip` | 4 |
| 10.42.0.3 | public-api `/etc/dnsmasq.d/nebula-dns.conf:7,13` (`listen-address=10.42.0.3`, `address=/ryzhkov.dev/10.42.0.3`) | mesh clients resolve every service to public-api's mesh address | literal, hand-managed, not in the repository | a mesh answer for the second edge VM; a VRRP VIP cannot float on Nebula, so mesh clients stay pinned to one edge VM unless this is solved separately | 4 |
| 192.168.20.33 | public-api `~/apps/traefik/.env` `HOST_IP` | Traefik's bind address | variable (`compose_stack` writes `HOST_IP`) | bind all addresses or the VIP (plan phase 4 step 2) | 4 |
| 192.168.20.33 | gaming-pc `ufw`: `18800/tcp ALLOW 192.168.20.33 # bellerophon-cartest via edge traefik`; referenced in public-api `file_provider/cartest.yml:7` | only the edge proxy may reach the cartest backend | literal, hand-managed | allow every edge VM's address | 4 |
| 192.168.20.33 | homelab `~/app/traefik/file_provider/homelab.yml:276` | leftover from the retired homelab Traefik (`docs/current-state.md:80`); no container reads it | literal, dead | delete | any |
| 192.168.70.223 | Home Assistant `configuration.yaml:15` `media_player: platform: emby, host:` | Emby media player | literal YAML | `emby.ryzhkov.dev` through the edge VIP, or an HA `!secret` holding the Emby host | 5 |
| 192.168.70.223 | Home Assistant `packages/requests.yaml:29,34,39,44` `rest_command` `emby_search`, `emby_next_up`, `emby_sessions`, `emby_play` (`http://192.168.70.223:8096`) | Emby API calls | literal YAML | same as above | 5 |
| 192.168.70.223 | Home Assistant `.storage/core.config_entries`, `influxdb` entry `url: http://192.168.70.223:8087` | HA state history into InfluxDB | UI (config entry) | a stable name for InfluxDB, through Traefik or a DNS record | 5 |
| 192.168.70.223 | Home Assistant `.storage/core.config_entries`, `wyoming` entry "openai" `host: 192.168.70.223, port: 10300` | voice speech-to-text | UI | a stable name or VIP for wyoming-openai (plain TCP, not HTTP) | 5 |
| 192.168.70.223 | Home Assistant `.storage/core.config_entries`, `wyoming` entry "piper" `host: 192.168.70.223, port: 10200` | voice text-to-speech | UI | same | 5 |
| 192.168.20.33 | Home Assistant `.storage/http` `trusted_proxies: [192.168.1.223/32, 192.168.20.33/32]` | lets HA trust `X-Forwarded-For` from Traefik | UI (HTTP settings) | add the second edge VM and the VIP | 4 |
| 192.168.70.223, 192.168.70.234 | NAS 192.168.40.40 (UniFi Drive) NFS allowed clients on all four exports (`public`, `services_backup`, `machine_learning`, `media`) | which hosts may mount | UniFi Drive UI | stays per host address (an NFS client list cannot use a VIP); new VMs that need exports are added by address | 3, if a VM's address changes; 6 for agent VMs |
| none | cloudflared ingress, tunnel `0e6276f1-7125-44d7-816b-fd74f23d1074` (connectors on homelab and normandy) | 17 public hostnames | Cloudflare dashboard; origins are names, not addresses | nothing to change in Cloudflare; the origins follow the AdGuard rewrite above | 4 (through AdGuard) |

The Home Assistant `.storage/auth` file also records `last_used_ip: 192.168.70.223` on two refresh tokens. That is a record of past use, not configuration, and needs no change.

## Recorded answers

- homelab-infra: the inventory is already the single source for nearly every address. Four live literals remain outside it (`all.yml:122`, `all.yml:147`, `t3.yml:95`, `t3.yml:105`), plus the two AdGuard reference copies; the rest are variables, of which about a dozen pin a service to one host by name.
- Home Assistant: Emby (YAML, 5 lines), InfluxDB and both Wyoming services (UI config entries) name homelab's address; `trusted_proxies` names public-api. Frigate and Immich are not integrated in HA.
- cloudflared: no origin names an address; failover depends on the AdGuard wildcard answer.
- Loki: 3 Docker hosts, 2 Proxmox nodes and 5 LXCs push to 192.168.70.223 literally, all hand-managed.
- Gatus is inventory-derived; CT 110 hard-codes public-api once and homelab four times.
- NFS: homelab and normandy are listed on all four exports; public-api is not on any. UniFi Drive's own settings were not read.

## Not covered

- UniFi firewall policies and DHCP that name these addresses (plan item 4, another deliverable).
- Nebula configurations on mesh clients (phone, iPad, laptop) that may name 10.42.0.3 as their DNS server.
- Home Assistant OS's own log shipping, if any, which was not inspected.

## Implications for later phases

- Phase 4: besides `edge_proxy_ip`, the edge VIP has to reach five places outside the AdGuard rewrite: HA `trusted_proxies`, gaming-pc's ufw rule for 18800, CT 110's Traefik check, the Jocasta firewall source list, and the Traefik `HOST_IP` bind. The mesh path (public-api's dnsmasq answering 10.42.0.3) cannot use a VRRP VIP and needs its own decision.
- Phase 5: Loki's push URL is literal and hand-managed on 10 shippers outside the repository; it has to become a stable name or address before Loki can move. Wyoming (TCP) and InfluxDB in Home Assistant are not behind Traefik, so they need a stable address that follows the service, not just a host name.
