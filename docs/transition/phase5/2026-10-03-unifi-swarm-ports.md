# Phase 5: UniFi, normandy added to the edge's Traefik backend policy

Date: 2026-10-02, about 20:50 to 21:00 PDT (the file name carries the phase 5 record date).
Executor E19, under Igor's authorisation relayed by the lead. Proposal:
`proposals/unifi-swarm-ports.md` (E20). Hosts: UDM Pro 192.168.90.1 (local Network API as
the local admin `claude`, password from the laptop keyring, never printed), public-api
192.168.20.33, public-api-b 192.168.20.4, normandy 192.168.70.234.

One edit only: the destination of `public-api -> homelab (Traefik backends)`. The proposal's
rename to `public-api -> workers (Traefik backends)` was not made, because the lead's brief
limited the change to the destination. The `edge-proxies` group was not touched; E18's VIP
cutover was running in parallel.

## Before

`GET /proxy/network/v2/api/site/default/firewall-policies`: 256 policies. The policy:

```
_id 6a966cc65afbaf142ea8887a  name "public-api -> homelab (Traefik backends)"  index 10001  ALLOW tcp
source:      zone 693c8833269937381b6a8575 (Public API), IP/OBJECT, ip_group_id 6ac0765c1825b50cb14c41c0 (edge-proxies)
destination: zone 68bf4d0401ce4321b7a02460 (Internal), IP/SPECIFIC, ips ["192.168.70.223"],
             port_matching_type OBJECT, port_group_id 6a966cc55afbaf142ea88874 (traefik-homelab-ports)
```

Probes, `curl -s -o /dev/null -m 5 -w '%{http_code}' http://192.168.70.234:<port>/`, the same
from public-api and from public-api-b:

| Port | Meaning | Before |
|---|---|---|
| 2283 | Immich, already allowed by `public-api -> normandy` | 200 |
| 3001 | in `traefik-homelab-ports`, not allowed to normandy | 000, curl exit 28 (timeout) |
| 5090 | same (excalidraw after batch S1) | 000, exit 28 |
| 7100 | same (dsh, homelab-only) | 000, exit 28 |

## Call

The policy as read, with `hits` and `last_hit` removed and
`.destination.ips = ["192.168.70.223","192.168.70.234"]`, sent as

```
PUT /proxy/network/v2/api/site/default/firewall-policies/6a966cc65afbaf142ea8887a
-> destination.ips ["192.168.70.223","192.168.70.234"], source ip_group_id 6ac0765c1825b50cb14c41c0, no error
```

Nothing was deleted.

## After

Diff of all policies (normalised by `_id`, `hits` and `last_hit` removed): 256 before and
after, none added or removed, two changed, and in each only `source` or `destination`:

- `public-api -> homelab (Traefik backends)`: destination ips `[192.168.70.223]` became
  `[192.168.70.223, 192.168.70.234]`;
- its system `(Return)` policy: source ips `[192.168.70.223]` became
  `[192.168.70.223, 192.168.70.234]`; destination still `[192.168.20.33, 192.168.20.4]`.

On the UDM, `ipset list UBIOS4policy_dst_ip_19` now holds 192.168.70.223 and
192.168.70.234.

Probes after the change, from both edge VMs: 2283 still 200; 3001, 5090 and 7100 still time
out (exit 28). The timeout now comes from normandy, not the UDM: normandy's `INPUT` policy is
DROP under ufw, nothing listens on those ports, and its policy-drop counter rose from 45 to 52
packets across one probe from each edge VM (3001 from public-api, 5090 from public-api-b).
So the UDM passes the traffic and the host drops it. A Swarm service published through the
routing mesh is DNATed before `INPUT`, so these ports will answer once a service publishes
them; that check belongs to batch S1 (excalidraw on 5090), as the proposal says.

Through public-api, `vault.ryzhkov.dev` and `photos.ryzhkov.dev` still return 200.

Recorded answer: the edge proxies may now reach normandy on the 30 ports of
`traefik-homelab-ports`; the only remaining filter on those ports is normandy itself.

Not measured: the normandy drop counter before the change, so the comparison rests on the
ipset contents and the counter after it, not on a before/after pair.

## Rollback

Read the policy, then `PUT .../firewall-policies/6a966cc65afbaf142ea8887a` with
`.destination.ips = ["192.168.70.223"]` (`hits`, `last_hit` removed). Never delete the
policy: removing the last policy of a zone pair also removes its return rule.
