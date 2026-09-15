# Helper maintenance, September 2026

Run `ansible-playbook playbooks/helper-maintenance.yml` from a controller with
SSH access to Homelab, Blackpearl, and proxmox-a (the laptop). This targeted
playbook avoids converging application stacks or overwriting unrelated edits.

- Bulk B2 jobs retain their schedules and destinations. The fixed wrapper
  reads Homelab's protected local application files with sudo rather than
  weakening their permissions. NAS copies remain unprivileged. Each copy has
  a lock, log, last-run status and last-success timestamp under
  `~/.local/state/homelab-backup`. Failures notify and enter the fleet feed.
  Dumps cover live Miniflux PostgreSQL; backup logs and installed node_modules
  are excluded.
- Offsite freshness is checked separately for OCI and phone. The optional
  travel router does not fail the job while powered off. A dedicated bare
  infrastructure mirror refreshes the repository bundle before each delivery.
- Restore verification serves the actual restored `db.sqlite3`, fails on
  SQLite/SQL errors, checks snapshot age, and uses unique scratch containers.
  It never mounts production data into those containers.
- Blackpearl retains nightly live-data rsync and gains a separate copy of
  its current Compose definitions. The disconnected host PIA daemon is disabled.
- The Proxmox whole-VM/whole-host self-heal escalation is retired. It treated
  a missing health monitor as evidence to restart production VMs and eventually
  the hypervisor. The healthcheck service, UPS protection, Authentik watchdog,
  and Zigbee recovery remain. The Zigbee watchdog now handles executable lookup,
  inspection failure, overlap and cooldown explicitly.

The one-time retirement also removed Blackpearl's stale hourly container-restart
cron line, disabled laptop `mic-pwmon` (keeping `mic-detect`), and cleared
completed transient test failures. Disabled units and saved crontabs provide
rollback; application data and diagnostic history are retained.

Healthcheck LXC 110's existing glibc Promtail binary needs Alpine `gcompat`.
OCI's fwupd daemon and library must be from compatible packages.
