# Media search scope

Run from the repository root:

```sh
ansible-playbook playbooks/media-search-scope.yml --check
ansible-playbook playbooks/media-search-scope.yml
```

The playbook runs on homelab as root and reads API keys directly from each
service's config.xml under /srv/swarm. Keys and API payloads are never printed.
Check mode performs GET requests only. Apply saves full original applications,
indexers, auto-tagging rules, tags, and library records to a root-only 0600 JSON
snapshot under /var/backups/media-search-scope before the first mutation.

Sonarr/Radarr application sync excludes adult categories 6000–6999 while leaving
the tracker enabled in Prowlarr. Nyaa gets the media-search-animation tag in each
target app. Native add-only auto-tagging rules cover Animation; Sonarr also covers
anime series type and anime root folders. Existing matching titles receive that
tag with the editor's add operation, preserving other tags and media properties.
Animation includes Western animation, preserving the existing mixed anime folder.
No quality profiles, monitored flags, files, downloads or import-list settings change.

After apply, a forced Prowlarr full sync verifies that Nyaa's user tag survives and
the adult tracker disappears from both target apps. Subsequent runs are idempotent.
If apply fails partway, use the printed snapshot path (or newest snapshot) to review
and restore the changed API objects; snapshots contain credentials and must stay
root-only. Restore target indexer objects after restoring Prowlarr applications and
forcing sync. Remove only the managed tags/rules created by this change, and restore
the original library tag arrays; never restore whole library records over newer state.

Implementation references:
- [Prowlarr sync command](https://github.com/Prowlarr/Prowlarr/blob/develop/src/NzbDrone.Core/Applications/ApplicationIndexerSyncCommand.cs)
- [Sonarr auto-tagging](https://github.com/Sonarr/Sonarr/blob/develop/src/NzbDrone.Core/AutoTagging/AutoTaggingService.cs)
- [Radarr animation matching](https://github.com/Radarr/Radarr/blob/develop/src/NzbDrone.Core/AutoTagging/Specifications/GenreSpecification.cs)

## Initial validation, 2026-10-14

Ansible syntax/check/apply and a second apply passed; the second apply made zero
changes. A forced Prowlarr sync preserved Nyaa's tag restriction. Both apps stayed
healthy, existing tags remained, and paths/monitoring/quality settings matched the
protected pre-change snapshot. The adult tracker remains enabled in Prowlarr.
Native tagging covered 123 series and 181 animation movies.

Read-only searches through the actual Sonarr/Radarr release endpoints returned:

| Search | Before | After | Results before/after | Requests before/after |
|---|---:|---:|---:|---:|
| Severance S01E01 | 10.243 s | 8.274 s | 0 / 0 | 11 / 5 |
| Severance season 1 | 12.462 s | 2.639 s | 30 / 30 | 7 / 1 |
| Interstellar | 2.455 s | 2.837 s | 39 / 39 | 3 / 1 |
| DAN DA DAN S01E01 | 19.869 s | 18.593 s | 294 / 294 | 17 / 15 |

These are single sequential samples, not controlled latency distributions. Movie
latency did not improve in this sample even though irrelevant requests stopped.
The clearest gain is the season search, which no longer waits for six unrelated
Nyaa queries. Empty episode searches still retry aliases against Seedpool. Anime
results remained 292 from Nyaa and two from Seedpool. No releases were grabbed.
