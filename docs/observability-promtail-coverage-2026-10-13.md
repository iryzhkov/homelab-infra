# Remaining Promtail coverage audit

Read-only inspection of live configurations, unit metadata and file permissions
found the following inputs. Client credentials and raw log content were not
printed.

| Host | Legacy Promtail inputs | Alloy replacement |
| --- | --- | --- |
| proxmox-a | Journal, max_age 12h, unit relabel; /var/log/pve/tasks/active; host=pve1 | Journal24h/unit relabel already active; explicitly declared task file through common redaction; host=proxmox-a |
| proxmox-b | Same inputs; host=pve2 | Same replacement; host=proxmox-b |
| ct100 | Only /var/log/messages, host=adguard | Legacy path absent; persistent guest journal and AdGuard out/err files are NEW coverage |
| ct104 | Only /var/log/messages, host=mqtt | Legacy path absent; persistent journal and Mosquitto file are NEW coverage |
| ct107 | Only /var/log/messages, host=adguard | Legacy path absent; persistent journal and AdGuard out/err files are NEW coverage |

All positions paths are /var/lib/promtail/positions.yaml, root-owned 0600.
Both PVE task files are root-owned0644 and already readable by the unprivileged
Alloy identity; no privilege changes are necessary. Only the exact legacy
active file is declared, without broadening to all archived PVE task logs.
Alloy retains its own persistent file cursor under /var/lib/alloy. A first read
may replay the small existing task file with the normalized inventory host label.

## Tiny Debian guest proposal

No rsyslog or syslog-ng is installed in ct100/104/107. Native Debian rsyslog
packages require about 1.9MiB installed rather than the large Alloy binary.
Run a dedicated bounded forwarder with an independent config/unit, suppressing
package default service startup and creation of extra local logs.

Use imjournal with a persistent cursor for guest journals, and imfile for:
- ct100/107: /var/log/AdGuardHome.out and /var/log/AdGuardHome.err (root0644).
- ct104: /var/log/mosquitto/mosquitto.log (0600, UID103/GID112).
Read permission on the MQTT file must be explicit; do not modify its logging
settings or restart Mosquitto merely to obtain logs.

Forward RFC5424 over TCP1515 to the owning parent, proxmox-a192.168.1.5.
Declare only ct100192.168.20.154, ct104192.168.70.241 and ct107192.168.20.106
as allowed source addresses, both in the parent firewall and the scoped UniFi
I1 policy. Map host labels from connection IP; reject all unknown peers and
ignore message-supplied host identity. Feed the existing common redaction
receiver. Persist input offsets and bound forwarding queue storage to64MiB.

Acceptance must use an owned synthetic journal marker and an owned temporary
file input, test all dummy redaction patterns, and prove queued delivery after
a bounded receiver outage. Only then disable the existing Promtail daemon,
preserving its configuration and positions for rollback. The old missing
/var/log/messages input provides no evidence of guest journal or application
log coverage; those new inputs require their own acceptance.
