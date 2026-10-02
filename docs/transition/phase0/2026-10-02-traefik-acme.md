# Phase 0 check: Traefik ACME method on public-api

Measured 2026-10-02 13:00 PDT. Host: public-api (VM 106, 192.168.20.33).
Repository read at branch `transition/phase-0`.

## Repository

- `stacks/traefik/docker-compose.yml`: `traefik:latest`, `network_mode: bridge`,
  ports 80, 443 and `127.0.0.1:8080`; environment `CF_API_EMAIL` and
  `CF_DNS_API_TOKEN` taken from `.env` next to the file; mounts
  `./traefik.yml:/traefik.yml:ro`, `./file_provider:/file_provider:ro`,
  `./certs:/certs`, and the Docker socket read-only.
- `secrets/public-api-traefik.sops.yaml` holds `CF_API_EMAIL` and
  `CF_DNS_API_TOKEN` (SOPS-encrypted); `inventory/host_vars/public-api.yml` links
  it to the stack with `secrets: public-api-traefik`.
- `inventory/group_vars/all.yml`: `traefik_cert_resolver: letencrypt` (the
  misspelling is the resolver's real name), entrypoint `websecure`.
- `roles/traefik` only renders dynamic configuration into
  `/home/igor/apps/traefik/file_provider`; it does not manage the static
  `traefik.yml`, which is not in the repository.
- `stacks/traefik/dynamic/catchall.yml` requests a wildcard certificate,
  `main: ryzhkov.dev`, `sans: *.ryzhkov.dev`, through `letencrypt`.

## Live container (read-only)

`docker inspect traefik`: Cmd `["traefik"]`, image `traefik:latest`, started
2026-09-19T11:07:45Z. Mounts:

```
/home/igor/apps/traefik/traefik.yml   -> /traefik.yml (ro)
/home/igor/apps/traefik/file_provider -> /file_provider (ro)
/home/igor/apps/traefik/certs         -> /certs (rw)
/var/run/docker.sock                  -> /var/run/docker.sock (ro)
```

Environment variable names: `TZ`, `CF_API_EMAIL`, `CF_DNS_API_TOKEN`, `PATH`
(values not recorded).

Static configuration `/home/igor/apps/traefik/traefik.yml`, resolver section:

```
certificatesResolvers:
  letencrypt:
    acme:
      storage: /certs/acme.json
      caServer: https://acme-v02.api.letsencrypt.org/directory
      dnsChallenge:
        provider: cloudflare
        delayBeforeCheck: 10
        resolvers: [1.1.1.1:53, 8.8.8.8:53]
```

Only the file provider is configured; the Docker socket is mounted but unused.
`api.insecure: true` serves the dashboard on 8080, published on loopback only.

Files: `/home/igor/apps/traefik/.env` (mode 600, igor);
`/home/igor/apps/traefik/certs/acme.json` (mode 600, root, 579009 bytes, modified
Sep 27) plus three older `acme.json.bak.*` copies.

## Recorded answer

DNS-01 through the Cloudflare provider, resolver `letencrypt`, including a
`*.ryzhkov.dev` wildcard; acme.json is `/home/igor/apps/traefik/certs/acme.json`
on public-api; the Cloudflare token is the `CF_DNS_API_TOKEN` environment
variable from `/home/igor/apps/traefik/.env`, sourced from
`secrets/public-api-traefik.sops.yaml`.

## Implications for later phases

- Phase 4 item 4: no switch from HTTP-01 is needed; each edge VM can already
  issue independently. What remains is the token hygiene the plan asks for: the
  token is currently an environment variable (visible in `docker inspect`) and
  is scoped to whatever zone it was created for, not a delegated
  `_acme-challenge` zone.
- Two edge VMs each requesting the same wildcard will count toward Let's
  Encrypt's duplicate-certificate limit (5 per week for the same name set);
  copying `acme.json` between them, or having one issuer, avoids it.
