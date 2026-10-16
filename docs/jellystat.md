# Jellystat and Emby playback

Jellystat is deployed by the Swarm Ansible role, pinned to homelab, with a
separate PostgreSQL 17 database. The release is fixed at 1.1.12 and both images
have immutable registry digests. Upstream development is paused during a rewrite;
validate upgrades before changing these pins.

The UI route is https://jellystat.ryzhkov.dev (Swarm port 3012). PostgreSQL is not
published. The UI has its own login; username `igor`, with the password from
`secrets/jellystat.sops.yaml`. Treat statistics and playback history as private.

## Secrets and state

The SOPS mapping contains `POSTGRES_PASSWORD`, `JWT_SECRET`, and `JS_PASSWORD`.
The role stages them privately and creates hash-versioned Docker secrets.
Jellystat's documented `FILE__` environment prefix reads those files;
PostgreSQL uses its native `POSTGRES_PASSWORD_FILE`.

State lives in `/srv/swarm/jellystat/{postgres,backup-data}` on the pinned worker.
The snapshot declaration takes hourly `pg_dump` copies (48 retained) and mirrors
Jellystat's own backup directory. Run `playbooks/backup.yml` after adding the
stack to install that declaration. Never use a live rsync of PostgreSQL as a
consistent database backup. Restore the database before restarting the stack
after loss of its state directory.

## Deployment and playback validation

```sh
ansible-playbook playbooks/swarm.yml --tags stacks -e only=jellystat --check --diff
ansible-playbook playbooks/swarm.yml --tags stacks -e only=jellystat
ansible-playbook playbooks/traefik.yml
```

`IS_EMBY_API=true` enables Emby support. Configure the internal Emby URL and
existing API key through Jellystat's authenticated settings, run a full library
sync, then import Emby Playback Reporting history. The import must be verified
against the source event counts and latest playback dates before Janitorr uses
it. Do not repeat historical imports without checking duplicate handling.

Jellystat stores application settings and its generated API key in PostgreSQL.
Deployment manages containers and secrets; it does not replay authenticated
first-run setup or historical import. Janitorr must remain in dry-run mode
through playback validation. A successful UI health check alone does not prove
that playback history was imported or that whole-series dates are correct.

Upstream: https://github.com/CyferShepard/Jellystat/tree/1.1.12
