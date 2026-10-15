# Emby favorite quality upgrades

This optional worker maps authenticated favorites from every Emby user onto existing
Sonarr/Radarr titles by provider identifiers. Movie favorites match TMDb or IMDb;
series match TVDb or IMDb. Favoriting an episode or season promotes its whole series.
Ambiguous and unmatched titles are reported and skipped. Favorites are the Emby
IsFavorite flag, not Likes.

Both applications must already have exactly one profile named
`Favorites - UHD + Original`. The worker does not create or relax profiles.
Deploy with `ansible-playbook playbooks/emby-favorite-upgrades.yml`; use
`--check` to perform the read-only preflight and preview installation.
Set `-e media_favorite_upgrades_enabled=false` to stop/disable its timer.

Every fifteen minutes, up to two titles receive the favorite profile and an
automatic search (whole series in Sonarr, movie in Radarr). Existing queued/running
worker searches count toward the limit. The first successful poll seeds all current
favorites into the backlog. Later favorites take priority over this initial backlog.
No files, monitoring flags or import lists are changed.
Unfavoriting never downgrades a title; pending unfavorited titles stop processing.
Previously completed titles are not searched repeatedly when toggling favorites.
Newly added matching *arr titles can be discovered on subsequent polls.

The complete all-user favorite inventory and both *arr profile/library reads must
succeed before any writes. Credentials are read from protected local *arr config.xml
and OmbiSettings.db (SQLite read-only), never printed or stored in queue state.
State lives at /var/lib/emby-favorite-upgrades (0700 directory, 0600 JSON).
Original profile IDs remain recorded for deliberate rollback. Never delete the
state file to retry: that would bootstrap all current favorites again.

Search intent is persisted before dispatch. A crash or HTTP failure during dispatch
leaves a `dispatching` record and does not automatically repeat the search.
Reconcile it against the corresponding *arr command history, then record its
commandId and queued phase or reset to profile-updated only after proving no command
was accepted. A failed search is also retained for operator review.

Inspect with `journalctl -u emby-favorite-upgrades.service` and
`sudo /usr/local/sbin/emby-favorite-upgrades` (read-only queue preview).
Stop the timer before maintenance. No automatic rollback or downgrade runs.

[Emby item query documentation](https://dev.emby.media/reference/RestAPI/ItemsService/getItems.html)
documents authenticated per-user favorites and provider identifiers.
