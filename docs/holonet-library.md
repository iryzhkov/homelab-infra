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

## Authentik host grant and scope mapping

- In the closed M3 host contract (`module_utils/agent_host_contract.py`), the
  exact scope tuple `["holonet.library"]` is granted to `chaldea-laptop` and
  `chaldea-omarchy-pc` only, emitting `holonet_server`
  `3c86e6d7ff6c4a3296522cdf03d22bb0`, `holonet_actions`
  `["library.read", "library.manage"]`, `holonet_libraries`
  `["3", "615", "1132"]`, `holonet_namespace` `"managed-library"`, and empty
  `holonet_catalog_users`, `holonet_history_users`, and `holonet_target_users`.
  `omarchy-pc` gains HoloNet resource membership for this purpose only (no
  catalog, writer, or janitor grants). Mixed or foreign-purpose scope requests
  emit no claims.
- Do not run `playbooks/authentik-holonet-catalog.yml`. The sole issuer writer
  is `playbooks/authentik-agent-hosts.yml` (via the `authentik-signer`
  enrollment wrapper) with the opt-in producer flag
  `authentik_agent_create_library_mapping: true` (default `false` in
  `playbooks/vars/authentik-agent-hosts.yml`), which creates the deny-only
  `M3 host HoloNet library` (`holonet.library`) scope mapping, attaches it to
  the `chaldea-holonet` provider, upgrades `chaldea-laptop` and
  `chaldea-omarchy-pc`, and renders the qualified host expressions. See
  [authentik-agent-hosts.md](authentik-agent-hosts.md) for preflight and
  `hosts.json` pending-recovery rules.

## Enable and rollback

- Enable:
  1. Run the standard CHECK -> REAL -> REAL -> CHECK sequence of
     `playbooks/authentik-agent-hosts.yml` with the existing protected cohort,
     baselines, and journal plus `-e authentik_agent_create_library_mapping=true`.
     Verify the second REAL and final CHECK report zero changes.
  2. Set `library.enabled: true` in the endpoint's `web_mcp_endpoint` inventory
     input alongside the explicit root folder paths and quality profile IDs, and
     re-render `/etc/holonet-playlists/policy.json`.
- Rollback: set `library.enabled: false` in the endpoint policy input and
  re-render `/etc/holonet-playlists/policy.json`. The worker immediately refuses
  all `library-*` operations with `Library management not enabled by policy`.
  Authentik-side withdrawal of the `holonet.library` mapping expression is a
  separate follow-up.
