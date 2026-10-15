# Size limits and original audio

Apply with `ansible-playbook playbooks/media-quality-policy.yml`; preview with
`--check`. The script reads local protected API keys, saves private rollback
snapshots under /srv/swarm/media-policy/backups, and reconciles settings through
the supported Sonarr v4/Radarr API. It never edits media files, searches, changes
monitoring or writes import-list records. Existing profile names and IDs remain,
so automatic lists retain their current destinations and profiles.

## Size policy

Limits use MiB per minute, shared by all profiles in each application. The
preferred value ranks releases; maximum is the rejection ceiling. Files already
imported are neither removed nor replaced just for exceeding these settings.

| Content | Resolution | Minimum | Preferred | Maximum |
| --- | --- | ---: | ---: | ---: |
| Movies | 720p | 2 | 35 | 50 |
| Movies | 1080p | 3 | 70 | 100 |
| Movies | 2160p | 10 | 250 | 350 |
| TV/anime | 720p | 2 | 20 | 35 |
| TV/anime | 1080p | 3 | 40 | 60 |
| TV/anime | 2160p | 10 | 140 | 200 |

A two-hour movie has approximately 12.6 GB/44.0 GB maximum at 1080p/2160p.
A 45-minute TV episode has 2.83 GB/9.44 GB maxima; a 24-minute anime episode
has 1.51 GB/5.03 GB maxima. These are decimal GB. Actual arr rejection can vary
with runtime metadata and season-pack estimates. Low minima accommodate animation
and efficient encodes. This is a household storage compromise, not the
unlimited maximum settings of TRaSH's highest-quality examples.

Remux, raw-HD and disc formats are disabled in every profile. Ordinary profiles
are capped at their existing resolution, with Any limited to 1080p; explicit
UHD/Ultra-HD profiles retain 2160p eligibility. Existing upgrade flags stay as
they were. The new `Favorites - UHD + Original` profile enables upgrades through
2160p Blu-ray with 720p/1080p fallback. It is assigned by the separate Emby
favorites worker, not by this reconciliation.

## Audio and subtitles

Both applications use a `Language: Not Original` custom format with score
-10000 and minimum score 0. Radarr's profile language is Any to allow this
custom format to decide. Existing English/Japanese/Korean boosts are neutralized.
Releases must include the original language: English originals in English,
Japanese originals in Japanese, and so forth. Multi-audio is acceptable when
the original track is included; English-only dubs of foreign titles are rejected.

Original-language metadata comes from the media catalogs, and release language
detection comes from naming/indexer metadata. This cannot prove audio track
contents before download, nor select the track used by an Emby player.
Bazarr manages English subtitles separately; embedded English subtitles count.
Player subtitle/audio preferences are separate from acquisition rules.

References:
- https://trash-guides.info/Radarr/Tips/How-to-setup-language-custom-formats/
- https://trash-guides.info/Radarr/Radarr-Quality-Settings-File-Size/
- https://trash-guides.info/Sonarr/Sonarr-Quality-Settings-File-Size/

## Deployment verification

On 2026-10-14, profile/size reconciliation and Ombi anime routing were applied.
Second applies reported zero changes. Profile invariants were checked across
every existing profile: original-language rejection, remux exclusion, UHD fallback,
and repeated transformation idempotence. Sonarr favorite profile ID is 7 and
Radarr ID is 13; consumers resolve them by name. Import-list API readback stayed
identical throughout reconciliation. Ombi's anime root changed from 1 to 3 and
profile from 4 to 6, preserving other settings.
