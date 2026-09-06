# Cloudflare Tunnel, captured 2026-09-06

Everything here was read from the Cloudflare API by
`scripts/snapshot-cloudflare.sh`; the raw JSON is in `snapshot/cloudflare/`,
which is untracked. This file is the committed summary, because the tunnel's
ingress rules exist in no other durable place: the connector is token-based, so
Cloudflare stores the configuration and the connector holds only a token.

Tunnel `self_host_open`, id `0e6276f1-7125-44d7-816b-fd74f23d1074`,
remotely managed (`"source": "cloudflare"`), configuration version 57, healthy.

## Two connectors already run

Eight connections from two distinct connector ids, both from the house WAN
address: one is the `cloudflared` container on homelab, the other the one on
normandy. Cloudflare load-balances requests across both.

This matters twice over. It proves the failover mechanism — a second connector
joining an existing tunnel with the same token, needing no dashboard change —
is already in production here rather than being a new idea. It also shows the
hazard: a standby connector that comes up while the homelab is healthy will
immediately be handed a share of live traffic. The connector must stay gated
behind the failover flag.

## Ingress rules

Every rule points at `https://<the same hostname>`, and the catch-all returns
`http_status:404`.

| Hostname | Service |
|---|---|
| home.ryzhkov.dev | https://home.ryzhkov.dev |
| budget.ryzhkov.dev | https://budget.ryzhkov.dev |
| uptime.ryzhkov.dev | https://uptime.ryzhkov.dev |
| request.ryzhkov.dev | https://request.ryzhkov.dev |
| photos.ryzhkov.dev | https://photos.ryzhkov.dev |
| manga.ryzhkov.dev | https://manga.ryzhkov.dev |
| obsidian.ryzhkov.dev | https://obsidian.ryzhkov.dev |
| auth.ryzhkov.dev | https://auth.ryzhkov.dev |
| rss.ryzhkov.dev | https://rss.ryzhkov.dev |
| speed.ryzhkov.dev | https://speed.ryzhkov.dev |
| draw.ryzhkov.dev | https://draw.ryzhkov.dev |
| git.ryzhkov.dev | https://git.ryzhkov.dev |
| blog.ryzhkov.dev | https://blog.ryzhkov.dev |
| irc.ryzhkov.dev | https://irc.ryzhkov.dev |
| 86.ryzhkov.dev | https://86.ryzhkov.dev |
| igor.ryzhkov.dev | https://igor.ryzhkov.dev |
| vault.ryzhkov.dev | https://vault.ryzhkov.dev |
| (catch-all) | http_status:404 |

**The rules depend on LAN DNS.** A rule pointing at `https://vault.ryzhkov.dev`
only reaches Traefik because AdGuard rewrites `*.ryzhkov.dev` to the edge proxy
for hosts on the LAN. On a cloud standby the same name resolves publicly to the
tunnel CNAME, so the connector would hand the request back to Cloudflare and
loop.

The fix, which the abandoned 2026-05 design had already arrived at: give the
standby's Traefik container network aliases for those hostnames on the Docker
network the connector shares, so the names resolve inside the standby before
they ever reach a public resolver. This is a property the standby must be built
with; it is not something that can be patched in during an outage.

## DNS

28 records in the zone. 17 proxied CNAMEs to
`0e6276f1-7125-44d7-816b-fd74f23d1074.cfargotunnel.com`, one per ingress
hostname. `emby.ryzhkov.dev` is an A record to 150.230.40.9 (the OCI VM's Caddy)
and is deliberately grey-clouded, because Cloudflare's terms forbid proxying
video. Google MX, SPF and DKIM records cover mail.

Three stale `_acme-challenge` TXT records are left over from DNS-01 challenges
that were never cleaned up: `dns`, `emby` and `prowlarr`. Harmless, but they
mean Traefik's cleanup step has failed at least three times.

`t3-<host>.ryzhkov.dev` has no public record, which is correct — those names
resolve only through AdGuard on the LAN.

## What failover needs from Cloudflare

Nothing, at the point of failover. The standby brings up a `cloudflared` with
the **same tunnel token** the homelab connector uses (it lives in
`~/app/cloudflare/.env` on homelab), and Cloudflare treats it as one more
connector for `self_host_open`. Ingress rules, DNS records and certificates all
stay as they are.
