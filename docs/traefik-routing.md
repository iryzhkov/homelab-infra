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
| `sso` | `forward` to put the route behind Authentik; see below |
| `health_check` | `{path, interval, timeout}`, or `false` for none |
| `url` | full backend URL, for a route that cannot reach its stack the ordinary way |

`traefik_static_routes` in `inventory/group_vars/all.yml` covers backends that
are not stacks this repository deploys: Home Assistant in its own VM,
Zigbee2MQTT in an LXC.

## Putting a service behind Authentik

For a service with no login of its own, add `sso: forward` to its route and
say who may open it in `playbooks/vars/authentik.yml`, under
`authentik_app_access`, keyed by the route's name:

```yaml
# inventory/host_vars/homelab.yml
      - host: ai.ryzhkov.dev
        port: 7100
        sso: forward

# playbooks/vars/authentik.yml
authentik_app_access:
  ai:
    title: AI
    users: [igor]
```

Then:

```sh
ansible-playbook playbooks/authentik.yml   # provider, application, outpost, access
ansible-playbook playbooks/traefik.yml     # middleware and the outpost router
```

Authentik first, so the outpost knows the host before Traefik sends anyone to
it. The Traefik side adds the `authentik-forwardauth` middleware and a second
router, `<name>-outpost`, that sends `/outpost.goauthentik.io/` on the same
host to Authentik for the login callback. The Authentik side creates a proxy
provider in forward-auth (single application) mode, the application, the
embedded outpost's provider list and the bindings, and checks every person's
access against the file afterwards. `playbooks/authentik.yml` refuses a
forward-auth route with no `authentik_app_access` entry, because an application
with no binding is open to every account.

Services that speak OIDC themselves (Immich, Forgejo, Miniflux and the others)
are not covered: their providers and client secrets are still set up in the
admin interface, and only their access is declared in the file. See
`docs/authentik-audit-2026-10-12.md`.

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

## Swarm stacks

From phase 5 a stack may run as a Docker Swarm stack instead of a Compose
project. Its entry, with the same `routes` shape, is in one of the
`swarm_stacks_*` lists in `inventory/group_vars/swarm/stacks-<batch>.yml`
(see `docs/transition/phase5/migration-plan.md`). The Swarm routing mesh
publishes the stack's ports on every worker, so the generator lists every
host of group `swarm_workers` as a server of the route's service, each probed
by the route's health check:

```yaml
    draw-service:
      loadBalancer:
        healthCheck: ...
        servers:
          - url: "http://192.168.70.223:5090"
          - url: "http://192.168.70.234:5090"
```

The rules that come with it:

- A Swarm entry wins over a host_vars entry of the same stack name. The
  host_vars entries of converted stacks stay until the phase 5 cleanup; their
  routes are dropped from the generated file, and `playbooks/site.yml` no
  longer converges their Compose projects.
- A route with an explicit `url` keeps it, as one server.
- The Authentik forward-auth middleware takes a single address. While Authentik
  is a Compose stack it is homelab's; once Authentik is a Swarm stack it is the
  first worker's (homelab's again, by name order), which serves through the
  mesh wherever the task runs, but not while homelab is down.
- `whisper.yml`'s CPU fallback lists every worker once whisper is a Swarm
  stack.
- The filters in `filter_plugins/stacks.py` follow the same rule:
  `stack_host_ip` gives the bootstrap manager's address for a Swarm stack (one
  address, for consumers that can hold only one), `stack_host_ips` every
  worker's.
- Gatus probes each Swarm route on every worker.
- UniFi lets the edge proxy reach normandy only on the ports in
  `traefik-normandy-ports`. Until a Swarm stack's port is in that group,
  Traefik's health check marks the normandy server down and serves through
  homelab's address alone, which works through the mesh while homelab is up.

Add a stack's Swarm entry in the same change that cuts it over, and run
`playbooks/traefik.yml` after the cutover: from the moment the entry exists the
route names both workers, and Gatus probes both.

## What stays hand-written

Four files express things the generator has no shape for, each being a single
instance rather than a pattern:

| File | Why | Where |
|---|---|---|
| `admin.yml` | Proxmox across both nodes with a sticky cookie, which the noVNC console needs | rendered, `roles/traefik/templates/` |
| `whisper.yml` | the whisper failover pair and its path-rewriting middleware | rendered, `roles/traefik/templates/` |
| `t3.yml` | T3 Code, including the laptop's LAN-to-mesh failover pair | verbatim, `stacks/traefik/dynamic/` |
| `catchall.yml` | the wildcard-certificate 404 for hostnames no router claims | verbatim, `stacks/traefik/dynamic/` |

The first two are templates rather than fixed files because they name
addresses: the Proxmox pair comes from the `hypervisors` group, and whisper's
GPU and CPU backends from gaming-pc and from whichever host runs the whisper
stack. `t3.yml` stays verbatim because two of its four backends are machines
this inventory does not describe and one is a Nebula address.

The Authentik forward-auth middleware is generated rather than hand-written,
because its address points at wherever the authentik stack runs.

## Health checks are not cosmetic

Traefik counts a 4xx answer as unhealthy and takes the backend out, so the path
a service is probed on decides whether its router works at all.

The generator's first version probed every service on `/`. Most services answer
2xx or a redirect there and were fine. Sonarr and adguardhome-sync answer
**401**, because they use basic authentication, so Traefik marked them down
within one 30-second interval and `sonarr.ryzhkov.dev` started returning 503 —
a service that was working, broken by a configuration change that looked
equivalent.

The hand-written files it replaced had known this: sonarr, radarr, lidarr and
prowlarr were probed on `/ping`, adguardhome-sync on `/healthz`, and voice had
no health check at all. Those are now declared per route, and `health_check:
false` means no check rather than the default one.

## Verifying a change before applying it

`scripts/compare-traefik.py` reduces a directory of dynamic YAML to the mapping
from rule to backends **and health check paths**, following failover chains, and
diffs two of them. Router and service names are free to change; what must not
change accidentally is where a hostname's traffic goes and how the backend is
probed.

The health check was added to that comparison after it was needed: the original
version compared backends, middlewares and priorities only, reported "41 rules
before, 41 after, 0 differing", and the change it passed had silently replaced
`/ping` with `/` on four services.

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

That dashboard has no authentication in front of it, and by decision on
2026-09-06 it stays that way: anything on the LAN can read the routing table.
It is not reachable from the internet, because no Cloudflare Tunnel hostname
exists for it and adding one is a deliberate act in the dashboard.

If that ever needs to change, it is `sso: forward` on the route in
`inventory/host_vars/public-api.yml` and an access entry in
`playbooks/vars/authentik.yml`, the same treatment `ai.ryzhkov.dev` already
has ("Putting a service behind Authentik", above).
