# Compatible application updates, 2026-11-03

This batch updates application images while preserving database major versions,
placement, ports, secrets, authentication policies and custom backend builds.
The encrypted Immich change updates only IMMICH_VERSION to v3.3.5.

## Targets

| Stack | Target |
| --- | --- |
| Authentik | 2026.8.5, both server and worker |
| Gatus, gatus-public | 5.38.0 |
| Lubelogger | 1.7.4 |
| NATS | 2.15.2-alpine |
| Lidarr | 3.1.6.5079-ls46 |
| Prowlarr | 2.7.0.5723-ls166 |
| Radarr | 6.4.4.10685-ls325, same app version |
| Sonarr | 4.0.20.3014-ls329, same app version |
| Bazarr | 1.6.2-ls370, same app version |
| Ombi | 4.53.10-ls275, same app version |
| Vaultwarden | 1.37.5 |
| Navidrome | 0.64.2 |
| Miniflux | 2.3.5 |
| Actual | 26.11.0 |
| Grafana, Loki | 13.3.0, 3.7.9 |
| VictoriaMetrics | 1.155.0, single-node |
| CouchDB | 3.5.1 |
| AdGuard sync | 0.9.6 |
| Wyoming OpenAI | 0.8.0 |
| Immich | 3.3.5, retain OpenVINO and PostgreSQL14/VectorChord |

Docker Hub references retain the Forgejo mirror. New images were pulled and
pushed on homelab, and the resulting single-platform mirror digests were read
from Docker. They differ from upstream manifest-list digests by design.

## Deployment requirements

These source changes are prepared, not proof of installation. Deploy after review.
Run stack-lint through Chaldea. Restricted Swarm converges must also use Chaldea
for their lint command, not the default local executable lint. Review each
check-mode result before applying only the named stack.

Take fresh hourly application snapshots before stateful updates. Retain old
images and all backups. Use a cold file archive for Lubelogger, Grafana, CouchDB,
and JetStream as appropriate. Migration failure pauses progression; an older
image alone is not a database rollback.

For Authentik follow docs/authentik-upgrade-2026-10-19.md's recovery packet,
stopped-service dump and isolated PostgreSQL16 restore proof. The running series
is already 2026.8, so only the final patch stage applies. Preserve the custom
Vaultwarden email mapping, embedded outpost, providers, signing keys and MFA.

For Immich the official upgrade guide permits current same-major upgrades after
release-note review. Read 3.2 and 3.3 notes; use 3.2.4 as a conservative health
checkpoint before 3.3.5. Back up the DB before each stage, retain media, and do
not reset facial recognition, enable new models or change OAuth claims/config.
Apply the checkpoint from the deployed Immich directory with
`IMMICH_VERSION=v3.2.4 docker compose up -d immich-server immich-machine-learning`.
Only after its health check passes, converge the reviewed encrypted source pin
3.3.5 through the owning site play. Upstream recommends updating mobile clients
before the server; existing v3 clients remain within this same-major update.
Downgrade against a migrated DB is unsupported.

Miniflux live read-only preflight found zero feed proxies and zero feeds with
localhost/RFC1918 IPv4 URLs. FETCHER_ALLOW_PRIVATE_NETWORKS stays unchanged.
Actual's existing private bridge allowlist is preserved; no bank/provider or
network policy changes are part of this update. Grafana uses GF_ env settings,
not the renamed marketplace ini section. VictoriaMetrics is single-node, so
its cluster-only upgrade note does not apply.

## Preserved and deferred

Forgejo stays 12.0.4 pending a separately validated multi-major migration of the
registry host. OpenViking stays on the qualified patched local backend until its
release gate is satisfied. Komga's existing image metadata does not establish a
safe newer version. Autobrr's CLI/notification changes and Kiwix's non-root/CLI
change need isolated compatibility qualification before moving their pins.
Custom cv-site, dsh, Jocasta, whisper and ov-http builds stay on their owned
release selections. Thelounge, Jellystat and Flaresolverr are app-current.
Nginx, Excalidraw, Caddy, Piper and InfluxDB digest refreshes are deferred because
this batch has no verified newer compatible app version for them. Blackpearl and
OCI OS updates are outside the three-host maintenance scope confirmed by Igor.
The OCI-hosted gatus-oci application is also outside that scope and remains5.37.0.
Librespeed's newer unversioned master build is deferred, preserving its current pin.

## References

- https://docs.immich.app/install/upgrading/
- https://github.com/immich-app/immich/releases/tag/v3.3.5
- https://github.com/goauthentik/authentik/releases/tag/version/2026.8.5
- https://github.com/dani-garcia/vaultwarden/releases/tag/1.37.5
- https://github.com/navidrome/navidrome/releases/tag/v0.64.2
- https://github.com/miniflux/v2/releases/tag/2.3.5
- https://github.com/actualbudget/actual/releases/tag/v26.11.0
- https://github.com/grafana/grafana/releases/tag/v13.3.0
- https://github.com/grafana/loki/releases/tag/v3.7.9
- https://docs.couchdb.org/en/stable/whatsnew/3.5.html

Live receipts and the remaining OS work are tracked in
Jocasta homelab/checkpoints/service-os-update-2026-11-03.md.
