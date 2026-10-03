# Phase 4 proposal: UniFi, gaming-pc and Home Assistant changes for two edge VMs

Drafted 2026-10-02, 19:59 to 20:10 PDT, by executor E19. Hosts read: UDM Pro 192.168.90.1
(local Network API, GET only, as the local admin `claude`, then logout; `ssh udm` for
`iptables-save` and `ipset`), gaming-pc (`ufw status numbered`), Home Assistant
(`/config/.storage/http`), public-api, proxmox-a, proxmox-b. Nothing was changed. Every
change below is Igor's to apply.

Address plan, agreed with E18:

| Address | Role |
|---|---|
| 192.168.20.33 | public-api (VM 106, proxmox-a), unchanged |
| 192.168.20.4 | public-api-b (VM 108, proxmox-b, MAC `BC:24:11:19:E9:AE`); answers ping now |
| 192.168.20.3 | the VRRP VIP; does not answer yet |

## 1. What names 192.168.20.33 on the UDM

Read live with `GET /proxy/network/v2/api/site/default/firewall-policies` (256 policies),
`rest/firewallgroup`, `rest/portforward`, `rest/firewallrule`, `v2/.../nat` and `rest/user`,
and on the UDM with `iptables-save | grep 192.168.20.33` and a search of every `ipset`.

- Port forwards: `rest/portforward` returns `"data":[]`; `v2/.../nat` returns `[]`; legacy
  `rest/firewallrule` is empty. Nothing is forwarded from the WAN to public-api: public
  traffic arrives through the Cloudflare Tunnel connectors on homelab and normandy, which
  resolve every name to the edge through AdGuard. So no rule targets the proxy as a
  destination, and no rule needs the VIP added.
- Firewall groups: four port groups (`traefik-homelab-ports`, `traefik-normandy-ports`,
  `traefik-gaming-pc-ports` = 18800, 7391, 8080, `traefik-laptop-ports` = 7391). No address
  group exists.
- Client reservations: `Public API Server` (hostname `publicAPI`, MAC `bc:24:11:7a:f3:a4`,
  fixed IP 192.168.20.33). No reservation and no client in .2 to .5.
- Policies: exactly eight user policies name 192.168.20.33, all as the single source address
  in the Public API zone. Each has an auto-generated `(Return)` twin (predefined, index
  300xx, `origin_id` = the user policy) that names .33 as destination; the UDM regenerates
  those when the user policy is saved, so they need no separate edit. On the UDM these are
  the eight ipsets `UBIOS4policy_src_ip_19` to `_25` and `_28`; `iptables-save` holds no
  literal 192.168.20.33.

Zone-wide policies already cover .3 and .4 with no change: `Internal -> Public API`,
`VPN/Hotspot/DMZ/IoT/High Seas -> Public API`, `Public <-> Public` (all, within the zone),
`Public API -> Gateway` (DHCP/NTP, DNS, ICMP, block rest) and `Cameras -> AdGuard DNS` all
match the zone, not an address.

### The eight policies

All are action ALLOW, protocol TCP, IPv4, source zone Public API, source matching
`IP / SPECIFIC`, `ips: ["192.168.20.33"]`.

| Index | `_id` | Name | Destination (zone, address, port) |
|---|---|---|---|
| 10001 | `6a966cc65afbaf142ea8887a` | public-api -> homelab (Traefik backends) | Internal, 192.168.70.223, port group `traefik-homelab-ports` |
| 10002 | `6a966d165afbaf142ea88add` | public-api -> normandy | Internal, 192.168.70.234, port group `traefik-normandy-ports` |
| 10003 | `6a966d165afbaf142ea88ae0` | public-api -> Home Assistant | Internal, 192.168.70.93, 8123 |
| 10004 | `6a966d165afbaf142ea88ae3` | public-api -> Proxmox web UI | Internal, 192.168.1.5 and 192.168.1.29, 8006 |
| 10005 | `6a966d165afbaf142ea88ae6` | public-api -> Zigbee2MQTT | Internal, 192.168.70.235, 8080 |
| 10006 | `6a966d175afbaf142ea88ae9` | public-api -> gaming-pc | Internal, 192.168.90.212, port group `traefik-gaming-pc-ports` |
| 10007 | `6a9cedf55afbaf142ed0f1ce` | public-api -> laptop (t3code) | Internal, 192.168.90.163, port group `traefik-laptop-ports` |
| 10001 | `6a966d175afbaf142ea88aec` | public-api -> Black Pearl | High Seas, 192.168.60.53, 8080 |

### Proposed definition

For each of the eight, the only change is the source address list:

```
source.ips: ["192.168.20.33"]   ->   ["192.168.20.33", "192.168.20.4"]
```

Destination, ports, protocol, index and action stay as they are. The `(Return)` twins then
name both addresses as destination on their own.

The VIP .3 is deliberately not added as a source. Traefik's backend connections leave from
the interface's primary address (.33 or .4); keepalived adds the VIP as a secondary address,
which the kernel does not pick as a source for routed traffic unless a route says `src`.
E18's keepalived configuration should not add such a route. If Igor prefers belt and
braces, adding .3 as a third source is harmless, but it hides a misconfiguration rather than
exposing it. Verification after the cutover: `conntrack -L -d 192.168.70.223` on the active
edge VM shows `src=192.168.20.33` or `.4`, never `.3`.

Optional rename, for clarity only: `public-api -> X` to `edge -> X`.

### Option B: one address group instead of eight lists

Create an IP address group `edge-proxies` = 192.168.20.33, 192.168.20.4 (UI: Settings,
Profiles or "Port and IP groups", Create, type IP Address) and point each policy's source at
it (Source, IP Address, choose the group). A later third edge VM is then one edit, not eight.
The cost is the same eight edits once. I have not confirmed by API that this UniFi build
accepts an address group as a policy source; the UI shows whether it is offered. If it is
not, use the per-policy lists above.

### Steps in the UniFi UI (recommended)

1. Open the Network application, Settings, then the zone-based firewall policy list
   (Policy Engine / Firewall, depending on the build).
2. Filter by `public-api`. Eight custom policies appear (the `(Return)` entries are
   system-generated and not editable).
3. For each of the eight: open it, in Source keep zone Public API and IP Address, add
   `192.168.20.4` beside `192.168.20.33`, and Save.
4. Check: the eight `(Return)` policies now list both addresses as destination.
5. From public-api-b: `curl -sk -o /dev/null -w '%{http_code}\n' http://192.168.70.223:4080/`
   (Authentik, in `traefik-homelab-ports`), `nc -vz 192.168.70.93 8123`,
   `nc -vz 192.168.60.53 8080`, `nc -vz 192.168.90.212 7391` each connect. Before the edit
   they time out, which is also a useful negative test to run first.

### The same change by API (untested here; I only ran GET)

```
# after logging in as in docs/transition/phase0/2026-10-02-unifi-vlans.md,
# with $jar holding the session cookie and $csrf the X-CSRF-Token
base=https://192.168.90.1/proxy/network/v2/api/site/default
for id in 6a966cc65afbaf142ea8887a 6a966d165afbaf142ea88add 6a966d165afbaf142ea88ae0 \
          6a966d165afbaf142ea88ae3 6a966d165afbaf142ea88ae6 6a966d175afbaf142ea88ae9 \
          6a9cedf55afbaf142ed0f1ce 6a966d175afbaf142ea88aec; do
  curl -sk -b "$jar" -H "X-CSRF-Token: $csrf" "$base/firewall-policies" \
    | jq --arg id "$id" '.[] | select(._id == $id)
         | del(.hits, .last_hit)
         | .source.ips = (.source.ips + ["192.168.20.4"] | unique)' > "/tmp/p-$id.json"
  curl -sk -b "$jar" -H "X-CSRF-Token: $csrf" -H 'Content-Type: application/json' \
    -X PUT --data @"/tmp/p-$id.json" "$base/firewall-policies/$id" | jq -c '{_id, name, src: .source.ips}'
done
```

Run it for one policy first and check the UI before the loop. Rollback: the same with
`.source.ips = ["192.168.20.33"]`.

## 2. Reservations for .2 to .5

The Public API network's DHCP pool is `192.168.20.6 - 192.168.20.254` (read live from
`rest/networkconf`, network `68bf406d01ce4321b79e357c`), so .2 to .5 can never be leased.
No static reservation is needed for the addresses to work, and none is possible for the VIP:
keepalived without `use_vmac` sends the VIP from the holder's own MAC, so the UDM will see
.3 move between the two VMs' MACs. That is expected; it is not an address conflict.

Recommended, not required: a fixed-IP client entry for public-api-b (MAC
`bc:24:11:19:e9:ae`, name `public-api-b`, fixed IP 192.168.20.4), so the UDM's client list
names it, matching how `Public API Server` is recorded. UI: Client Devices, the client,
Settings, Fixed IP Address. Keep .2 and .5 unassigned and note in the plan that they are the
reserve for a third edge VM.

## 3. VRRP between .33 and .4

Confirmed, no rule needed. Both VMs are on VLAN 20 (`vmbr1` tag 20 on each node), so
unicast VRRP (IP protocol 112, .33 to .4 and back) is bridged in the same layer-2 segment
and never reaches the UDM's forwarding path. Even if it did, `Public <-> Public` allows all
traffic within the zone. Phase 0 found no device or layer-3 isolation on the switch
(`acl_device_isolation: []`, `acl_l3_isolation: []`) and network isolation off on VLAN 20.

On the Proxmox side: VM 106's `net0` has `firewall=1`, VM 108's has no firewall flag, and
`/etc/pve/firewall/` is empty on proxmox-a, so no `ipfilter` is in force and nothing drops
the VIP. If the Proxmox firewall is ever enabled with `ipfilter: 1` on VM 106, the VIP has to
be added to its `ipfilter-net0` set first. public-api's `iptables` INPUT policy is ACCEPT and
`ufw` is not active.

## 4. gaming-pc: port 18800

Current, from `sudo ufw status numbered` on gaming-pc:

```
[43] 18800/tcp   ALLOW IN   192.168.20.33   # bellerophon-cartest via edge traefik
```

The rule is hand-made. It is not in homelab-infra (gaming-pc is in the `external` group,
which nothing is deployed to) and no repository on the laptop or under gaming-pc's `~/Work`
holds it. The neighbouring T3 rule `[39] 7391/tcp ALLOW IN 192.168.20.0/24` already covers
.4.

Proposed, run by Igor on gaming-pc:

```
sudo ufw allow proto tcp from 192.168.20.4 to any port 18800 comment 'bellerophon-cartest via edge traefik (public-api-b)'
```

I recommend a second /32 rule rather than widening to 192.168.20.0/24: VLAN 20 also holds
the AdGuard containers and DHCP clients. The UniFi policy `public-api -> gaming-pc` already
limits the source, so this host rule is the second layer.

Related finding: the route itself, `~/apps/traefik/file_provider/cartest.yml` on public-api
(written 2026-10-01 20:59), is also hand-made and not in the repository, so public-api-b
will not serve the cartest name until it is either added to the repository or copied. The
same holds for Traefik's static `~/apps/traefik/traefik.yml` (resolver, entrypoints): it is
not in `stacks/traefik/`. E18 should confirm how public-api-b gets both.

## 5. Home Assistant `trusted_proxies`

Current, `/config/.storage/http` (`yaml_migration_done: true`; `configuration.yaml` has no
`http:` block, so this file is the source):

```
"use_x_forwarded_for": true,
"trusted_proxies": ["192.168.1.223/32", "192.168.20.33/32"],
```

Proposed:

```
"trusted_proxies": ["192.168.20.33/32", "192.168.20.4/32", "192.168.20.3/32"],
```

.4 is required: without it, once public-api-b holds the VIP, Home Assistant rejects every
proxied request that carries `X-Forwarded-For` (HTTP 400) and logs it. .3 is included for the
same reason .3 may be added in UniFi: harmless, and it covers a source-address
misconfiguration. Do not use a prefix such as 192.168.20.0/29: it would include .6 and .7,
which are in the DHCP pool.

192.168.1.223/32 is unexplained. The UDM has no client record for it (current or historical,
in `rest/user`), it does not answer ping, and no VM or container on proxmox-a uses it. It
looks like a leftover of an earlier proxy on the untagged network. I propose removing it;
that is Igor's decision.

How to apply (Igor): Home Assistant rewrites `.storage` files from memory, so do not edit the
file while Core runs. Either use the UI if this build exposes the HTTP settings (Settings,
System, Network), or `ha core stop`, edit `/config/.storage/http`, `ha core start`. Check:
after the change, `https://home.ryzhkov.dev` loads through public-api and, after the failover
test, through public-api-b.

## Decisions for Igor

1. Per-policy source lists (recommended) or an `edge-proxies` address group.
2. Whether to add the VIP .3 as a policy source and trusted proxy (I recommend yes for
   `trusted_proxies`, no for the UniFi sources).
3. Remove 192.168.1.223/32 from `trusted_proxies`, or keep it until it is identified.
4. A fixed-IP client entry for .4 (optional).
5. Bring the cartest route and Traefik's static configuration into the repository, or copy
   them to public-api-b by hand.
