# Phase 0 check: UniFi VLANs 20, 60 and 70

Measured 2026-10-02 12:58-13:00 PDT. Host: UDM Pro at 192.168.90.1 (UniFi OS
5.1.33). Read only: GET requests on the local Network API as the local admin
`claude` (password from the laptop keyring, `secret-tool lookup service
unifi-local`), followed by a logout. Nothing was changed.

Endpoints: `/proxy/network/api/s/default/rest/{networkconf,setting,firewallgroup,user}`,
`/proxy/network/api/s/default/stat/sta`,
`/proxy/network/v2/api/site/default/{firewall-policies,firewall/zone}`.

## Networks

```
VLAN  name             zone        subnet            DHCP pool        DHCP DNS                                    isolation  IGMP snoop
20    Public API       Public API  192.168.20.1/24   .6 - .254        192.168.20.154, .106, .83 (AdGuard)          off        off
60    High Seas        High Seas   192.168.60.1/24   .6 - .254        1.1.1.1, 8.8.8.8, 1.0.0.1                    on         off
70    Trusted Servers  Internal    192.168.70.1/24   .6 - .254        192.168.20.154, .106, .83 (AdGuard)          off        off
```

Every other LAN network also advertises the three AdGuard addresses except
VLAN 60. IGMP snooping is off on every network. The cameras are on VLAN 110
"Unify Protect" (zone Cameras); VLAN 60 is not a camera VLAN, it is the
"High Seas" network that holds black-pearl (192.168.60.53) and normandy's
second NIC.

Zone membership: Internal = Unify (untagged, 192.168.1.0/24), Storage (40),
Trusted Servers (70), Trusted Devices (90), Proxmox (120). High Seas, Public API,
IoT and Cameras are each their own custom zone.

## Firewall policies for VLAN 60 (High Seas)

User policies (index 10000-10003, evaluated before the predefined ones):

- High Seas -> Internal: only `192.168.40.40` (the NAS, all protocols) and
  `192.168.70.223` port 8096 (Emby on homelab). Everything else into Internal is
  blocked by the predefined Block All and the "Isolated Networks" rule for
  192.168.60.0/24.
- High Seas -> Public API: allow all ("High Seas -> Public API").
- High Seas -> Gateway: UDP 67,123 and TCP/UDP 53 and ICMP allowed, the rest
  blocked.
- High Seas -> External: allowed (predefined).
- High Seas -> IoT, Cameras, Hotspot, DMZ, VPN, and within High Seas itself:
  blocked (network isolation on).

What can reach VLAN 60:

- Internal networks 1, 70, 90, 120 -> High Seas: allow all ("Internal -> High
  Seas"). Storage (40) is not in that list.
- Public API: `192.168.20.33` -> `192.168.60.53` TCP 8080 ("public-api -> Black
  Pearl"); other Public API traffic into High Seas is blocked.
- External, IoT, Cameras, VPN, Hotspot: blocked (return traffic only).

## Firewall policies for VLAN 70 (Trusted Servers, zone Internal)

- Internal -> Internal: allow all for networks 1, 70, 90, 120 ("Internal ->
  Internal (non-isolated)"); Storage (40) is isolated.
- Internal 1/70/90/120 -> Public API, IoT, High Seas: allow all; Internal ->
  Cameras: allow all.
- Internal -> External: allowed, except DoT (853) and a list of DoH resolver
  IPs on 443, which are blocked.
- Into VLAN 70 from Public API, by single source address `192.168.20.33` only:
  homelab `192.168.70.223` (TCP, port group `traefik-homelab-ports`), normandy
  `192.168.70.234` (TCP, `traefik-normandy-ports`: 2283, 7391, 8971), Home
  Assistant `192.168.70.93:8123`, Zigbee2MQTT `192.168.70.235:8080`; also
  gaming-pc `192.168.90.212`, the laptop `192.168.90.163` and Proxmox
  `192.168.1.5`, `192.168.1.29` port 8006.
- Into VLAN 70 from High Seas: NAS and Emby only (above). From IoT:
  `192.168.70.93` (Home Assistant, Matter) and `192.168.70.223:8096`.
- Cameras -> Public API: TCP/UDP 53 (AdGuard).

## VLAN 20 addressing for a VIP and a second edge VM

The DHCP pool is `192.168.20.6 - 192.168.20.254`, so the only addresses outside
it are `192.168.20.2 - 192.168.20.5` (`.1` is the gateway). Active clients on
VLAN 20: `.33` publicAPI, `.83`, `.106`, `.154` (AdGuard), all with fixed-IP
reservations inside the pool; no client and no reservation uses `.2 - .5`, and
none of `.2 - .32` answered ping from proxmox-a.

## VRRP on VLAN 20

- IGMP snooping: off on VLAN 20 (and every network). 224.0.0.18 is link-local
  and is flooded within the VLAN regardless.
- Switch settings (`global_switch`): `dhcp_snoop: true` (DHCP guarding only),
  `acl_device_isolation: []`, `acl_l3_isolation: []`; no device isolation.
- Network isolation: off on VLAN 20. "Public <-> Public" allows all traffic
  within the zone.
- Proxmox `vmbr1`: `multicast_snooping=1`, `vlan_filtering=1`; link-local
  224.0.0.x is not subject to snooping. Proxmox firewall disabled (see the
  qm-config file).
- Client isolation applies only to WLANs and does not touch wired VLAN 20.

## Recorded answer

VLAN 60 "High Seas" (isolated, DNS 1.1.1.1/8.8.8.8/1.0.0.1, not cameras) may
reach only the NAS, Emby on homelab and the Public API zone, and is reachable
from Internal 1/70/90/120 and from public-api to black-pearl:8080; VLAN 70 is in
the open Internal zone and gets AdGuard DNS (.154/.106/.83 on VLAN 20); VLAN 20's
DHCP pool covers .6-.254, so only .2-.5 are free for a VIP and second edge VM;
nothing blocks VRRP on VLAN 20.

## Implications for later phases

- Phase 4: every policy from Public API into VLAN 70, 90, 60 and the Proxmox
  hosts names the single source `192.168.20.33`. A second edge VM gets no
  backend access until each of those policies also names its address (or an IP
  group of both). The VIP itself is only a destination for inbound traffic, but
  if keepalived makes the VIP the source of backend connections those rules
  must include it too.
- Use `192.168.20.2 - .5` for the VIP and the second edge VM, or shrink the DHCP
  pool first (a UniFi change, outside phase 0).
- Phase 5 rehearsal: a VLAN 70 client resolves through the three AdGuard LXCs on
  VLAN 20, so DNS survives normandy's drain as long as those LXCs stay up.
  VLAN 60 clients use public resolvers and do not depend on AdGuard.
