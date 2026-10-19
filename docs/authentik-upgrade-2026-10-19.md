# Authentik 2025.8.6 to 2026.8.4 maintenance

This is a staged upgrade of the existing `authentik` Swarm stack on its pinned
homelab node. Do not deploy the final stack straight onto a 2025.8 database.
All source changes use the normal Swarm stack owner. No provider, account,
grant, signing key, MFA or application access policy is changed by this upgrade.

## Verified starting state

2026-10-19: server/worker 2025.8.6 at digest
`f162a2664fc54337be65f796130443e19d038caa82be572b7d44aa8b1a895af2`,
PostgreSQL 16, embedded proxy outpost only, no Docker socket mounts.
All four services run on homelab. The DB is about 100 MiB, 16 connections
against max_connections=200, and duplicate group-name count is zero.
These observations must be refreshed before another execution.

Vaultwarden requires verified email, but its existing Authentik email scope
mapping is custom, unmanaged, and returns email_verified=True. Preserve that
mapping; do not replace it with the new managed default or relax Vaultwarden.
No account email was independently verified by this maintenance.

## Immutable sequence

Official upgrade instructions require each release series, latest patch first.
Registry index digests checked 2026-10-19:

| Version | ghcr.io/goauthentik/server index digest |
| --- | --- |
| 2025.10.4 | sha256:4b4f9ae106dbda902b836aa7a79d2f456b8302f090b862f7ad1bf268402730b2 |
| 2025.12.6 | sha256:d4c1750e26bb7faa4d09e23305d75b54957ce4b81cee8e3acf4cc4bb8635d705 |
| 2026.2.7 | sha256:da7024c4a7136b2f0fbbae3740c5ed54633edb393b10dc4fae2cdc056b8f70a8 |
| 2026.5.7 | sha256:76bf433fd434c067cb25dc3e197cee793998441cda912441ea275293e76cc32c |
| 2026.8.4 | sha256:e7226ae116dac5aa0a5643803e735b010038f492c401e08e16b3bf1ac54c090c |

Use full `ghcr.io/goauthentik/server:VERSION@sha256:DIGEST` references.
Pre-pull on homelab before downtime. Do not update PostgreSQL as part of this.

## Recovery packet and stage procedure

Use a root-only disk scratch directory under
`/root/.cache/authentik-upgrade-20261019/`; never put backups in job snapshots,
Git, prompts or local agent scratch. Keep all pre-stage backups until a later
explicit retention decision. Record stage state before any non-idempotent action.

Before each stage:

1. Confirm both current Authentik services are healthy at the expected same
   version, and no other maintenance is changing the stack.
2. Save the current server/worker/Redis/PostgreSQL service specs and deployment
   files privately. Preserve the existing Docker secret references and private
   deployment secret files in the recovery packet without printing their values.
3. Scale server and worker to zero and wait for their running tasks to stop.
   PostgreSQL and Redis remain running. This is a login outage.
4. Run a custom-format `pg_dump -U authentik -d authentik` inside the pinned
   PostgreSQL container into a new 0600 pre-stage file. Archive the existing
   media, templates, certs and any new data directory with numeric ownership.
   Do not copy the live PostgreSQL data directory as a backup.
5. Prove the first pre-stage dump using a temporary isolated PostgreSQL16
   container, no published ports and `--network none`, restoring with
   `pg_restore --exit-on-error` and comparing core table counts. For later
   dumps verify the archive and retain its hash. Sensitive restore logs stay
   private on homelab. Remove only that temporary container and allocated
   restore scratch after success; retain the backup.
6. Update both service specifications to the SAME target image while their
   replicas remain zero. Use stop-first and failure-action=pause; never use
   automatic rollback to an older image against a migrated database.
7. Start both matching services. Allow up to ten minutes for migrations and
   health; inspect their private logs for migration failures before proceeding.
   A timeout/failure stops progression, never skips a release.
8. Confirm expected version/image, both health checks, DB migration completion,
   unchanged custom email mapping and embedded-only outpost, public ready/login
   page and OIDC discovery/JWKS continuity for the existing consumers.
   Real interactive login remains a separate user-observed check if no approved
   browser session is available. Do not claim it passed from HTTP200 alone.

At 2025.10 remove AUTHENTIK_REDIS__HOST from the two service environments.
Retain the unused Redis service and its data through the intermediate stages;
the final reviewed stack reconcile prunes that service only after acceptance.
Keep its state directory for recovery.

Before 2025.12 create `/srv/swarm/authentik/data` and its `media` mountpoint,
UID/GID1000, mode0750. Mount the data root at `/data` and the EXISTING
`/srv/swarm/authentik/media` at `/data/media` in both services, replacing
the old `/media` target. This nested bind preserves existing media at its
host location; no copy or destructive media move is needed. Snapshot both
host paths. Templates/certs mounts remain unchanged.

For 2026.8 set the fixed public base URL and trusted proxy CIDRs from stack.yml.
Observed direct ingress peers were10.200.0.2 and10.200.0.3, with ingress
subnet10.200.0.0/24. Verify that translated path before reusing this setting.
Both final service checks use `ak healthcheck` with120s startup allowance.
The pinned lifecycle/ak reads the process mode and selects the appropriate
Rust health check. The final image's OCI metadata did not retain its Dockerfile
HEALTHCHECK, so the Swarm checks must be explicit.

## Final source reconciliation

Only after the stages reach2026.8.4, run the normal restricted stack converge:

```sh
ansible-playbook playbooks/swarm.yml --tags stacks -e only=authentik -l homelab --check
ansible-playbook playbooks/swarm.yml --tags stacks -e only=authentik -l homelab
```

Inspect the check output first. The final stack uses the same secret names,
state/ports/pin and PostgreSQL image, three services, nested media/data mounts,
and explicit health/proxy settings. Update the homelab snapshot manifest through
the owning backup role so it includes both data and media. Do not deploy unrelated
Traefik, Gatus, registry, node, firewall or Akasha changes.

Repeat the stack converge to confirm no unintended changes. Check health,
discovery/issuer/JWKS and forward-auth before closing. Record exactly which host
and services were updated and any client-login qualification still outstanding.

## Failure and recovery

Authentik does not support schema downgrade. On a failed stage, keep both
Authentik processes stopped, retain failure logs, and restore that stage's
pre-upgrade dump into a clean Authentik database using PostgreSQL16. Restore
the matching media/data/template/cert state and exact pre-stage service specs,
then start the previous matching images. Never point an old image at a migrated
DB, use `docker service rollback` alone, delete current state without a preserved
recovery copy, or restore older security state into a running service.

Recovery can undo legitimate account/session changes made since the backup.
The stopped-service backup boundary limits that interval; investigate uncertain
outcomes before repeating any restore. Existing provider/signing/MFA state must
survive. No new ACL/OBO integration is activated by reaching2026.8.

References:
- https://docs.goauthentik.io/install-config/upgrade/
- https://docs.goauthentik.io/releases/2025.10/
- https://docs.goauthentik.io/releases/2025.12/
- https://docs.goauthentik.io/releases/2026.2/
- https://docs.goauthentik.io/releases/2026.5/
- https://docs.goauthentik.io/releases/2026.8/
