# Read-only Proxmox guest metrics

The exporter uses prometheus-pve-exporter 3.10.1 in a private root-installed
virtual environment. Its dedicated OS user reads only its 0600 API configuration.
Provision observability@pve!metrics with PVEAuditor beforehand and supply
PVE_USER, PVE_TOKEN_NAME and PVE_TOKEN_VALUE in
secrets/observability-exporters.sops.yaml. The role creates no Proxmox API users,
tokens or permissions and never changes application services.

The service listens only on 127.0.0.1:9221. Its systemd network policy restricts
connections to loopback, and the scrape target must remain 127.0.0.1. The
Proxmox API port is fixed at 8006 in configuration. CPU is limited to 20% of
one core and memory to 256 MiB.

TLS verification stays enabled: the existing public cluster CA is copied from
/etc/pve/pve-root-ca.pem into the configuration directory. Exporter 3.10.1
passes the module configuration directly to proxmoxer, whose HTTPS backend
passes the verify_ssl path to requests. This is a CA path, not a false boolean
or client certificate. The inspected certificates on both current hypervisors
include localhost and 127.0.0.1 in their subject alternative names.

The existing cluster CA omits the keyUsage extension. Python 3.13 and current
urllib3 enable VERIFY_X509_STRICT and reject that legacy CA. A role-owned
launcher clears only VERIFY_X509_STRICT in this exporter process. It leaves
CA-chain/signature, hostname and certificate validity verification enabled;
it never sets verify_ssl=false and does not alter the host's global TLS policy.
An invalid trust root and a hostname mismatch must still be rejected.

Alloy on each hypervisor should scrape:

    targets: [{"__address__": "127.0.0.1:9221", "host": INVENTORY_HOSTNAME}]
    metrics_path: "/pve"
    params: {"target": ["127.0.0.1"], "module": ["default"], "cluster": ["1"], "node": ["1"]}
    scrape_interval: "60s"
    scrape_timeout: "30s"

The /metrics route describes the exporter process; /pve performs the actual
Proxmox API collection. Both hypervisors collect the cluster; keep their distinct
host labels to avoid overlapping series. Dashboards aggregating guest values
should deduplicate by guest id rather than sum both cluster views.

Deployment readiness requires /pve HTTP 200 and pve_up guest series with qemu
or lxc ids; a running process or HTTP 200 at the root page is insufficient.
No credentials appear in unit arguments, Ansible output or diffs.

References and validation:
- https://github.com/prometheus-pve/prometheus-pve-exporter#usage
- https://pypi.org/project/prometheus-pve-exporter/3.10.1/
- Inspected installed 3.10.1 pve_exporter.cli, .http, .config and .collector.
- Inspected proxmoxer HTTPS backend and ProxmoxHttpSession.request CA handling.
