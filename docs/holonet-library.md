# HoloNet Library Management v1

Default-off root worker support for Arr-native library management across Movies
(Emby library `3`, Radarr), TV (Emby library `1132`, Sonarr), and Anime (Emby
library `615`, Sonarr anime root folder `3`).

Normative contract: `tooling/contracts/holonet-library-v1-2026-11-22.md`
(`jocasta:d07e09665f67d1ea7b2fb00eae9b6fe0@1`).

## What it does

`scripts/emby_operator_library.py` adds twelve worker operations dispatched over
the existing root operator socket (`scripts/emby_operator_socket.py`):

Read operations (require `library.read`):
- `library-lookup`: search existing titles in the library's Arr root folder and,
  when `term` is given, Arr lookup results for titles not yet added (cap 25).
- `library-detail`: inspect one movie or series with files, media info, and
  best-effort Emby item IDs.
- `library-releases`: Arr interactive search returning up to 50 releases with
  stable handles (budget 180 s).
- `library-queue`: Radarr/Sonarr download queue status and messages. Sonarr
  records sharing the same `downloadId` (e.g. season packs) are grouped into one
  entry carrying `series_title`, `download_id`, sorted `seasons`,
  `episode_count`, and bounded `episode_ids` (cap 100 items, 100 episode IDs,
  20 deduplicated status messages per entry); Radarr records are ungrouped and
  also expose `download_id`.
- `library-subtitles`: list subtitle streams for an Emby item in libraries `3`,
  `615`, or `1132`, and extract one text stream as `[hh:mm:ss] text` lines
  (default 20,000 chars, cap 60,000, budget 120 s). When `stream_index` is
  omitted, the worker selects the first English (`eng`/`en`) text stream
  (preferring non-forced); if no English text stream exists, it returns
  `image_subtitle` (when an English image stream exists) or `no_english_text`
  (otherwise) with `lines: []` and the full `streams` list without extracting,
  so callers can pass an explicit `stream_index`. Image subtitles (PGS, VobSub,
  DVB) are reported as `image_subtitle` without decoding.
- `library-recommendations`: read-only recommendations from configured mdblist
  lists (`https://api.mdblist.com`) and Radarr TMDb discovery recommendations
  (`/api/v3/importlist/movie?includeRecommendations=true&includeTrending=false&includePopular=false`),
  marked with live Radarr/Sonarr library membership and exclusions, ready to
  pass into `library-add` (`mode: "preview"`) with `monitor` and `search_now`.
  Defaults: limit 20, include_in_library false, include_excluded false.
  Excluded rows appear only with include_excluded true and remain marked.
  Ranking and min_score use a 0–100 score: mdblist score, else IMDb rating
  times 10, else TMDb rating times 10. mdblist rank breaks score ties.
  Language is an ISO 639-1 code or null. Budget 60 s, including lock wait.
  Non-429 failures also serve stale cache with a warning, or refuse without cache. Reports a warning
  for any Radarr (`enabled && enableAuto`) or Sonarr (`enableAutomaticAdd`)
  import list that still has automatic add enabled. Refuses
  `recommendations_disabled` when policy omits `library.recommendations`, and
  `quota_exhausted` when the daily mdblist ceiling or HTTP 429 is reached with
  no cache available.
- `library-prune-status` (HoloNet tool `library_prune_status`): library size
  against the prune target (per library), pending marks with title, size, last
  activity and `remove_after`, the projected size once they are removed, and the
  last run summary. Refuses `prune_disabled` without `library.prune` and
  `prune_state_invalid` when the state files fail validation. Budget 60 s.
- `library-watch-history` (HoloNet tool `library_watch_history`): titles played
  in the last `days` (default 90, 1–365) across all Emby users, with play count,
  user names, last played time, library, Arr ID, TMDb/TVDb IDs and Arr genres,
  plus every current Emby favourite (any user) mapped to its Arr title. Bounded
  by `limit` (default 100, max 200) and 200 favourites; `history` reports the
  coverage of the playback source. Budget 90 s.

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
  only when `remove_torrents: true`, category is allowed, exclusive ownership is
  proven, and seeding meets the threshold (ratio >= 1.0 or >= 14 days).
  Exclusive ownership is proven first by complete Radarr+Sonarr history
  (`ownership_proof: "arr_history"`), and if history returns
  `ownership_unverified`, by bounded `O_PATH`/`O_NOFOLLOW` hardlink evidence
  (`ownership_proof: "hardlink"`): every torrent file under the configured
  `payload_prefixes` must either share `(st_dev, st_ino)` with a deleted library
  file and have `st_nlink == 2` (at least one must match), or have
  `st_nlink == 1`; any other case keeps the torrent with a specific reason
  (`hardlink_extra_links`, `hardlink_linked_elsewhere`, `hardlink_no_match`,
  `hardlink_missing`, `hardlink_unreadable`, `hardlink_symlink`,
  `hardlink_outside_download_root`, `hardlink_path_unmapped`, etc.).
- `library-import`: preview/apply Arr manual import (`GET /api/v3/manualimport?downloadId=<id>`,
  with `filterExistingFiles=false` on Sonarr; never `seriesId` or `folder` modes)
  followed by `POST /api/v3/command` (`ManualImport`, `importMode: "auto"`),
  allowing replacement of existing files even when Arr rejects automatic import
  (e.g. swapping a single-audio 2160p file for dual-audio 1080p). Accepts
  `library`, `arr_id`, `download_id`, optional `season` or `episode_arr_ids`
  restriction (for `tv`/`anime`), and optional `wait: true` on apply to poll the
  command briefly within the call budget. Refuses `recycle_bin_unset` when Arr's
  `recycleBin` is empty, `no_import_candidates` when no candidates match,
  `target_mismatch` unless every candidate in the download maps to the requested
  `arr_id`, `file_limit_exceeded` over 200 files, and `plan_changed`.
  Movies require exactly one candidate, and series candidates must not overlap
  episode IDs. Only upgrade-class rejections may be overridden; sample, unparsed
  and unknown rejections refuse the import. Candidate paths must be outside the
  title's library folder and within configured download payload roots.
  Preview and apply require finished Arr queue records or qBittorrent progress
  of 1; the completion evidence is bound into the plan digest.
  Hardlink removal pins library inodes across torrent checks and rechecks library
  paths and link counts. Immediately before torrent deletion, every proven
  payload path must still resolve to the same inode with exactly one link after
  Arr deletion; changes keep the torrent with `hardlink_changed_before_delete`.

All effects occur through existing Arr, qBittorrent, and Emby HTTP APIs; when
`library.recommendations` is configured, the worker unit adds
`ReadWritePaths=/var/lib/holonet-operator/recommendations` (0700 `root:root`)
for the root-only normalized cache (`cache.json`, 0600, 6-hour TTL, atomic
write) and per-UTC-day mdblist call counter (`quota.json`, 0600, hard ceiling
100 calls/day maximum, counted durably before each attempted request). The mdblist API key is read from a root-only file
(`/etc/holonet/mdblist.key` by default, 0400 `root:root`, raw key text) using
directory-FD `O_NOFOLLOW` custody, is never logged, and is sent only as the
`apikey` query parameter over HTTPS to `api.mdblist.com` (no redirects, 4 MiB
response cap). Outbound HTTPS is already permitted by the worker unit
(`RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6`, no `IPAddressDeny`).

## Library prune

`scripts/emby_operator_library_prune.py` keeps the managed library at or below
`library.prune.target_bytes` (default 20 TiB) by removing old, unwatched,
non-favourite titles. The root unit `holonet-library-prune.service` runs it
weekly from `holonet-library-prune.timer` (Sunday 04:30 with up to 15 minutes of
random delay, `Persistent=true`). It is never reachable from the MCP service;
the two HoloNet tools above only read its state.

Library size is the sum of Radarr movie and Sonarr series `sizeOnDisk` inside
the configured `movies`, `tv` and `anime` root folders. Candidates are whole
movies and whole series. A title is eligible only when all of these hold:

- no Emby user has it as a favourite (movie, series, or any season or episode
  of the series); the tool descriptions tell agents that favouriting protects;
- its Arr quality profile is not `Favorites - UHD + Original`;
- no active HoloNet keep resolves to any of its Emby items (the same keep list
  `library-remove` honours, read from the Go janitor journal);
- it is not in the Radarr or Sonarr download queue;
- the Arr added date and the newest file `dateAdded` are both older than
  `min_idle_days` (180);
- nobody played it within `min_idle_days`.

Candidates are ordered by last activity (the later of the Arr added date and
the last play), oldest first, then larger first. Each run first applies, then
marks. Apply re-checks every pending mark against the facts read by this run
and removes the marks whose `remove_after` has passed (marked at least
`grace_days`, default 7, earlier) while the library is still over the target,
within `max_titles_per_run` (40) and `max_bytes_per_run` (2 TiB). Mark then adds
candidates until the projected size (current size minus pending marks) is at
or below the target, within the same caps; a title larger than the remaining
byte budget is skipped. Marks that became ineligible (watched, favourited,
kept, queued, gone) are released, and all marks are released once the library
is under the target. `plan` mode (`emby_operator_library_prune.py plan`, as
root) prints the same evaluation without writing state.

Each removal is one `library-remove` preview/apply of the whole title with the
current keep list, so the recycle bin requirement, keep refusal, file limit
and hardlink ownership proof all apply unchanged. Torrents that library-remove
would delete are additionally checked against the SeedPool policy
(`scripts/seedpool_policy.py`): SeedPool torrents need the whole-batch
retirement plan to pass (seeded at least 10 days and the 20 TiB active floor
still held afterwards); other trackers need the same 10-day minimum; unknown
trackers or an unknown snapshot block. When that check fails, the title is
removed with `remove_torrents: false` and the torrent keeps seeding; the run
summary records `bytes_held_by_torrents`. Removed library files go to the Arr
recycle bin, which empties after 30 days ([media-recovery.md](media-recovery.md)),
so disk space returns only after that and only where no torrent still links
the data.

Fail-closed reads: any Radarr, Sonarr, Emby, playback history, keep list or
qBittorrent read error aborts the run before its first removal and leaves the
marks unchanged. A backend error during apply stops further removals and
marking; an uncertain post-dispatch result (`partial`/`uncertain` from
library-remove) stops the run.

Playback source: Emby's activity log (`/System/ActivityLog/Entries`), read
through the worker's existing admin session. Per-user `UserData` was checked on
live homelab on 2026-11-22 and carries no `LastPlayedDate` (and `PlayCount` 0)
even for played items, so it cannot date a play. Jellystat history starts on
2026-07-16, shorter than the 180-day window, and would need a new credential.
The activity log names item and user for every `playback.start` since
2026-08-29; older entries (`VideoPlayback`) carry no item. The prune therefore
treats the window as complete only when the log reaches back past the window
start and every playback start inside it is attributable. Until then (about
February 2027 with the default window), a title is eligible only if no Emby
user has any play state on it (played or resumable) and no attributable play
falls in the window; anything played at an unknown time stays
`history_incomplete`. Plays of items that no longer resolve (deleted or
re-imported files) are counted as unmapped: a re-import also resets the newest
file date, so that cannot unprotect a title.

Keep list: systemd passes a read-only snapshot of
`/var/lib/holonet/janitor/state.json` to the unit with
`LoadCredential=holonet-janitor-state:...`; the unit has no capabilities, and a
missing journal fails the start. Active keeps for this server and the movies,
tv and anime Emby libraries are used; a malformed journal aborts the run.

State (`/var/lib/holonet-operator/prune`, 0700 `root:root`, the only writable
path of the unit): `marks.json` (pending marks with `marked_at` and
`remove_after`), `last_run.json` (last run summary) and `.lock` (non-blocking
exclusive run lock; a second run reports `busy`). Files are 0600 and replaced
atomically (temporary file, `fsync`, `rename`, directory `fsync`). Each run
writes one JSON line `{"event":"holonet_library_prune",...}` with counts only
(no titles) to the journal for Loki, next to the media observability
`media_collection` events.

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
  - `recommendations` (optional; omitted disables `library-recommendations`):
    - `mdblist_key_file`: normalized absolute path, default
      `/etc/holonet/mdblist.key` (0400 `root:root`). The deployment does not
      install the key; it was placed on homelab by hand, and the recovery copy is
      `secrets/mdblist.sops.yaml` (field `mdblist_api_key`).
    - `lists`: 1–16 `{slug, kind}` mappings (`kind` in `movie|series|anime`),
      matched by slug against `/lists/user` (e.g., `top-movies -> movie`,
      `top-anime-movies -> anime`, `top-tv -> series`, `top-anime -> anime`).
    - `daily_ceiling`: 1–100, default `100` mdblist requests per UTC day. At
      the ceiling or on HTTP 429, stale cache is served with `cache.stale=true`
      and a warning, or `quota_exhausted` is refused when no cache exists.
    - `cache_ttl_seconds`: 21600–86400, default `21600` (6 hours).
    - `min_refresh_age_seconds`: 3600–21600, default `3600` (1 hour; `refresh: true`
      bypasses the cache only once `age_seconds >= min_refresh_age_seconds`).
    - `state_dir`: fixed `/var/lib/holonet-operator/recommendations` (0700
      `root:root`).
  - `prune` (optional; omitted disables the prune and its status tool):
    `enabled` (default `false`; the timer runs only when `true` and library
    management is enabled), `target_bytes` (default `21990232555520`, 20 TiB;
    1 TiB–1 PiB), `min_idle_days` (180; 30–3650), `grace_days` (7; 1–90),
    `max_bytes_per_run` (`2199023255552`, 2 TiB; at most `target_bytes`),
    `max_titles_per_run` (40; 1–200). `state_dir`
    (`/var/lib/holonet-operator/prune`) and `keep_journal`
    (`/var/lib/holonet/janitor/state.json`) are fixed. Enabling the prune
    requires the Go janitor config, whose journal holds the keep list.
  - `torrents`: when `library.enabled` is `true`, the qBittorrent descriptor
    must be `canonical_torrent_descriptor(library=True)` (`radarr-movies` and
    `tv-sonarr`, both payload roots); the Movies-only descriptor is refused.
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
- Prune: apply `playbooks/media-recovery.yml` first so both recycle bins expire
  after 30 days. Deploy with `library.prune.enabled: false`, then run
  `sudo python3 -E -s -B /opt/holonet-playlists/releases/<release>/emby_operator_library_prune.py plan`
  on homelab and review it. Set `enabled: true` and redeploy to start the timer.
  To stop it, set `enabled: false` (or remove `prune`) and redeploy: the timer
  is stopped and disabled, and marks and the last run summary stay in place.
- Rollback: flip Go `config.library.enabled` and worker `library.enabled` to
  `false` together (removing `library.read` and `library.manage` from
  `config.policy.Actions`) and redeploy the endpoint to re-render
  `/etc/holonet-playlists/policy.json`. The worker immediately refuses all
  `library-*` operations with `Library management not enabled by policy`.
  Authentik-side withdrawal of the `holonet.library` mapping expression is a
  separate follow-up.
