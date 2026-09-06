# AdGuard Home

AdGuard is the one piece of managed infrastructure here that is not a Compose
stack. It runs in three LXCs, and it answers DNS for every VLAN, so the role
that deploys it is built around not breaking that.

| Instance | Where | Address | Managed |
|---|---|---|---|
| CT100 | proxmox-a | 192.168.20.154 | yes, by this repository |
| CT107 | proxmox-a | 192.168.20.106 | no, replica |
| CT201 | proxmox-b, Alpine | 192.168.20.83 | no, replica |

## Only the origin is managed

`adguardhome-sync`, a Compose stack on homelab, already propagates the origin's
configuration to the two replicas. Pushing configuration to all three from here
would fight that sync rather than help it, so `playbooks/adguard.yml` writes to
CT100 only and lets the existing mechanism do the rest.

The replicas' configurations are still copied into
`roles/adguard/files/reference/`, with the password line redacted, so a replica
that has to be rebuilt from nothing has something to start from. They are
reference material, not deployed by anything.

The three configurations differ in exactly three ways, checked 2026-09-06: the
bcrypt hash of the same admin password, `use_private_ptr_resolvers` (true on the
origin, false on CT107), and the numeric ids AdGuard assigns to filter lists,
which are creation timestamps.

## How it is deployed

None of the LXCs accepts SSH — two have host keys Ansible does not trust, and
the Alpine one runs no sshd — so the role runs on the Proxmox node and works
through `pct push` and `pct exec`.

The order exists because DNS is not a thing to be optimistic about:

1. Render the configuration to a temporary file on the Proxmox node.
2. Compare its checksum with the one the container is running; stop if equal.
3. `pct push` it into the container **beside** the live file, as `.new`.
4. Run AdGuard's own `--check-config` against the candidate.
5. Copy the live file aside as `.ansible-bak`, then move the candidate in.
6. Restart, wait for port 53 to accept connections, resolve
   `vault.ryzhkov.dev` through the instance and require an answer.
7. On any failure: remove the candidate, restore the backup if one was taken,
   restart, and fail loudly.

A configuration AdGuard rejects therefore never reaches the live file, and the
service is never restarted with it. Both paths were exercised on 2026-09-06:
invalid YAML was caught at step 4 with the live file untouched, and a real
change went through steps 5 to 7 and was reverted the same way.

Step 6 earns its wait: without it the probe hit a connection refused a second
after the restart and rolled back a change that was perfectly good.

## The two addresses that are derived, not written twice

DNS and routing point at each other, and both directions now come from the
inventory rather than from a literal typed into a file:

- **AdGuard's `*.ryzhkov.dev` rewrite** answers with the address of whichever
  host is in the `edge_proxy` group — Traefik's host. If Traefik moves, the
  name that reaches it moves with it. Rendering this from the inventory
  reproduced the live configuration byte for byte, which is how the derivation
  was checked.
- **adguardhome-sync's origin and replica URLs** come from `adguard_instances`,
  the same list the AdGuard role deploys from. Adding a fourth AdGuard instance
  is one entry in that list rather than an edit in two places.

The sync configuration is a `config_templates` entry on the `adguard` stack.
Compose does not notice a bind-mounted file changing, so `compose_stack`
restarts the project when one of these files changes, which the sync log
confirms with a full run to both replicas on startup.

That log also explains a difference noted earlier between origin and replicas:
the sync itself disables `use_private_ptr_resolvers` on a replica, because no
private reverse DNS servers are configured there.

## The secret

The file's only secret is the admin password hash, which lives in
`secrets/adguard-origin.sops.yaml` and is rendered into the template. Everything
else — upstreams, blocklists, the `*.ryzhkov.dev` rewrite, the allowlist rules —
stays readable in git, which is the point: those are the lines worth seeing in a
diff.

## Still worth fixing

`adguardhome-sync.yaml` holds the AdGuard admin credentials in a file the
container has to read as uid 1001, so it is world-readable — mode 0644, down
from the 0777 it had before. adguardhome-sync can take those credentials from
environment variables instead, which would let them live in a `.env` at mode
0600 like every other stack's secrets. That change was not made blind: the
image's help output does not document the variable names, and guessing them
would break DNS synchronisation silently.

## What this does not cover

The LXC containers themselves. Their Proxmox configuration is captured by
`scripts/snapshot.sh` into `snapshot/<node>/guests/all.conf`, but creating or
resizing them is still manual.
