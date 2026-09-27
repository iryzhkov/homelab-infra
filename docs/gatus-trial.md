# Gatus trial

Three Gatus instances run beside Uptime Kuma:

- Homelab at `http://192.168.70.223:3002` and Normandy at `http://192.168.70.234:3002` independently check routed services, Home Assistant, Zigbee2MQTT, MQTT, the edge proxy, and steward workers. Each keeps its own SQLite history. The endpoints are generated from `stacks[].routes`, `traefik_static_routes`, and `gatus_steward_worker_ids`. Probe names identify which machine observed a result.
- OCI at `http://10.42.0.1:3002` (private mesh) checks the public URLs in `gatus_public_hosts` from outside the house. This follows public DNS and the actual Cloudflare Tunnel path. Public exposure is explicit; adding an internal route alone does not publish it.

Deploy both LAN instances with `ansible-playbook playbooks/site.yml -l 'homelab,normandy' -e only=gatus`. Deploy the read-only steward health bridge with `ansible-playbook playbooks/gatus-steward-health.yml -l normandy`. Deploy the OCI instance with `ansible-playbook playbooks/gatus-oci.yml -l homelab`; Ansible stages generated configuration on homelab, which holds the existing SSH key for OCI. Run `ansible-playbook playbooks/gatus-influx.yml -l homelab` to reconcile a dedicated `gatus` bucket with 52-week retention and InfluxDB scrapers for all three instances. Gatus still uses local SQLite for its dashboard; InfluxDB holds longer-term time-series metrics and is covered by the existing daily backup. If homelab or InfluxDB is down, Normandy and OCI keep recording locally, but missed InfluxDB scrapes are not backfilled automatically. The OCI dashboard is reachable over Nebula; from a machine without mesh access, use `ssh -L 3003:10.42.0.1:3002 homelab` and open `http://localhost:3003`.

These are availability checks. A routed service without an HTTP health endpoint can still answer with an authentication response; non-routed infrastructure utilities need a declared health endpoint before Gatus can probe them. Emby is hosted on OCI, so both LAN probes check `emby.ryzhkov.dev` through Google Public DNS; this tests the public path to OCI from the house. The Normandy health bridge calls `t3-steward worker list --json` and returns HTTP 200 only for workers the coordinator reports as connected, fresh, and ready. It returns 503 when the coordinator cannot be queried. Its port 3003 is restricted to the LAN and Docker networks. Kuma continues to send Discord alerts during the trial. Retire it only after coverage and alert parity are established.

## Shadow mode and remediation

Kuma remains the alert source during the trial. Gatus does not send alerts or dispatch steward tasks. Review the two LAN probes, OCI public checks, InfluxDB ingestion, and alert parity after two days before considering Kuma removal.

For a later remediation dispatcher, require several consecutive failures, deduplicate by service, and cap retries. Suppress dispatch during a declared development maintenance window and while the steward schedule `docker-update-weekly` has an active run, then allow a settling period after it ends. The schedule currently starts Saturday at 04:00 America/Los_Angeles, but the active run state is the reliable gate if updates take longer. A service should have an inventory owner and a scoped diagnostic task before any automatic fix is allowed. Availability data stays visible during suppression.

To stop a trial instance without affecting Kuma, run `docker compose down` in its stack directory. Named volumes retain local history.
