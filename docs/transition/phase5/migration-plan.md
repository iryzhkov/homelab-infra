# Phase 5 migration plan: Compose stacks to Swarm

Date: 2026-10-02 (PDT). Author: executor E20. Basis: the survey in `2026-10-03-foundation.md` (every container on homelab, normandy, blackpearl and public-api inspected), plan phase 5 revision 4, and the phase 0 to 3 acceptance records.

The Swarm exists: three autolocked managers, homelab and normandy (workers, labels `gpu=intel`, `host`, `pool`) and OCI (`availability drain`), control plane on the Nebula addresses, VXLAN data path on VLAN 70, overlay pool 10.200.0.0/16. This document says, for every stack, whether it becomes a Swarm service, where it runs, how its state is protected, and which executor converts it with which files.

## Where the plan's assumptions fail

- **OCI as third manager.** Not possible as written (the workers' VLAN 70 addresses are unreachable from OCI). Resolved by the lead: raft and gossip advertise and listen on the Nebula addresses of all three managers, the workers' VXLAN data path stays on VLAN 70, OCI is drained and carries no VXLAN. Three managers, so one may be down.
- **"Stateless" stacks with state.** cv-site appends to `data/analytics.jsonl` (stateful-files). thelounge keeps 1.9 GB including `logs/igor.sqlite3` (stateful-sqlite). gatus is a per-host cross-probe by design and stays Compose. wyoming-piper's volume is a 671 MB voice cache (stateless with a re-downloadable cache).
- **"SQLite services already have the hourly snapshot."** Only vaultwarden, navidrome and actual are hourly. Forgejo, the *arr apps, autobrr, ombi, komga and emby are in the twice-weekly `bulk-sqlite` job (Mon and Thu). Jocasta has its own backup unit. OpenViking is a vector store plus files, not SQLite. obsidian-livesync is CouchDB. lubelogger is LiteDB. Grafana's SQLite is on NFS (`/mnt/backup/grafana`), which the plan forbids for databases.
- **"The only change to the backup tier is the second destination."** Not enough: a stateful service can now run on either worker, so the hourly tier has to run on both, snapshot only what is pinned locally, and read state from `/srv/swarm`. Postgres needs a new `pg_dump` entry. Design below.
- **Immich server and its Postgres as stateful-postgres.** immich-server bind-mounts `/dev/dri` for video transcoding, which does not work in a Swarm service. It also reaches immich-machine-learning (GPU, Compose) by service name on `immich_default`. The whole Immich stack stays compose-pinned on normandy, and its daily `pg_dump` stays.
- **The fleet feed as a stateless stack.** It is `feedd.service`, a systemd user service on homelab, not a container. It stays where it is; converting it means building an image first. Out of this phase.
- **`docker compose config | docker stack deploy -c -`.** It fails on Docker 29: Compose renders published ports as strings. `docker stack config` with the variables in its environment does the same job; `roles/swarm/tasks/stack.yml` uses it.
- **hugo as a locally built image.** It is `nginx:latest` serving a directory that the Forgejo webhook builds on homelab.
- **Traefik targeting both workers.** UniFi lets the edge proxy reach normandy only on 2283, 7391 and 8971. Every port a Swarm stack publishes must join `traefik-normandy-ports` before Traefik's normandy backend can be healthy. That is Igor's change, not an executor's. Until then Traefik reaches every Swarm service through homelab's address and the routing mesh, which works while homelab is up and not during a proxmox-a drain.
- **Routing mesh exposure.** A published port answers on every interface of every node, including normandy's VLAN 60 NIC and both nodes' `nebula1`, and bypasses ufw. The foundation already drops forwarded traffic from those interfaces to `docker_gwbridge` (homelab `SWARM-INGRESS`, normandy ufw route rules). Batch S1 must prove it with a probe from black-pearl (VLAN 60) and over the mesh.
- **Control plane over Nebula.** Raft and gossip now depend on Nebula being up on the managers. A Nebula restart on one manager is a short partition of that manager only; a restart on two at once (a fleet-wide Nebula config change) loses quorum for its duration. Nebula changes on homelab, normandy and OCI are done one host at a time.

## Conventions for every stack

- Stack file: `stacks/<name>/stack.yml`, beside the existing `docker-compose.yml`, which stays until cleanup. Optional `stacks/<name>/swarm-prepare.yml` for the stack's own preparation (seeding state).
- Inventory: `inventory/group_vars/swarm/stacks-<batch>.yml` defines exactly one variable, `swarm_stacks_<batch>`, a list of entries. Keys are documented at the top of `roles/swarm/tasks/stack.yml`: `name`, `source`, `env_extra`, `secrets`, `pin`, `compose: {host, dir, project}`, `prepare_tasks`, `routes` (same shape as host_vars routes), and `snapshot` (below).
- Deploy: `ansible-playbook playbooks/swarm.yml --tags stacks -e only=<name>`. The first run refuses while the Compose project exists. The cutover run adds `-e swarm_cutover=<name>`, which removes the Compose project (volumes kept) and deploys the stack. Ask the lead before every cutover run; the lead serialises them fleet-wide.
- Lint: `scripts/stack-lint.py stacks/<name>/stack.yml` runs as the first task of every deploy. It refuses a stack without `deploy.placement.constraints` or `deploy.resources.limits.memory`, and any `devices:`, `/dev` bind, `privileged`, `build`, `network_mode`, address-bound port, or `start-first` with a mount.
- Placement. Stateless: `node.labels.gpu == intel`, `replicas: 1` (2 with `max_replicas_per_node: 1` for the few named below), default update order. Stateful: `node.labels.pin.<name> == true`, `replicas: 1`, `update_config.order: stop-first`, `restart_policy.condition: any`. The pin label is set once by `pin:` in the inventory and moved afterwards only by the move playbook.
- State: `/srv/swarm/<name>/...` binds (the symlink to `<pool>/swarm` on both workers). Never a named volume for durable state, never NFS for a database. Bind sources must exist before deploy; `swarm-prepare.yml` creates them.
- Secrets: Docker file secrets named `<stack>_<key>_${SECRET_HASH_<KEY>}`, `file: ./secrets/<KEY>`, consumed through `*_FILE` variables where the image supports them. Where an image reads only plain environment variables (vaultwarden's `SSO_CLIENT_SECRET`, miniflux's `DATABASE_URL`, authentik's `AUTHENTIK_SECRET_KEY` support `_FILE` or `file://`; check each), use `env_file: ./secrets.env` written by `swarm-prepare.yml` with mode 0600 and record the exception.
- Images: pinned by tag as today; locally built images by `git.ryzhkov.dev/igor/<name>:<tag>` (pushed and pre-pulled by `--tags registry`). Run `--tags registry` after every rebuild.
- Logging: services inherit the daemon's Loki driver. Do not add `logging:` unless the Compose file had one (jocasta: json-file).
- Limits: memory limit about twice the measured working set (audit, cgroup anon), reservation about the working set. Figures per stack below. CPU limits only where a service is known to burst (whisper, komga, openviking, authentik-worker).

## Batch 0: plumbing (one executor, before S1)

Files: `roles/traefik/templates/generated.yml.j2`, `roles/traefik/templates/whisper.yml.j2`, `filter_plugins/stacks.py`, `playbooks/site.yml`, `stacks/gatus/templates/config.yaml.j2`, `roles/drift_check/**` if its manifest needs it, `docs/traefik-routing.md`.

1. `playbooks/site.yml` skips every stack whose name appears in any `swarm_stacks_*` entry (union, as in `playbooks/swarm.yml`), so a converged host never recreates a retired Compose project. The host_vars entries stay until cleanup.
2. Traefik generator: for a route of a Swarm stack, `servers` lists `http://<ansible_host>:<port>` for every host in group `swarm`, with the route's health check; a Swarm entry wins over a host_vars entry of the same name. The Authentik forward-auth middleware cannot load-balance; point it at the homelab address while Authentik is Compose, and at the Swarm stack's first worker once P2 converts it (or at `https://auth.ryzhkov.dev` through Traefik itself).
3. `stack_host_ip` returns, for a Swarm stack, the bootstrap manager's address. Through the routing mesh any worker works; the limitation (no failover for literal consumers) is recorded per stack.
4. Gatus: internal checks for a Swarm stack probe the port on both workers.
5. Check: `site.yml --check` and `traefik.yml --check --diff` show no change while no Swarm stack exists.

## Batch B: backup role, peer snapshots and the move playbook (one executor, before F1)

Files: `roles/backup/**`, `playbooks/backup.yml`, `playbooks/swarm-move.yml` (new), `inventory/group_vars/swarm/backup.yml` (new), `scripts/test-*.py` for it, `.github/workflows/check.yml`, `docs/backups.md`.

### Paths

| Path | homelab | normandy | Holds |
|---|---|---|---|
| live state | `/srv/swarm/<stack>` = `/mnt/docker-data/swarm/<stack>` | `/srv/swarm/<stack>` = `/mnt/data/swarm/<stack>` | the pinned service's binds |
| local snapshots | `/mnt/docker-data/snapshots/<stack>/` | `/mnt/data/snapshots/<stack>/` | hourly SQLite `.sqlite3.gz` (keep 48), Postgres `.dump` (keep 48), produced only on the node holding `pin.<stack>` |
| peer copy | `/mnt/docker-data/peer-snapshots/normandy/<stack>/` | `/mnt/data/peer-snapshots/homelab/<stack>/` | the other worker's `snapshots/<stack>/` mirrored (`--delete`), plus `files/` (hourly mirror of the live directory for stateful-files stacks) and `final/` (the drain's cold copy) |

Both pools are ZFS zvols on the hosts' second drives (`tank`), outside vzdump (`backup=0`). The peer copy is the only second copy until B2 shipping.

### Snapshot spec per stack

Each stack entry carries `snapshot`:

```yaml
snapshot:
  class: sqlite          # sqlite | postgres | files
  sqlite:                # class sqlite: as backup_sqlite_sources today
    - { name: vaultwarden, path: /srv/swarm/vaultwarden/data/db.sqlite3, keep: 48 }
  postgres:              # class postgres: service and database
    - { service: authentik_postgresql, database: authentik, user: authentik, keep: 48 }
  files:                 # every class: directories mirrored to the peer hourly
    - /srv/swarm/vaultwarden/data
  exclude: [MediaCover/**, logs/**]
```

The backup role builds its source list from the union of `swarm_stacks_*` (as `playbooks/swarm.yml` does), so stack batches never edit a backup file.

### Hourly job (both workers)

The existing `homelab-backup-hourly` timer and `homelab-backup-run hourly` stay the entry point; the role installs them on normandy too. For each stack whose live directory holds `.swarm-owner` naming this host (written by the move playbook and by `swarm-prepare.yml` at cutover):

1. sqlite: `homelab-sqlite-backup` with the stack's sources, output `<pool>/snapshots/<stack>/`.
2. postgres: `docker exec $(docker ps -q -f label=com.docker.swarm.service.name=<service>) pg_dump -Fc -U <user> <database>` into `<pool>/snapshots/<stack>/<database>_<stamp>.dump`, pruned to `keep`.
3. files: nothing local.
4. Second destination: `sudo rsync -a --numeric-ids --delete -e 'ssh -i /etc/swarm-sync/id_ed25519' <pool>/snapshots/<stack>/ swarmsync@<peer>:<stack>/` and, for `files:`, `... /srv/swarm/<stack>/<dir>/ swarmsync@<peer>:<stack>/files/<dir>/` with the stack's excludes.
5. B2: homelab's existing `rclone sync` of `$root/sqlite` continues for stacks pinned to homelab; homelab also ships `peer-snapshots/normandy/` to `b2:igor-offsite-backup/apps/normandy/swarm`, so normandy needs no rclone credentials. While homelab is down, normandy's snapshots stay local and on nothing else; accepted.
6. Failure goes to the feed and the phone, as today.

The existing `vaultwarden`, `navidrome` and `actual` entries in `backup_sqlite_sources` stay for the Compose stacks until each converts; F1 then removes them in its own change to `inventory/host_vars/homelab.yml` (lead serialises).

### Transport

- Each worker gets a system user `swarmsync` (no password, shell `/bin/sh`), and root on each worker gets `/etc/swarm-sync/id_ed25519` (0600), generated by the role; the public half is collected into facts and authorised on the peer.
- `~swarmsync/.ssh/authorized_keys` on the receiver: `from="<sender VLAN 70 address>",restrict,command="sudo -n /usr/bin/rrsync -wo <pool>/peer-snapshots/<sender>" ssh-ed25519 ...`.
- `/etc/sudoers.d/swarmsync`: `swarmsync ALL=(root) NOPASSWD: /usr/bin/rrsync -wo <pool>/peer-snapshots/<sender>`, the one command. Root on the receiver keeps ownership (`--numeric-ids`); `-wo` means a compromised sender can overwrite only its own peer directory and read nothing.
- Traffic stays on VLAN 70 (the peer's `ansible_host`); sshd is already open between the workers (normandy's ufw allows 22 from VLAN 70).
- rrsync is `/usr/bin/rrsync` on both workers (checked).

### The scripted move (`playbooks/swarm-move.yml -e stack=<name> -e to=<host>`)

Planned drain, a few minutes per stack, no data loss:

1. Preconditions: both workers `Ready` and `Reachable` (the two-manager rule), `to` differs from the current `pin.<stack>` node, the peer copy's newest snapshot is under 2 hours old.
2. Stop: `docker service scale <stack>_<svc>=0` for every service of the stack, then wait until `docker service ps` shows no running task.
3. Final snapshot: `sudo rsync -a --numeric-ids --delete /srv/swarm/<stack>/ swarmsync@<to>:<stack>/final/` from the old node (the data is quiescent, so this is an exact cold copy for every class, Postgres included).
4. Restore on the new node: `sudo rsync -a --numeric-ids --delete <pool>/peer-snapshots/<from>/<stack>/final/ /srv/swarm/<stack>/`, write `.swarm-owner` = `to`.
5. Retire the old copy: rename `/srv/swarm/<stack>` on the old node to `/srv/swarm/.moved/<stack>-<stamp>` (kept until the next move; the hourly job ignores it).
6. Flip the pin: `docker node update --label-rm pin.<stack> <from>`, `docker node update --label-add pin.<stack>=true <to>`.
7. Start: `docker service scale <stack>_<svc>=<replicas from the stack file>`, wait for running and healthy, probe the Traefik route.
8. Post to the feed.

Crash (old node gone): skip 2, 3 and 5; step 4 restores from the newest hourly material in `peer-snapshots/<from>/<stack>/` (SQLite snapshot gunzipped into place, Postgres `pg_restore` into an empty data directory started by the service, files from `files/`). Step 6 needs quorum: the surviving worker and OCI hold 2 of 3. If OCI is unreachable too, the survivor first runs `docker swarm init --force-new-cluster --advertise-addr <its mesh address> --data-path-addr <its VLAN 70 address>` (the Swarm counterpart of `pvecm expected 1`), and the others rejoin later through the nodes play with `-e swarm_rebuild=true` after `docker swarm leave --force`. Up to an hour of loss.

## Operating rules: autolock, quorum, OCI

From plan phase 5 step 1, with three managers (homelab, normandy, OCI drained).

- **Quorum.** Two of three managers must be up and unlocked for the cluster to schedule, reschedule after a drain, update a service or move a pin label. One manager down or locked is tolerated.
- **Unlock after a manager restart.** Autolock is on: a manager whose Docker daemon restarted (VM reboot, Docker upgrade, OCI reboot) comes back `locked` and holds no raft vote until unlocked. Its Compose containers start normally; Swarm tasks on it do not. Procedure: `ansible-playbook playbooks/swarm.yml --tags nodes -l <host>`; the role sees `LocalNodeState locked` and runs `docker swarm unlock` with `swarm_unlock_key` from `secrets/swarm.sops.yaml` (decrypted on the laptop, passed on stdin). Check with `docker node ls` that it shows `Reachable` again. By hand, if the laptop is unavailable: `sops -d --extract '["swarm_unlock_key"]' secrets/swarm.sops.yaml` on any machine with the age key, then `docker swarm unlock` on the host and paste it. Do this after every reboot of homelab, normandy or OCI, before anything else in the cluster is touched.
- **No drain while OCI is unreachable.** A drain of one worker leaves the other worker and OCI as the quorum. If OCI is down, unreachable over Nebula, or locked, the drain would leave one vote of three, and the cluster would stop scheduling with the drained worker's services stopped. So: before any drain, `docker node ls` must show OCI `Ready` and `Reachable`, and `ping 10.42.0.1` must answer from both workers. OCI is also the Proxmox QDevice and the Nebula lighthouse; the same check gates a hypervisor shutdown (plan phase 7). `pvecm expected 1` and `--force-new-cluster` are the written-down emergency steps, not routine ones.
- **Never two managers restarted together.** Reboot, upgrade or Nebula-reconfigure homelab, normandy and OCI one at a time, unlocking each before the next.
- **Rotating the unlock key.** `docker swarm unlock-key --rotate` on a manager, then the nodes play records the new key (it compares the key with the secret and writes it with `sops set`). Keep the old key until every manager has been unlocked once with the new one.

The compose-pinned stacks move by their inventory host variable and the phase 7 runbook, not by this playbook.

## Stack table

WS = measured working set (cgroup anon, audit). "Backend" is the Traefik change after batch 0: Swarm stacks get both worker addresses; the UniFi port group must gain the port for normandy to serve.

| Stack | Host now | Images (built?) | State | Ports | Class | Placement | Snapshot | Backend | Conversion notes and risks | Batch |
|---|---|---|---|---|---|---|---|---|---|---|
| excalidraw | homelab | excalidraw/excalidraw (pulled) | none | 5090 | stateless | gpu=intel, 1 | none | both workers :5090 | trivial; limit 64M | S1 |
| librespeed | homelab | ghcr.io/librespeed/speedtest | none | 7080 | stateless | gpu=intel, 1 | none | both :7080 | throughput test measures the mesh hop; limit 128M | S1 |
| kiwix | homelab | ghcr.io/kiwix/kiwix-serve:3.7.0 | NFS `/mnt/shared/kiwix` (ro use) | 3151 | stateless | gpu=intel, 1 | none | both :3151 | needs `/mnt/shared` on both (present); limit 256M | S1 |
| flaresolverr | homelab | ghcr.io/flaresolverr/flaresolverr | anonymous `/config` volume, disposable | 8191 | stateless | gpu=intel, 1 | none | none (no route); *arr apps call `192.168.70.223:8191` | on the default `bridge` today; stack network instead. Consumers keep the literal address, which works through the mesh while homelab is up; limit 1G (Chromium) | S1 |
| adguard (adguardhome-sync) | homelab | ghcr.io/bakito/adguardhome-sync | config file with credentials (templated) | 8321 | stateless | gpu=intel, 1 | none | both :8321, `/healthz` | credentials move from a world-readable file to a Docker secret or `env_file` 0600; the template becomes a swarm config named by hash; limit 128M | S2 |
| gatus-public | homelab | ghcr.io/twin/gatus:v5.37.0 | history in a volume, disposable | 192.168.70.223:3001 | stateless | gpu=intel, 1 | none | both :3001 | loses the address bind; history restarts empty on move; limit 128M | S2 |
| voice | homelab | caddy:2-alpine | Caddyfile and `site/` from the host | 8097 | stateless | gpu=intel, 1 | none | both :8097, no check | Caddyfile and site become swarm configs, or `swarm-prepare.yml` copies them to `/srv/swarm/voice` on both workers; limit 64M | S2 |
| wyoming-openai | homelab | ghcr.io/roryeckel/wyoming_openai | none | 192.168.70.223:10300 | stateless | gpu=intel, 1 | none | none; HA config entry names 192.168.70.223:10300 | loses the address bind (now on every interface of both workers, VLAN 70 only after the ingress filter); HA keeps working through the mesh while homelab is up; limit 128M | S2 |
| wyoming-piper | homelab | rhasspy/wyoming-piper | 671 MB voice cache volume | 192.168.70.223:10200 | stateless (cache) | gpu=intel, 1 | none; cache re-downloads | none; HA names 192.168.70.223:10200 | pre-seed `/srv/swarm/wyoming-piper` on both workers so a move does not download 671 MB; limit 768M | S2 |
| whisper | homelab | whisper-cpp-dispatch:local (built) | models 466 MB, read-only | 192.168.70.223:8080 | stateless | gpu=intel, 1 | none | `whisper.yml.j2` fallback server becomes both workers :8080 | image `git.ryzhkov.dev/igor/whisper-server:latest`; models copied to `/srv/swarm/whisper/models` on both; normandy's CPU (i5-9500, no AVX-512) runs the same dispatch build; limit 1.5G, cpus 4 | S2 |
| hugo | homelab | nginx:latest | `~/app/hugo/blog/public` (2.4 MB), built by the webhook | 4580 | stateless (replicated content) | gpu=intel, 2, max 1 per node | none | both :4580 | the Forgejo webhook handler (`roles/forgejo_webhooks/files/handlers/igor/blog.sh`) also rsyncs `public/` to `/srv/swarm/hugo/public` on both workers; nginx gzip conf as a swarm config; limit 64M | S3 |
| cv-site | homelab | cv-site-app (built) | `data/` 102 MB, `analytics.jsonl` appended live, root-owned | 8091 | stateful-files | pin, homelab | files: `/srv/swarm/cv-site/data` hourly mirror | both :8091 | image `git.ryzhkov.dev/igor/cv-site:latest`; `IMMICH_URL` follows Immich (compose on normandy), `OLLAMA_URL` gaming-pc; secrets DISCORD_WEBHOOK, IMMICH_API_KEY via env_file 0600; limit 256M | F4 |
| vaultwarden | homelab | vaultwarden/server | `data/` 4.4 MB: SQLite + attachments + rsa keys | 8099 | stateful-sqlite | pin, homelab | sqlite `db.sqlite3` hourly keep 48 + files `data/` | both :8099 | essential; ADMIN_TOKEN and SSO secret via `_FILE`/env_file; cut over at night; limit 256M | F1 |
| actual | homelab | actualbudget/actual-server | `~/app/actual` 19 MB: `server-files/account.sqlite`, `user-files/*.sqlite` | 5006 | stateful-sqlite | pin, homelab | sqlite account + budgets glob + files | both :5006 | root-owned directory; `numeric-ids` copy; limit 256M | F1 |
| navidrome | homelab | deluan/navidrome:0.64.0 | `data/` (navidrome.db) + `cache/`, NFS music ro | 192.168.70.223:4533 | stateful-sqlite | pin, homelab | sqlite + files `data/` (exclude cache) | both :4533 `/ping` | loses the address bind; UID 1000 GID 988 must match on normandy (igor is 1000; group 988 exists? check); limit 256M | F1 |
| sonarr-tv | homelab | linuxserver/sonarr | config 717 MB (sonarr.db, MediaCover) + NFS | 8989 | stateful-sqlite | pin, homelab | sqlite sonarr.db + files (exclude MediaCover, logs) | both :8989 `/ping` | PUID/PGID as today; download client is black-pearl:8080 by address; indexers via prowlarr by address; limit 512M | F2 |
| radarr-movie | homelab | linuxserver/radarr | 1.4 GB (radarr.db, MediaCover) + NFS | 7878 | stateful-sqlite | pin, homelab | sqlite + files (exclude MediaCover) | both :7878 `/ping` | as sonarr; limit 512M | F2 |
| lidarr-music | homelab | linuxserver/lidarr | 15 MB + NFS | 8686 | stateful-sqlite | pin, homelab | sqlite + files | both :8686 `/ping` | limit 384M | F2 |
| prowlarr | homelab | lscr.io/linuxserver/prowlarr | 164 MB + NFS | 9696 | stateful-sqlite | pin, homelab | sqlite + files (exclude logs) | both :9696 `/ping` | uses flaresolverr at 192.168.70.223:8191; limit 256M | F2 |
| bazarr | homelab | linuxserver/bazarr | 62 MB + NFS | 6767 | stateful-sqlite | pin, homelab | sqlite `db/bazarr.db` + files | both :6767 | limit 512M | F2 |
| autobrr | homelab | ghcr.io/autobrr/autobrr | 9.5 MB | 7474 | stateful-sqlite | pin, homelab | sqlite + files | both :7474 | limit 128M | F2 |
| ombi | homelab | lscr.io/linuxserver/ombi | 8.8 MB (*.db) | 3579 | stateful-sqlite | pin, homelab | sqlite glob + files | both :3579 | public via Cloudflare (`request.`); limit 512M | F2 |
| komga | homelab | gotson/komga | 43 MB (database.sqlite, tasks.sqlite) + NFS manga | 25600 | stateful-sqlite | pin, homelab | sqlite both + files | both :25600 | JVM: set `JAVA_TOOL_OPTIONS=-Xmx768m` with limit 1G | F2 |
| forgejo | homelab | codeberg.org/forgejo/forgejo:12 | `~/app/forgejo` 565 MB: gitea.db, repositories 207 MB, ssh keys, now 852 MB of registry packages | 3500, 2222 | stateful-sqlite | pin, homelab | sqlite gitea.db + files `git/`, `gitea/` (packages included) | both :3500; SSH 2222 through the mesh | hosts the registry: a drain of homelab moves the registry too, which is why every node pre-pulls; `forgejo_ssh_host_key` pinned on clients stays valid (keys move with the data); registers its Authentik source at boot before Authentik is ready (phase 3 carry) - add a restart policy delay; `/etc/timezone` bind exists on both? (normandy is Arch: no `/etc/timezone`, use `TZ`); limit 512M | F3 |
| openviking | homelab | openviking-openviking (built) | 120 MB vectordb + viking files | 1933 | stateful-files | pin, homelab | files `/srv/swarm/openviking/data` hourly; drain cold copy | none in host_vars (mesh/T3 route elsewhere); dsh and the fleet call `192.168.70.223:1933` | image `git.ryzhkov.dev/igor/openviking:latest`; the healthcheck carries a literal bearer token in the Compose file, which should become a secret; agents stall while it is down (I tier); limit 1.5G | F3 |
| jocasta | homelab | jocasta:<commit> (CI-built) | `data/jocasta.db` + secrets; own backup unit | 10.42.0.2:1935 (mesh only, `JOCASTA` chain) | stateful-sqlite, conditional | pin, homelab | its existing `jocasta-backup` (keep), plus files | route `url: http://<mesh_host>:1935` | Swarm cannot bind to the mesh address. Convert only if `jocasta-firewall` is generalised to both workers and to ingress traffic (accept 1935 only from the edge proxies' mesh addresses on `nebula1`, drop it on every other interface). Otherwise it stays compose-pinned; the executor decides and records. Read-only rootfs, cap_drop, pids_limit carry over | F3 |
| thelounge | homelab | ghcr.io/thelounge/thelounge | 1.9 GB: config, users, `logs/igor.sqlite3`, packages | 9009 | stateful-sqlite | pin, homelab | sqlite logs + files (exclude packages cache if rebuildable) | both :9009 | WebSocket through Traefik unaffected; limit 256M | F3 |
| lubelogger | homelab | ghcr.io/hargata/lubelogger | 9.4 MB, LiteDB `cartracker.db` + documents/images | 8095 | stateful-files | pin, homelab | files hourly mirror; drain cold copy | both :8095 | LiteDB is not SQLite: the existing sqlite tool cannot snapshot it; limit 256M | F4 |
| obsidian | homelab | couchdb:3.5.0 | 18 MB CouchDB data + `local.ini` | 5984 | stateful-files | pin, homelab | files hourly mirror (append-only .couch files, crash-consistent); drain cold copy | both :5984 | CouchDB, not SQLite; credentials via env_file 0600; limit 512M | F4 |
| logging (grafana, influxdb, loki) | homelab | grafana/grafana, influxdb:2, grafana/loki | Grafana SQLite on NFS 556 MB; InfluxDB volumes 925 MB (daily archive tier); Loki binds | 3200, 8087, 3100 | stateful-files (+ influx tier) | pin, homelab | influx: existing daily `homelab-influx-backup` re-pointed; grafana sqlite hourly; loki files mirror | both :3200; InfluxDB/Loki have no route | Grafana's database moves off NFS to `/srv/swarm/logging/grafana`. Every Docker daemon, both Proxmox nodes and five LXCs push to `192.168.70.223:3100` literally; through the mesh that keeps working while homelab is up, and during a homelab drain logging stops. Prerequisite: base role writes `loki_push_url` and `mode: non-blocking` into daemon.json (applies at each daemon's next restart). HA's InfluxDB entry and the Proxmox metric server name `192.168.70.223:8087`. The phase 0 note (InfluxDB published on 0.0.0.0 to plain HTTP) is closed by the ingress filter plus a UniFi source rule for 8087. limits grafana 768M, influx 1G, loki 1G | F5 |
| miniflux (db, miniflux) | homelab | postgres:17-alpine, miniflux/miniflux | Postgres data dir 164 MB (bind) | 5080 | stateful-postgres | pin, homelab (both services) | pg_dump hourly keep 48 + drain cold copy | both :5080 | `depends_on: service_healthy` is dropped: miniflux must retry its DB connection (it does, with restart policy); DATABASE_URL carries the password, use `DATABASE_URL_FILE`; Postgres uid 70 ownership kept by `--numeric-ids`; limits db 256M, app 128M | P1 |
| authentik (postgresql, redis, server, worker) | homelab | postgres:16-alpine, redis:alpine, ghcr.io/goauthentik/server:2025.8.6 | named volumes `authentik_database` 115 MB, `authentik_redis`; binds media, custom-templates, certs | 4080, 4443 | stateful-postgres | pin, homelab (all four) | pg_dump hourly + drain cold copy of the data dir; redis disposable | both :4080; forward-auth middleware address (batch 0) | essential; SSO for everything behind forward auth, so cut over last and at night. Volumes become `/srv/swarm/authentik/{postgres,redis,media,templates,certs}` (copied from the named volumes with Compose stopped). The worker's Docker socket is for outpost integration; drop it unless an outpost is Docker-managed (check in Authentik's outposts list). The nightly restore check in the backup role leaves an anonymous volume per run (phase 0 carry). The Authentik watchdog (`authentik_watchdog`) watches the Compose containers by name and must be pointed at the Swarm service or retired. limits pg 512M, redis 128M, server 1G, worker 1G | P2 |
| emby | homelab | emby/embyserver | 16 GB config | 8096, 8920 | compose-pinned (GPU) | homelab, inventory variable | existing bulk tier | unchanged | `devices: /dev/dri`; moved by the runbook | - |
| immich (server, ml, postgres, redis) | normandy | immich v3.1.0, ml openvino, postgres vectorchord, redis | Postgres on `/mnt/data`, uploads on NFS | 192.168.70.234:2283 | compose-pinned (GPU, see above) | normandy | existing daily dump (`backup_nas_dump`) | unchanged | ML `devices: /dev/dri`, server `/dev/dri` bind; B2 retention for `apps/normandy/postgres` still open (phase 2 carry) | - |
| frigate | normandy | ghcr.io/blakeblackshear/frigate:stable | config + `/mnt/data/frigate/media` | 192.168.70.234:5000, 8971 | compose-pinned (GPU, camera VLAN) | normandy, never moves | config in `backup_nas_dump` config tar | unchanged | `/dev/dri` bind, `shm_size` | - |
| gatus | homelab, normandy | ghcr.io/twin/gatus | volume, disposable | 3002 per host address | compose-pinned (per-host probe) | both, one each | none | none | each instance watches the other host; converting to a global service loses that asymmetry and normandy's alerting/SMART role. The phase 0 carry (SMART token in a world-readable config) belongs to whoever next edits `stacks/gatus` | - |
| cloudflare | homelab, normandy | cloudflare/cloudflared | none | none | compose-pinned (plan: stays as is) | both | none | none | two connectors already | - |
| dsh | homelab | dsh-dsh (built, pushed) | `data/` 881 MB, NFS workspace | 7100 | compose-pinned (host-tied) | homelab | existing bulk tier | unchanged | mounts the Docker socket and `/home/iryzhkov/app` at the same path to run `docker compose` on homelab's projects; meaningless on another node | - |
| torrent, diun | blackpearl | gluetun, qbittorrent, port manager; diun | config | 8080 etc. | compose-pinned (own VM) | blackpearl | existing | unchanged | not a Swarm node | - |
| traefik, diun | public-api | traefik, diun | acme | 80, 443 | compose-pinned (edge, phase 4) | edge VMs | existing | - | phase 4 owns it | - |
| feedd, notify-relay | homelab | systemd user services | feed database | 1934 | not converted | homelab | (feed's own) | - | not containers | - |

Counts: 44 Compose projects (homelab 36, normandy 4, blackpearl 2, public-api 2) plus 2 systemd services. To Swarm: 32 stacks, 11 stateless (S1 4, S2 6, S3 1), 15 SQLite (F1 3, F2 8, F3 4 with jocasta conditional), 4 files (F4 3, F5 1), 2 Postgres (P1, P2). Compose-pinned: 12 (homelab cloudflare, dsh, emby, gatus; normandy cloudflare, frigate, gatus, immich; blackpearl torrent, diun; public-api traefik, diun).

S1 also proves the ingress filter before anything else converts: with excalidraw deployed, `curl -m5 http://192.168.60.97:5090` from black-pearl (VLAN 60) and `curl -m5 http://10.42.0.7:5090` and `http://10.42.0.2:5090` from the laptop over the mesh must time out, and `curl http://192.168.70.234:5090` from VLAN 70 must answer.

## Batches, owners' file sets, order

Every stack batch owns only `stacks/<its stacks>/stack.yml` (and `swarm-prepare.yml`), `inventory/group_vars/swarm/stacks-<batch>.yml`, and its evidence file `docs/transition/phase5/<date>-<batch>.md`. Nothing else, so batches can be prepared in parallel. Cutover runs are service-affecting and are serialised by the lead. Removing a converted stack's entry from `inventory/host_vars/homelab.yml` (stacks list, `backup_sqlite_sources`) is one cleanup change by the lead after the class is done.

| Order | Batch | Stacks | Owner's files beyond the common set | Depends on | Window |
|---|---|---|---|---|---|
| 1 | 0 plumbing | - | listed under Batch 0 | foundation | none |
| 2 | S1 stateless-trivial | excalidraw, librespeed, kiwix, flaresolverr | - | 0 | seconds each, any time |
| 3 | S2 stateless-voice-and-misc | adguard, gatus-public, voice, wyoming-openai, wyoming-piper, whisper | `roles/traefik/templates/whisper.yml.j2` (taken over from batch 0 once 0 is done) | 0, registry | seconds each; HA voice briefly |
| 4 | S3 static sites | hugo | `roles/forgejo_webhooks/files/handlers/igor/blog.sh` | 0 | seconds |
| - | rehearsal | drain normandy (`docker node update --availability drain`), confirm S1 to S3 answer through Traefik, undrain | lead | S1 to S3 | none |
| 5 | B backup | - | listed under Batch B | 0 | none |
| 6 | F1 sqlite-essential | vaultwarden, actual, navidrome | - | B | minutes, at night |
| 7 | F2 sqlite-media | sonarr-tv, radarr-movie, lidarr-music, prowlarr, bazarr, autobrr, ombi, komga | - | B | minutes each |
| 8 | F3 sqlite-agent-infra | forgejo, openviking, jocasta (conditional), thelounge | `stacks/jocasta/files/jocasta-firewall`, `stacks/jocasta/templates/jocasta-firewall.service.j2` | B, registry | minutes each; agents pause |
| 9 | F4 files | cv-site, lubelogger, obsidian | - | B, registry | minutes each |
| 10 | F5 logging | logging | `roles/base/**` (loki_push_url, non-blocking mode) | B | minutes; logs gap |
| - | rehearsal | move one F stack to normandy with the move playbook and back; drain normandy | lead | F1 to F5 | per stack |
| 11 | P1 postgres-small | miniflux | - | B | minutes, at night |
| 12 | P2 postgres-auth | authentik | `roles/authentik_watchdog/**` | B, P1 | minutes at night; SSO down for the cutover |
| - | rehearsal and cleanup | drain normandy; lead removes converted entries from host_vars and the old Compose directories' Ansible entries | lead | all | none |

F1 to F4 do not share files with each other and can be prepared together; their cutovers are serialised. Within a batch, cut over one stack at a time and verify it before the next.

## Per-stack cutover procedure (every class)

1. Prepare: stack file, inventory entry, lint passes, `--tags stacks -e only=<name> --check`.
2. Stateful only: `swarm-prepare.yml` creates `/srv/swarm/<name>` on the pin node and, during the cutover run only, stops the Compose project and copies its state there (`rsync -a --numeric-ids`), writes `.swarm-owner`. Named volumes are copied from `/mnt/docker-data/docker/volumes/<vol>/_data`.
3. Cutover: ask the lead, then `ansible-playbook playbooks/swarm.yml --tags stacks -e only=<name> -e swarm_cutover=<name>`.
4. Verify: `docker service ps`, the route through Traefik, the Gatus check, the application's own health, the first hourly snapshot and its peer copy (stateful).
5. Record start and end times of the interruption in the batch's evidence file.
6. Rollback: `docker stack rm <name>`; stateful: copy `/srv/swarm/<name>` back into the Compose directory if the service wrote anything; remove the entry from the batch's stacks file; `ansible-playbook playbooks/site.yml -l <host> -e only=<name>` brings the Compose project back (the old directory was left in place).

## Blocked or needing Igor

- UniFi: the edge proxies reach normandy only on 2283, 7391 and 8971. The exact change (add 192.168.70.234 to the destination of policy `public-api -> homelab (Traefik backends)`, which uses port group `traefik-homelab-ports`) is in `proposals/unifi-swarm-ports.md`, for the lead to dispatch. Batches S1 onwards work without it, but only through homelab's address.
- Home Assistant's literal addresses (Emby, InfluxDB 8087, Wyoming 10300 and 10200) work through the routing mesh only while homelab is up.
