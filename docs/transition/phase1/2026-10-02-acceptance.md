# Phase 1 acceptance record

Date: 2026-10-02. Implementation commit: `7ca0840` on `transition/phase-1`.

## What phase 1 became

The plan's premise was wrong. The "92 GB of current downloads" was the allocation of black-pearl's 1.8 TB qcow2 on proxmox-a; inside the guest it held 1.5 GB of stale partial files. All 1243 completed torrents (25.6 TB) already lived in `/mnt/shared/downloads` inside the NAS `media` share, and the *arr imports were already hardlinks. A separate `downloads` export would have sat beside `media` and turned every import into a copy. Igor chose, on 2026-10-02: no new export; qBittorrent's incomplete directory moves onto the media share; black-pearl's media mount becomes `hard`, managed by the base role and ordered before Docker; the 1.8 TB disk is retired without the plan's week of watching.

## Verdict

Accepted. Sonnet 5.5 reviewers for storage and Proxmox, Ansible, and service availability returned ACCEPT; the security and network reviewer returned FIX REQUIRED with should-fix items only. The Fable 5.1 reviewer accepted against the amended acceptance after its own read-only checks on proxmox-a, black-pearl and homelab. `site.yml`, `base.yml` and `backup.yml` in check mode report changed=0 on every host.

## Errata

- Five torrents, not four, were removed by qBittorrent's own seeding limits (`ShareLimitAction=RemoveWithContent`): four at 13:45:03 PDT as the torrents stopped, one at 13:52:42 PDT. This is qBittorrent's normal behaviour, seen one to three times a day since 2026-09-01.
- The cutover file mentions one suspicious executable in passing. There are more, listed below.
- Root squash on the NAS exports remains unconfirmed; writes land as `977:988`, consistent with all-squash.

## Suspicious files, awaiting Igor's consent

All were identified by their first bytes only (`MZ`, or `Rar!` for archives); none was opened.

- In the download area: `downloads/complete/tv/Lanterns 2026 S01E08 ... MeGusta.exe` (869 MB, 2026-09-29, still seeding, blocked in Sonarr's queue) and `downloads/complete/tv/Stuart Fails to Save the Universe S01E08 ....mkv.exe` (1.1 GB, 2026-09-04, torrent already removed).
- Already in the library, from 2025: `anime/One-Punch Man (2015) [tvdb-293088]/Season 3/` (S03E12 `.rar` and `.scr`, a stray folder beside `Season 03`), `tv shows/Stranger Things .../Season 5/` (S05E05 `.rar` and `.scr`), `tv shows/PLUR1BUS .../Season 1/` (S01E09 `.rar`), and `tv shows/Severance .../Season 2/` (S02E08 and S02E10 `.mkv.lnk`, 1.1 GB each).

The risk is confined to a Windows client opening them through a share; no Linux host executes from the share.

## Carried findings

| Finding | Where it goes |
|---|---|
| `local2tb` on proxmox-a is mounted by a hand-written `/etc/systemd/system/mnt-pve-local2tb.mount` (UUID `3ab4fa0b-26d3-4454-9935-d2e8339784ab`), not by fstab | Phase 2 precondition: disable and delete the unit, `pvesm remove local2tb`, re-grep `/etc/pve` for consumers, wipe by serial `CT2000P3SSD8 2420E8AFBCE0` only; update `docs/storage.md:11` |
| A changed `smart_on_calendar` would not restart the timer (carried from phase 0) | Phase 2, with the next base-role edit |
| black-pearl is also an allowed client of the `services_backup` and `public` exports, and still has the hand-written `/mnt/apps` automount | Phase 3: Igor removes 192.168.60.53 from both exports in UniFi Drive; `/mnt/apps` retired through `mounts_absent` |
| `/mnt/shared` lacks `nosuid,nodev,noexec` on black-pearl and homelab | Phase 3 stop-and-start windows, since the remount stops Docker |
| gluetun's `FIREWALL_OUTBOUND_SUBNETS` includes `192.168.20.0/24`, which UniFi lets High Seas reach on every port | Phase 3: trim to what the port manager and the *arr connection need, then test |
| If the NAS is unreachable at black-pearl's boot, Docker stays down until someone intervenes; a changed `nfs_mounts` entry stops Docker and the play does not start it again | Record only; documented in `2026-10-02-local2tb-retire.md`. A Gatus check on black-pearl's Docker belongs to phase 5 |
