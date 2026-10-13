# Existing dashboard adoption and query repair

The three pre-existing dashboard UIDs are explicitly adopted by Ansible without moving their folders or deleting any dashboards. Saved originals remain private in `/tmp/grafana-legacy-{ha,overview,security}.json`; dashboard histories remain available in Grafana. The module refuses any other legacy UID and permits external sharing only for the three separate aggregate copies.

Before repair, the 21 legacy queries included three reproducible HTTP 400 errors: both Home Assistant presence/sleep Flux timelines combined string and numeric `_value` schemas after removing their grouping fields; the Traefik error regex used invalid escaped syntax. Three current HA statistics were empty because 2–10-minute windows on event-driven Influx writes do not represent current state.

Repairs preserve historical Influx charts and its datasource UID `bfn7e4xhxpce8d`, normalize timeline values to strings, and use native VictoriaMetrics measurements for current light states, thermostat temperature and thermostat mode. Humidity uses percent units. Native current-temperature Celsius is converted to Fahrenheit for consistency with the existing historical dashboard.

Legacy Loki charts now normalize old `container_name` and new `container` labels without dropping retained history; PVE journal selectors accept actual `journal` and `systemd-journal` jobs with confirmed Proxmox inventory host labels. Traefik status matching accepts common JSON status fields and text access-log formats. Authentication/password-manager inputs remain deliberately excluded; their panels explicitly describe retained historical coverage instead of reenabling sensitive collection.

Initial repaired-query acceptance: 21/21 valid queries; all 10 HA queries return data, all 6 legacy overview queries return data, and 2 security queries return events. The three empty security windows are expected: Authentik/Vaultwarden excluded and no matching Traefik errors. No alert/notification resource changes are included. Grafana backend/browser acceptance and post-cutover datasource checks are recorded separately as they complete.
