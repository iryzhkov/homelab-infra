# Offsite ingest edge failover

Laptop and OCI use a dedicated HAProxy TCP relay bound only to 127.0.0.1:18443.
Alloy still has one metrics endpoint and one log endpoint. TLS passes through
the relay to Traefik, retaining normal server certificate verification and
encrypted per-host Basic credentials. The relay cannot read request contents.

The primary mesh address is 10.42.0.3; the backup is 10.42.0.10. Certificate-
verified HTTPS health checks require the unauthenticated metrics ingest path
to answer 401. Two failures mark an edge down and close its existing sessions,
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
