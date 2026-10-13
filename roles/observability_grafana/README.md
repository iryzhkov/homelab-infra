# Grafana observability provisioning

Run `ansible-playbook playbooks/observability-grafana.yml --check` to inspect drift without API writes, then run without `--check` to reconcile.

The role owns only datasource UID `homelab-metrics`, folder UID `homelab-observability`, and the five dashboard UIDs `homelab-observe-{overview,hosts,containers,logs,agents}`. Existing Loki and InfluxDB sources, other dashboards, defaults, alerts and notifications are retained. An occupied metrics UID with a non-Prometheus type fails safely.

Admin credentials are loaded from the existing encrypted logging secret on the controller and marked secret in the module. The role makes API requests on the managed Grafana host, using its loopback listener by default. The metric source points at a stable private Swarm service name; no host address is embedded in dashboards. When moving Grafana, apply the role to its new inventory host and override the API URL only if its local listener changes. Reapplying recreates these resources from committed JSON artifacts.

Dashboard comparisons include every desired field while tolerating additional server defaults. A changed source document or managed-field drift triggers an update. Grafana-generated IDs and versions are retained on updates. Repository dashboards omit alert objects.

The existing Loki UID is discovered by type. Exactly one existing Loki source is required to avoid accidentally querying another tenant. New logs are expected to carry `host` and a nonempty `job`; the dashboard queries these labels. Metrics expect stable `host`, `job`, and `instance` labels. Containers use standard cAdvisor `name` labels.

Optional hwmon/SMART and agent token/quota panels may show no data. No synthetic zero or inferred token/quota value is generated. Agent capacity panels are clearly described as whole-host usage.

Readiness checks use the datasource health GET API and a proxied GET Prometheus `up` query. A successful empty result is accepted while collectors are being installed. All check-mode requests are GET; a module guard refuses any attempted mutation. No target API POST, PUT, or DELETE can occur in check mode.
