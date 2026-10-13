# Lightweight Debian LXC metrics

Use this role for small Debian LXCs such as ct104 and ct107. It installs the
distribution's native `prometheus-node-exporter` and `iptables` without recommended
packages. Package installation suppresses service autostart until a scoped
firewall and listener configuration are installed. No application service or
existing Promtail service is changed or restarted.

The exporter binds only the guest's private IPv4 address on TCP 9911.
The dedicated `OBSV-LXC-NODE` chain allows local loopback checks and the owning
Proxmox host, then drops other inbound sources for that port. The helper replaces
only this chain atomically and inserts one narrow INPUT jump; all other policies,
chains and application ports remain untouched. A systemd dependency applies the
firewall before exporter startup on every boot.

Defaults infer the guest address from `ansible_default_ipv4.address` and the
owning Proxmox source from `ansible_host`, as provided by the pct connection.
Declare explicit `observability_lxc_node_bind_address` and
`observability_lxc_node_collector_address` for other connection arrangements.
After a guest migration, update its owning host/source address and reapply before
scraping from the new host. A guest DHCP address change also needs reapplication.

Configure the owning hypervisor's Alloy to scrape `GUEST_IP:9911` with stable
`host=ct104` or `host=ct107`, `instance`, `job=integrations/node_exporter`, and
`vmid` labels. The guest contains neither ingest credentials nor a remote-write
queue. Its root filesystem and network counters are useful here; Proxmox
guestmetrics remain authoritative for guest CPU/memory limits and usage because
LXC /proc views can reflect host resources depending on lxcfs configuration.

The exporter enables only CPU, filesystem, loadavg, meminfo, netdev, stat, time
and uname collectors. Limits are 96MiB maximum memory, 64MiB high threshold, 10%
CPU and 32 tasks. The role checks disk headroom and real metrics readiness.

Observed 2026-10-13:

| Guest | OS | Address | Root free | RAM |
|---|---|---|---|---|
| ct104 | Debian 12 | 192.168.70.241 | 696MiB / 2GiB | 1GiB |
| ct107 | Debian 13 | 192.168.20.106 | 818MiB / 2GiB | 2GiB |
| ct110 | Alpine 3.23 | 192.168.70.250 | 773MiB / 1GiB | 128MiB |
| ct201 | Alpine 3.22 | 192.168.20.83 | 613MiB / 1GiB | 2GiB |

The small native package is about 13–17MiB installed. Existing Promtail remains
the temporary log transport on these guests; moving it is a separate action.
Alpine guests use Proxmox guestmetrics for this deployment, avoiding Python and
additional agents. Their already-running BusyBox syslogd supports later UDP
forwarding with `-R DESTINATION:PORT -L` while preserving local logging. Parent
Alloy listener, source restrictions, host relabeling, redaction and delivery
acceptance must be in place before that change.

Validation: Debian's official 1.5.0 package binary was downloaded and exercised
with these flags on a temporary loopback port; it returned CPU/memory/filesystem
and uname metrics. Ansible syntax, YAML parsing, rendered firewall shell syntax
and narrow firewall scope checks passed.
