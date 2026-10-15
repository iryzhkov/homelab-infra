# Jellystat and Emby playback

Jellystat is deployed by the Swarm Ansible role, pinned to homelab, with a
separate PostgreSQL 17 database. The release is fixed at 1.1.12 and both images
have immutable registry digests. Upstream development is paused during a rewrite;
validate upgrades before changing these pins.

The UI route is https://jellystat.ryzhkov.dev (Swarm port 3012). PostgreSQL is not
published. The UI has its own login; username `igor`, with the password from
`JS_PASSWORD` in `secrets/jellystat.sops.yaml`. A convenience copy is in the
operator's local GNOME keyring (`service=jellystat`, `account=igor`). Treat
statistics and playback history as private.

## Secrets and state

The SOPS mapping contains `POSTGRES_PASSWORD`, `JWT_SECRET`, and `JS_PASSWORD`.
The role stages them privately and creates hash-versioned Docker secrets.
Jellystat's documented `FILE__` environment prefix reads those files;
PostgreSQL uses its native `POSTGRES_PASSWORD_FILE`.

State lives in `/srv/swarm/jellystat/{postgres,backup-data}` on the pinned worker.
The snapshot declaration takes hourly `pg_dump` copies (48 retained) and mirrors
Jellystat's own backup directory. Run `playbooks/swarm-backups.yml` after adding the
stack to install that declaration. Never use a live rsync of PostgreSQL as a
consistent database backup. Restore the database before restarting the stack
after loss of its state directory.

## Deployment and playback validation

```sh
ansible-playbook playbooks/swarm.yml --tags stacks -e only=jellystat --check --diff
ansible-playbook playbooks/swarm.yml --tags stacks -e only=jellystat
ansible-playbook playbooks/jellystat-bootstrap.yml
# Wait for first-run scheduler initialization before starting sync jobs.
ansible-playbook playbooks/jellystat-bootstrap.yml -e jellystat_sync=true
# First historical import only; review the limitation below before repeating.
ansible-playbook playbooks/jellystat-bootstrap.yml -e jellystat_import_history=true
ansible-playbook playbooks/swarm.yml --tags stacks -e only=janitorr
ansible-playbook playbooks/traefik.yml
ansible-playbook playbooks/swarm-backups.yml
```

`IS_EMBY_API=true` enables Emby support. The bootstrap helper reads Ombi's
existing Emby URL/key through read-only SQLite, configures Jellystat, and creates
or reuses a named Janitorr API key. It writes that key mode 0400 under the private
state directory; credentials and authenticated requests are never logged. A
normal repeat does not run sync/import again. Check mode makes no API changes.

On 2026-10-14, full sync completed successfully (57 seconds): 7 users, 832
library titles and 6,082 episodes. The Playback Reporting import staged 894
source events and produced 854 usable playback rows. Coverage is partial;
missing history must never be interpreted as proof that media was unwatched.
Whole-series API checks found recent Ted Lasso playback on October 13 and anime
history. Janitorr's restarted task logged 38 playback-history updates, including
identical latest series dates applied to multiple seasons. Live activity polling
and actual inserts/merges were also observed.

Upstream's historical conversion procedure has a batch-level imported-row guard;
repeating import can stage new events without converting them after the first
batch. Use live monitoring for subsequent playback, and audit any later backfill.
First-run scheduler initialization can also mark an in-flight sync Failed even
when library data loaded; rerun full sync after initialization and require an
actual Success task result. An HTTP 200 alone is insufficient.

Jellystat stores settings and API keys in PostgreSQL. Janitorr remains in dry-run
mode; its 180-day expiry and favorites exclusions still apply. Neither a health
check nor this partial history validates automatic deletion.

The HTTPS route is Ansible-managed. On deployment, port 3012 was appended to
UniFi's `traefik-homelab-ports` group (ID `6a966cc55afbaf142ea88874`) through the
controller API; that firewall exception is outside Ansible. Both edge proxies
and both Swarm backends were verified, with HTTPS returning 200.

Upstream: https://github.com/CyferShepard/Jellystat/tree/1.1.12
