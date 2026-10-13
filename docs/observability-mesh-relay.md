# Offsite ingest edge failover

Laptop and OCI use a dedicated HAProxy TCP relay bound only to 127.0.0.1:18443.
Alloy still has one metrics endpoint and one log endpoint. TLS passes through
the relay to Traefik, retaining normal server certificate verification and
encrypted per-host Basic credentials. The relay cannot read request contents.

The primary mesh address is 10.42.0.3; the backup is 10.42.0.10. Certificate-
verified HTTPS health checks require the unauthenticated metrics ingest path
to answer 401. This proves edge/authentication reachability, not storage backend
writability. Two failures mark an edge down and close its existing sessions,
allowing Alloy to reconnect to the backup rather than hanging on a blackhole.
Checks run every five seconds, with three-second timeouts and faster checks
during transitions. Both unavailable means failed writes remain subject to
Alloy's native queues/retry limits; this does not create another copy of data.

Two DNS addresses alone failed the primary blackhole acceptance drill: TCP
sessions remained on the primary and remote write timed out. Multiple Alloy
endpoint blocks send to each destination rather than implementing failover.

The relay is a separate unprivileged service with no capabilities, a read-only
filesystem, a 64 MiB memory limit and a 10% CPU quota. Its config, unit and
collector routing are managed by Ansible and survive host rebuilds.

## Acceptance on 2026-10-13

Both laptop and OCI deployed successfully; a subsequent Ansible check reported
changed=0 and failed=0 on both. HAProxy configuration validates on both hosts,
both collectors are ready, and both relay listeners bind only to loopback.
OCI's relay used about 2.1 MiB; laptop's used about 20 MiB, within the 64 MiB
limit. No kernel OOM events were observed during deployment and drills.

The fault drills changed only laptop's relay cgroup through uniquely named
runtime systemd drop-ins. No edge, host or household application was stopped.

| Scoped fault or check | Observed result |
| --- | --- |
| Primary mesh address denied | Primary marked DOWN; two secondary connections; a host metric sample 41.156 seconds newer than the failure and owned journal marker arrived |
| Secondary mesh address denied | Primary connection remained usable; verified TLS unauthenticated POST answered 401 |
| Both mesh addresses denied for 60 seconds, then restored | Both marked DOWN; historical sample generated during the outage arrived after recovery; queued journal marker arrived with dummy token redacted |
| Wrong TLS hostname / unrelated test CA | Both rejected with certificate verification error (curl exit 60) |
| Query and snapshot paths through relay | Both 404; unauthenticated write 401 |
| LAN access to relay | Unreachable; kernel listener shows 127.0.0.1:18443 only |

All owned runtime deny files were removed, daemon configuration reloaded, and
the relay restored. Relay and Alloy remained active afterward, with empty
IPAddressDeny and no unsupported firewall warnings. Temporary negative TLS
fixtures were removed. The fault matrix ran on laptop; OCI received the same
checked relay configuration and steady-state runtime verification.

This provides automatic edge transport failover, not zero interruption or
unlimited offline retention. Native queue/backoff limits still apply, and Loki
delivery can repeat entries after retries. The 401 health check does not prove
that the shared metrics/log stores are writable; storage recovery and movement
are separate acceptance gates.
