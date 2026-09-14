# Navidrome

Navidrome runs on the homelab VM as the `navidrome` Compose project.
`music.ryzhkov.dev` is routed through Traefik using the stack's inventory entry.

- Music: `/mnt/shared/media/music`, alongside `movies`, `anime`, and `tv shows`.
  The existing NFS mount is `/mnt/shared`; music is read-only in the container.
- Database: `/home/iryzhkov/app/navidrome/data/navidrome.db`, on local disk.
- Cache: `/home/iryzhkov/app/navidrome/cache`, excluded from backups.
- Process identity: UID 1000, NAS media group GID 988.
- Backend: port 4533 on the inventory host address; `/ping` is the health check.
- Image: `deluan/navidrome:0.64.0`; missing images are pulled on deployment.

## Deploy

```sh
ansible-playbook playbooks/site.yml -l homelab -e only=navidrome
ansible-playbook playbooks/traefik.yml --check --diff
ansible-playbook playbooks/traefik.yml
ansible-playbook playbooks/backup.yml
```

The normal site playbook includes storage preparation. It refuses to deploy if
NFS is not mounted, and Docker refuses to create missing bind-mount sources.
Existing media ownership and contents are preserved. Music must be readable
by UID 1000 or GID 988 and its parent directories must be searchable.

Use Navidrome's own account authentication, including for OpenSubsonic clients;
there is no browser-only forward-auth gate in front of the API. On a fresh
installation, create the first admin through the setup UI/API on the private
backend before exposing the route. The account persists in the database; a
redeploy must not recreate it or reset its password. Public sharing is disabled.

The UniFi `traefik-homelab-ports` group must include TCP port 4533 for the
existing scoped public-api-to-homelab policy. This group is currently managed
through UniFi, outside Ansible; a Traefik route alone does not open the firewall.

LAN DNS already resolves this hostname to Traefik. Public DNS and tunnel ingress
are separate from the Traefik role; check them before promising internet access.
VPN access follows the existing LAN/mesh connectivity.

## Import purchased music

Copy extracted albums into the NAS music folder, preferably organized as
`Album Artist/Album/01 - Track.flac`, with artist, album, track and artwork tags.
Navidrome scans every five minutes; an admin can also request a scan in the UI.
ZIP downloads must be extracted first. This remains the canonical source for
a future iPod USB copy; Navidrome does not sync an iPod itself.

## Backup

The database joins the existing `backup_sqlite_sources` hourly online snapshots
(48 retained), with the existing offsite upload. The twice-weekly app backup
excludes the live database and cache. Music files are separate NAS data; verify
their file-backup coverage as the purchased collection grows. The current
homelab cron jobs do not include `/mnt/shared/media/music`.

## Validation on 2026-09-14

- Targeted Ansible redeploy/check: zero changes after deployment.
- A non-NFS library path was refused before starting Docker.
- `/music` is a read-only bind mount; database/cache are writable local mounts.
- Authenticated OpenSubsonic search, FLAC streaming and MP3 transcoding passed
  against a temporary two-second silent FLAC; the test file was removed afterward.
- The hourly SQLite snapshot was uploaded to B2, downloaded, and checked with
  `PRAGMA integrity_check`; the initial admin account was present in the restore.
- Traefik loaded the music route; the preview changed no unrelated routes.
- TCP 4533 is blocked between public-api and homelab until the UniFi port group
  is extended. HTTPS end-to-end verification is pending that firewall change.

The targeted site playbook now merges the deployed stack into an existing drift
manifest instead of requiring a full-host converge. Validation compared parsed
manifests and confirmed every pre-existing entry and all metadata were preserved.
