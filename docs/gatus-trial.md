# Gatus trial

Two Gatus instances run beside Uptime Kuma:

- Homelab at `http://192.168.70.223:3002` checks each routed Compose service directly through its inventory host and port, plus Home Assistant, Zigbee2MQTT, MQTT ping, and edge proxy TCP. The endpoints are generated from `stacks[].routes` and `traefik_static_routes`.
- OCI at `http://10.42.0.1:3002` (private mesh) checks the public URLs in `gatus_public_hosts` from outside the house. This follows public DNS and the actual Cloudflare Tunnel path. Public exposure is explicit; adding an internal route alone does not publish it.

Deploy the internal instance with `ansible-playbook playbooks/site.yml -l homelab -e only=gatus`. Deploy the OCI instance with `ansible-playbook playbooks/gatus-oci.yml -l homelab`; Ansible stages generated configuration on homelab, which holds the existing SSH key for OCI. Run `ansible-playbook playbooks/gatus-influx.yml -l homelab` to reconcile a dedicated `gatus` bucket with 52-week retention and InfluxDB scrapers for both instances. Gatus still uses local SQLite for its dashboard; InfluxDB holds longer-term time-series metrics and is covered by the existing daily backup. If homelab or InfluxDB is down, OCI keeps recording locally, but missed InfluxDB scrapes are not backfilled automatically. The OCI dashboard is reachable over Nebula; from a machine without mesh access, use `ssh -L 3003:10.42.0.1:3002 homelab` and open `http://localhost:3003`.

These are availability checks. A routed service without an HTTP health endpoint can still answer with an authentication response; non-routed infrastructure utilities need a declared health endpoint before Gatus can probe them. Emby is hosted on OCI, so the homelab Gatus checks `emby.ryzhkov.dev` through Google Public DNS; this tests the public path to OCI from the house. Kuma continues to send Discord alerts during the trial. Retire it only after coverage and alert parity are established.

To stop either trial instance without affecting Kuma, run `docker compose down` in its stack directory. Named volumes retain local history.
