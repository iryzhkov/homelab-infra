# homelab-infra

Infrastructure as code for the homelab: the Debian and Arch hosts, their Docker
Compose stacks, and eventually a warm cloud standby that runs the critical
subset of those stacks when the homelab is off.

The design goal that shapes everything here: **the cloud standby is an inventory
group, not a second codebase.** The same role that deploys Vaultwarden on
homelab deploys it on the standby host, so the two cannot drift apart. An
earlier attempt at cloud failover (2026-03 to 2026-05) kept a hand-written copy
of each stack and did not survive the machine it was written on.

The remote is `github.com/iryzhkov/homelab-infra`, private. Deliberately not the
self-hosted Forgejo: that runs on homelab, which is one of the machines this
repository exists to be able to rebuild.

## Layout

```
ansible.cfg           inventory path, SSH tuning
inventory/hosts.yml   every managed machine, grouped by role
group_vars/           per-group settings
host_vars/            per-host settings
roles/                base, docker, compose_stack, traefik, backup
stacks/               the Compose files, one directory per stack
secrets/              SOPS-encrypted .env content, decrypted at deploy time
playbooks/            site.yml (converge), standby.yml, restore.yml
scripts/snapshot.sh   read-only capture of what the hosts run today
snapshot/             its output; untracked, may contain secrets
docs/                 findings and decisions
```

## Status

Every container on every Docker host is now deployed from this repository.
As of 2026-09-06: 38 containers on homelab, 6 on normandy, 4 on Black Pearl and
2 on public-api, across 38 Compose projects, all of them Compose-managed and
all of them listed in `inventory/host_vars/`. `ansible-playbook
playbooks/site.yml` reports no changes on a converged fleet.

The current state at the moment of adoption is captured in
[docs/current-state.md](docs/current-state.md), and the Cloudflare tunnel
configuration — which existed nowhere but the dashboard — in
[docs/cloudflare-tunnel.md](docs/cloudflare-tunnel.md).

Two stacks needed a one-time playbook before Compose could own them, and both
are kept because a restore from an older backup would need them again:

- `playbooks/adopt-torrent.yml` — Black Pearl's torrent stack ran as project
  `1` out of a deleted Portainer's volume, and had to be torn down before it
  could come back as `torrent` from `~/apps/torrent`.
- `playbooks/adopt-flaresolverr.yml` — flaresolverr ran from `docker run` with
  no Compose labels, so Compose could not take the container name over until it
  was removed.

Traefik's dynamic configuration is generated from the same inventory since
2026-09-06, so a service's backend address follows the host it runs on; see
[docs/traefik-routing.md](docs/traefik-routing.md).

homelab runs a daily drift check since 2026-09-06, comparing itself against
what was last deployed to it and reporting into the fleet feed when it does
not match; see [docs/drift-check.md](docs/drift-check.md).

AdGuard Home joined on 2026-09-06 as well. It is not a Compose stack — three
LXCs, one managed and two kept in step by adguardhome-sync — so it has its own
role and its own guard rails; see [docs/adguard.md](docs/adguard.md).

Next: the cloud standby. The pieces it needs are now in place — the stack list
is data, the routing is generated from it, and the tunnel configuration is
captured.

## Secrets

Encrypted with [SOPS](https://github.com/getsops/sops) to an age key, one file
per stack under `secrets/`. The recipient is in `.sops.yaml`; the private key is
at `~/.config/sops/age/keys.txt` on the laptop, with a copy in the GNOME keyring
under `service=sops-age, account=homelab-infra`.

```sh
sops secrets/blackpearl-torrent.sops.yaml     # edit, re-encrypting on save
sops -d secrets/blackpearl-torrent.sops.yaml  # read
```

Playbooks decrypt on the controller with `community.sops.load_vars` and render
the values into a `.env` beside the stack's Compose file, mode 0600. Nothing is
decrypted on a managed host, and no plaintext secret is ever written into this
repository.

## Running it

Ansible is installed in a user virtualenv rather than system-wide:

```sh
python3 -m venv ~/.local/share/venvs/ansible
~/.local/share/venvs/ansible/bin/pip install ansible-core
ln -s ~/.local/share/venvs/ansible/bin/ansible* ~/.local/bin/
```

Then, from the repository root:

```sh
ansible-playbook playbooks/ping.yml          # connectivity and interpreter check
./scripts/snapshot.sh                        # refresh snapshot/
```

Note for agent sessions: Ansible refuses to start when stdout or stderr is
non-blocking, which is how most agent harnesses pipe a command. Redirect stderr
to a file (`ansible-playbook ... >/tmp/ansible.out 2>&1`) and it runs normally.

## Conventions

- Playbooks never change a host that a person has not looked at first. Every
  adoption step is expected to be a no-op against the running system; if
  `--check` reports a change, the repository is wrong, not the host.
- Secrets never enter this repository in plaintext, including in `snapshot/`.
- The stack roots differ per host (`~/app`, `~/apps`, and two exceptions on
  homelab). The inventory records each host's `stack_root` rather than assuming
  one convention.

## Addresses are declared once

An address that appears in two places will eventually disagree with itself.
cv-site pointed at Immich on homelab for a week after Immich moved to normandy,
and nothing complained.

So: `inventory/hosts.yml` is the only place a host's address is written. Every
other reference derives from it.

- A Compose file publishing a port on its own host uses `${HOST_IP}`, which
  `compose_stack` writes into the stack's env file.
- A service pointing at another service uses `env_extra` with the
  `stack_host_ip` filter: `http://{{ hostvars | stack_host_ip(groups, 'immich')
  }}:2283` follows the immich stack wherever it is deployed.
- Traefik's routers and services are generated the same way, and the two
  hand-written files that carry addresses are templates rather than fixed
  files.
- AdGuard's `*.ryzhkov.dev` rewrite answers with the `edge_proxy` host's
  address, and adguardhome-sync's origin and replicas come from
  `adguard_instances`.

What remains hardcoded is deliberate: VLAN CIDRs in the torrent stack's
firewall rule, the Nebula and off-inventory addresses in `t3.yml`, and the
addresses in `docs/` and in the AdGuard replica reference copies.
