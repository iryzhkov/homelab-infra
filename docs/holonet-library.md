# HoloNet Library Management v1

Default-off root worker support for Arr-native library management across Movies
(Emby library `3`, Radarr), TV (Emby library `1132`, Sonarr), and Anime (Emby
library `615`, Sonarr anime root folder `3`).

Normative contract: `tooling/contracts/holonet-library-v1-2026-11-22.md`
(`jocasta:d07e09665f67d1ea7b2fb00eae9b6fe0@1`).

## What it does

`scripts/emby_operator_library.py` adds eight worker operations dispatched over
the existing root operator socket (`scripts/emby_operator_socket.py`):

Read operations (require `library.read`):
- `library-lookup`: search existing titles in the library's Arr root folder and,
  when `term` is given, Arr lookup results for titles not yet added (cap 25).
- `library-detail`: inspect one movie or series with files, media info, and
  best-effort Emby item IDs.
- `library-releases`: Arr interactive search returning up to 50 releases with
  stable handles (budget 180 s).
- `library-queue`: Radarr/Sonarr download queue status and messages.
- `library-subtitles`: list subtitle streams for an Emby item in libraries `3`,
  `615`, or `1132`, and extract one text stream as `[hh:mm:ss] text` lines
  (default 20,000 chars, cap 60,000, budget 120 s). Image subtitles (PGS,
  VobSub, DVB) are reported as `image_subtitle` without decoding.

Manage operations (require `library.manage`, each supporting `mode: "preview"`
and `mode: "apply"` with canonical `plan_sha256` verification):
- `library-add`: add a movie or series using the policy-configured root folder
  and quality profile. Refuses `already_in_library` and `plan_changed`.
- `library-grab`: grab a release via Arr `POST /api/v3/release`. Refuses
  `rejected_release` unless `allow_rejected: true`, `release_expired` when Arr
  no longer caches the release, and `plan_changed`.
- `library-remove`: preview/apply Arr deletion (`deleteFiles=true`,
  `addImportExclusion=false` / `addImportListExclusion=false`) for a title,
  season, or episode list (unmonitoring episodes before deleting episode files).
  Refuses `recycle_bin_unset` when Arr's dynamically read `recycleBin` is empty
  (live Radarr uses `/mnt/shared/holonet-recycle/radarr`; Sonarr uses
  `/mnt/shared/.recycle/sonarr`), `keep_listed` when any resolved Emby ID for
  the title/season/episodes is in `keep_emby_ids`, `file_limit_exceeded` over
  500 files, and `plan_changed`. Matching qBittorrent torrents are deleted
  only when `remove_torrents: true`, category is allowed, every supplied library
  file is deleted, and seeding meets the threshold (ratio >= 1.0 or >= 14 days).

All effects occur through existing Arr, qBittorrent, and Emby HTTP APIs; the
worker unit's `ReadWritePaths` does not change and no new credentials are added.

## Policy inputs

`filter_plugins/web_mcp.py` renders an optional `library` block into
`/etc/holonet-playlists/policy.json`:

- `enabled`: explicit boolean, default `false`.
- `libraries`: explicit mapping for `movies` (`3`, `movie`, `radarr`), `tv`
  (`1132`, `series`, `sonarr`), and `anime` (`615`, `series`, `sonarr`). Every
  entry requires explicit `root_folder_path` and `quality_profile_id` (no
  guessed defaults).
- `allowed_categories`: explicit list of qBittorrent categories eligible for
  torrent removal (for example `["radarr-movies", "tv-sonarr"]`).
- `removal_thresholds`: `min_ratio` (default `1.0`) and `min_seeding_days`
  (default `14`).
- `limits`: `lookup_max_rows` (25), `releases_max_rows` (50),
  `subtitles_default_chars` (20000), `subtitles_max_chars` (60000),
  `remove_max_files` (500).

## Authentik scope and gaming-pc token

- Scope `holonet.library` projects a flat grant (`namespace: managed-library`,
  `libraries: ["3", "615", "1132"]`, `actions: ["library.read", "library.manage"]`)
  onto the existing original writer subject (`chaldea-laptop-holonet-recommendations`).
  Controlled by `holonet_catalog_library_enabled` (default `false`) in
  `playbooks/vars/authentik-holonet-catalog.yml`.
- Separate gaming-pc (`omarchy-pc`) app-password token for the same writer
  account (`chaldea-laptop-holonet-recommendations-omarchy-pc-g1`, same expiry
  policy, delivered via the existing `host.yml` path) is controlled by
  `holonet_catalog_gaming_pc_writer_token_enabled` (default `false`).

## Enable and rollback

- Enable: set `library.enabled: true` in the endpoint's `web_mcp_endpoint`
  inventory input alongside the explicit root folder paths and quality profile
  IDs, and enable `holonet_catalog_library_enabled: true` in Authentik.
- Rollback: set `library.enabled: false` in the endpoint policy input and
  re-render `/etc/holonet-playlists/policy.json`. The worker immediately refuses
  all `library-*` operations with `Library management not enabled by policy`.
