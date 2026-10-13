# Docker local-log activation and interrupted recovery

Run from a pinned Ansible controller checkout, with an accepted controlled
interruption window:

```sh
ansible-playbook playbooks/observability-docker-restart.yml \
  -e observability_docker_restart_confirm=true
```

The play is serial and stops the fleet batch on any failure. It requires prepared
validated local-log daemon configuration, no active Loki-driver containers,
maintenance clearance, household backend health and healthy manager quorum.
An edge hands its VIP to its peer. Manager unlock credentials are decrypted and
checked privately before service interruption; no key appears in command arguments.

Originally running Compose IDs are saved in the root-owned, mode0600
`/var/lib/observability/docker-activation.json` before stopping services. Swarm
tasks carrying Compose labels are excluded. Managers start those services after
private unlock, then check native container health and household backends.
The journal is removed only after all checks pass.

If a run fails, retain the journal and rerun the same play for that host during
the controlled window. Recovery runs even if the daemon now reports local.
A stopped daemon is started for recorded recovery. It is not restarted again
when its default is already local. Manager services are stopped for ordering
only if another daemon restart is needed, the manager is locked, or recovery
actually started the daemon. A failure limited to external backend health does
not stop otherwise healthy services again. Edge failover membership is restored
by the always block.

If captured containers were recreated out of band, recovery deliberately fails
closed on stale IDs. Reconcile the original Compose service identities and their
health before replacing recovery state; never clear the journal merely to make
the play pass. A Docker service that cannot start still requires daemon repair.

An unchanged completed host has no journal and local default, and requires no
restart. Use `--check` to verify that path. Check mode does not execute recovery
or prove the saved containers/backend will recover.

## Accepted evidence, 2026-10-13

All five hosts activated local defaults;65 active containers have no Loki driver.
After the Normandy restart, Immich dependency DNS and Frigate startup needed
focused recovery. Both recovered and household backends passed. The source now
orders Compose startup after manager unlock and persists interrupted recovery.
Eight regression tests cover label exclusion, literal Go templates, ordering,
local-driver resume, daemon-down entry and avoiding health-only restarts.
Ansible syntax and all-five-host check pass unchanged. Review found no further
blocker. The new durable recovery path has not been fault-tested through another
real manager interruption; these are source and unchanged-runtime checks.
