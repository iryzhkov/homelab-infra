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
  (default 20,000 chars, cap 60,000, budget 120 s). When `stream_index` is
  omitted, the worker selects the first English (`eng`/`en`) text stream
  (preferring non-forced); if no English text stream exists, it returns
  `image_subtitle` (when an English image stream exists) or `no_english_text`
  (otherwise) with `lines: []` and the full `streams` list without extracting,
  so callers can pass an explicit `stream_index`. Image subtitles (PGS, VobSub,
  DVB) are reported as `image_subtitle` without decoding.

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

`filter_plugins/web_mcp.py` validates both the Go service `config` and the
optional worker `library` block in `web_mcp_endpoint`, rendering the worker
ceiling into `/etc/holonet-playlists/policy.json`:

- Go `config.library.enabled`: explicit boolean (`false` by default; must match
  worker `library.enabled`).
- Go `config.policy.Actions`: must include `"library.read"` and
  `"library.manage"` when `config.library.enabled` is `true` (and omit them when
  `false`).
- Go `config.purpose_grants.library` (required when enabled):
  - `scopes`: `["holonet.library"]`
  - `namespace`: `"managed-library"`
  - `actions`: `["library.read", "library.manage"]` (or `["library.read"]`)
  - `libraries`: `["3", "615", "1132"]`
  - `catalog_users`, `history_users`, `target_users`: `[]`
  - `subjects`: 1–4 unique lowercase 64-hex Authentik `uid` values for the
    authorized M3 host accounts (`chaldea-laptop` and `chaldea-omarchy-pc`),
    independent of the playlist writer subject. Read the exact `uid` values from
    a public Authentik user GET (`/api/v3/core/users/?username=chaldea-laptop`
    and `/api/v3/core/users/?username=chaldea-omarchy-pc`). Use each returned
    user's `uid`, not its numeric `pk`: `hosts.json` and `public_host_users`
    expose the `pk` and cannot supply these subject values.
- Worker `library` block in `web_mcp_endpoint`:
  - `enabled`: explicit boolean, default `false` (must match Go
    `config.library.enabled`).
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
  - `subjects`: rendered into `/etc/holonet-playlists/policy.json` from Go
    `config.purpose_grants.library.subjects` (single source of truth; if also
    supplied on the worker `library` input, any mismatch is refused). The worker
    authorizes `library-*` calls only for subjects in `policy.library.subjects`
    (independent of the writer subject).

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

- Enable (strict order):
  1. **Merge** this revision. Once merged, the closed expected baseline set
     includes `holonet.library` for `laptop`, so every
     `playbooks/authentik-agent-hosts.yml` run without
     `authentik_agent_create_library_mapping=true` refuses until the producer
     has run. The producer run is the mandatory next `authentik-agent-hosts` run
     after merge; no other run (rotation, reenable, revocation, or Akasha) can
     succeed before it completes.
  2. **Producer run**: run the standard `CHECK -> REAL -> REAL -> CHECK`
     sequence of `playbooks/authentik-agent-hosts.yml` with the existing
     protected cohort, baselines, and journal plus
     `-e authentik_agent_create_library_mapping=true`. Every selected host must
     already be enrolled; no lifecycle effects are permitted during the producer
     run. Verify the second REAL and final CHECK report zero changes.
  3. **Endpoint deploy**: read the Authentik `uid` values for `chaldea-laptop`
     and `chaldea-omarchy-pc` via a public user GET (not `pk`), set all policy
     inputs above (`config.library.enabled: true`, `config.policy.Actions` with
     `library.read`/`library.manage`, `config.purpose_grants.library` with those
     host `uid` subjects, and the worker `library` block with `enabled: true`,
     explicit `libraries` mapping, `allowed_categories` including
     `radarr-movies` and `tv-sonarr`, `removal_thresholds`, and `limits`), and
     deploy the HoloNet endpoint to render both the Go config and
     `/etc/holonet-playlists/policy.json`.
  4. **Fleet flag**: enable `webmcp_interactive_holonet_library_enabled` on
     `laptop` and `omarchy-pc` in `chaldea-fleet-config`.
- Rollback: flip Go `config.library.enabled` and worker `library.enabled` to
  `false` together (removing `library.read` and `library.manage` from
  `config.policy.Actions`) and redeploy the endpoint to re-render
  `/etc/holonet-playlists/policy.json`. The worker immediately refuses all
  `library-*` operations with `Library management not enabled by policy`.
  Authentik-side withdrawal of the `holonet.library` mapping expression is a
  separate follow-up.
