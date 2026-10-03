# Phase 5: drain rehearsal after batches S1 to S3

Date: 2026-10-02, 22:48 to 22:58 PDT (2026-10-03 UTC). Executor E24.
Hosts: homelab (bootstrap manager, where the node commands ran), normandy; probes from the laptop
through the edge VIP 192.168.20.3.
Plan: `migration-plan.md`, the rehearsal after S3: drain normandy, confirm every S stack answers
through Traefik, undrain.

## Before

- `ping -c1 10.42.0.1` from homelab and from normandy: both answer (OCI reachable, the
  precondition for any drain). `docker node ls`: OCI Drain/Reachable, homelab Active/Leader,
  normandy Active/Reachable.
- On normandy: excalidraw, flaresolverr, hugo replica 1, librespeed, voice, wyoming-openai. On
  homelab: adguard, gatus-public, hugo replica 2, kiwix, whisper, wyoming-piper.
- normandy's Compose containers (`docker ps --filter label=com.docker.compose.project`):
  cloudflared, frigate, gatus, immich_machine_learning, immich_postgres, immich_redis,
  immich_server, with their start ages recorded.

## Drain

`docker node update --availability drain omarchy-normandy` at 22:48:33, with a probe every 0.5 s
on four routes for 90 s. By the next poll normandy ran no task.

| Route | Failed of 180 | Window | Cause |
|---|---|---|---|
| blog | 0 | none | two replicas; the homelab one kept serving |
| voice | 1 | 22:48:39 (000) | no health check; the new task served at once |
| speed | 26 | 22:48:36 to 22:48:49, 502 then 503 | image health check, start period 5 s |
| draw | 56 | 22:48:36 to 22:49:10, 502 then 503 | image health check, no interval: first probe 22:49:08, 31 s after the task started at 22:48:37 |

After the drain every service showed 1/1 on homelab; hugo showed 1/2, its first replica
`Pending` (`max_replicas_per_node: 1` and one available node), as designed.

## Every S stack after the move

Through the VIP: draw, speed, wiki, voice, uptime, blog 200; dns-sync `/healthz` 200;
`https://whisper.ryzhkov.dev/inference` with a test WAV 200. Wyoming `describe` to
192.168.70.223:10200 and :10300 answer `info`; `http://192.168.70.223:8191/` (flaresolverr)
200. Both Gatus instances: 84 probes on `*.ryzhkov.dev`, all OK, none failing.

## Undrain

`docker node update --availability active omarchy-normandy` at 22:50:28. 45 s later: hugo's
pending replica had started on normandy (2/2); every other task stayed on homelab. Swarm does not
rebalance running tasks by itself; they move back only when a service is updated (the
excalidraw and librespeed deploys at 22:51 and 22:53 and the wyoming-piper deploy at 22:54
placed their new tasks on normandy).

## Compose on normandy

The same `docker ps` after the undrain lists the same seven containers with the same creation
ages; the only difference is gatus's uptime ("Up 2 minutes" to "Up 4 minutes"; it was restarted
by the Gatus deploy at 22:46, before the drain). Answer: the drain touched no Compose container.

## Implications for later phases

- A drain costs each moved service its image's first health probe. Swarm keeps a starting task
  out of the routing mesh until it is healthy, so an image health check without a short start
  interval means about 30 s of 502/503 per move. Shortening Traefik's interval does not help.
  Fixed for excalidraw, librespeed and wyoming-piper with `healthcheck.start_interval: 2s` (S1
  evidence). F and P stacks should check their images' `HEALTHCHECK` the same way.
- After an undrain the workers stay unbalanced until the services are next updated. For the
  stateless stacks, `docker service update --force` per service rebalances them, and with
  start-first there is no gap.
