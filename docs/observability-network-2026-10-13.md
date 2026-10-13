# Observability network register (2026-10-13)

Consumer: the two edge Traefik processes. Owner: homelab-infra maintainer.
Purpose: authenticated POST-only telemetry ingestion into the private stores.
Source: public-api 192.168.20.33 and public-api-b 192.168.20.4.
Destination: homelab 192.168.70.223 and Normandy 192.168.70.234, TCP 8429.
Review date: 2026-11-13, together with security tightening.

I1 scope: existing UniFi policy 6a966cc65afbaf142ea8887a already names exactly
those two sources (IP group 6ac0765c1825b50cb14c41c0) and destinations.
Append only 8429 to its destination port group traefik-homelab-ports
6a966cc55afbaf142ea88874. Sources and destinations were read and asserted before
the update. No other existing member was changed; readback verified the append.

The gateway on 8429 requires per-client credentials on exactly /api/v1/write and
/loki/api/v1/push. It rejects query, admin and other paths. The two TLS routes live
on a separate 8443 edge listener, never on household 443 and never in Cloudflare
Tunnel. VictoriaMetrics has no published port. /ready discloses only readiness.

Rollback: remove only 8429 from this port group's current members and verify
normal household routes; do not restore an old whole-controller snapshot.

Baseline policy/group evidence (private, no credentials):
laptop /tmp/monitoring-unifi-baseline.json. Baseline had 300 policies. Update did
not create or delete a policy. TLS push paths subsequently returned 401 without
credentials; query returned 404, proving end-to-end backend reachability.

Agent telemetry I1 (same owner/review date): only agent-a 192.168.130.2 and
agent-b 192.168.130.3 may reach VIP 192.168.20.3 TCP 8443, authenticated push paths
only. Applied policy 6acdfde31825b50cb1321662 plus its generated return rule;
readback proved the exact sources, destination and port, with no old policy changed.
Rollback deletes only that new policy. Existing 443 and isolation remain.

Alpine I1 (same owner/review date): ct110 192.168.70.250 reaches its owning
proxmox-a 192.168.1.5 UDP 1514 through existing routing. ct201 192.168.20.83
needed new UniFi policy 6ace05fc1825b50cb132a9d9 to only proxmox-b
192.168.1.29 UDP 1514. It has no generated return rule because syslog is
unidirectional. Readback verified all 302 prior rules remained semantically
unchanged. Rollback deletes only this new policy. Each hypervisor additionally
has a persistent INPUT rule restricted to its declared guest; Alloy requires
that firewall unit at boot. Local guest logs remain available. UDP forwarding
is best effort and has no delivery acknowledgement or encryption on this LAN.

I2 Proxmox identity: observability@pve!metrics, privilege separation enabled;
PVEAuditor on / for both user and token, propagated. Only Audit privileges
were read back. Owner: homelab-infra maintainer; review 2026-11-13; token expiry
2027-10-13. Credential is SOPS-held in observability-exporters.sops.yaml,
service-owned 0600 on each node, used only against its local verified API.
Rollback stops observability-pve, removes its token ACL and token, then its
user ACL and user. Existing cluster identities are untouched.

OCI mesh -> edge:8443 was qualified read-only: 10.42.0.1 reaches edge
10.42.0.3:8443 and gets 404 for an unmatched path. No new mesh rule needed.
