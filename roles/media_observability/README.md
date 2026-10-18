# Read-only media observability

Run `ansible-playbook playbooks/media-observability.yml --check` before applying.
This installs a minute timer, a read-only collector and a private dashboard in the
existing Grafana folder. It creates no alerts, contact points, policies, outbound
messages, torrent actions, or media mutations.

The collector reads Sonarr/Radarr/Lidarr health and import queues, Prowlarr health
and indexer backoffs, Bazarr health/missing subtitle counts/provider state, Emby
public status, Ombi status, Jellystat scheduler configuration and successful sync
logs, favorite upgrade phase/heartbeat state, and qBittorrent's torrent inventory.
Private app credentials stay in their current protected configurations. The
existing qBittorrent SOPS credentials are copied into a root-only collector config.

Metrics are atomically replaced at /var/lib/observability-textfile/media.prom,
read by the existing Alloy Unix exporter and remote-written to VictoriaMetrics.
Per-source failures publish source_up=0, preserve the last successful collection
timestamp, and omit incomplete source values. They do not turn failures into
healthy zeroes or retain stale queue counts. Queue pages above 10,000 items are
explicitly incomplete and fail that source. The collector runs at most four API
sources concurrently, with bounded request timeouts.

The collector emits credential-free JSON summaries to its systemd journal,
already collected through Alloy's common redaction receiver into Loki.
Application logs already use Docker collection. The dashboard links both.

SeedPool metrics use scripts/seedpool_policy.py, shared with the parent's
read-only policy/retirement planner. The floor is 20 TiB (21990232555520 bytes);
the minimum seeding age is ten days (864000 seconds). Active seeded bytes are
not NAS free space. Missing/unclassified torrent metadata remains visible.

Favorite instrumentation writes a separate private heartbeat.json alongside the
unchanged version-1 durable queue. It records attempts, successful runs and actual
phase progress. A terminated run can leave running=1; compare its age with the
existing 12-minute systemd deadline. This role updates an already installed worker
without starting/restarting it or dispatching a search.

Jellystat's API exposes task schedules and only the last 50 task logs. Missing
successful sync history is unknown. Its ActivityMonitor emits Socket.IO events
and does not expose a supported authenticated REST poll heartbeat. Thus the
dashboard explicitly reports activity_monitor_freshness_known=0; last playback
is never used as a liveness proxy.

Use `collector.py --config PATH --stdout` for read-only output validation without
writing metrics or state. Unit tests: `python3 -B scripts/test_media_observability.py`.
After deployment, verify source_up and collection age, execute dashboard queries
against VictoriaMetrics, inspect journal JSON and confirm Grafana alert/contact
resources remain unchanged. This branch has not deployed itself.
