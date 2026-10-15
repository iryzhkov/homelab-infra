# English subtitles for original and dual audio

Run `ansible-playbook playbooks/media-subtitles.yml --check` to preview, then
omit `--check` to reconcile. API credentials are read on homelab from Bazarr's
protected configuration; they are never sent in output.

The managed profile requests full English subtitles for movies, television and
anime, including dual-audio releases. It does not exclude English audio:
Bazarr's matching-audio exclusion checks **any** audio track, so excluding English
would also skip subtitles when watching Japanese on Japanese/English releases.
Player subtitle display and audio selection remain player preferences.

Embedded English subtitles satisfy the profile. Hearing-impaired English tracks
are accepted as a fallback; forced-only tracks do not satisfy full dialogue.
The profile is assigned to current content and enabled as the default for future
movies and series. No files are renamed, remuxed or removed. Subtitle upgrades
are disabled, and missing subtitles are searched every six hours. Applying this
playbook never explicitly invokes a library-wide download/search endpoint;
subsequent scheduled searches can download subtitles.

## Providers and validation

Live validation on 2026-10-14 used Bazarr v1.6.2. After profile assignment,
Bazarr reports 38 of 614 movies and 445 of 6,007 episodes still missing English
subtitles. It already indexes full embedded English subtitles for 572 movies
and 5,521 episodes. No end-to-end subtitle download was performed.

Validation details:

- No provider credentials or providers existed before configuration.
- YIFY Subtitles and Gestdown (Addic7ed proxy) require no account, and both
  returned provider status Good.
- The candidate-only movie API for Amélie (Radarr ID 142) returned six English
  YIFY candidates. Their score was 57, below the existing movie threshold of 70;
  the threshold was preserved, so these particular results are not automatic
  download candidates.
- The candidate-only episode API for Anohana episode 4390 returned no results.
  Anime coverage is therefore incomplete; embedded tracks still satisfy English
  requirements. AnimeTosho requires a registered AniDB client, which is absent.
- Podnapisi was removed upstream because its service is offline.
- Emby already has Open Subtitles v1.0.70.0 and nonempty username/password
  configuration. Its `System/Configuration/opensubtitles` endpoint returns a
  `PasswordHash` field. A single attempt to reuse that value in Bazarr returned
  AuthenticationError; portability was not established. The failed provider and
  copied credentials were removed immediately. No repeated login was attempted.
  A pointer to the actual OpenSubtitles.com password is needed to validate reuse.
- No subtitle download endpoint was called during validation.

The provider probes use GET `/api/providers/movies?radarrid=ID` and
GET `/api/providers/episodes?episodeid=ID`. Their separate POST endpoints perform
downloads and are not used by this tool.

For a controlled provider probe, override the playbook argument string:

```sh
ansible-playbook playbooks/media-subtitles.yml -e '{"media_subtitles_extra_args":"--providers yifysubtitles gestdown --movie-candidate 142 --episode-candidate 4390"}'
```

Without `--automatic-search` this stages missing-subtitle searches at Bazarr's
supported manual interval. Reapply the default playbook afterward to restore six
hour searches. AniDB or an existing OpenSubtitles.com account can improve coverage;
configure real credentials separately, never invent API client identities.

## Rollback and implementation

Before every changed apply, the script creates a mode-0700 directory under
`/var/backups/media-subtitles/`, holding mode-0600 configuration, API state and
a consistent SQLite backup. The original pre-change snapshot is
`/var/backups/media-subtitles/20261015T015415.860079Z` on homelab. A full database
restore must stop Bazarr first, restore the database/config with original
ownership, and remove stale WAL/SHM files only while stopped.

Upstream semantics were verified in Bazarr v1.6.2's
`bazarr/api/system/settings.py`, `bazarr/api/{movies,series}/`,
`bazarr/subtitles/indexer/{movies,series}.py`, and
`frontend/src/pages/Settings/Providers/list.tsx`.
Official setup: https://wiki.bazarr.media/Getting-Started/Setup-Guide/
