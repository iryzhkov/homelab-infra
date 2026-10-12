# Current state, as captured on 2026-09-06

This is the starting point the Ansible roles in this repository are being
written against: what actually runs today, discovered by `scripts/snapshot.sh`
rather than taken from documentation. The raw capture lives in `snapshot/`,
which is deliberately untracked — it contains configuration pulled straight off
the hosts, and a few of those files carry secrets (see "Secrets found in the
open" below).

Re-run `./scripts/snapshot.sh` at any time; it only reads.

## Hosts

Verified reachable over SSH by `playbooks/ping.yml` on 2026-09-06.

| Host | OS | vCPU | RAM | Role |
|---|---|---|---|---|
| homelab | Debian 12.15 | 4 | 28 GB | 38 containers, the main application server |
| public-api | Debian 12.15 | 2 | 3.9 GB | Traefik edge proxy |
| normandy | Arch (Omarchy) | 4 | 24 GB | Immich, Frigate, GPU workloads |
| blackpearl | Debian 12.15 | 3 | 24 GB | Torrent stack behind gluetun |
| proxmox-a | Debian 13.6 | 12 | 64 GB | Hypervisor hp-elite-mini-01 |
| proxmox-b | Debian 13.6 | 6 | 32 GB | Hypervisor hp-workstation-01 |
| oci | Ubuntu 24.04 | 2 | 954 MB | Nebula lighthouse, Caddy for emby.ryzhkov.dev |

## Compose stacks

31 Compose projects on homelab, 2 on public-api, 3 on normandy, 2 on
blackpearl. `snapshot/<host>/facts/compose-projects.txt` maps every container to
its project and working directory.

Stack roots differ per host and there is no single convention:
`~/app` on homelab and normandy, `~/apps` on public-api and blackpearl, plus
`~/cv-site` and `~/app/hugo/blog/deploy` on homelab, which sit outside the
pattern entirely.

## Findings

**Nothing is version controlled.** Across all four Docker hosts, `~/cv-site` is
the only stack directory that is a git repository. Every other Compose file,
Traefik route and helper script exists in exactly one place, on the host that
runs it.

**Black Pearl's torrent stack is Compose-managed, but its Compose file is not
where anyone would look for it.** `gt-qb`, `1-gluetun-1` and
`1-gluetun-qbittorrent-port-manager-1` run from
`/var/lib/docker/volumes/portainer_data/_data/compose/1/docker-compose.yml`,
inside a volume belonging to a Portainer that was removed in the 2026-08-30
cleanup. The directory is mode 700 and root-owned, so the file cannot even be
read as `iryzhkov`. Consequences: the home directory holds only the stack's
bind-mounted data (`~/app/gluetun`, `~/app/qbittorrent`), the definition is
outside every backup job, and the VPN credentials sit inline in
`environment:` rather than in a `.env`. `scripts/snapshot.sh` now captures the
file explicitly over sudo.

Every other container on every other host does trace to a Compose file in a
normal home directory. Verified 2026-09-06 by resolving each container's
`com.docker.compose.project.working_dir` label and checking for a Compose file
there: 31 projects on homelab, 2 on public-api, 3 on normandy, all present.

**The same is true, less urgently, on public-api.** Its Portainer volume still
holds the pre-2026-08-30 Traefik Compose file. It differs from what runs today:
it joins an external `frontend` network and publishes the dashboard on
`0.0.0.0:8080`, where the current container uses `network_mode: bridge` and
binds the dashboard to `127.0.0.1:8080`.

**Traefik has since been adopted into Compose.** The OpenViking note records the
reconstructed Compose files as written but not applied; the running `traefik`
container now carries `com.docker.compose.*` labels and port bindings matching
`~/apps/traefik/docker-compose.yml`, so that step happened after the note was
written. `flaresolverr` on homelab is now the only container still running
without a Compose project.

**Traefik's dynamic config is edited in place.** `file_provider/` on public-api
holds 5 live YAML files and 17 timestamped `.bak` copies, one per change going
back to June. That backup-by-copy habit is precisely what this repository
replaces; the `.bak` files are the change history that git should be holding.

**Homelab keeps a second, stale Traefik config.** `~/app/traefik/` on homelab
contains `traefik.yml` and a `file_provider/` directory but no Compose file and
no container. It looks like a leftover from before the proxy moved to
public-api, and reads as authoritative if found by accident.

**Two orphan directories on homelab.** `~/app/tailscale/` holds only container
state with no Compose file and no running container; `~/app/lounge/` is The
Lounge's data directory, while its stack lives in `~/app/thelounge/`.

**86-challenge has moved.** The OpenViking notes place it at `~/86-challenge`;
it is now under `~/app/_archive/86-challenge/` and not running. That matters for
the cloud standby design, which listed it as a tier 2 service.

## Secrets found in the open

Both of these sit in files that the Monday/Thursday `rclone copy ~/app` job
uploads to Backblaze B2, so they exist in plaintext in the bucket as well as on
the host:

- `~/app/zigbee_watchdog.sh` on homelab hardcodes a Home Assistant long-lived
  access token and a Proxmox API token (`root@pam!watchdog`).
- `~/app/miniflux/docker-compose.yml` hardcoded `OAUTH2_CLIENT_ID` and
  `OAUTH2_CLIENT_SECRET` for the Authentik OIDC client. Resolved: the copy
  left in a `.bak` beside it was deleted and every credential in it rotated
  on 2026-10-12 (`docs/authentik-audit-2026-10-12.md`).

Every other Compose file references secrets through `${VAR}` and reads them from
a `.env` beside it. There are 8 such files: `dsh`, `authentik`, `obsidian`,
`vaultwarden`, `cloudflare`, `logging`, `cv-site` and `traefik` on homelab, plus
`traefik`, `frigate` and `cloudflare` on the other hosts.

## Not captured yet

- **Cloudflare Tunnel ingress rules.** Token-based connector, so the rules live
  only in the Cloudflare dashboard. Needs an API token with Tunnel:Read. This is
  the single most important missing piece: no cloud standby can serve traffic
  without it.
- **`.env` contents and `acme.json`.** Held back until the SOPS + age flow is in
  place, so that they are only ever written to this repository encrypted.
- **UniFi, AdGuard LXCs, Home Assistant.** Out of scope for the first pass. Home
  Assistant's `/config` is already its own git repository.

## Adoption, completed 2026-09-06

Every stack listed above is now deployed from this repository, and the findings
that described missing or hidden definitions are resolved:

- Black Pearl's torrent stack came out of the Portainer volume and runs from
  `~/apps/torrent`.
- flaresolverr, the last container with no Compose project, was removed and
  recreated as one.
- Each host's `diun` keeps its own copy under `stacks/`, because the three
  hosts' files genuinely differ.
- immich on normandy keeps its upstream layout: project directory
  `immich-stack`, variables in `stack.env`. Compose needs `--env-file` for
  that, or every `${VAR}` in its Compose file resolves to an empty string.

Adopting 38 projects recreated 15 containers once each, a few seconds apiece:
adguardhome-sync, Autobrr, cv-site, dsh, Emby, forgejo, Kiwix, komga, Kuma,
Lidarr, miniflux-db, openviking, Radarr, and earlier authentik's server and
worker with obsidian-livesync. The cause was not this repository's copies —
those are byte-identical to what was on disk, verified by hash — but a config
hash those containers carried from an earlier Compose invocation. Everything
else, including Traefik on the edge proxy, was adopted with no restart at all.
All 17 public hostnames answered afterwards.

## Hardcoded addresses, audited 2026-09-06

An audit for LAN addresses written as literals found twenty. Most were latent;
three were not:

- `cv-site` pointed at Immich on homelab, where nothing had answered since the
  move to normandy on 2026-08-30, and at an Ollama endpoint that had become a
  whisper.cpp server. Both now derive from the inventory.
- `diun` on public-api and Black Pearl posted their update notifications to
  `webhook-batcher` on homelab:8090, a service archived in the 2026-08-30
  cleanup. Nothing had listened there since, so both hosts' notifications were
  discarded silently. The notifier was removed on 2026-09-06 rather than
  repointed: `docker-update-sweep` already reports container updates into a T3
  thread and the fleet feed. Diun now logs "No notifier available" and only
  keeps its own state.

The archived `webhook-batcher` compose file on homelab carries its Discord
webhook URL in plaintext. It is not running, but the URL is a credential and
anyone holding it can post to that channel.

What is still written as a literal is deliberate: VLAN CIDRs in the torrent
stack's firewall rule, the off-inventory and Nebula addresses in Traefik's
`t3.yml`, the AdGuard instance list, the two static Traefik routes whose
backends are not stacks, and `inventory/hosts.yml` itself.

## Machine-to-machine SSH

Declared since 2026-09-06 in `fleet_ssh_keys` and each host's
`fleet_ssh_authorized`, applied by `playbooks/ssh.yml`. homelab's key is
authorised on normandy and public-api, restricted with `from="192.168.70.223"`,
and homelab reaches both by name through a managed block in its `~/.ssh/config`.

Worth recording because it caused a wrong conclusion earlier: homelab could
already reach normandy. The probe that said otherwise connected as `iryzhkov`,
homelab's own username, while normandy and public-api run as `igor`. The peer
block in `~/.ssh/config` exists so that nothing has to remember which host uses
which user again.

Black Pearl accepts no fleet key yet; nothing needs to reach it from another
host.
