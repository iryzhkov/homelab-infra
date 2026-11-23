# Media recovery configuration

This configuration-only increment uses existing Sonarr, Radarr, NAS mounts and Grafana. It installs no agent service, receiver, database or executable.

Run `ansible-playbook playbooks/media-recovery.yml --check --limit homelab`, then apply and repeat the same command without `--check`. The repeat must change zero resources.

The playbook requires the real NFS mount and creates separate application-owned paths outside scanned library roots:

- Sonarr: `/mnt/shared/.recycle/sonarr`
- Radarr: `/mnt/shared/holonet-recycle/radarr` (the playbook owns this live path; the HoloNet library worker reads Arr's configured recycle bin path dynamically)

Both Swarm stacks explicitly run with PUID 1002 and PGID 988, matching NAS media ownership. The LinuxServer default 911:911 cannot traverse the share. Recovery directories are created by the existing SSH user through the NAS mapping, which maps ownership to 977:988. Mode 0770 gives the actual application group 988 access, and the playbook verifies that group plus read/write/traversal from a shell as abc. It does not attempt NFS chown or change the export. Apply the two stack identity settings before the recovery playbook. Both apps use 30 cleanup days: removed and replaced files stay recoverable from the recycle bin for 30 days, then Arr deletes them. Igor approved automatic library pruning on 2026-11-22 with this 30-day recoverability as the retention decision; the bins share the NFS volume with the library, so the earlier zero days (keep forever) meant removals freed nothing. Restore a file within 30 days by moving it back from the recycle path and rescanning the title in Arr. Existing NAS free-space monitoring remains necessary. Configuration rollback snapshots are root-only under `/var/backups/media-recovery/`. Restore their settings through supported APIs if needed; do not delete retained files as a rollback side effect.

Only recycle path and cleanup days are owned. API readback compares every other media-management field exactly. Settings outside this resource, profiles, import lists, torrents and media files are untouched. A container-user write-access check verifies the mounted directory. A later isolated replacement/recovery exercise is still required before claiming end-to-end recovery or implementing repair.

Run `ansible-playbook playbooks/media-dashboard.yml --check --limit homelab` to preview only the existing media dashboard. Apply and repeat to verify zero drift. This does not redeploy the collector or favorite worker, retire dashboards, change Grafana home preferences or add notification channels. It adds panels using existing metrics for last worker result and queue items with unknown age. Existing Loki ingestion and selectors are reused.

The favorite worker currently has only a global enable switch and no anime-only configuration. An anime-only pause is deferred with the code change; this increment does not disable movie/non-anime upgrades or edit the durable queue.

Evidence checks must distinguish API reachability from worker success and last data collection from playback coverage. Jellystat ActivityMonitor freshness remains unknown through its supported API. Queue warnings are findings, not authorization to import or fail a release. Indexer warnings do not authorize searches or grabs.
