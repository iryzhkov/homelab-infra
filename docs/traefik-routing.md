# Traefik routing, generated from the inventory

Traefik's dynamic configuration used to be five hand-edited YAML files on the
edge proxy, each change leaving a timestamped `.bak` beside it — 17 of them by
September 2026. The backend addresses in those files repeated what the
inventory already knew: which host a service runs on.

Since 2026-09-06 the routes live next to the stacks that serve them, and
`playbooks/traefik.yml` renders them into one file.

## Declaring a route

In `inventory/host_vars/<host>.yml`, on the stack that serves it:

```yaml
  - name: vaultwarden
    routes:
      - host: vault.ryzhkov.dev
        port: 8099
    secrets: homelab-vaultwarden
```

The backend URL is built from the host the stack runs on, so this renders as
`http://192.168.70.223:8099` on homelab and would render as the standby's
address if the stack moved there.

Fields a route may carry beyond `host` and `port`:

| Field | For |
|---|---|
| `scheme` | `https` backends, as Frigate's authenticated UI needs |
| `name` | router and service name; defaults to the first label of `host` |
| `service` | borrow another route's service instead of defining one |
| `path_prefix` | added to the rule as `PathPrefix` |
| `priority` | when two rules can both match the same request |
| `middlewares` | list of middleware names |
| `url` | full backend URL, for a route that cannot reach its stack the ordinary way |

`traefik_static_routes` in `inventory/group_vars/all.yml` covers backends that
are not stacks this repository deploys: Home Assistant in its own VM,
Zigbee2MQTT in an LXC.

## Moving a service between hosts

Move the stack's entry, with its `routes`, from one file in
`inventory/host_vars/` to another, then:

```sh
ansible-playbook playbooks/site.yml -l <new-host> -e only=<stack>
ansible-playbook playbooks/traefik.yml
```

Traefik's file provider watches the directory and reloads in well under a
second, so the edge proxy is never restarted.

Three things do **not** follow the stack, and are worth stating plainly:

- **The data.** Volumes and databases are the actual work of a move.
- **The firewall.** The UDM's zone policies are not managed here. A move within
  VLAN 70 needs nothing; crossing VLANs needs a policy first.
- **Public exposure.** Cloudflare Tunnel ingress points at the hostname, not the
  address, so a move needs no Cloudflare change at all — but a brand new public
  hostname still has to be added in the dashboard.

## What stays hand-written

Four files in `stacks/traefik/dynamic/` are deployed verbatim, because each is
a single instance rather than a pattern the generator should learn:

| File | Why |
|---|---|
| `admin.yml` | Proxmox across both nodes with a sticky cookie, which the noVNC console needs |
| `t3.yml` | T3 Code, including the laptop's LAN-to-mesh failover pair |
| `whisper.yml` | the whisper failover pair and its path-rewriting middleware |
| `catchall.yml` | the wildcard-certificate 404 for hostnames no router claims |

The Authentik forward-auth middleware is generated rather than hand-written,
because its address points at wherever the authentik stack runs.

## Verifying a change before applying it

`scripts/compare-traefik.py` reduces a directory of dynamic YAML to the mapping
from rule to backends, following failover chains, and diffs two of them. Router
and service names are free to change; what must not change accidentally is
where a hostname's traffic goes.

```sh
scripts/compare-traefik.py /tmp/traefik-old /tmp/traefik-new
```

The switch to generated configuration was made on exactly this evidence: 41
rules before, 41 after, zero differing.

After applying, the playbook asks Traefik's API which routers it actually
loaded and fails if any is not `enabled` — a router Traefik cannot load is
reported there rather than by refusing to start.

## One thing the move fixed, one it did not

`proxy.ryzhkov.dev` returned 502. Its backend was `http://192.168.20.33:8080`,
but Traefik publishes its dashboard on `127.0.0.1:8080`, so nothing answered on
the LAN address. The route now uses loopback, which Traefik reaches inside its
own network namespace, and the dashboard responds.

That dashboard has no authentication in front of it. It is LAN-only — no
Cloudflare Tunnel hostname exists for it — but anything on the LAN can read the
routing table. Putting it behind `authentik-forwardauth`, as `ai.ryzhkov.dev`
already is, would be one line in `inventory/host_vars/public-api.yml`.
