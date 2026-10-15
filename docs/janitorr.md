# Janitorr observation service

Janitorr v2.2.2 runs through Ansible/Swarm on homelab. This addition reports
candidates in container logs and does not enable media deletion. The image is
pinned by tag and registry manifest digest.

The generated configuration sets `application.dry-run: true`,
`clients.emby.delete: false`, `file-system.access: false` and
`clients.emby.leaving-soon-type: NONE`. No NAS/library mount, published port,
public route, Emby login, stats dependency or persistent log directory is added.
Tag schedules and individual episode deletion are disabled. Normal stack logging
collects stdout. Janitorr runs cleanup passes with a one-hour delay between them.
Upstream logs say `Deleting movie/show` even during dry-run; those lines describe
candidates, and do not prove that a deletion happened.

The preview uses a conservative 180-day most recent Sonarr/Radarr grab age and
treats TV/anime as whole shows. Any episode download refreshes the show's age.
Emby favorites from all users and the arr tag `janitorr_keep` protect candidates.
Favorites apply to Movie/Series records; Emby Likes is a different field upstream.
No import-exclusion records or other application settings are changed.

This is not the full proposed retention policy. It has no playback statistics
provider, so it does not enforce 90-day watch protection. It does not implement
the independent report's 60-day new-file grace, airing-show protection, streaming
availability ranking or 20 TB budget. With filesystem access disabled, Janitorr
cannot determine disk usage, validate seeding or create Leaving Soon libraries;
it always uses the largest configured expiry, here 180 days. It only considers
media for which Sonarr/Radarr has download history. Candidate counts can therefore
differ from the independent audit.

Upstream v2.2.2 catches failures fetching an individual user's favorites and
continues with an empty list. An unsuccessful favorite lookup can produce an
unsafe candidate preview. Never disable dry-run based on this preview alone.
A later deletion rollout needs separate review of favorites, historical playback,
active series, download age, torrent/hardlink reclamation and list re-addition.
Dry-run alone does not suppress Leaving Soon writes upstream; both filesystem
access and Leaving Soon are explicitly disabled here.

## Existing API credentials

Preparation reads Sonarr/Radarr `config.xml` and the single Emby server API key
already configured in Ombi's `OmbiSettings.db` on homelab. SQLite uses read-only
mode and query-only protection. Missing or ambiguous records stop preparation.
It does not create keys, modify those applications or store credentials in Git.
Discovery, rendered configuration and all credential facts have `no_log: true`.
The generated config is mode 0400 owned by container UID 1000, inside a root-owned
0700 directory, mounted read-only. Only homelab receives a copy. Rotated keys are
rediscovered on the next deployment; a config hash triggers a service update.

The service is pinned to its source host. Preparation refuses an existing pin
elsewhere instead of rendering stale credentials onto an unrelated node.
Moving this service needs an explicit update of its source/config placement.

## Scoped deployment and verification

```sh
ansible-playbook playbooks/swarm.yml --tags stacks -e only=janitorr --check --diff
ansible-playbook playbooks/swarm.yml --tags stacks -e only=janitorr
docker service ps janitorr_janitorr
docker service logs --since 1h janitorr_janitorr
```

Review startup and a completed cleanup pass, confirm no authentication/favorite
lookup errors, and compare protected titles against the independent Emby audit.
These commands deploy only the Janitorr stack; Traefik and other arr settings do
not need convergence. Repeating the deployment with unchanged inputs should
report no service update.

Upstream evidence is pinned to v2.2.2, commit
`30e8f8f9c813c4b87313a8e81256d5bb858eceb2`:
`src/main/resources/application-template.yml`,
`mediaserver/MediaServerClient.kt`,
`mediaserver/BaseMediaServerService.kt`, and `stats/config/StatsConfig.kt`
under `src/main/kotlin/com/github/schaka/janitorr/`.
