# Phase 5: Authentik forward auth through a loopback entry point (checkpoint)

Date: 2026-10-02, PDT. Executor E21. Hosts: public-api (192.168.20.33), public-api-b (192.168.20.4).
Status: done on both edges (22:10 to 22:13 PDT). The rollout gate has been removed. The VIP 192.168.20.3 is on public-api-b at the end.

## Findings so far

- The forward-auth middleware in `roles/traefik/templates/generated.yml.j2` points at `http://192.168.70.223:4080/outpost.goauthentik.io/auth/traefik`.
- Pointing it at `https://auth.ryzhkov.dev` fails. A curl with `X-Forwarded-Host: ai.ryzhkov.dev` returns `302` to Authentik's authorize URL directly against `192.168.70.223:4080`, but `404` through the VIP 192.168.20.3, from both edges. The edge's `websecure` entry point does not trust incoming `X-Forwarded-*` headers and replaces them.
- The Traefik containers on both edges resolve `auth.ryzhkov.dev` to 192.168.20.3, and the hairpin to the VIP works from both.
- State at the stop: the VIP is on public-api (`ens18`). keepalived is active on both edges. Traefik `OomScoreAdj` is 0 on both edges. Traefik started at 2026-10-03T03:56:53Z on public-api and at 03:21:28Z on public-api-b.
- `auth-service` currently lists only `http://192.168.70.223:4080`, with health check `/`.

## Changes in the repository

- `stacks/traefik/templates/traefik.yml.j2`: when `traefik_authloop` is set, an entry point `authloop` on `127.0.0.1:8081` (`traefik_authloop_port`, default 8081) with `forwardedHeaders.trustedIPs: ["127.0.0.1/32"]`. Without it the file renders as before.
- `stacks/traefik/docker-compose.yml`: `oom_score_adj: -500`.
- `roles/traefik/templates/generated.yml.j2`: records the Authentik route's service name (`auth`). With `traefik_authloop` set it renders `authentik-authloop-router` (entry point `authloop`, `PathPrefix(`/outpost.goauthentik.io/`)`, service `auth-service`, no TLS), and the middleware address `http://127.0.0.1:8081/outpost.goauthentik.io/auth/traefik`. `trustForwardHeader: true` and the 11 `authResponseHeaders` are unchanged. The only change without the flag is the middleware's comment, so a plain `traefik.yml --check` reports changed=1 on both edges until the gate is removed.

## public-api-b (standby, 192.168.20.4)

Check mode first: `traefik.yml --check --diff -l public-api-b -e traefik_authloop=true` showed only the router and the address; `site.yml --check --diff -l public-api-b -e only=traefik -e traefik_authloop=true` showed only the `oom_score_adj` lines and the (no_log) static config.

| Time (PDT) | Command | Result |
|---|---|---|
| 22:10:36 to 22:10:45 | `ansible-playbook playbooks/site.yml -l public-api-b -e only=traefik -e traefik_authloop=true` | changed=4: Compose file, `traefik.yml`, container recreated (compose up), then restarted by the role's config-change task; container started 05:10:44Z |
| 22:10:45 to 22:10:50 | `ansible-playbook playbooks/traefik.yml -l public-api-b -e traefik_authloop=true` | changed=1 (generated.yml); "48 routers loaded, all enabled" (47 before, plus `authentik-authloop-router`) |

Verification on public-api-b:

- `docker inspect traefik`: `OomScoreAdj` -500; `/proc/<pid>/oom_score_adj` -500. Published ports unchanged: 80, 443, `127.0.0.1:8080`; 8081 is not published.
- Inside the container `netstat -ltn`: `127.0.0.1:8081 LISTEN`. From the VM itself `curl http://127.0.0.1:8081/` gets no connection (`000`): the entry point exists only in Traefik's own network namespace.
- The same header test that gave 404 through the edge: `wget` inside the container to `http://127.0.0.1:8081/outpost.goauthentik.io/auth/traefik` with `X-Forwarded-Host: ai.ryzhkov.dev`, `X-Forwarded-Proto: https`, `X-Forwarded-Uri: /` answers `302 Found`, `Location: https://auth.ryzhkov.dev/application/o/authorize/?client_id=zWIc4mP6...`. The headers reach Authentik intact.
- End to end: `curl --resolve ai.ryzhkov.dev:443:192.168.20.4 https://ai.ryzhkov.dev/` answers `302` to `https://auth.ryzhkov.dev/application/o/authorize/?client_id=zWIc4mP6...`.
- Traefik API: `authentik-authloop-router@file` `status: enabled`, `using: [authloop]`; `auth-service` `serverStatus {"http://192.168.70.223:4080": "UP"}`. It lists one server because Authentik is still a Compose stack. Once P2 gives it a Swarm entry with its route, the same service lists both workers with no further change here.
- keepalived: `/etc/keepalived/check-traefik` exits 0; `keepalived` active. The journal shows `chk_traefik failed` and `Entering FAULT STATE` at 05:10:44Z during the restart, then `succeeded` and `Entering BACKUP STATE` at 05:10:48Z. public-api-b held no VIP (`eth0` 192.168.20.4/24 only), so no traffic was affected.

## public-api (192.168.20.33), after the coordinator's go

| Time (PDT) | Command | Result |
|---|---|---|
| 22:11:31 | `ssh public-api sudo systemctl stop keepalived` | public-api-b logged `(edge) Entering MASTER STATE` at 05:11:32Z; `eth0` gained 192.168.20.3/24; `https://draw.ryzhkov.dev` through the VIP answered 200 |
| 22:11:41 to 22:11:59 | `ansible-playbook playbooks/site.yml -l public-api -e only=traefik -e traefik_authloop=true` | changed=5 (as on public-api-b, plus the drift manifest entry); container started 05:11:57Z |
| 22:11:59 to 22:12:04 | `ansible-playbook playbooks/traefik.yml -l public-api -e traefik_authloop=true` | "48 routers loaded, all enabled" |
| 22:12:08 | `ssh public-api sudo systemctl start keepalived` | active. With `nopreempt` the VIP stayed on public-api-b |

Verification on public-api: `OomScoreAdj` -500. Published ports are unchanged (80, 443, `127.0.0.1:8080`). `127.0.0.1:8081` listens inside the container only, and the host gets `000`. `curl --resolve ai.ryzhkov.dev:443:192.168.20.33 https://ai.ryzhkov.dev/` answers `302` to `https://auth.ryzhkov.dev/application/o/authorize/?client_id=zWIc4mP6...`. `check-traefik` exits 0.

## Gate removed

The `traefik_authloop` conditions were taken out of `traefik.yml.j2` and `generated.yml.j2`. The authloop entry point, the router and the loopback middleware address are now the default; the middleware falls back to the direct address only when no authentik route exists. The rendered static file was unchanged by this (`site.yml --check -l public-api,public-api-b -e only=traefik`: changed=0 on both). The middleware comment changed, so `ansible-playbook playbooks/traefik.yml` installed that comment on both edges (a file-provider reload, no restart; 48 routers enabled on each).

Final checks, 22:12:58 PDT:

- `ansible-playbook playbooks/traefik.yml --check`: `public-api ok=6 changed=0`, `public-api-b ok=6 changed=0`.
- `ansible-playbook playbooks/keepalived.yml --check`: `public-api ok=8 changed=0`, `public-api-b ok=8 changed=0`.
- `ai.ryzhkov.dev` through each edge's own 443: `302` on both. keepalived is active on both.
- VIP: `ip -4 -br a`: public-api `ens18` 192.168.20.33/24 only; public-api-b `eth0` 192.168.20.4/24 and 192.168.20.3/24. **public-api-b holds the VIP.**

## Plan as approved (option 2, kept for the record)

1. Static config (`stacks/traefik/templates/traefik.yml.j2`): add an entry point `authloop` on `127.0.0.1:8081` inside the container, with `forwardedHeaders.trustedIPs: ["127.0.0.1/32"]`. Do not publish it.
2. Compose (`stacks/traefik/docker-compose.yml`): add a negative `oom_score_adj`, for example -500. Applying this recreates the container. The config change also triggers the role's restart task, so expect two short interruptions per edge.
3. Dynamic config (`generated.yml.j2`): add a router on `authloop` with `PathPrefix(`/outpost.goauthentik.io/`)`, no TLS, and the Authentik route's own service. Point the middleware at `http://127.0.0.1:8081/outpost.goauthentik.io/auth/traefik`. Keep `trustForwardHeader` and `authResponseHeaders` as they are.
4. During the rollout, gate the static and dynamic changes behind `-e traefik_authloop=true`. An edge whose static config lacks the entry point would otherwise get a disabled router and broken forward auth. Remove the gate once both edges are converged, and confirm that `traefik.yml --check` and `keepalived.yml --check` report changed=0.
5. Order: converge and verify public-api-b first. Then ask the coordinator before moving the VIP (stop keepalived on public-api), converging public-api and starting keepalived on public-api again.
