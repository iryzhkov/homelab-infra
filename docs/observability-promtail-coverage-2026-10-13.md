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

## PVE acceptance

Deployed the declared-file source on both PVE nodes without changing application
services or file permissions. Each exact active-file stream delivered 25 entries
under job=pve-tasks with the normalized inventory host label. Only counts were
returned during inspection, not raw production logs.

An additional owned temporary file target exercised the same declared_files
component. All nine dummy-secret patterns were redacted before ingest on both
nodes. The canary target was then removed by restoring the ordinary Ansible
configuration, and the owned fixture files and temporary variable file deleted.
A subsequent Ansible check reported changed=0 and failed=0 on both PVE nodes.
The explicitly gated retirement then stopped and disabled only Promtail on both
PVE nodes, with Alloy active. Original private config/positions, binary and unit
remain available for rollback; no application services were restarted.

## Tiny Debian guest deployment and acceptance

No rsyslog or syslog-ng was initially installed in ct100/104/107. Native Debian
rsyslog packages add about 1.9MiB plus three small library dependencies. Signed
official Debian indexes were refreshed independently of unrelated vendor feeds;
existing repositories, application packages and configuration were preserved.
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

Acceptance passed all nine dummy-secret patterns for all six guest journal/file
canary paths through the actual parent receiver and shared redaction. The file
fixture used foreign UID103/GID112 and mode0600, confirming read permission with
only CAP_DAC_READ_SEARCH without changing application file permissions. A ct100
message claiming ct107 was attributed to ct100; no ct107 marker appeared. An
undeclared parent-local TCP peer was rejected.

A temporary source-scoped TCP reject interrupted only ct100 forwarding. Journal
and file markers stayed absent during the outage, survived a restart of only
the dedicated forwarder, and arrived redacted after the rule was removed.
Journal timestamps stayed inside the outage interval. The reject was removed
in finally; household/application services were untouched. Pure-disk action
queues are capped at 32MiB each with persistent input cursors; capacity exhaustion
or a prolonged outage can still lose telemetry. TCP1515 is unencrypted on the
scoped LAN, so only explicitly permitted guests may reach the receiver.

New journals begin at first deployment; retained local history is not deleted.
Existing cursors resume after restart. ct100 initially replayed local history
before this first-start bound was added, and caught up before acceptance. The
default package rsyslog service is stopped and disabled to prevent duplicate
local logging at reboot. The dedicated forwarder limits memory to64MiB, CPU to10%
and tasks to16; final RSS was1.8–2.1MiB with5–6 tasks and no automatic restarts.

After normal configuration was reconciled without canary inputs, each guest
check returned changed=0/failed=0. The gated retirement returned ok13/changed3/
failed0 for each guest, stopped/disabled only Promtail, and retained its original
private config/positions, binary and unit. Dedicated forwarders and AdGuard
(ct100/107) or Mosquitto(ct104) were active; distro rsyslog remained disabled.
State and retirement directories are root0700. Owned canary files and controller
variables were deleted, and their targets and outage rule were confirmed absent.
The old missing /var/log/messages input watched nothing; journal and native file
streams are additional accepted coverage. Quiet/empty AdGuard stdout does not
produce entries until the application writes it.
