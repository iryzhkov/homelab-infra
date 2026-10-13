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

Separate scopes are needed for agent VLAN -> VIP:8443 and OCI mesh -> edge:8443.
Neither is implied by the backend grant.
