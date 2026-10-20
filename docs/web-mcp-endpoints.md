# HA and homelab observer packaging source

This slice packages two reviewed service interfaces as static Linux amd64
binaries. It does not enroll identities, publish releases, obtain credentials,
activate production inventory, or establish runtime acceptance. Deploy and config
source belongs in homelab-infra. These playbooks are not imported by site.yml.

The full Jocasta guide `tooling/guides/web-mcp-authentik-authoring-2026-10-19.md`
was read before implementation. Existing chaldea_host_artifact checksum staging,
Akasha dedicated-user/systemd ownership, and Traefik file-provider/resolver patterns
informed this role. It does not invoke those roles or alter their managed files.

## Input contract

`web_mcp_endpoint_enabled` defaults false and makes the role a no-op.
`web_mcp_activate` defaults false. Enabled staging refuses an already running or
enabled service, installs source-selected files, and leaves a new unit disabled.
Only a later explicitly approved run with activation true enables/starts it.
A fresh check-mode run cannot prove installation, checksum or runtime behavior.

The required `web_mcp_endpoint` object has exactly these fields:

| Field | Meaning |
| --- | --- |
| service | Only `ha-mcp` or `homelab-observer` |
| uid, gid | Explicit unused dedicated system UID/GID, integers 100..999 |
| listen | Explicit private/loopback IPv4 and unprivileged port, no wildcard |
| artifact | `url`, `sha256`, `source_revision`, `contract_revision` |
| config | Exact service-specific JSON object described below |
| credentials | Exact referenced list of `{name, source, source_uid}` objects |

No values or release checksums are supplied by inventory in this change.
The operator must supply the reviewed static binary's explicit HTTPS URL and
SHA256. There is no default URL or release-asset claim. The service source
repositories are `iryzhkov/homeassistant-mcp` and `iryzhkov/homelab-cli`.
No archives, mutable latest URL, source checkout, package installation or build
occurs on a service host. Review and record the binary SHA256, published source
commit, static amd64 build provenance and corresponding accepted contract first.
The role downloads to a content-addressed directory, verifies the checksum and
static ELF format before installing config or switching the unit, then runs the
exact binary path. TLS certificate verification remains enabled.

The accepted contract revisions are:

- HA `027505484cf33947e64f5232cf17a1ff0d96d522`.
- Observer `8af24606b7cf5cd89e727660266104699313e993`.

`artifact.source_revision` identifies the actual published repository commit;
`artifact.contract_revision` identifies the accepted interface above. These are
provenance assertions by the reviewer, not an automated proof that an artifact
contains that source. HA publication is qualified at merged commit
[`9b296c6ab56432be34856f733e25843f0ca097ef`](https://github.com/iryzhkov/homeassistant-mcp/commit/9b296c6ab56432be34856f733e25843f0ca097ef),
module `http-mcp/`, executable `ha-mcp`. The integration owner reported its
source byte-identical to accepted 0275054 after assembly 0b1baf0 and passing
GitHub push39376978478/PR39376979811 checks. This is source publication evidence,
not a deployed runtime or release asset. Build/review a static binary from that
commit and set its explicit operator artifact URL and SHA256 in the later
scoped approval vars. Keep the original contract revision unless the interface
changes and is accepted again. Observer interface remains accepted at 8af2460;
homelab-cli PR20 publication is pending an unrelated legacy-help CI repair.

## Service config and custody

HA config has exactly `listen, resource_url, issuer_url, jwks_url, ha_url,
backend_token_file, entities`. Resource is canonical HTTPS /mcp; issuer includes
its exact trailing slash and JWKS is pinned HTTPS. `ha_url` is a pathless
HTTP/HTTPS origin. `listen` must equal the top-level explicit bind.
`backend_token_file` is fixed to `/etc/ha-mcp/backend-token`.

Each of 1..64 entities has `id, name, actions` and optional
`control_qualified, brightness`. IDs are explicit light/switch/sensor/binary_sensor
entity IDs. Actions are state.read/light.control/switch.control; control requires
matching domain and `control_qualified: true`. Optional light-control brightness
requires both integer min_percent/max_percent within 0..100. The service README
defines the physical qualification and uncertain-write limits; Ansible does not
qualify a device or grant access.

Observer config has exactly `resource, issuer, jwks_url, inventory_revision,
stale_after_seconds, hosts, services, gatus`. Its resource/trust URL constraints
match HA. Revision is 40..64 lowercase hex; freshness is 1..86400 seconds.
Hosts are bounded `{id,name}` records; services add an explicit host_id and
1..4 `{backend_id,endpoint_key}` probes. Each probe belongs to one service.
Up to 16 Gatus records have `id, base_url, version, source_revision` and optional
`credentials_file`. Only HTTPS origins, version v5.37.0 and Gatus source
ae605f291928ed9b3bdbe60ae5528cc57631433b are qualified. The optional path is
`/etc/homelab-observer/gatus-<backend-id>.json`. Omitting it sends no auth
and requires a Gatus endpoint that allows anonymous access. token_file and
inline credentials are rejected. Service/host signed grants and backend
authorization stay in the observer.

Every credential reference must have one matching `credentials` entry:

```yaml
# Synthetic shape only; no credential material goes in variables or inventory.
credentials:
  - name: backend-token
    source: /protected/operator-inputs/ha-backend-token
    source_uid: 1000  # Explicit actual controller file owner, not an assumption.
```

The observer's corresponding name is `gatus-<backend-id>.json`. Its existing
protected source must contain the strict username/password JSON expected by the
binary. HA's source contains its bounded HA bearer. This code does not read
protected sources during development or create fixtures for real enrollment.

At an authorized later run, controller source preflight checks an absolute
non-symlink regular file, link count one, exact 0600 mode and supplied owner.
Parents must be root/operator-owned, never group/world writable. Credential
copy and config rendering use no_log and diff:false. Never put these sources
in a repository, Chaldea snapshot, scratch fixture or logged variable output.
No SOPS, Authentik enrollment/admin credential or helper access is implemented.

The role creates a dedicated nologin system identity without a home. Existing
name/UID/GID collisions, supplementary group members, unowned package roots or
unit files are refused. Fixed config/release paths and every ancestor are
checked before writes; symlinks and writable parents are refused. An ownership
marker bounds later updates. Root owns the executable, directory and unit.
Runtime config and credential files are mode0600 and owned by the explicit
runtime UID/GID, inside a root-owned 0750 directory. The service loads them once
and restarts on config, credential, binary or unit changes.

The systemd unit has a read-only filesystem, no writable data paths, empty
capability sets, private tmp/devices, nologin HOME, no privilege gain, namespace
restrictions and CPU/memory/process/file limits. It pins the Debian system CA
bundle; protect and qualify that bundle and actual JWKS/backend TLS access on
the destination. The source requires Debian-family Linux amd64 with systemd.
It does not install a CA or disable TLS/host validation.

## Separate ingress contract

`web_mcp_route_enabled` defaults false. Required `web_mcp_route` fields are
`service, state, directory_uid`. For state present, add `resource, upstream`.
State absent removes only an existing owned route. Specify the real operator
UID of the existing edge file-provider directory; no default owner is assumed.
No service-host/edge-host co-location is assumed.

The fixed existing directory is
`/home/igor/apps/traefik/file_provider`; resolver is `letencrypt`.
The narrow role manages only `web-mcp-<service>.yml`, hot-reloaded by the
existing file provider. It checks directory ancestry and rejects an existing
file without the exact management marker. It never starts/reconfigures Traefik.

Upstream must be an explicit reachable private IPv4 origin with port.
HTTP carries these services' private backend traffic. If an independently
qualified HTTPS backend is supplied, Traefik's default CA verification stays
enabled; there is no insecureSkipVerify or Host rewrite. Route only exact /mcp
and /.well-known/oauth-protected-resource/mcp under the resource Host on
websecure. Preserve Host and Origin. There is no ForwardAuth or custom-header
middleware. Cf-Ray exclusion follows the existing private-ingress convention;
it is not a network allowlist. Firewall/private-network reachability still
needs runtime qualification.

Nested MCP names request exact-host certificates. The existing single-label
wildcard does not cover them. Certificate issuance needs separate authority.
HA /healthz and observer /livez, /readyz are never public routes. Probe the
explicit private backend with the canonical public Host, particularly for
observer probes. Do not change host checks or globally expose health to
simplify probing.

## Later scoped execution, upgrade and rollback

These are command shapes for the integration owner after concrete scope approval,
not commands run by this source task. Use an absolute standalone inventory path
that contains only the approved target aliases, outside production inventory.
The playbooks reject the repository default inventory, implicit host patterns,
missing enabled flags and selection of multiple hosts.

```sh
ansible-playbook -i /protected/approved/scope.yml playbooks/web-mcp-endpoint.yml \
  -e web_mcp_target=approved_service_alias -e @/protected/approved/endpoint-vars.yml --check
ansible-playbook -i /protected/approved/scope.yml playbooks/web-mcp-route.yml \
  -e web_mcp_edge_target=approved_edge_alias -e @/protected/approved/route-vars.yml --check
```

Set explicit enabled and activation flags in reviewed vars. Stage and verify
the binary before activation. Keep the route absent until the nonroot runtime,
file ownership, CA/DNS/JWKS/backend access, canonical Host/Origin, anonymous
refusal and bounded ACLs pass acceptance. Install the route in its own run.
Then verify actual loaded Traefik routers, exact paths and health exclusion.
A YAML render cannot prove that Traefik accepted a route.

For an upgrade, retain the old binary SHA, source/release pins and protected
config/credential version outside this repository. Run the same endpoint role
with the new reviewed pin and activation true; it restarts only that endpoint
after verification. For rollback, restore those exact old vars and protected
file versions and re-run. Releases are retained; there is no automatic garbage
collection or inferred rollback after an uncertain service result.

For retirement, remove the owned route first with the route role state absent,
verify it is gone from Traefik, then stop/disable that exact endpoint under
separate scope approval. Remove only files evidenced by the ownership marker
after confirming no consumers remain. This role does not delete users,
credentials, release directories or other owners' files.

OV packaging is deferred. After its semantic profile and source interface settle,
qualify an actual reviewed static release and its service-specific config,
ownership and health/authorization contract. Only then add an explicit profile
and tests to this mechanism in homelab-infra. There is no OV role, fake image
digest, network pip install or generic plugin/auth broker in this slice.

## Isolated validation

Run on agent-a from a minimal Git fixture, never the full infrastructure
checkout. Initialize a separate scratch Git repository, open it with Huyang,
and use guarded copy_file operations to copy only these paths from the source
revision under test:

- ansible.cfg and filter_plugins/web_mcp.py
- playbooks/library/web_mcp_guard.py and the two playbooks/web-mcp-*.yml files
- roles/web_mcp_endpoint/ and roles/web_mcp_route/
- scripts/test-web-mcp-packaging.py and this document

Commit that fixture and record its tree beside the original source commit/tree.
Exclude inventory/, secrets/, vars/, stacks/ and all protected inputs. Test
dependencies are pinned only for this fixture; uv creates its environment under
the scratch HOME. No production inventory, backend credential, SOPS input or
live host belongs in the snapshot.

```sh
chaldea exec --host agent-a -- sh -c '
  set -eu
  export HOME="$PWD/.cache/web-mcp-tests/home"
  export TMPDIR="$HOME/tmp"
  export PYTHONDONTWRITEBYTECODE=1
  export UV_CACHE_DIR="$HOME/.cache/uv" XDG_CACHE_HOME="$HOME/.cache"
  test "$HOME" = "$PWD/.cache/web-mcp-tests/home"
  test "$TMPDIR" = "$HOME/tmp"
  mkdir -p "$TMPDIR"
  uv run --no-project --with ansible-core==2.19.3 --with PyYAML==6.0.3 \
    --with Jinja2==3.1.6 python scripts/test-web-mcp-packaging.py
'
```

Tests exercise schema refusals, source/identity/bind/trust constraints,
qualified controls, Gatus credential references, no-follow/mode/parent checks,
route ownership refusal, actual templates, Ansible syntax, disabled no-op roles
and two-pass template idempotence on a synthetic local inventory. Full package
installation, systemd activation, private network/CA access, actual Traefik
loading, Authentik issuance, native clients and physical HA effects remain
runtime gaps. No fixture result is deployment evidence.
