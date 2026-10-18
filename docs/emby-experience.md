# Native Emby home and playback configuration

Apply only on homelab, to exactly named existing profiles Igor and TV:

```sh
ansible-playbook playbooks/emby-experience.yml --limit homelab --check
ansible-playbook playbooks/emby-experience.yml --limit homelab
```

The playbook installs no program, service, plugin or HoloNet component. It uses the existing protected Emby connection and the APIs used by the installed Emby 4.10 web client. All credentials and full preference payloads remain behind no_log. User preference snapshots may contain private settings; root-only originals are saved once in /var/backups/emby-experience, mode0700 directory and0600 files. Completed snapshots are published atomically without overwriting originals. Every existing snapshot is parsed and shape-checked before any API mutation; a corrupt snapshot stops the run. Never publish them.

The JSON file declares six ordered rows: unwatched Movie Night Spotlight, Continue Watching including next episodes, library artwork cards, unexplored anime, favorites and native movie collections. Latest Anime/Movies/TV rows are removed. The library cards use existing widescreen library artwork; a null ViewType selects the native Cards layout and may be omitted in API responses. Rows can be empty when there are no matching items; no playlist, media, watched-state or favorite changes are performed. All previous home rows for the two selected profiles are intentionally replaced, after new rows round-trip successfully. The installed server allocates new row IDs and rejects unknown supplied IDs. Subsequent runs identify each row by section type and its unique configured title, preserve its server ID, and refuse ambiguous matches. Later runs enforce this declared layout.

Playback adds a five-second resume rewind and hides watched items in suggestions and similar-title results. Autoplay and ShowButton intro mode are retained. Language choices, subtitle mode, remembered tracks, user policies and all other users remain unchanged.

Movies ImportCollections is enabled, with every other library option preserved. Collection metadata can populate during subsequent normal refreshes; no full library refresh or analysis job is forced. Existing playlists are not generated and HoloNet recommendations remain deferred.

Existing TheIntroDB is configured for intros/recaps/credits with marker protection and bounded daily lookup. Native fingerprint detection stays disabled to avoid overlapping generation. Chapter extraction is already enabled and scheduled. Read-only sample on 2026-10-17: 30 recent episodes, 19 intro starts/ends, 17 credit starts and259 chapter images. This is sample evidence, not library-wide coverage or proof every client exposes every skip feature.

Acceptance: preview performs no mutation; apply/readback validates home row order and owned values, preserves unrelated user/library settings and other profiles; repeat apply changes0. Native section item endpoints must return playable matching content for populated rows. UI rendering varies by client; compatible apps may need to refresh/reopen Home.

Rollback uses the protected snapshot through Emby configuration/HomeSections APIs, removing only managed row IDs and restoring saved row contents/order. A snapshot from the legacy default layout may contain duplicate IDs; restore its visible semantics instead of assuming IDs are unique. No generic deletion endpoint or media deletion belongs in this procedure.
