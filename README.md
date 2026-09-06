# homelab-infra

Infrastructure as code for the homelab: the Debian and Arch hosts, their Docker
Compose stacks, and eventually a warm cloud standby that runs the critical
subset of those stacks when the homelab is off.

The design goal that shapes everything here: **the cloud standby is an inventory
group, not a second codebase.** The same role that deploys Vaultwarden on
homelab deploys it on the standby host, so the two cannot drift apart. An
earlier attempt at cloud failover (2026-03 to 2026-05) kept a hand-written copy
of each stack and did not survive the machine it was written on.

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

Phase 0 is done: every host answers, and the current state is captured and
written up in [docs/current-state.md](docs/current-state.md). No host has been
changed by this repository yet.

Next: adopt the Compose stacks one at a time, proving each with
`--check` and a no-op `docker compose up -d`, starting with the ones that have
no Compose file on disk at all.

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
to a file (`ansible-playbook ... 2>/tmp/ansible.err`) and it runs normally.

## Conventions

- Playbooks never change a host that a person has not looked at first. Every
  adoption step is expected to be a no-op against the running system; if
  `--check` reports a change, the repository is wrong, not the host.
- Secrets never enter this repository in plaintext, including in `snapshot/`.
- The stack roots differ per host (`~/app`, `~/apps`, and two exceptions on
  homelab). The inventory records each host's `stack_root` rather than assuming
  one convention.
