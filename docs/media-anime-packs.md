# Fast manual anime pack discovery

Install with `ansible-playbook playbooks/media-anime-packs.yml`. On homelab:

```sh
sudo media-anime-packs --series-id 90 --season 1
```

Use the existing Sonarr series ID and season number. The helper reads its canonical
title, premiere year and available aliases from Sonarr; it does not maintain scene
IDs or change series types. It sends one canonical title query each to Seedpool
and Nyaa through Prowlarr, concurrently, with TV/anime categories and a 100-result
request limit. API keys come directly from protected local config.xml files.

Output is JSON containing release titles, sizes, tracker names, seeders and
explicit identity/season/year review flags. Links, hashes, API addresses and
credentials are omitted. Episode and film results are excluded; absolute-number
batches remain visible with season uncertainty. An explicit season number is a
discovery hint, never proof of correct content. A differing release year can be
a later season's year rather than a remake, so it always requires review.
Quality, original audio, subtitles, size limits and tracker rules require manual
checking before any separate download action. This helper performs GET requests
only; it never downloads, changes profiles or invokes Sonarr searches.
Errors from either tracker produce partial results and exit status 2.
Each request has a 20-second timeout; full result coverage is not promised.

The native Sonarr interface remains unchanged. Its deployed 4.0.20.3014 code
searches anime season packs and then unconditionally searches every aired episode.
Our twelve-episode Chainsaw Man benchmark generated 50 Seedpool requests, with
a median 2.005 seconds between requests and only 13.967 seconds of tracker time.
Sonarr hardcodes this two-second interval, explaining its 98.449-second result.

A reversible test disabled Nyaa automatic and interactive searches while retaining
RSS. It still took 98.449 seconds with Seedpool alone, versus a prior 120-second
timeout with both trackers. The experiment was rolled back and the temporary
profile removed; tags, categories and standard-format anime searching were
verified preserved. Raw targeted Prowlarr queries took 0.45–0.85 seconds. The finished helper's
live Chainsaw Man season-one check took 0.592 seconds total and returned nine
pack candidates: six from Seedpool and three from Nyaa. Seven Seedpool and
fifty-nine Nyaa episode/film/unclassified results were excluded. These are
manual-review candidates, with no download acceptance implied.

Do not disable standard-format anime searching as a speed shortcut: the deployed
Torznab generator would remove anime season-pack requests. Native full searches
retain their coverage; this helper provides a separate, bounded manual pack view.

Primary implementation evidence:
- [Anime season search loop](https://github.com/Sonarr/Sonarr/blob/v4.0.20.3014/src/NzbDrone.Core/IndexerSearch/ReleaseSearchService.cs)
- [Two-second request interval](https://github.com/Sonarr/Sonarr/blob/v4.0.20.3014/src/NzbDrone.Core/Indexers/HttpIndexerBase.cs)
- [Prowlarr targeted search API](https://github.com/Prowlarr/Prowlarr/blob/v2.6.5.5623/src/Prowlarr.Api.V1/Search/SearchResource.cs)
