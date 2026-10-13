# Small Debian guest logs

This role installs only native rsyslog (about2MiB plus small library dependencies),
with a dedicated config and systemd unit. It never starts the distribution's
default daemon, takes over a syslog socket, changes application logging settings,
or installs Alloy in the tiny guest.

First startup tails the guest journal from the current end; existing local
historical journals stay available. Later starts resume the stored journal cursor.
This avoids replaying years of previously uncollected journal records as a new
initial backlog. The parent preserves sender timestamps for correct outage-time
queries. Declared imfile sources and imjournal retain persistent cursors in the private
/var/lib/observability-guest-logs directory. Separate pure disk queues allow
32MiB each for journal and file forwarding, total64MiB, with checkpoint and
synchronous queue files. CPU is capped at10%, memory at64MiB and tasks at16.
Disk queues add I/O and finite retention; they do not provide unlimited outages
or application-level delivery acknowledgements over ordinary TCP.

A restricted root reader is necessary for the existing Mosquitto0600 log.
The unit permits only DAC read/search, keeps the filesystem read-only except its
own state, and hides application configuration, SSH and private key directories.
No AdGuard or Mosquitto restart is required.

The first hop is internal RFC5424 TCP1515 to the declared owning parent.
The parent admits only the three exact guest IPs, fixes identity from the actual
connection source, and sends through the shared redaction receiver before its
credentialed HTTPS Loki push. Inventory ownership, receiver peer list, network
grant and forwarder target must be updated together after a guest migration.

Before guest deployment, the parent must apply the exact reviewed I1 UniFi
route and deploy the TCP receiver/firewall. Acceptance uses owned synthetic
journal/file fixtures, all nine redaction patterns and a bounded receiver
outage/recovery. Keep legacy Promtail until that proof passes. It currently
watches nonexistent /var/log/messages on these three guests; their journal and
actual application files are newly added coverage.

Never modify the existing source files merely to manufacture a test marker.
A temporary imfile input can prove the same reader and queue path, then must
be removed from the managed configuration and deleted.
