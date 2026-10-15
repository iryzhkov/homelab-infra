# Ombi anime request defaults

Ombi's Sonarr integration currently sends anime requests to the ordinary TV root
and profile. This playbook changes only `rootPathAnime` and
`qualityProfileAnime` through Ombi's supported settings API. The default targets
are root ID 3 (`/mnt/shared/media/anime`) and quality profile ID 6. Other Ombi
Sonarr settings, existing requests, existing series and all Sonarr/Radarr import
lists remain unchanged. No searches or downloads are started.

```sh
ansible-playbook playbooks/ombi-anime-settings.yml --check
ansible-playbook playbooks/ombi-anime-settings.yml
```

If the approved size/language work creates a different anime quality profile,
supply its validated Sonarr ID using `-e ombi_anime_quality_profile_id=ID`.
Preparation validates the profile exists and the root is the anime path.

The script runs on homelab and reads the existing Ombi API key from the
`OmbiSettings` record in `/srv/swarm/ombi/config/OmbiSettings.db`, opening SQLite
read-only with query-only protection. It does not write to the database.
GET requests use the local published Ombi API. Check mode performs only GETs.

Apply fetches settings, edits a copy of the two owned fields, checks that the
original has not changed, saves the original to a root-only 0600 JSON snapshot
under `/var/backups/ombi-anime-settings`, then POSTs the complete settings record
and verifies exact readback. Existing unrelated settings and credentials are
preserved. Repeated apply is a no-op. Snapshots contain API credentials: keep
them root-only and never attach their contents to reports or pull requests.
Ansible suppresses raw execution output and prints only the sanitized change
summary and snapshot path.

A concurrent edit can still race the final POST because Ombi has no conditional
settings update endpoint. Avoid editing the Sonarr settings page during apply.
For rollback, retrieve current settings and copy only the original two anime
fields from the protected snapshot through the same API; do not overwrite newer
unrelated settings with an entire old record.

The deployed Ombi Swagger schema confirms GET and POST
`/api/v1/Settings/sonarr`, GET `/api/v1/Sonarr/Profiles` and GET
`/api/v1/Sonarr/RootFolders`. Authentication uses the `ApiKey` header.
