# Additive Alpine BusyBox syslog forwarding

Run on the parent hypervisor, using pct rather than assuming Python or SSH in
Alpine. Declare observability_syslog_guest.name (ct110 or ct201), and set
observability_alpine_forwarding_verified=true only after actual UDP socket
delivery, scoped receiver firewall and Loki dummy-secret redaction pass.
By default this role skips changes.

The only accepted existing configuration is SYSLOGD_OPTS="-t" or the exact
managed form. The role appends -R PARENT_LAN_IP:1514 -L, preserving local
/var/log/messages and existing -t semantics. It restarts only BusyBox syslog,
then requires a harmless marker in the same local file. A failed acceptance
restores the original configuration and restarts syslog with original options.
Healthcheck, AdGuard and Promtail are never restarted or removed. Future
syslog restarts use rc-service --nodeps to avoid touching dependent services.
The first deployment used OpenRC restart without that flag, which also restarted
its dependent crond. Healthcheck PID344 and AdGuard PID339 stayed unchanged.

Before sender rollout, apply observability_syslog_parent and deploy the
RFC3164 UDP Alloy listener with guest host labels through the common redaction
receiver. Verify a dummy API token is absent from the new Loki syslog stream.
Do not retire Promtail until all of its previous sources have equivalent
coverage. AdGuard's separate /var/log/AdGuardHome.log does not automatically
become a syslog source, so BusyBox forwarding alone does not justify retiring
its Promtail.

Actual delivery evidence on 2026-10-13:
- ct110 192.168.70.250 → PVE-a 192.168.1.5 UDP1514: parent socket received
  a harmless 59-byte datagram with the expected source.
- ct201 initially timed out at the parent socket. Bridge capture alone saw
  transit and did not prove delivery. The parent subsequently added the exact
  UDP1514 policy (id 6ace05fc1825b50cb132a9d9), after which actual delivery passed.
- Both direct CT datagrams and the actual BusyBox logger/syslogd path reached
  Loki with host=ct110/ct201, job=syslog and all nine dummy-secret patterns
  redacted. No raw dummy secret remained in those streams.
- The existing /var/log/messages still received the local acceptance markers.
  After verifying both Promtail configs read only /var/log/messages, the
  explicitly gated retirement stopped and removed default boot membership for
  both Promtail services. Their binaries, configs and init scripts remain.
- Harmless UDP probes from non-guest homelab incremented the DROP counter on
  each parent firewall chain. All eight household HTTP checks stayed 200.

Retirement defaults off via observability_alpine_promtail_retire=false. Enabling
it also requires verified forwarding, a fresh inspection showing exactly the
known /var/log/messages input, and only the known default boot membership.
Promtail shutdown uses OpenRC --nodeps with a bounded TERM/10/KILL/5 retry
schedule; its default five-second attempt was insufficient on ct201. Neither
syslog nor application/cron processes restarted during retirement. New fixture
messages still appeared locally and centrally with all nine redactions.

Rollback, using the retained init script and configuration:

    rc-update add promtail default
    rc-service --nodeps promtail start

No UniFi policy is changed by this role. The parent owns network policy records
in docs/observability-network-2026-10-13.md. These remain the exact source and
receiver paths; they are never generalized to subnet-wide logging access.
