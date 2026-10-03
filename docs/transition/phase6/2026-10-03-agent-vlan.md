# Phase 6: the agent VLAN (VLAN 130, "Agents")

Applied 2026-10-02 23:10-23:20 PDT (2026-10-03 UTC) by executor E27. Host: UDM Pro at
192.168.90.1, local Network API, as the local admin `claude` (password in the laptop
keyring, `secret-tool lookup service unifi-local`), log in, calls, log out each time, in
the idiom of `docs/transition/phase0/2026-10-02-unifi-vlans.md`. Igor authorised the
change on 2026-10-02 ("Follow the style of vlan tags").

The full JSON before and after (networkconf, zones, the 256 and 297 policies, firewall
groups, devices, port profiles, and every request body and response) is kept outside the
repository on the laptop in `~/.cache/homelab-transition/phase6-e27/`.

## Before

`GET rest/networkconf`: VLANs 20 Public API, 30 DMZ, 40 Storage, 50 IoT, 60 High Seas,
70 Trusted Servers, 80 Untrusted Servers, 90 Trusted Devices, 100 Untrusted Devices,
110 Unify Protect, 120 Proxmox. Every one is tag N with gateway `192.168.N.1/24` and DHCP
pool `.6 - .254`; every one except 60 hands out the three AdGuard addresses
192.168.20.154, .106, .83. No network used VLAN 130 or 192.168.130.0/24.

`GET v2/.../firewall/zone`: Internal, External, Gateway, Vpn, Hotspot, Dmz, and the custom
zones IoT, High Seas, Public API, Cameras. `GET v2/.../firewall-policies`: 256 policies.

Recorded answer: the next tag in the series is 130, and 192.168.130.0/24 is free.

## Calls

1. Zone: `POST /proxy/network/v2/api/site/default/firewall/zone`
   `{"name":"Agents","network_ids":[]}` answered `_id 6ac09e4c1825b50cb14d2dc4`.
2. Network: `POST /proxy/network/api/s/default/rest/networkconf` with High Seas'
   object as the template (ids removed) and: `name Agents`, `vlan 130`,
   `ip_subnet 192.168.130.1/24`, DHCP `192.168.130.6 - .254`, DNS `192.168.20.154,
   .106, .83`, `network_isolation_enabled false`, `firewall_zone_id` the new zone.
   Answered `rc ok`, `_id 6ac09e501825b50cb14d2ddf`; the zone then listed it in
   `network_ids`. Statics for the agent VMs are .2 and .3, outside the pool, as VLAN 20
   keeps .2 - .5.
3. Eight policies, `POST /proxy/network/v2/api/site/default/firewall-policies`, each
   IPv4, enabled, no logging, description pointing here:

| `_id` | Name | Source | Destination | Protocol, port |
|---|---|---|---|---|
| `6ac09e6a1825b50cb14d2eff` | Agents -> edge VIP (HTTPS) | Agents, any | Public API, 192.168.20.3 | TCP 443 |
| `6ac09e6e1825b50cb14d2f13` | Agents -> AdGuard DNS | Agents, any | Public API, 192.168.20.154, .106, .83 | TCP/UDP 53 |
| `6ac09e6e1825b50cb14d2f16` | Agents -> Nebula peers (Public API) | Agents, any | Public API, 192.168.20.33, .4 | UDP 4242 |
| `6ac09e6e1825b50cb14d2f19` | Agents -> Nebula peers (Internal) | Agents, any | Internal, 192.168.70.223, .70.234, 192.168.1.5, .1.29, 192.168.90.163, .90.212 | UDP 4242 |
| `6ac09e6e1825b50cb14d2f1c` | Laptop and steward coordinator -> Agents (SSH) | Internal, 192.168.90.163, 192.168.70.234 | Agents, any | TCP 22 |
| `6ac09e6e1825b50cb14d2f1f` | Agents -> Gateway (DHCP/NTP) | Agents, any | Gateway | UDP 67, 123 |
| `6ac09e6e1825b50cb14d2f22` | Agents -> Gateway (ICMP) | Agents, any | Gateway | ICMP |
| `6ac09e6f1825b50cb14d2f2a` | Agents -> Gateway (block rest) | Agents, any | Gateway | all, NEW and INVALID, BLOCK |

The UDM generated a `(Return)` twin for each of the five ALLOW policies that cross zones.

Why each one:

- The VIP on 443 carries OpenViking (`ov.ryzhkov.dev`) and Jocasta
  (`jocasta.ryzhkov.dev`), both resolved by AdGuard to 192.168.20.3. The steward is not
  reached that way (see below), so no other port is opened.
- AdGuard DNS is the price of handing out AdGuard on DHCP: without it the agents resolve
  nothing. It is the one exception to "VLAN 20 only on VIP:443".
- Nebula: the lighthouse (150.230.40.9) is on the internet, which the predefined
  `Allow All Traffic` to External already covers. The two Nebula policies allow only UDP
  4242 to the LAN addresses of the mesh nodes, so the agents can hole-punch directly
  instead of relaying every packet through OCI. Nothing else on those hosts is open by
  them. No inbound Nebula policy: the agent's own punch opens the conntrack entry the
  peer's packets return on.
- SSH in: the steward coordinator on normandy reaches every worker over a persistent SSH
  connection (`connection: persistent-ssh`, carrier `ssh`); the laptop converges them with
  Ansible. Nothing else may open a connection into the zone.
- Gateway: as High Seas, DHCP and NTP and ping to the UDM, the rest of the UDM (its UI,
  SSH, its DNS) blocked.
- No NAS policy. No steward project names a NAS path (`backlog_v2.projects` on the
  coordinator: every project is a Git repository or a fresh workspace), the coordinator
  carries repositories to workers as bundles over its SSH transport, and the plan forbids
  NFS mounts of agent memory. So the agents get no NFS at all; adding one export later is
  one policy to 192.168.40.40 TCP 2049 in the Internal zone.
- No path to the Swarm ports: nothing into VLAN 70 or 20 is allowed except the VIP on 443
  and DNS, so 2377, 7946 and 4789 are unreachable.

## After

The new zone's predefined policies (read back after the change) block it to and from
Internal (which holds VLANs 1, 40, 70, 90 and 120), Public API, IoT (50), Cameras (110),
High Seas, Vpn, Hotspot and Dmz, and within the zone itself; External and Gateway are
allowed, Gateway then narrowed by the three policies above. 297 policies in all.

Compared byte for byte, without the hit counters: the 256 policies that existed before are
unchanged, and every network other than VLAN 130 is unchanged. No policy was deleted.

## Switch ports: VLAN 130 is not carried to the hypervisors

The controller added the new network to the excluded list of every switch port with a
custom tagged-VLAN list. On Grand Central (USW Pro Max 16 PoE, `692fa9668233ea1abff740cc`)
that includes ports 10 and 11 (proxmox-a and proxmox-b `eno1`, `vmbr0`) and ports 13 and
14 (their `enp3s0`, `vmbr1`, which carries every VM's tagged VLAN): each port's
`excluded_networkconf_ids` gained `6ac09e501825b50cb14d2ddf`. The Proxmox side is fine
(`vmbr1` is VLAN-aware with `bridge-vids 2-4094`, and `tap111i0` carries 130), but
agent-a's ARP for 192.168.130.1 goes unanswered: the switch drops VLAN 130 at port 13.

The fix is to remove `6ac09e501825b50cb14d2ddf` from `excluded_networkconf_ids` of
`port_overrides` ports 13 and 14 only. Ports 10 and 11 stay as they are. It re-provisions
the switch that carries both hypervisors' VM traffic, so the lead approved it to run only
between phase 5 stateful cutovers, on the lead's go.

Prepared 2026-10-02 23:45 PDT from a fresh read
(`GET /proxy/network/api/s/default/stat/device/6c:63:f8:1d:bb:29`; model USPM16P,
firmware 7.5.15.17146, state 1, cfgversion `ec4d69d9fe21ffb6`; `GET rest/device/<id>`
answers `api.err.NotFound` on this build, the write endpoint is still `rest/device/<id>`):

- `gc-put.json` (sha256 `34c5823b...62bf81`): `{"port_overrides": [...]}`, the five
  existing entries (ports 10, 11, 13, 14, 15) in their existing order. `diff` against the
  current list shows only the two removed lines, one in port 13's excluded list (7 to 6
  entries) and one in port 14's (8 to 7). No other field of the device is sent.
- `gc-rollback.json` (sha256 `af8fafbb...f7175c`): the same body with the current list,
  unedited.

Both are in `~/.cache/homelab-transition/phase6-e27/`. Apply, then verify:

```
PUT /proxy/network/api/s/default/rest/device/692fa9668233ea1abff740cc  --data @gc-put.json
GET stat/device/6c:63:f8:1d:bb:29   # state back to 1, ports 13/14 lists without 6ac09e50...
ping 192.168.130.1 from agent-a; ping household guests (homelab, normandy, public-api)
```

Rollback: the same `PUT` with `gc-rollback.json`.

### Applied 2026-10-03 00:16 PDT, on the lead's go

Re-read first: cfgversion still `ec4d69d9fe21ffb6` and `port_overrides` identical to
`gc-rollback.json`, so the prepared body was sent unchanged.

- `PUT .../rest/device/692fa9668233ea1abff740cc --data @gc-put.json` at 00:16:18,
  answered `rc ok` at 00:16:19. The switch went to state 5 (provisioning) at 00:16:24 and
  back to 1 with cfgversion `71cdb4719d90dd59` by 00:16:29.
- Read back: `port_overrides` equals `gc-put.json`. Of the device's top-level fields only
  `port_overrides`, the cfgversion pair, `provisioned_at`,
  `last_successful_config_application_time` and counters differ; every port that was up
  stayed up.
- A monitor on the laptop, once a second from 00:16:09 to 00:16:51, pinged homelab,
  normandy, the VIP, public-api and black-pearl and fetched `ov`, `jocasta` and `vault`
  through the VIP (302, 401, 200). Every one of 37 samples answered: no blip.
- Gatus on normandy (76 endpoints) and homelab (71): none failing, and no failed result
  since 07:10Z (the edit was 07:16Z).
- agent-a: `ping 192.168.130.1` 2/2, 0.19 ms.

Recorded answer: VLAN 130 now reaches both hypervisors' `vmbr1`; household connectivity
never dropped.

## Forgejo SSH for the agents (2026-10-03 00:20 PDT)

UpKeeper's doctor checks Forgejo over SSH, and the steward projects clone from it.
`git.ryzhkov.dev` resolves to the VIP, but the VIP does not serve 2222 (`ssh-keyscan -p
2222 192.168.20.3` answers nothing): Traefik has no TCP route for it. Forgejo's
`forgejo_server` Swarm service publishes `*:2222->22` on the routing mesh, so homelab and
normandy both answer with the pinned key (`AAAAC3NzaC1lZDI1NTE5AAAAICXpj9...`), and
`roles/forgejo_client` points every client's `Host git.ryzhkov.dev` at `forgejo_host`
(homelab, 192.168.70.223) port 2222. The policy names that one destination:

| `_id` | Name | Source | Destination | Protocol, port |
|---|---|---|---|---|
| `6ac0ac181825b50cb14de4ef` | Agents -> Forgejo SSH | Agents, any | Internal, 192.168.70.223 | TCP 2222 |

299 policies afterwards (the new one and its `(Return)`). From agent-a 2222 on
192.168.70.223 opens; 22 on the same host stays closed. Rollback: `DELETE` this `_id`.

## Rollback

In this order, with the agent VMs stopped first:

1. `DELETE /proxy/network/v2/api/site/default/firewall-policies/<_id>` for the nine
   `_id`s above (eight, and the Forgejo one). All eight sit on zone pairs that involve only the Agents zone, so
   deleting them removes no return rule anyone else relies on.
2. `DELETE /proxy/network/api/s/default/rest/networkconf/6ac09e501825b50cb14d2ddf`.
3. `DELETE /proxy/network/v2/api/site/default/firewall/zone/6ac09e4c1825b50cb14d2dc4`.
4. Before step 2, `PUT` `gc-rollback.json` to put `6ac09e501825b50cb14d2ddf` back in
   ports 13 and 14's excluded lists (or let step 2 remove it from them).

## Recorded answer

VLAN 130 "Agents", 192.168.130.0/24, DHCP .6 - .254 with AdGuard DNS, in its own zone
`Agents`, may reach only the edge VIP on 443, AdGuard on 53, Nebula on the mesh nodes'
LAN addresses, the UDM's DHCP/NTP/ICMP and the internet, and is reached only by the
laptop and normandy on 22; no existing policy or network changed. Forgejo SSH on
homelab:2222 was added for UpKeeper and the steward's clones, and the VLAN is carried on
ports 13 and 14 since 00:16 with no household interruption.

## Implications for later phases

- Carried item: the agents are not on the Nebula mesh. The household nodes' hand-written
  Nebula firewalls accept any peer inbound (as public-api-b's `inbound: any` does), so an
  agent on the mesh would bypass this VLAN's isolation. Agents join the mesh only once
  those firewalls are managed by homelab-infra and deny the group `agent`. Their
  certificates were signed on the laptop on 2026-10-02 (group `agent`, mesh addresses
  10.42.0.11 and .12, valid to 2031-09-01) and never deployed; they were moved out of the
  role's source directory to `~/.nebula/held-agent-certs/`. No node configures a
  `pki.blocklist`, so they cannot be revoked; their fingerprints are agent-a
  `655f9685ba441b58e55e0112488aa8dd2d2df66a240f2870a05070b3c86031d4` and agent-b
  `53d476626ecee70c113dab6c0ac14277461460cc0ed027609b243677741f866b`, to be blocklisted
  if they are ever exposed. The two UDP 4242 policies stay, unused until then.
- A later NAS export for agents, or the steward reached over HTTPS through the VIP, is one
  more policy each, recorded here.
