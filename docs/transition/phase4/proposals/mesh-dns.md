# Phase 4 proposal: mesh DNS when public-api is down

Drafted 2026-10-02, 20:00 to 20:10 PDT, by executor E19. Read only: public-api (dnsmasq,
Nebula firewall, a 90-second capture on `nebula1`), the laptop's own resolver, the OCI
lighthouse, the Mobile Nebula site files under `~/.nebula/` on the laptop, and the
OpenViking notes on the travel router, phone and iPad. Nothing was changed.

## 1. Where the configuration lives

public-api, `/etc/dnsmasq.d/nebula-dns.conf` (root, 0644, 2026-09-01 16:44), not owned by
any package and not in the repository:

```
bind-dynamic
listen-address=10.42.0.3
listen-address=127.0.0.1
no-resolv
server=192.168.20.154
server=192.168.20.106
server=192.168.20.83
address=/ryzhkov.dev/10.42.0.3
cache-size=1000
```

`dnsmasq` is active, listening on `10.42.0.3:53` and `127.0.0.1:53` (UDP), with query
logging off. Everything outside `ryzhkov.dev` is forwarded to the three AdGuard containers.
public-api's Nebula firewall admits `port: any, proto: any, host: any` inbound, so any mesh
node may query it. The OCI lighthouse does not serve DNS (no `serve_dns` in its config).

## 2. Which mesh clients depend on it

| Client | Uses 10.42.0.3 for DNS? | Evidence |
|---|---|---|
| travel router, 10.42.0.4 | Yes, by design | OV note `viking://resources/homelab/travel-router-nebula-dns.md` (2026-09-02): its `/etc/dnsmasq.d/ryzhkov.conf` has `no-resolv`, `strict-order`, `server=1.1.1.1`, `server=10.42.0.3`, and its own `address=/ryzhkov.dev/10.42.0.3`. Not re-read tonight: 10.42.0.4 timed out (it is probably not powered or not travelling). |
| laptop, 10.42.0.5 | No | `resolvectl status nebula1`: `Current Scopes: none`, `Default Route: no`; no file under `/etc/systemd/resolved.conf.d`, `/etc/NetworkManager` or `~/.nebula` names 10.42.0.3. |
| phone (`s25_ultra`), 10.42.0.25 | No evidence | `~/.nebula/s25_ultra.yml` has no DNS setting; Mobile Nebula sets no resolver from the site file. Android Private DNS needs a DoT host name, not a mesh IP. |
| iPad, 10.42.0.26 | No evidence | Same YAML shape as the phone's (OV note `ipad-nebula-t3-node.md`); no DNS setting recorded. |
| homelab, normandy, gaming-pc, Proxmox nodes | No | All are on the LAN and use AdGuard through DHCP. |

Live check: `tcpdump -ni nebula1 'udp dst port 53 or tcp dst port 53'` for 90 seconds on
public-api saw no query, and `/proc/net/nf_conntrack` held no DNS flow from a 10.42.x
source. So tonight nothing is using it; the travel router is the one configured dependent,
and it matters only while it is away from home.

The comment in `stacks/traefik/dynamic/t3.yml:12-13` ("a phone on Nebula off-LAN reaches
Traefik over the mesh") is not supported by what the phone's site file contains; it is true
for clients behind the travel router.

What happens today if public-api is down: the travel router answers every `*.ryzhkov.dev`
name itself with 10.42.0.3 (its local `address=` line), so every home name fails for its
clients, including the public tunnel names that would otherwise work through Cloudflare. Other
names fall back to 1.1.1.1 without ad blocking. VRRP cannot help here: the VIP is a VLAN 20
address and Nebula has no floating address.

## 3. Options

### A. Each edge VM answers with its own mesh address (recommended)

1. public-api-b joins the mesh as `public-api-b`, group as public-api's, at 10.42.0.10
   (free: nothing in the inventory, no answer to ping on 10.42.0.10 to .20, no certificate of
   that name under `~/.nebula/new-certs/`). It needs to be a mesh node anyway: Traefik's
   `t3-laptop` failover backend is the laptop's Nebula address, and Jocasta's firewall admits
   only the edge's `mesh_host` (`stacks/jocasta/templates/jocasta-firewall.service.j2`).
   In the repository: add it to `nebula_managed` with `mesh_host: 10.42.0.10`.
2. Move `nebula-dns.conf` into the repository as a template deployed to every `edge_proxy`
   member, with `listen-address={{ mesh_host }}` and `address=/ryzhkov.dev/{{ mesh_host }}`,
   and the upstream servers from the AdGuard inventory rather than literals. Each edge VM
   then answers with itself, so an answer only ever comes from a live edge.
   dnsmasq's default `local-ttl` is 0, so clients do not cache a dead edge's address.
3. Travel router (Igor, by hand, over its LAN address 192.168.90.158 as the OV note
   advises): remove its own `address=/ryzhkov.dev/10.42.0.3` and ask both edges instead:

   ```
   no-resolv
   strict-order
   server=1.1.1.1
   server=10.42.0.10
   server=10.42.0.3
   server=/ryzhkov.dev/10.42.0.3
   server=/ryzhkov.dev/10.42.0.10
   ```

   The domain lines send `ryzhkov.dev` only to the edges, never to 1.1.1.1; the general
   lines keep the existing order rule (mesh first, 1.1.1.1 last). Test the order the way it
   was tested on 2026-09-02: a blocked domain gives 0.0.0.0 with the mesh up. The check
   that `blog.ryzhkov.dev` still answers with public-api off belongs in the phase 4
   failover test, not before it.

Cost: one Nebula certificate, one template, one hand edit on the travel router. Limit: a
mesh client fails over after a DNS timeout (a few seconds), and an edge whose dnsmasq is up
but whose Traefik is down still answers. A later refinement is to have keepalived's
`notify` scripts or the Traefik health check stop dnsmasq on an unhealthy edge.

### B. Route mesh clients to the VIP

dnsmasq answers 192.168.20.3, and mesh clients carry an `unsafe_routes` entry for
192.168.20.3/32 via a LAN mesh node (homelab 10.42.0.2 already routes 192.168.40.0/24 for
the travel router). Needs homelab's certificate re-signed with that subnet, forwarding on
homelab, and a re-import of the phone and iPad sites; makes homelab a single point and adds a
hop. Not recommended.

### C. Accept the pin for now

Keep the mesh path on public-api only and record it as a known gap of the phase 4 failover.
Defensible because the only configured dependent is the travel router while travelling.
Public names still resolve through Cloudflare for clients that use public DNS.

## Decisions for Igor

1. A (recommended), B or C.
2. If A: mesh address 10.42.0.10 for public-api-b, and the group name in its certificate.
3. If A: when to edit the travel router (it must be reachable on its LAN address).
4. Whether to fix the misleading comment in `stacks/traefik/dynamic/t3.yml` now or with A.
