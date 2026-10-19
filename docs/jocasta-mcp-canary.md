# Jocasta MCP HTTPS canary route

An opt-in Traefik route for `https://jocasta.mcp.ryzhkov.dev/mcp`, off by
default. It exists to qualify the official MCP SDK clients against a dedicated
canary Jocasta backend over trusted HTTPS on the LAN. It never routes to
production Jocasta (`jocasta.ryzhkov.dev`, port 1935), which this change leaves
alone.

This file describes source configuration only. Nothing here has been deployed,
and no certificate for the name has been issued.

## What it is

| Piece | File |
|---|---|
| Flag, default `false` | `roles/traefik/defaults/main.yml` (`jocasta_mcp_canary_route_enabled`) |
| Checks, render and removal | `roles/traefik/tasks/jocasta-mcp-canary.yml`, imported from `tasks/main.yml` |
| Route | `roles/traefik/templates/jocasta-mcp-canary.yml.j2`, rendered to `jocasta-mcp-canary.yml` in the file provider directory |
| Test | `scripts/test-jocasta-mcp-canary.py` |

It follows the same pattern as the Mission Control route: a flag, a dedicated
template, and removal of that one file when the flag is off. The route
generator (`generated.yml.j2`), `router-tls.j2`, the wildcard settings and
every other route are unchanged.

When enabled, the route:

- matches `Host(jocasta.mcp.ryzhkov.dev)` on `websecure`, except requests
  carrying a `Cf-Ray` header, which excludes Cloudflare tunnel requests.
  Direct LAN and mesh clients can reach it; Jocasta enforces bearer and
  project permissions. This is not a network allowlist;
- asks the existing `letencrypt` resolver (DNS-01 through Cloudflare) for a
  certificate for exactly `jocasta.mcp.ryzhkov.dev`. The shared
  `*.ryzhkov.dev` wildcard covers one label only and cannot serve this name;
- sends every path to the backend, with no `PathPrefix` and no forward-auth.
  `/mcp` and `/.well-known/oauth-protected-resource/mcp` both arrive
  unchanged. Jocasta verifies bearer tokens itself;
- rewrites `Host` to a loopback authority and keeps `Origin` as the client sent
  it. The MCP SDK's DNS rebinding protection refuses a non-loopback Host on a
  loopback listener; when the listener is not loopback, the rewrite has no
  effect on the SDK. Jocasta still requires a supplied Origin to equal its
  configured resource origin, and it never derives trust from Host
  (`docs/http-mcp.md` in the Jocasta source, public commit `694b498`);
- health-checks `/healthz` every 15 seconds.

## Variables

| Variable | Default | Meaning |
|---|---|---|
| `jocasta_mcp_canary_route_enabled` | `false` | Render the route when true. Remove its file when false. |
| `jocasta_mcp_canary_backend_url` | none, required | `http://HOST:PORT` of the dedicated canary backend. |
| `jocasta_mcp_canary_upstream_host` | none, required | Loopback authority the backend listens on, `127.x.x.x:PORT` or `localhost:PORT`. Sent as Host. |

When the flag is true, the role refuses to render if:

- either variable is missing;
- the backend URL is not `http://HOST:PORT` with no path, user or query;
- the backend URL uses port 1935 (production Jocasta);
- the backend URL is loopback.

Traefik runs in Docker's default bridge network (`stacks/traefik/docker-compose.yml`),
so a loopback URL would point at the Traefik container itself, whose API
listens on port 8080 there. The backend URL must be an address that
Traefik's network namespace can reach. Which address that is depends on the
backend's deployment, which is a separate, pending step.

## Prerequisites before enabling

None of these are done by this change.

1. Backend. A dedicated canary `jocastad` built from an exact reviewed source
   commit (public source `694b498` or its successor), pinned by full commit tag
   or digest. It holds synthetic documents only, with no production data
   mounts, accounts or credentials. It is started with
   `JOCASTA_MCP_RESOURCE_URL=https://jocasta.mcp.ryzhkov.dev/mcp` and the
   canary issuer settings. It must be reachable from the Traefik container at
   the chosen backend URL, and its firewall must admit the edge.
2. Issuer. A disposable canary provider and application whose access tokens
   carry the exact audience `https://jocasta.mcp.ryzhkov.dev/mcp`, scope
   `jocasta.read` and the canary project grant. Its issuer URL and JWKS URL
   are pinned in the backend's configuration.
3. Certificate. The existing `letencrypt` DNS-01 resolver must be able to
   issue for the nested name. This uses the Cloudflare DNS token the edge
   already holds; check that the zone's CAA records, if any, allow Let's
   Encrypt. The first certificate is issued when Traefik loads the route.
4. Edge operator. Choose which `edge_proxy` members get the variables. Setting
   them in `inventory/host_vars/public-api.yml` enables the route only on the
   current VIP holder. Setting them in `inventory/group_vars/edge_proxy.yml`
   also enables it on `public-api-b`, which works only if that edge can reach
   the backend. VIP failover is not a prerequisite for a LAN canary.
   Requests to the name while the VIP is on an edge without the route get the
   catch-all's wildcard certificate, which does not match.

LAN clients already resolve the name to the VIP through AdGuard's
`*.ryzhkov.dev` rewrite. A public DNS record is not needed for a LAN canary
and is not part of this change.

## Opt in

1. In a reviewed inventory change, set the three variables for the chosen
   edge members.
2. Preview the change: `ansible-playbook playbooks/traefik.yml --check --diff --limit public-api`.
3. Apply it: `ansible-playbook playbooks/traefik.yml --limit public-api`.
   The file provider reloads without restarting Traefik. The role's final
   task fails if any router is not enabled.

## Verify (read-only)

On the edge, check the router's status:

```sh
curl -s http://127.0.0.1:8080/api/http/routers/jocasta-mcp-canary@file
```

From a LAN client, run the following checks.

The name resolves to the VIP, `192.168.20.3`:

```sh
getent ahosts jocasta.mcp.ryzhkov.dev
```

The certificate is a trusted one for the exact name, with `Verify return code: 0`:

```sh
openssl s_client -connect 192.168.20.3:443 -servername jocasta.mcp.ryzhkov.dev \
  -verify_hostname jocasta.mcp.ryzhkov.dev </dev/null 2>/dev/null |
  sed -n '/Verify return code/p'
openssl s_client -connect 192.168.20.3:443 -servername jocasta.mcp.ryzhkov.dev </dev/null 2>/dev/null |
  openssl x509 -noout -subject -issuer -ext subjectAltName -enddate
```

The backend answers health checks, metadata and unauthenticated MCP requests:

```sh
curl -sS -o /dev/null -w '%{http_code}\n' https://jocasta.mcp.ryzhkov.dev/healthz
curl -sS https://jocasta.mcp.ryzhkov.dev/.well-known/oauth-protected-resource/mcp
curl -sS -o /dev/null -D - -X POST -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":1,"method":"ping"}' https://jocasta.mcp.ryzhkov.dev/mcp
```

Expected results:

- `/healthz` returns `200`.
- The metadata names `"resource":"https://jocasta.mcp.ryzhkov.dev/mcp"`.
- The unauthenticated `POST /mcp` returns `401` with a `WWW-Authenticate: Bearer`
  header pointing at that metadata. A `403` that mentions an invalid Host means
  the Host rewrite does not match the backend's listener.

These checks prove the ingress only. Client qualification is separate.

## Roll back

1. Set `jocasta_mcp_canary_route_enabled: false`, or remove the three
   variables.
2. Run `ansible-playbook playbooks/traefik.yml` for the same edges. It removes
   `jocasta-mcp-canary.yml` only, and Traefik drops the route within a second.

Disable and run the playbook before reverting this source change. Once the
source is reverted, the removal task no longer exists and an already-rendered
file would stay on the edge. The issued certificate stays in `acme.json` until
it expires. It is harmless and does not affect production Jocasta.

## Test

The test renders the route through the role's own tasks against `localhost`,
writing into a scratch directory. It checks the default, the required
variables, the route's shape, enable and disable. It then runs the pinned
Traefik v3.7.15 against a local echo backend that applies the SDK's loopback
Host rule. It uses no inventory, SSH, ACME, DNS or credentials. Run it on
agent-a:

```sh
chaldea exec --host agent-a -- sh -eu -c '
  base="$(pwd -P)/.cache/jocasta-mcp-canary-test"
  export TMPDIR="$base/tmp" HOME="$base/tmp/.scratch-home"
  export JOCASTA_CANARY_TEST_HOME="$HOME" UV_CACHE_DIR="$TMPDIR/uv-cache"
  mkdir -p "$HOME"
  test "$(cd "$HOME" && pwd -P)" = "$base/tmp/.scratch-home"
  uv venv -q --python 3.11 "$TMPDIR/venv"
  uv pip install -q --python "$TMPDIR/venv/bin/python" ansible-core==2.19.13
  "$TMPDIR/venv/bin/python" -I scripts/test-jocasta-mcp-canary.py -v
'
```
