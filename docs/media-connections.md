# Declarative media connections

Run only this playbook from the repository root:

```sh
ansible-playbook playbooks/media-connections.yml --check
ansible-playbook playbooks/media-connections.yml
```

Check performs application GET requests and read-only protected credential reads.
Its only host writes are a root-only temporary input file and Ansible's execution
scratch files; the temporary input is removed in an always block. Apply updates
only an existing, uniquely named connection with the supported API. Missing,
ambiguous, disabled or unsupported targets stop preflight rather than creating
duplicates or selecting a different application.

| Connection | Owned fields |
| --- | --- |
| Sonarr/Radarr qBittorrent | host, port, SSL, URL base, username/password |
| Prowlarr Sonarr/Radarr applications | Prowlarr URL, target base URL, target API key |
| Bazarr Sonarr/Radarr | IP, port, SSL, URL base, target API key |
| Ombi Sonarr/Radarr | IP, port, SSL, subdirectory, target API key |
| Ombi single existing Emby server | IP, port, SSL, subdirectory, existing Emby API key |

The source inventory supplies endpoint hosts and fixed service ports; no IP
addresses or credential values are embedded in the helper. Both arr apps,
Prowlarr and Emby currently use homelab's published endpoints. qBittorrent uses
blackpearl. Changing service placement requires changing that inventory mapping
in the playbook. Empty and null URL bases/subdirectories are equivalent and
preserve their current representation.

qBittorrent username/password come from the existing controller-side
`secrets/blackpearl-torrent.sops.yaml`. The decrypted values enter only a mode
0600 remote temporary JSON input. No secrets are passed on command lines.
Sonarr/Radarr/Prowlarr API keys are read fresh from protected config.xml files,
including the rotated Prowlarr key. Bazarr's API key comes from the protected
auth section in config/config.yaml. Ombi's own API key and the existing single
Emby server key come from read-only OmbiSettings.db records. SQLite uses
`mode=ro`, query-only protection and narrowly selected Settings/Content columns.
The arr APIs mask credentials, so only managed credentials are hydrated from
protected DownloadClients/Applications settings for accurate comparisons.
Ansible suppresses credential-bearing tasks, diffs and raw API payloads; the
public summary contains connection names and changed field paths, never values.

The Emby credential is adopted from Ombi's currently configured server, rather
than minted or recovered from Emby. Missing credentials stop the run. Recovery of
a lost/revoked Emby API key and new integrations require an explicit operator
decision; this helper does not recreate them.

Preflight reads all eight resources and the preservation baseline before any
API update. Apply checks for stale state, writes a mode 0600 rollback snapshot
under the root-only `/var/backups/media-connections` directory, patches a copy of
each current API resource, and checks exact readback. A second scoped apply must
report `changed: false`.

Existing request approvals, anime paths/profiles, Radarr4K settings, categories,
tags, initial torrent state, provider/subtitle/timing settings, import lists,
quality profiles and monitored flags are not owned by this helper. List/profile/
tag/indexer readback is checked against the baseline. Prowlarr force sync runs
only when one of its managed application connections changed. That sync may
propagate only the new Prowlarr URL/API key into its existing remote indexers;
their other fields and tags must remain identical. No indexer tests, searches,
downloads, grabs, seeding changes, file operations or notification settings are
requested. Bazarr may reconnect/resync when its connection fields change.

These APIs do not offer a shared transaction or conditional updates. Avoid
editing managed connection pages during apply. A partial API failure stops
subsequent writes and retains the private snapshot. Inspect current settings and
restore only the affected fields from it through supported APIs; never overwrite
newer application settings with a complete old snapshot. A preservation failure
after Prowlarr sync needs the same review before continuing. Snapshots contain
credentials and must stay root-only; never attach their contents to reports.

## Validation

On 2026-10-15, the scoped Ansible check passed with all eight existing resources
already matching and zero changes. No live apply was performed during helper
implementation. Eight offline tests cover check-only behavior, missing credentials,
ambiguous targets, schema failures, stale state, unrelated-field preservation,
idempotence, private snapshots after a partial API failure, and narrowly permitted
Prowlarr URL/key propagation. Run them with
`python3 -B scripts/test-media-connections.py`.

Playbook syntax and whitespace checks passed. A whole-tree Huyang parser run was
blocked by the existing unrelated YAML error in
`roles/observability_collector/tasks/docker-proxy.yml:62`; that file was untouched.
