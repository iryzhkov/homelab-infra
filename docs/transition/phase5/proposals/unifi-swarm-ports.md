# Proposal: let the edge Traefik reach Swarm services on normandy

Date: 2026-10-02, 20:45 PDT. Author: executor E20. Status: proposal for the lead to dispatch; nothing below has been applied.
Read from the UniFi Network API (GET only, local admin `claude`) at 20:42 PDT.

## Why

Every Swarm service publishes its port through the routing mesh on both workers, and Traefik's generated backends will list both worker addresses (migration plan, batch 0). UniFi lets the edge proxies reach homelab on 30 ports but normandy on three, so Traefik's normandy backend would stay unhealthy and nothing would fail over to normandy during a proxmox-a drain.

## Current state

| Object | ID | Content |
|---|---|---|
| port group `traefik-homelab-ports` | `6a966cc55afbaf142ea88874` | 3001, 3151, 3200, 3500, 3579, 4080, 4580, 5006, 5080, 5090, 5984, 6767, 7080, 7100, 7391, 7474, 7878, 8080, 8091, 8095, 8096, 8097, 8099, 8321, 8686, 8989, 9009, 9696, 25600, 4533 |
| port group `traefik-normandy-ports` | `6a966cc55afbaf142ea88877` | 2283, 7391, 8971 |
| policy `public-api -> homelab (Traefik backends)` | `6a966cc65afbaf142ea8887a`, index 10001, ALLOW | source IP group `6ac0765c1825b50cb14c41c0`; destination ips `[192.168.70.223]`, port group `6a966cc55afbaf142ea88874` |
| policy `public-api -> normandy` | `6a966d165afbaf142ea88add`, index 10002, ALLOW | source IP group `6ac0765c1825b50cb14c41c0`; destination ips `[192.168.70.234]`, port group `6a966cc55afbaf142ea88877` |

The source is already an IP group (the generated return policies list 192.168.20.33 and 192.168.20.4), so phase 4's second edge VM is covered once it is in that group.

## Proposed change (one edit)

Give normandy the same port group homelab has, by adding normandy's address to the homelab policy's destination:

- Policy `6a966cc65afbaf142ea8887a` (`public-api -> homelab (Traefik backends)`): `destination.ips` from `["192.168.70.223"]` to `["192.168.70.223", "192.168.70.234"]`. Rename it `public-api -> workers (Traefik backends)`.
- Leave `public-api -> normandy` (`6a966d165afbaf142ea88add`, 2283, 7391, 8971) as it is, for the Compose-pinned Immich and Frigate and T3 Code.
- Leave both port groups unchanged.

Effect: the edge proxies may reach normandy on the 30 ports of `traefik-homelab-ports`. On normandy those ports answer only where a Swarm service publishes through the routing mesh (no Compose project on normandy uses any of them; 7391 is T3 Code and already allowed). Nothing on VLAN 70 other than the edge proxies gains access; normandy's ufw does not filter routing-mesh traffic, so this policy is the filter.

Ports a Swarm stack will publish that are not in the group yet, to add to `traefik-homelab-ports` only if a later batch routes them through Traefik: 4443 (Authentik HTTPS, not routed today), 1933 (OpenViking, not routed through the generated file), 10200 and 10300 (Wyoming, plain TCP from Home Assistant, not through Traefik). None is needed for the first batches.

## Verification after the change

1. `GET /proxy/network/v2/api/site/default/firewall-policies`: the policy lists both addresses, and its generated return policy follows.
2. From public-api: `curl -s -o /dev/null -w '%{http_code}' http://192.168.70.234:5090/` answers once batch S1 has deployed excalidraw (before that, a connection refusal rather than a timeout shows the path is open).
3. From public-api: `curl -m5 http://192.168.70.234:7100/` (dsh, homelab-only Compose) times out or is refused: nothing listens there.

## Rollback

Set the policy's `destination.ips` back to `["192.168.70.223"]` and its name back.
