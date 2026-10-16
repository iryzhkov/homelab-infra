# Gatus monitoring

Gatus replaced Uptime Kuma on 2026-10-01. Four instances serve different roles:

- Homelab at `http://192.168.70.223:3002` and Normandy at `http://192.168.70.234:3002` independently check routed services, Home Assistant, Zigbee2MQTT, three AdGuard instances, MQTT, the edge proxy, and each other's probe health. Checks are generated from `stacks[].routes` and `traefik_static_routes`. The steward checks and their health bridge on normandy were removed on 2026-10-16 along with all agent software on homelab and normandy.
- OCI at `http://10.42.0.1:3002` (private mesh) checks `gatus_public_hosts` from outside the house using public DNS and the Cloudflare Tunnel path. Normandy checks OCI's `/health` endpoint. From outside the mesh, use `ssh -L 3003:10.42.0.1:3002 homelab` for its dashboard.
- The public-only view at `https://uptime.ryzhkov.dev` runs on homelab port 3001 and shows 16 public services, including Emby. It has no Discord alert rules and exposes no LAN or steward checks. OCI owns alerts for public tunnel services; Normandy owns internal and Emby alerts. Homelab alerts if Normandy's probe fails. Each alert requires five failed minute checks and resolves after two successes.

Deploy both LAN probes with `ansible-playbook playbooks/site.yml -l 'homelab,normandy' -e only=gatus`. Deploy the public-only view with `ansible-playbook playbooks/site.yml -l homelab -e only=gatus-public`. Deploy OCI with `ansible-playbook playbooks/gatus-oci.yml -l homelab`; homelab stages inventory-derived files and uses its existing SSH key. Traefik's generated route for `uptime.ryzhkov.dev` points to homelab port 3001; apply `playbooks/traefik.yml` from homelab, which has SSH access to the edge proxy.

Run `ansible-playbook playbooks/gatus-influx.yml -l homelab` to reconcile the `gatus` InfluxDB bucket with 52-week retention and scrapers for homelab, Normandy, and OCI. The public-only view is a presentation endpoint and is not scraped separately. Each probe keeps SQLite history locally; missed InfluxDB scrapes are not backfilled. The existing daily backup covers InfluxDB.

These are availability checks. A routed service without a dedicated HTTP health endpoint may pass on an authentication response. Non-routed utilities need an explicit health declaration. Emby runs on OCI, so the LAN probes check it through Google Public DNS. The Normandy steward health bridge reports 200 only for workers the coordinator considers connected, fresh, and ready; its port 3003 is restricted to LAN and Docker networks.

The Discord webhook is SOPS encrypted in `secrets/gatus-alerting.sops.yaml`. An actual pair of stale steward workers triggered Gatus Discord sends at 2026-10-02 03:54 UTC, and Gatus persisted both triggered states without delivery errors. Kuma's other active notification was a Home Assistant webhook to port 8093, whose listener had already been archived. Kuma's Immich and Traefik direct targets were stale; current Gatus checks use the live Immich route and edge proxy TCP path.

## Cutover and rollback

Kuma's container was stopped during the cutover; `uptime.ryzhkov.dev` now serves the public-only Gatus view successfully. Its database remains at `/home/iryzhkov/app/kuma`, and `stacks/kuma/docker-compose.yml` is retained for rollback. To restore it, stop `gatus-public`, start the Kuma Compose project, restore the `kuma` inventory route, and deploy Traefik from homelab.

Gatus does not dispatch steward repair tasks. Before adding a dispatcher, require consecutive failures, deduplication by service, capped retries, an inventory owner, and a scoped diagnostic task. Suppress dispatch during development maintenance and while `docker-update-weekly` has an actively progressing run; allow a settling period afterward. That schedule starts Saturday at 04:00 America/Los_Angeles, but `activeRunId` remains populated after success, so check run progress. Availability data remains visible during suppression.

## Shadow review: 2026-09-29

After 48 hours, homelab and Normandy each had 41/41 healthy checks and OCI had 16/16. InfluxDB contained series from all three. Brief failures recovered in Home Assistant, public routes, Emby, and a steward worker. Three AdGuard checks were added at review time, reaching 44/44 on each LAN probe. Kuma remained active until the Discord alert path and public status replacement were deployed.
