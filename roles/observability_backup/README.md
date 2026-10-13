# Native metrics recovery

This role schedules a native VictoriaMetrics backup on both workers. Only the
worker whose existing /srv/swarm/observability/.swarm-owner names it and whose
VM task is running creates a backup. Missing ownership with a running task fails;
a non-owner and a deliberately scaled-to-zero service skip. It never claims data.

The script calls the authenticated snapshot API inside the running VM and reads
its mounted secret there. The credential is never copied into host configuration,
passed to the host command line, or printed. Official vmbackup handles the native
snapshot layout; snapshot deletion runs through the API even on backup failure.
Helper images match v1.154.0 and are pinned to linux/amd64 digests.

Backups live outside VM storage in pool/snapshots/observability/victoriametrics.
Six complete generations are retained. The job refuses sources larger than 20 GiB
and requires source size plus 5 GiB of spare space before copying. I/O is capped
at 10 MiB/s with two native helper workers. These are configurable policy limits.

The existing restricted swarm-sync key mirrors only the reserved peer subtree
observability/snapshots/victoriametrics/. It does not refresh the full-stack move
freshness stamp. Existing offsite upload can include these directories once the
observability stack is in its declared snapshot list; this role does not claim
offsite publication. A failed peer copy fails the job while preserving its local
complete backup. Review journalctl -u observability-vm-backup.service for results.

Run a first backup and isolated restore on the active owner:

    sudo /usr/local/sbin/observability-vm-backup backup
    sudo /usr/local/sbin/observability-vm-backup restore-check

The backup saves a nonempty historical query (default: up at now minus two minutes)
and exact series labels, timestamps and values. Native vmrestore restores into a
fresh scratch directory. A temporary VM container, without networks, published
ports, secrets or live storage mounts, must return the exact saved vector.
It uses the backed-up immutable VM image and is removed together with scratch
storage afterwards. Helpers are forcibly removed on timeouts before cleaning up
snapshot references or scratch. If removal cannot be confirmed, the job fails and
preserves those files for operator recovery. The running service is never stopped
or modified.

A peer recovery copy can be checked without switching service ownership:

    sudo /usr/local/sbin/observability-vm-backup restore-check --source /path/to/peer/observability/snapshots/victoriametrics/GENERATION

Pre-pull the saved VM image on the peer and make sure its scratch pool has enough
space. The default age gate is two hours. The proof query must select real stored
metrics; an expression synthesizing a vector cannot demonstrate data recovery.

Official references:
- https://docs.victoriametrics.com/victoriametrics/single-server-victoriametrics/#how-to-work-with-snapshots
- https://docs.victoriametrics.com/victoriametrics/vmbackup/
- https://docs.victoriametrics.com/victoriametrics/vmrestore/
