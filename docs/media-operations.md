# Media operations

The SeedPool retention floor is **20 TiB (21,990,232,555,520 bytes)**. Ten days (864,000 seconds) of actual seeding is the minimum before considering retirement; it is not an automatic stopping deadline. Keep indefinite seeding. This change does not stop torrents, remove files or download replacements.

Only complete, fully selected SeedPool torrents in active uploading/stalled-upload/forced-upload states count toward the pool. Downloads, paused/queued/checking/error states and partially selected torrents do not count. Unknown or malformed data blocks retirement planning. The guard checks the entire requested batch against the live pool; proposed replacements receive no capacity credit until they are complete and actively seeding.

The current pool is approximately 19.6 TiB, below the chosen floor. There is no retirement headroom. Removing a library hardlink also may not release physical disk space while the torrent still links the same data. The earlier nonfavorite 1080p replacement proposals therefore remain proposals, not realized savings.

## Read-only retirement planning

After deploying media observability, run on homelab:

```sh
sudo /usr/local/lib/media-observability/seedpool-policy-check.py
sudo /usr/local/lib/media-observability/seedpool-policy-check.py --retire-file /root/retirement-hashes.json
```

The optional private JSON file is an array of torrent hashes. The second command exits 2 when blocked, 1 on collection/configuration failure and 0 when the proposed batch passes. It only evaluates policy; it has no torrent stop or delete implementation and is not an enforcement hook inside qBittorrent or Janitorr. Any future deletion automation must call the guard immediately before acting and handle concurrent changes safely.

## Scope

Monitoring uses the existing Alloy textfile, VictoriaMetrics, Grafana and Loki paths. Dashboard and logs only: no new alerts, contact points or Discord messages. Missing telemetry must remain visibly unknown rather than be presented as healthy. Seeded pool capacity and NAS free disk space are different measurements. Jellystat synchronization freshness is available; an activity-monitor heartbeat is not exposed by its current REST API and cannot be inferred from the absence of playback.

Application connections are managed by `playbooks/media-connections.yml`; see [media-connections.md](media-connections.md). Existing automatic lists, tags, categories, quality/language policy, subtitles and request approvals are preserved. Vaultwarden/OpenSubtitles credential work remains deferred.

Anime search details and the manual pack-search command are documented with the fast-search helper. Native Sonarr season searches retain their existing coverage. No workaround that silently drops episode or season-pack search coverage is enabled.

Run targeted verification:

```sh
python3 -B -m unittest discover -s scripts -p test_seedpool_policy.py
python3 -B scripts/test-media-connections.py
```
