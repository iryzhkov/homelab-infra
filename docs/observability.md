# Homelab observability

This deployment implements the additive portion of Jocasta
`homelab/plans/checkmk-shadow-2026-10-12.md@8`, interactively on 2026-10-13.
Alerts were explicitly excluded. Existing Influx history and writers are kept.

Open [the overview](https://logs.ryzhkov.dev/d/homelab-observe-overview).
Follow its links to hosts/hardware, containers, logs and agent hosts.
Existing Grafana authentication applies. Agent quota/token panels show no data
until the steward/coordinator supply their own metrics. Live discovery confirmed
Steward rc.126 has no Prometheus endpoint; T3's `/metrics` path returns its HTML
application shell. These are dependencies on native agent-tooling work, not
missing scrape credentials.

## Layout and portability

- `observability`: VictoriaMetrics and an authenticated push gateway, pinned
  initially to homelab. Edge Traefik follows either worker through health checks.
- `logging`: existing Loki/Grafana/Influx services, still pinned to homelab.
- Credentials and images are pinned in Ansible/SOPS, state lives on the worker
  pool, and ingestion uses stable TLS names rather than the store host address.
- VM snapshots use native vmbackup/vmrestore; complete generations are copied
  to the other worker. Existing offsite backup ships both workers' snapshots.
- Both real metrics-stack moves and isolated owner/peer restores passed.
  See [the evidence and recovery procedure](observability-portability-2026-10-13.md).
- This is one active metrics store with recoverable peer copies. It is not an
  automatically elected redundant metrics cluster. A move interrupts ingestion
  briefly; remote-write buffering resumes after the endpoint returns.

Collectors use an unprivileged Alloy identity and private credential files.
Docker collection passes through a read-only, endpoint-restricted Unix socket
proxy, with no TCP listener and no network interface. Its persistent socket
is accessible only to the Alloy group, the dedicated proxy identity and root.
Container inspection can include environment values, so access to this socket
is restricted; cAdvisor exports only allowlisted labels.
cAdvisor runs in a separately constrained root helper. Small 2 GB Debian LXCs
use the distribution node exporter, accessible only to their owning hypervisor;
Proxmox metrics cover every VM/LXC, including stopped guests. Alpine logs use
scoped syslog to their parent, redacted before Loki. Local logs are retained.

SMART checks read only declared stable drives, and UPS metrics use anonymous
read-only NUT variable access on loopback. No UPS control credentials are given
to the exporter. iGPU PMU reporting produces an atomic textfile in parallel
with its existing Influx writes.

## Controller and convergence

Use the laptop controller, whose SSH aliases and SOPS identity already reach
the management hosts. Install pinned `requirements.yml` collections and the
controller Python dependencies `netaddr` and `paramiko`. The latter is required
by the Proxmox pct connection. No SSH daemon is added to a guest.

Deploy the store with `swarm.yml --tags stacks -e only=observability` and the
dedicated edge configuration with `observability-edge.yml`. The edge play moves
the VIP to its peer during each convergence and checks household routes.

The focused plays in `playbooks/` are:

- `observability-collectors.yml`: host metrics/logs, serial rollout.
- `observability-pve.yml`, `observability-hardware.yml`,
  `observability-nut.yml`: native read-only exporters.
- `observability-lxc-node.yml`: lightweight small Debian guests.
- `observability-alpine.yml`: additive Alpine syslog forwarding after receiver
  firewall and real redaction acceptance; existing application file logs remain
  on their original Promtail path until a separate complete file handoff.
- `observability-gpu.yml`: existing GPU writer's parallel Prometheus output,
  after the root-owned `/var/lib/observability-textfile` directory exists.
- `observability-unifi.yml`: the native read-only poller on the declared member.
- `observability-grafana.yml`: only dedicated dashboard/data-source UIDs.
- `observability-peer-backup.yml`: existing full-stack peer configuration.
- `observability-backup.yml`: consistent native metric backups and timers.

No broad base play is needed for this deployment. Run exporters before their
scraping collectors. For a newly added host, install collection before its GPU
writer. Persistent syslog firewall is required before opening a parent receiver.

## Acceptance and remaining migration gates

Redaction fixtures cover bearer/basic headers, named token/key/password/secret
fields, connection strings, provider key formats and JWTs. The live file,
journal and Docker acceptance script is `scripts/verify-observability-redaction.py`.
The socket proxy denies write, exec, archive and export operations. The laptop
uses mesh addresses while travelling, and both laptop/OCI resolve the ingest
names to both mesh edges. The LAN collectors use the failover VIP. Native backup
authentication uses a private stdin pipe rather than a secret in process arguments.
Metric queries, dashboard provisioning and household route health are checked.

Existing Docker Loki drivers, toolfeedback, Debian Promtail, Influx readers and
writers remain during the parallel period. Recreating household application
containers or restarting Docker to retire the drivers would violate the
overnight no-outage constraint. Do that in a separate maintenance window after
checking coverage and deciding the history retention per bucket. Do not remove
the logging stack's move/maintenance guards before its plan gates and two move
tests pass. Its current portability is not claimed.

Home Assistant's live configuration has `default_config` and `influxdb`, but
no `prometheus` integration. Enabling its scrape requires a Home Assistant core
restart, which is deferred under the overnight no-outage constraint.
Home Assistant integration/embedding remains pending its dedicated non-admin
identity and configured integration. UniFi polling uses a dedicated local-only
Network viewer with only read/view scopes, a pinned console certificate and
SOPS-held credentials. No existing administrator credential is reused for
telemetry. Move its single `observability_unifi_pollers` inventory membership,
converge the new host and collector, then stop the old poller after validation.
Agent read access must wait for the remaining unredacted legacy log paths to
be retired; MCP/skill publication then follows the agent-tooling release process.

Monitoring retention is bounded (VM 400 days, Loki 30 days, local journald and
service memory limits). Disk capacity still needs routine observation; a
retention period does not guarantee that an arbitrary input rate will fit.
No alert rules, contacts or notification policies are provisioned here.
