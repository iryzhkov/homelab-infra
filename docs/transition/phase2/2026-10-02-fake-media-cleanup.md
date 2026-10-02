# Fake media cleanup

Date: 2026-10-02, 14:20 to 14:31 PDT (21:20 to 21:31 UTC).
Hosts: black-pearl (VM 102, `gt-qb` qBittorrent container, NAS `media` share at `/mnt/shared`), homelab (VM 101, Sonarr and Emby, API calls only).

Igor approved the deletion on 2026-10-02 in the lead session. The list came from the "Suspicious files" section of `docs/transition/phase1/2026-10-02-acceptance.md`. No file was opened, extracted or executed. Files were identified by name, size and their first four bytes only. black-pearl has no `xxd`, so the first bytes were read with `head -c4 | od -An -tx1` instead.

Secrets used: the qBittorrent WebUI login from `~/apps/torrent/.env` on black-pearl (`QBITTORRENT_USER`, `QBITTORRENT_PASS`), the Sonarr API key from `~/app/sonarr-tv/config.xml` on homelab, and the Emby API key row `Id=34` (`AppName=Emby`) of `Tokens_2` in `~/app/emby/data/authentication.db` on homelab, read read-only. None was printed.

## 1. Re-confirm the files and sweep for more

Command, on black-pearl from `/mnt/shared`:

```
find "media/tv shows" media/anime media/movies downloads -type f \( -iname "*.exe" -o -iname "*.scr" -o -iname "*.lnk" -o -iname "*.bat" -o -iname "*.cmd" -o -iname "*.msi" -o \( -iname "*.rar" -ipath "*/season*" \) \) \
  -print0 | while IFS= read -r -d "" p; do printf "%s  %s\n" "$(head -c4 "$p" | od -An -tx1 | tr -d " ")" "$p"; done
```

Result: exactly the nine listed files, nothing else.

| Magic | Size (bytes) | mtime | Path (under `/mnt/shared`) |
|---|---|---|---|
| `4d5a9000` (MZ) | 869211326 | 2026-09-29 | `downloads/complete/tv/Lanterns 2026 S01E08 Dirt and Stars 1080p WEB H264-MeGusta.exe` |
| `4d5a9000` (MZ) | 1128121946 | 2026-09-04 | `downloads/complete/tv/Stuart Fails to Save the Universe S01E08 1080p WEB-H264.mkv.exe` |
| `52617221` (Rar!) | 1614216540 | 2025-12-23 | `media/anime/One-Punch Man (2015) [tvdb-293088]/Season 3/One-Punch Man S03E12 1080p WEB H264-SENSEI.rar` |
| `4d5a9000` (MZ) | 1262104064 | 2025-12-23 | `media/anime/One-Punch Man (2015) [tvdb-293088]/Season 3/One-Punch Man S03E12 1080p WEB H264-SENSEI.scr` |
| `52617221` (Rar!) | 1614216551 | 2025-12-24 | `media/tv shows/Stranger Things (2016) [tvdb-305288]/Season 5/Stranger.Things.S05E05.1080p.WEB.h264-ETHEL.rar` |
| `4d5a9000` (MZ) | 947469824 | 2025-12-23 | `media/tv shows/Stranger Things (2016) [tvdb-305288]/Season 5/Stranger.Things.S05E05.1080p.WEB.h264-ETHEL.scr` |
| `52617221` (Rar!) | 1614216506 | 2025-12-23 | `media/tv shows/PLUR1BUS (2025) [tvdb-436457]/Season 1/Pluribus S01E09 1080p HEVC x265-MeGusta.rar` |
| `4c000000` (shortcut) | 1147908610 | 2025-03-03 | `media/tv shows/Severance (2022) [tvdb-371980]/Season 2/Severance.S02E08.1080p.WEB.h264-ETHEL.mkv.lnk` |
| `4c000000` (shortcut) | 1176739714 | 2025-03-20 | `media/tv shows/Severance (2022) [tvdb-371980]/Season 2/Severance.S02E10.1080p.ATVP.WEB-DL.DDP5.1.H.264.mkv.lnk` |

The stray `One-Punch Man .../Season 3/` folder held only its `.rar` and `.scr`.

Answer: all nine listed files exist with the recorded sizes; no new suspicious file was found, so nothing was added to the list and nothing is held for Igor.

## 2. Lanterns S01E08 (still seeding)

Before: Sonarr queue item `35655072`, `trackedDownloadState=importPending`, status message "Caution: Found executable file with extension: '.exe'", `downloadId=5C51B90355E4A90B7F1E696E3CCBD945DCB21964`, series 316, episode 18548. qBittorrent showed the torrent `state=uploading`, `size=869211326`, category `tv-sonarr`.

Command, on homelab:

```
curl -X DELETE -H "X-Api-Key:$K" "http://localhost:8989/api/v3/queue/35655072?removeFromClient=true&blocklist=true&skipRedownload=true"
```

Output: HTTP 200. `skipRedownload=true` was used because step 5 runs the search explicitly. The queue then held no Lanterns item, and the blocklist gained entry `id=21`, `sourceTitle=Lanterns 2026 S01E08 Dirt and Stars 1080p WEB H264-MeGusta`, `2026-10-02T21:28:46Z`. Sonarr history recorded `downloadFailed` for episode 18548 at the same time.

Check, on black-pearl: `GET /api/v2/torrents/info?hashes=5c51b903...` returned `[]`; `ls /mnt/shared/downloads/complete/tv/Lanterns*` shows only the genuine S01E01 to S01E07 files, no `.exe`.

Answer: Sonarr removed the torrent and its data from qBittorrent and blocklisted the release; the qBittorrent API fallback was not needed.

## 3 and 4. Deletions

The files are owned by `977`, `1001` or `1002` with group `988` and mode `rw-rw----`, in group-writable directories; NAS writes land as `977:988`. The deletion ran as the qBittorrent user (`abc`, uid 1000) inside `gt-qb` on black-pearl, which sees the share at the same `/mnt/shared` path. A script removed each file only if it still had the size recorded in step 1, then removed the `Season 3` folder only if empty:

```
docker exec -u abc gt-qb sh /tmp/e14-del.sh    # rm -- "$path" per entry after a stat -c %s size check; rmdir if ls -A is empty
```

Output, every line:

```
DELETED 1128121946 downloads/complete/tv/Stuart Fails to Save the Universe S01E08 1080p WEB-H264.mkv.exe
DELETED 1614216540 media/anime/One-Punch Man (2015) [tvdb-293088]/Season 3/One-Punch Man S03E12 1080p WEB H264-SENSEI.rar
DELETED 1262104064 media/anime/One-Punch Man (2015) [tvdb-293088]/Season 3/One-Punch Man S03E12 1080p WEB H264-SENSEI.scr
DELETED 1614216551 media/tv shows/Stranger Things (2016) [tvdb-305288]/Season 5/Stranger.Things.S05E05.1080p.WEB.h264-ETHEL.rar
DELETED 947469824 media/tv shows/Stranger Things (2016) [tvdb-305288]/Season 5/Stranger.Things.S05E05.1080p.WEB.h264-ETHEL.scr
DELETED 1614216506 media/tv shows/PLUR1BUS (2025) [tvdb-436457]/Season 1/Pluribus S01E09 1080p HEVC x265-MeGusta.rar
DELETED 1147908610 media/tv shows/Severance (2022) [tvdb-371980]/Season 2/Severance.S02E08.1080p.WEB.h264-ETHEL.mkv.lnk
DELETED 1176739714 media/tv shows/Severance (2022) [tvdb-371980]/Season 2/Severance.S02E10.1080p.ATVP.WEB-DL.DDP5.1.H.264.mkv.lnk
RMDIR media/anime/One-Punch Man (2015) [tvdb-293088]/Season 3
```

The Lanterns `.exe` (869211326 bytes) was deleted by Sonarr through qBittorrent in step 2. In total, nine files and 11374205081 bytes (about 11.4 GB) were removed, plus one empty folder.

Re-running the step 1 `find` afterwards returned 0 files.

Answer: every listed file is gone; the stray `Season 3` folder was empty and was removed.

## 5. Sonarr rescan and search

`POST /api/v3/command` with `RescanSeries` for each affected series:

| Command ID | Series | Result |
|---|---|---|
| 2063058 | 316 Lanterns | completed, successful |
| 2063059 | 310 Stuart Fails to Save the Universe | completed, successful |
| 2063060 | 71 One-Punch Man | completed, successful |
| 2063061 | 121 Stranger Things | completed, successful |
| 2063062 | 120 PLUR1BUS | completed, successful |
| 2063063 | 25 Severance | completed, successful |

After the rescan, only Lanterns S01E08 (episode 18548) lacks a file. The others already had genuine imported files, so the fakes had been strays beside them: Stuart S01E08 file 10733 (CAKES, 1381551080 bytes), One-Punch Man S03E12 file 4566 (`Season 03`, BiOMA), Stranger Things S05E05 file 4299 (FLUX), PLUR1BUS S01E09 file 4209 (NTb, 720p), Severance S02E08 file 1385 (FLUX) and S02E10 file 1555 (SuccessfulCrab). No search was needed for those.

`EpisodeSearch` for `episodeIds=[18548]`: command `2063064`, completed, successful, "Episode search completed. 0 reports downloaded." at 21:29:42Z. Sonarr will pick the episode up from RSS when a release passes its filters.

Answer: all six rescans succeeded; the one missing episode was searched and nothing was grabbed yet.

## 6. Emby

`POST /emby/Library/Refresh` returned 204; the `RefreshLibrary` task then reported `Idle`, last result `Completed`. `GET /emby/Items?Recursive=true&IsFolder=false&Fields=Path` returned 8328 items, none with a path ending in `.rar`, `.scr`, `.lnk`, `.exe`, `.bat`, `.cmd` or `.msi`.

Answer: Emby lists none of the deleted items.

## Implications for later phases

The same release groups' fakes (MeGusta, ETHEL, SENSEI tags on `.exe`, `.scr`, `.rar`, `.lnk` files) reached the library in 2025 before Sonarr's executable check caught them. A periodic version of the step 1 `find` would catch any recurrence; it belongs with the phase 5 monitoring work.
