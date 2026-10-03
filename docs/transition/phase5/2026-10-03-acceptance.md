# Phase 5 acceptance record

Date: 2026-10-03. Branch `transition/phase-5`, accepted at `eefe885` (plus this record).

## Verdict

Accepted. Plan phase 5 steps 1 to 7 are met:

1. Three Swarm managers: homelab, normandy, and OCI drained. The control plane runs over Nebula and the data path on VLAN 70, which resolves the plan's conflict between VLAN 70 advertise addresses and an OCI manager. Autolock is on with the key in sops; unlock, quorum and OCI rules are in `migration-plan.md`.
2. Swarm ports are filtered to the managers; VXLAN stays on VLAN 70 (tcpdump); the routing mesh does not answer on VLAN 60, the mesh or OCI's public address.
3. Locally built images are in Forgejo; every external image is mirrored there and pre-pulled on both workers.
4. Conversion rules: placement, limits, stop-first for stateful services, file secrets, Loki in non-blocking mode on every container; the lint refuses stacks without placement or limits and any `$` in commands.
5. Traefik targets both workers for Swarm routes with 5 s health checks; forward auth goes through a loopback entry point on each edge to Authentik on both workers.
6. Eleven stateless and eighteen stateful stacks, Miniflux and Authentik run on Swarm. Stateful stacks are pinned, snapshot hourly to the peer worker and B2, and move with `swarm-move.yml`. Compose-pinned with reasons: Emby, Immich, Frigate, both Gatus instances, cloudflared, dsh, jocasta, and the torrent stack on black-pearl. Emby and Immich gained scripted moves (`compose-move.yml`) on Igor's decision.
7. normandy was drained after the stateless and the stateful classes; autobrr, Miniflux, lubelogger, Authentik, Emby and Immich were each moved to the other worker and back with data verified.

Review: four Sonnet 5.5 reviewers (Swarm state ACCEPT, availability ACCEPT, Ansible FIX REQUIRED, security FIX REQUIRED, should-fix items only). Every required fix is applied and recorded in the batch files. The Fable 5.1 reviewer re-checked managers, services, permissions, lint, all check-mode playbooks, routes through the VIP, the cv-site image and B2, and accepted. `swarm.yml`, `site.yml`, `backup.yml` and `traefik.yml` in check mode report changed=0 on every host.

## Household cost

- Unplanned: Authentik, Miniflux and the ai route down 34 minutes after a swarm leave removed Docker's embedded DNS (`2026-10-02-incident-authentik.md`); cv-site 4.6 minutes; public Emby 2.5 minutes (an OCI Caddy Host header); photos 49 seconds.
- Logging: Loki refused pushes for 10.5 minutes at its cutover, and then homelab's containers could not reach Loki through `localhost` (IPv6) from about 23:50 to 09:30, so roughly ten hours of homelab container logs are missing (about 6,300 failed batches). Stops of those containers could hang while it lasted.
- Planned: about 45 cutover and roll gaps of 2 to 35 seconds each, SSO 53 seconds; several ran in the daytime against the plan's night rule.
- Backups: one false-alarm failure from the hourly job, and 4.25 GB of move staging (Emby's config, Immich's raw Postgres) briefly uploaded to Igor's private B2 bucket, then removed and its hidden versions purged on Igor's approval.

## Privacy exposure

The cv-site image pushed to Forgejo carried the site's data directory as of 2026-09-09, including 79.8 MB of visitor analytics and the contact-form submissions. It was pullable anonymously from the internet from 2026-10-02 20:20 to 2026-10-03 10:43, about 14.5 hours. Forgejo keeps no pull log, so whether anyone downloaded it cannot be known. The image is rebuilt without the data and the old version returns 404; the cv-site repository's own Dockerfile still copies `data/`.

## Carried

- Routing-mesh ports and Loki's 3100 answer anywhere in the Internal zone and from VLAN 20 (pre-existing; a UniFi tightening).
- Built images are anonymously pullable (no secrets found); move them to a private Forgejo organisation.
- Seven services run tag-only `:latest` images rather than digest pins.
- `registry_push.yml` token creation resolves Forgejo by container name; `homelab-restore-verify` runs fixed images.
- Literal `192.168.70.223` consumers remain: Home Assistant's Wyoming and InfluxDB entries, Loki clients, and the forward-auth fallback.
- cv-site's `ADMIN_KEY` is not checked by the application.
- `tank` boot import is unproven until a host reboot (phase 7).
