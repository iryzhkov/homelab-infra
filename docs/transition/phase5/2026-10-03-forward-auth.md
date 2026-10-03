# Phase 5: Authentik forward auth through a loopback entry point (checkpoint)

Date: 2026-10-02, PDT. Executor E21. Hosts: public-api (192.168.20.33), public-api-b (192.168.20.4).
Status: stopped at the coordinator's quota drain before any change. No file in the repository and nothing on either edge was changed for this task.

## Findings so far

- The forward-auth middleware in `roles/traefik/templates/generated.yml.j2` points at `http://192.168.70.223:4080/outpost.goauthentik.io/auth/traefik`.
- Pointing it at `https://auth.ryzhkov.dev` fails. A curl with `X-Forwarded-Host: ai.ryzhkov.dev` returns `302` to Authentik's authorize URL directly against `192.168.70.223:4080`, but `404` through the VIP 192.168.20.3, from both edges. The edge's `websecure` entry point does not trust incoming `X-Forwarded-*` headers and replaces them.
- The Traefik containers on both edges resolve `auth.ryzhkov.dev` to 192.168.20.3, and the hairpin to the VIP works from both.
- State at the stop: the VIP is on public-api (`ens18`). keepalived is active on both edges. Traefik `OomScoreAdj` is 0 on both edges. Traefik started at 2026-10-03T03:56:53Z on public-api and at 03:21:28Z on public-api-b.
- `auth-service` currently lists only `http://192.168.70.223:4080`, with health check `/`.

## Plan for resuming (option 2, approved)

1. Static config (`stacks/traefik/templates/traefik.yml.j2`): add an entry point `authloop` on `127.0.0.1:8081` inside the container, with `forwardedHeaders.trustedIPs: ["127.0.0.1/32"]`. Do not publish it.
2. Compose (`stacks/traefik/docker-compose.yml`): add a negative `oom_score_adj`, for example -500. Applying this recreates the container. The config change also triggers the role's restart task, so expect two short interruptions per edge.
3. Dynamic config (`generated.yml.j2`): add a router on `authloop` with `PathPrefix(`/outpost.goauthentik.io/`)`, no TLS, and the Authentik route's own service. Point the middleware at `http://127.0.0.1:8081/outpost.goauthentik.io/auth/traefik`. Keep `trustForwardHeader` and `authResponseHeaders` as they are.
4. During the rollout, gate the static and dynamic changes behind `-e traefik_authloop=true`. An edge whose static config lacks the entry point would otherwise get a disabled router and broken forward auth. Remove the gate once both edges are converged, and confirm that `traefik.yml --check` and `keepalived.yml --check` report changed=0.
5. Order: converge and verify public-api-b first. Then ask the coordinator before moving the VIP (stop keepalived on public-api), converging public-api and starting keepalived on public-api again.
