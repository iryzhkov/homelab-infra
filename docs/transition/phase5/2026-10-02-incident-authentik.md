# Incident 2026-10-02: Authentik, Miniflux and ai.ryzhkov.dev down after the Swarm rebuild

Executor: E23. All times are PDT.

## Summary

Rebuilding the Swarm on Nebula addresses ran `docker swarm leave --force` on
homelab (20:34:32) and normandy (20:35:13). Leaving tore down the libnetwork
agent and, with it, the embedded DNS (127.0.0.11) records of every container
that was already running on a local Compose bridge network. Those containers
kept running and external names kept resolving, but Compose service names
returned NXDOMAIN. Authentik's server and worker could no longer resolve
`postgresql` and `redis`, so Authentik went down, and Miniflux (OIDC through
Authentik) and `ai.ryzhkov.dev` (dsh behind Authentik forward auth) went down
with it. Service was restored at 21:08 by restarting the containers whose
records were lost. The Docker daemon was not restarted and no volume was
touched.

## Timeline

- 19:18-19:23  Phase 3: data-root moved to `/mnt/docker-data/docker`; homelab
  VM rebooted (up since 19:22:28). All Compose containers start at 19:22-19:23.
- 19:19-19:20  Watchdog logs two readiness failures (502) during the reboot
  window; it never reaches the restart threshold.
- ~19:40  Phase 3 review confirms the Authentik login page. Healthy.
- 20:17:21  First `docker swarm init` on homelab (advertise 192.168.70.223,
  pool 10.200.0.0/16).
- 20:34:32  `roles/swarm` with `swarm_rebuild=true`: homelab runs
  `docker swarm leave --force` ("Stopping manager", "left gossip cluster").
- 20:34:41  Swarm re-initialised on 10.42.0.2, ingress re-created.
- 20:35:13  normandy leaves the old cluster and joins the new one.
- 20:35:34  Watchdog: first readiness failure.
- 20:36:04  dockerd on both hosts: `Inconsistent driver and libnetwork state
  for endpoint ...` (three endpoints each).
- 20:37:41  Watchdog restarts `authentik-server-1` and `authentik-worker-1`
  for the first time; from here it restarts them every 3-4 minutes. The
  restarted containers get their own records back, but postgres and redis,
  untouched, still have none.
- 20:43:08  dockerd's resolver forwards `postgresql` A/AAAA to the upstream
  DNS servers: the name is no longer in the embedded DNS.
- 20:40  Jocasta firewall change; 20:53-21:04 E22 backup converge and
  `swarmtest` stack. Both after the onset; neither involved (see below).
- 21:06  E23 diagnosis: server and worker logs show only
  `PostgreSQL connection failed, retrying... ([Errno -2] Name or service not known)`.
  From inside `authentik-server-1`, `server` and `worker` resolve,
  `postgresql`, `redis` and `authentik-postgresql-1` do not, `google.com`
  does. All four containers are on `authentik_default` with the right
  aliases. Same in Miniflux (`nslookup db` NXDOMAIN) and Grafana (`loki`,
  `influxdb`). Disk: `/mnt/docker-data` 7% used.
- 21:07:36  Restarted `authentik-postgresql-1` and `authentik-redis-1`;
  `postgresql` and `redis` resolve immediately.
- 21:08:00  Authentik ready. `auth` 302, `rss` 200 (after restarting
  `miniflux-db-1` and `miniflux-miniflux-1`), `ai` 302. Service restored.
- 21:08  Restarted `loki` and `influxdb` (Grafana could not resolve them).
- 21:09  normandy: `immich_server` could not resolve `database`, `redis` or
  `immich-machine-learning` (latent; it was still serving on its open
  connections). Restarted `immich_postgres`, `immich_redis`,
  `immich_machine_learning`; names resolve, `photos` 200.
- 21:10-21:12  Gatus: homelab internal 45/45, public 16/16, normandy 50/50
  passing.

## Root cause

`docker swarm leave --force` in the rebuild branch of `roles/swarm`
(`tasks/main.yml`, "Leave the old cluster"). It removes the embedded DNS
entries of containers attached to local bridge networks at the time of the
leave. Containers started (or restarted) after the leave register normally,
which is why every affected container was one started at 19:22 and never
restarted since, and why the restart loop of server and worker never helped:
the names they needed belonged to postgres and redis.

The role's header said init and join never touch a Compose container; that
was true of init and join but not of leave.

## Ruled out

- authentik_watchdog: not the cause. Its restarts began a minute after the
  onset and only amplified the noise; once DNS was back it went green. It
  was left running.
- Firewall unit (SWARM-INGRESS), DOCKER-USER, FORWARD policy: external DNS
  and container-to-container traffic by IP worked; the failure was name
  resolution only, and NXDOMAIN came from the embedded resolver.
- Disk space: 260G free on `/mnt/docker-data`.
- Phase 3 data-root move: service was healthy at 19:40, after the move.
- Jocasta firewall (20:40), UniFi/AdGuard changes: external resolution and
  routing to the edge worked; the failure was inside the compose networks.
- E22 (20:53-21:04): after the onset. `swarmtest` ran on its own overlay
  network with no published ports and a stack-prefixed container name, so it
  could not clash with `authentik-postgresql-1`. The backup scripts did not
  restart the Authentik containers (their start times are 19:22 until E23's
  restart).

## Fix

Immediate: restart the containers whose DNS records were lost, so they
re-register. homelab: `authentik-postgresql-1`, `authentik-redis-1`,
`miniflux-db-1`, `miniflux-miniflux-1`, `loki`, `influxdb`. normandy:
`immich_postgres`, `immich_redis`, `immich_machine_learning`. Other
containers still dating from before the leave are single-container stacks
that resolve no Compose peers; Gatus passes for all of them.

Repository: `roles/swarm/tasks/main.yml` now lists the running Compose
containers before `docker swarm leave --force` and restarts them right after
it, and the header comment no longer claims leave is harmless. Converged with
`ansible-playbook playbooks/swarm.yml --tags nodes --limit homelab,normandy`
(changed=0: the clusters are already on the right addresses, so the leave
branch does not run).

## Prevention for the remaining phase 5 work

- Any step that leaves, re-inits or force-recreates a Swarm on a host that
  also runs Compose stacks must be followed by a name-resolution check, not
  only a port check: for each multi-container Compose project,
  `docker exec <app> getent hosts <peer>`.
- Gatus probes from outside the host do not see this failure while a client
  keeps an open connection (Immich stayed green with no DNS). Add a probe
  that exercises a fresh connection, or a host-local DNS check per project.
- The watchdog should log the reason when it can (the server log line), so
  its restarts are not mistaken for the cause; it restarted the wrong
  containers for 30 minutes.
- Swarm stack cutovers (moving Compose stacks into Swarm) recreate
  containers anyway; keep `swarm_rebuild=true` runs out of the hours the
  household uses SSO.
