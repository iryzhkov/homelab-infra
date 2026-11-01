# Guarded web MCP endpoint packaging source

This slice packages reviewed HA, observer and catalog-only HoloNet interfaces
as static Linux amd64 binaries. It does not enroll identities, publish releases, obtain credentials,
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
| service | Only `ha-mcp`, `homelab-observer` or `holonet` |
| uid, gid | Explicit unused dedicated system UID/GID, integers 100..999 |
| listen | Explicit private/loopback IPv4 and unprivileged port, no wildcard |
| artifact | Required `url`, `sha256`, `source_revision`, `contract_revision`; optional `local_source` |
| config | Exact service-specific JSON object described below |
| credentials | Exact referenced list of `{name, source, source_uid}` objects |

No values or release checksums are supplied by inventory in this change.
The operator must supply the reviewed static binary's explicit HTTPS URL and
SHA256. There is no default URL or release-asset claim. The service source
repositories are `iryzhkov/homeassistant-mcp` and `iryzhkov/homelab-cli`.
No archives, mutable latest URL, source checkout, package installation or build
occurs on a service host. Review and record the binary SHA256, published source
commit, static amd64 build provenance and corresponding accepted contract first.
The role stages to a content-addressed directory, verifies the checksum and
static ELF format before installing config or switching the unit, then runs the
exact binary path. TLS certificate verification remains enabled.

For a private release already obtained and verified by the operator, add exactly
`artifact.local_source: {path, owner_uid}`. The four existing provenance fields
remain required and validated, including the explicit HTTPS release URL. No URL
fetch occurs when local_source is present; omission preserves the unauthenticated
HTTPS download path. No field accepts authentication data.

```yaml
artifact:
  url: https://github.com/iryzhkov/homelab-cli/releases/download/REVIEWED-TAG/homelab-observer-linux-amd64
  sha256: <reviewed-64-lowercase-hex>
  source_revision: <published-40-lowercase-hex>
  contract_revision: 8af24606b7cf5cd89e727660266104699313e993
  local_source:
    path: /protected/operator-inputs/homelab-observer-linux-amd64
    owner_uid: 1000  # Supply the actual controller file owner.
```

The local path is absolute canonical ASCII with letters, digits, underscore,
dot and hyphen components, at most 4096 bytes, with no empty, dot or dot-dot
components. owner_uid is an explicit integer 0..2147483647. Before any managed
host write, the controller opens every ancestor through held directory
descriptors with no-follow semantics. Ancestors must be owned by root or the
specified operator UID and never group/world writable. The source must be a
single-link regular file owned by exactly owner_uid, mode0600, size 64 bytes
through 256 MiB. The same bounded read must match the required SHA256 and contain
a little-endian ELF64 Linux/SystemV amd64 executable with a valid bounded program
table and neither PT_INTERP nor PT_DYNAMIC. No source contents enter task output.

The preflight also derives SHA1 from those same bytes and checks the open
descriptor metadata again after reading. Ansible copy receives that explicit
checksum and refuses different uploaded bytes before replacing the mode0555
destination. SHA1 is only the existing copy protocol's transfer check; SHA256
remains authoritative. A later source replacement can fail the deployment, but
cannot silently install different bytes without defeating the pinned transfer
digest. The independent target SHA256 stat and static ELF guard still run
before config, unit or activation. Keep operator custody of the protected source
through the run. No credential or arbitrary secret path belongs here.

In check mode the controller metadata, SHA256 and ELF checks still run without
managed writes. Copy predicts a change for an absent destination; target
installation and runtime checks remain unproven. Staging/activation flags and
the refusal to stage over a running or enabled service are unchanged. The
content-addressed root-owned mode0555 target is identical for both input modes.

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
changes and is accepted again. Observer publication is qualified at merged commit
[`db6f9ead377cded2a7229443c9daecf2c057c3b9`](https://github.com/iryzhkov/homelab-cli/commit/db6f9ead377cded2a7229443c9daecf2c057c3b9),
module `mcp-observer/`. The integration owner reported PR20 merged after
push39377683292 and PR39377687338 succeeded at exact e713583. The observer
interface remains accepted at 8af2460. This also establishes source publication
only; no binary release asset or deployment is claimed.

## Opt-in HoloNet managed recommendations source

The guarded installer also admits source f0620d641710b6da89b0cd04587b68a998fe22b4
with contract c0c429ed04aa06f46c5e0c639a4dcc000dde4431, the merged recommendation
membership interface. Historical catalog artifacts retain their original exact
contract and remain available for rollback. Writer configuration is rejected
with those older artifacts. Every build still needs its own reviewed SHA256,
static executable and custody proof; the source pin is not an activation.

The optional recommendations object requires the fixed state directory
/var/lib/holonet/recommendations, the dedicated catalog owner, bounded subjects,
namespace managed-recommendations, the same library/catalog-user ceiling,
the exact catalog.read/playlist.read/playlist.manage/object.share/home.read/
home.manage action tuple and an explicit recipient map. object.delete is the
only optional additional action. Each recipient has explicit baseline_ids and
an anchor_id contained in that baseline. No history permission is added.

The role guards existing state ancestors and directory ownership before any
managed write, creates the root-owned 0755 state parent and runtime-owned 0700
journal directory only when selected, and permits writes only to that journal
in the hardened unit. It does not read, rewrite, prune or remove existing
journal files. Omitting recommendations keeps the catalog unit and configuration
behavior and retains any old journal for explicit recovery.

The existing HoloNet issuer role accepts a separate writer grant only with all
three explicit holonet.read/holonet.curate/holonet.home scopes, a nonempty bounded
target list, the exact managed action tuple and empty history. Existing catalog
declarations and their historical read-only scope expression remain unchanged.
Composed scopes elect one mapping to emit the arrays once under Authentik's
deepmerge behavior. No live identity declaration or native helper is enabled by
these generic source contracts.

Source checks, real issuer/helper custody, named cohort and baseline ownership,
minimum production backend reach, exact reviewed artifact, deployment and
native client acceptance remain separate gates. In particular, the isolated
owner's deletion permission is not a production grant. No deployment, account,
grant, history collection, recurrence or live canary follows from this section.

## Opt-in HoloNet playlist operator management

The HoloNet config accepts an optional top-level `playlist_operator` object, the
Go service's management ceiling (homelab-cli `mcp-holonet/OPERATOR.md`). Absent
means off: no worker file, unit or socket is written. The object has exactly
`wire_version` (integer 1), `subject` (1..128 printable ASCII without space),
`namespace` (`managed-recommendations`), `library`, `catalog_user` and
`recipients` (1..16 unique IDs). It excludes `recommendations`. Issuer (at most
512 characters) and server are the config's own `issuer` and `server_id`.
The installer additionally pins homelab: `server_id`
3c86e6d7ff6c4a3296522cdf03d22bb0, library `3` and catalog user
54b7d36cef804d3c8478474553ed3a82, each also inside the existing read policy,
runtime UID/GID 940, and recipients drawn only from the seven provisioned
operator configs in `OPERATOR_RECIPIENTS`. Reader policy, credentials and the
existing `holonet.service` unit are unchanged.

Artifact pins: `HOLONET_OPERATOR_SOURCE` and `HOLONET_OPERATOR_CONTRACT` in
`filter_plugins/web_mcp.py` name the one reviewed build that understands the
key: full-lifecycle source `49022d6fc1cccb6f76c225a760b401cce2b115f6`, interface
`2cd99eb967690de97e9be01b411f4df119283d4c`. Both passed independent review and
hosted CI. Deploy the compatible worker first. Complete any unfinished lifecycle
request before downgrading the client, or use protected exact-request recovery. Older
binaries reject the unknown key. Existing exact catalog and writer pairs stay
accepted without the key and refuse it even when null. The build's SHA256 stays an operator input, as for
every other artifact.

That pair is the first-slice build (set-members and set-title). The worker
modules pinned in `OPERATOR_MODULES` now carry all seven list operations
(docs/emby-operator-mcp.md). Roll out in two reviewed changes: first the
worker pins with the first-slice pair unchanged, which that build uses exactly
as before; then, in a separate change after the worker is installed, the
reviewed full-lifecycle source and interface commits replace the pair. The
second change never ships ahead of the first, because a first-slice worker
refuses the new operations and a first-slice client would report a completed
new operation as an unknown outcome. Reverting the second change restores the
first-slice build against the new worker.

When the key is non-null the role, after the existing guards and before any
write, runs a metadata-only `playlist-operator` guard. It refuses symlinks,
wrong owners or modes, group or world writable ancestors, an unmarked existing
`/opt/holonet-playlists`, `/etc/holonet-playlists`, `/run/holonet-playlists` or
unit file, unit files without the managed header, a release whose files are
not exactly the pinned bytes, a socket parent other than `root:holonet 0750`,
and any selected recipient whose `journal/` directory (`0700`) or `config.json`
(`0600`, parent `0700`) is missing or unsafe. It never opens, lists or hashes a
journal or config and never touches the shared credential. Nothing is repaired
or adopted. Check mode runs the same guard and writes nothing.

Then it installs, all root-owned:

| Path | Mode | Content |
| --- | --- | --- |
| `/opt/holonet-playlists/releases/<release>/` | `0555` | the four pinned worker modules, `0444` |
| `/etc/holonet-playlists/policy.json` | `0600` in `0700` | root ceiling rendered from the validated config |
| `/etc/tmpfiles.d/holonet-playlists.conf` | `0644` | `d /run/holonet-playlists 0750 root holonet -` |
| `/etc/systemd/system/holonet-playlists-worker.socket` | `0644` | `Accept=yes`, `operator.sock`, `root:holonet 0660` |
| `/etc/systemd/system/holonet-playlists-worker@.service` | `0644` | hardened root worker, `ReadWritePaths=` the selected journals |

`<release>` is the SHA256 of the module manifest, so a repeat run changes
nothing and a module change gets a new directory. Older releases are kept.
Without `web_mcp_activate` the role refuses to stage over an active or enabled
socket and starts nothing. With it, the role creates the socket parent through
`systemd-tmpfiles` and enables and starts the socket. Removing the key later
stops and disables an installed socket and keeps the release, policy, configs,
journals and credential. docs/emby-operator-mcp.md describes the worker, its
units and the wire contract.

The synthetic tests cover the off default, old artifact refusal, the ceiling
and cohort, the plan, rendered units, the guard's refusals and its file access,
and check, apply and repeat of the staging tasks in scratch. Actual root custody
on homelab, `systemd-tmpfiles`, unit loading, the hardened service reaching the
socket, the worker running under its unit and native acceptance remain runtime
gates.

## HoloNet catalog-only boundary

The accepted HoloNet command/config interface remains homelab-cli
4aceab9770f0098b4369e37657f6b64051884baf (PR23 on PR22).
artifact.contract_revision must still equal that interface. The closed
artifact.source_revision allowlist admits that original build for rollback
and reviewed catalog/target-guidance build
31817f70f7fed5900622bc7274c6a8a31275189b. Its optional title/director/composer
queries and metadata preserve the existing default projection and catalog-only
policy. Authenticated tool descriptions expose only an unambiguous target
allowed by that request and operator policy, without a backend discovery read. No arbitrary source
revision is admitted. Every artifact still needs its exact reviewed SHA256
and all existing protected transport, static-byte and target checks.
The original artifact SHA256 is
1259cd1dcacff79454bf52533a2c009379137a9786a83c9c72fb30b8caa4dfdb;
the new Go1.25 Linux/amd64 artifact from agent-a
fb9f6763-50cd-4a58-b011-0a6c200064f0 is
8c482dff88ed227a494419fbcf9d0e58c91d1d7aa47b05f98f42ea394f0654c3.
These are build receipts, not a deployment or native acceptance claim.
For HoloNet with local_source, url may be omitted: an unpublished exact Chaldea
artifact does not have a release URL. If supplied it is still validated; no URL
fetch occurs. All descriptor, static-byte, transfer-binding and target gates
above are reused unchanged, not reimplemented. The artifact is not a secret;
backend credentials remain separate protected operator inputs.

Config requires exactly resource, issuer, jwks_url, server_id, backend_url,
credential_file, policy_revision, policy, library_guids. Resource is fixed
https://media.mcp.ryzhkov.dev/mcp. Canonical HTTPS issuer/JWKS; trusted HTTPS
backend /emby or exact http://127.0.0.1:8096/emby only. Server is hex32;
policy revision40..64hex. Policy has exact Server/Actions/Libraries/CatalogUsers/
HistoryUsers keys, server equal to configured server, Actions [catalog.read],
HistoryUsers empty, exactly one decimal library and one hex32 user. Private
operator inventory must establish those refer to Movies and the dedicated
catalog principal; source validates IDs, not human names. library_guids maps that decimal ItemId to hex32 virtual-folder
Guid, a distinct installed UserPolicy namespace. No UserData/history, playlist,
collection, home or mutation capability is enabled.

Credential path is fixed /etc/holonet/backend.json with the accepted binary's
strict single api_key string object, supplied out of band. No operator/admin
credential fallback is authorized. A least-privilege backend identity and actual
disabled/folder/tag/subfolder ACL qualification remain separate live gates.
The exact binary --check-config runs as dedicated holonet identity after config
installation and before credential copying or unit installation/activation;
it opens no runtime secret or network. Existing unit hardening/private bind and
owned exact MCP/discovery route are reused; health stays private.

Igor adopted catalog-only qualification first in plan
jocasta:3d249002623f32471f6b6bc617526f20@6. No runtime action follows from source.
P0 playlist/client/audience/cleanup gates remain unwaived. Actual target host,
UID/GID/port, protected backend identity, Authentik enrollment, helper/trust,
ingress/network/TLS and native renewal require separate concrete operator scope.
Activation packet jocasta:722e6f93b96e74c6291c3e741eab4641 owns those remaining gates.

## HoloNet scoped playback

The optional `playback` object enables the writer's `playback_statistics` and
`recipient_movies` tools (`mcp-holonet/PLAYBACK.md` in homelab-cli; contract
`jocasta:fe4aa946d677bdf308348aac626b869c@2`). Absent or null keeps today's
behavior: the catalog-only policy and `backend.json` alone.

Playback is accepted only with a non-null `playlist_operator` and only from
the reviewed build named by `HOLONET_PLAYBACK_SOURCE` and
`HOLONET_PLAYBACK_CONTRACT` in `filter_plugins/web_mcp.py`. They pin Go
`6c13126` and the compatible worker interface `cfea03b`. This build accepts
the numeric pagination echoes returned by the qualified Jellystat API. The final integration
review and exact artifact SHA256 remain required before activation. Older builds refuse the unknown key, so even
`playback: null` needs that build. The existing management and catalog pins
stay admissible for rollback without playback.

The object has exactly three fields:

- `jellystat_url`: `http://127.0.0.1:3012`, or a canonical HTTPS origin with
  no path, query, fragment, userinfo, default port or escape.
- `credential_file`: fixed `/etc/holonet/jellystat.json`, separate from the
  Emby `backend.json`.
- `credential_expires_at`: the writer app password's declared expiry, RFC 3339
  UTC with whole seconds. Keep it equal to the issuer declaration's
  `holonet_catalog_identity_expires` after any renewal.

With playback, `policy.Actions` is exactly `[catalog.read, history.read]` and
`policy.HistoryUsers` is 1..16 unique configured management recipients. Keep
it equal to the writer's `holonet_history_users` in the issuer declaration;
source cannot compare the two playbooks. `userdata.read` is never added. The
server, library, catalog user, subject, namespace, recipients and UID 940 stay
the existing management ceiling. The worker policy receives this explicit
history subset only when playback is configured. Existing policies omit it and
keep metadata reads disabled.

`credentials` then names exactly `backend.json` and `jellystat.json`, each
from its own protected controller source; the same source for both is
refused. The existing protected-source check, `no_log`/`diff: false` copy,
owner and mode 0600 as the runtime identity, and the guard's exact name and
mode checks apply to both. The Jellystat source is a minimal
`{"api_key": "..."}` JSON that root stages privately on the controller from
the existing nonadmin key `/srv/swarm/jellystat/janitorr-api-key` during
deployment. Source never reads or copies that key and never stages an admin
credential; the Emby backend stays the existing nonadmin key. The root worker
plan and policy, the endpoint unit (read-only paths, no new writable path,
empty capabilities, no new privileges) and the worker socket are unchanged.

## Service config and custody

HA config has exactly `listen, resource_url, issuer_url, jwks_url, ha_url,
backend_token_file, entities`. Resource is canonical HTTPS /mcp; issuer includes
its exact trailing slash and JWKS is pinned HTTPS. `ha_url` is a pathless
HTTP/HTTPS origin. `listen` must equal the top-level explicit bind.
`backend_token_file` is fixed to `/etc/ha-mcp/backend-token`.

Each of 1..64 entities has `id, name, actions` and optional
`control_qualified, brightness`. IDs are explicit light/switch/sensor/binary_sensor
entity IDs. Actions are state.read/light.control/switch.control; control requires
matching domain and `control_qualified: true`. A read-only entity must not set
control_qualified true. HA URL preflight also rejects explicit default ports,
trailing-dot hosts and noncanonical URL/escaped-path round trips before writes.
Optional light-control brightness
requires both integer min_percent/max_percent within 0..100. The service README
defines the physical qualification and uncertain-write limits; Ansible does not
qualify a device or grant access.

Observer config has exactly `resource, issuer, jwks_url, inventory_revision,
stale_after_seconds, hosts, services, gatus`. Its resource/trust URL constraints
retain the operator restrictions above. All observer URLs, including Gatus origins,
are limited to 512 UTF-8 bytes and refuse alternate escaped-path spellings. Revision is 40..64 lowercase hex; freshness is 1..86400 seconds.
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
name/UID/GID collisions, other members of the dedicated group, membership of
the service identity in any other NSS group, unowned package roots or unit files
are refused before writes. The primary GID is included in that membership check. Fixed config/release paths and every ancestor are
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
`/home/igor/apps/traefik/file_provider`; resolver is `letencrypt`. Both are
role-local constants, so standalone inventory needs no Traefik variables.
If inventory supplies traefik_dynamic_dir or traefik_cert_resolver, the role
cross-checks them against those constants before writes.
The narrow role manages only `web-mcp-<service>.yml`, hot-reloaded by the
existing file provider. It checks directory ancestry and rejects an existing
file without the exact management marker. It never starts/reconfigures Traefik.

Upstream must be an explicit reachable private IPv4 origin with port.
HTTP carries these services' private backend traffic. If an independently
qualified HTTPS backend is supplied, Traefik's default CA verification stays
enabled; there is no insecureSkipVerify. Route only exact /mcp
and /.well-known/oauth-protected-resource/mcp under the resource Host on
websecure. Preserve Host and Origin. There is no ForwardAuth or custom-header
middleware, with one service-specific exception below.

Route-only service `jocasta` serves the existing Jocasta Compose stack
(docs/jocasta.md). Its resource is fixed to
https://jocasta.mcp.ryzhkov.dev/mcp and its upstream to
http://10.42.0.2:1935, homelab's mesh address. As its HTTPS canary qualified,
that route alone adds a headers middleware that sets Host to the upstream
authority for the MCP SDK's host protection; Origin is preserved. Every other
service renders exactly as before, without middleware. Cf-Ray exclusion follows the existing private-ingress convention;
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

If initial staging fails after creating an endpoint root but before its ownership
marker, a later run deliberately refuses adoption. Under separate operator
scope, inspect the interrupted run's evidence and the exact partial directory.
Restore the marker only after establishing that all contents belong to this
package, or remove only evidenced files from that interrupted run. Do not bypass
the guard or delete an unmarked directory merely because its name matches.

For retirement, remove the owned route first with the route role state absent,
verify it is gone from Traefik, then stop/disable that exact endpoint under
separate scope approval. Remove only files evidenced by the ownership marker
after confirming no consumers remain. This role does not delete users,
credentials, release directories or other owners' files.

OV now has separate bounded Compose packaging source in [ov-http-compose.md](ov-http-compose.md).
It shares only the route role and guarded Compose seam. The static endpoint
profile includes HA, the observer and catalog-only HoloNet. OV image/runtime qualification
and deployment remain later operator gates.

## Isolated validation

Run on agent-a from a minimal Git fixture, never the full infrastructure
checkout. Initialize a separate scratch Git repository, open it with Huyang,
and use guarded copy_file operations to copy only these paths from the source
revision under test:

- ansible.cfg and filter_plugins/web_mcp.py
- playbooks/library/web_mcp_guard.py and the two playbooks/web-mcp-*.yml files
- roles/web_mcp_endpoint/, including library/web_mcp_local_artifact.py, and roles/web_mcp_route/
- scripts/emby_operator_{adapter,entry,recommendations,socket}.py, the pinned worker modules
- scripts/test-web-mcp-packaging.py, scripts/run-web-mcp-ci.py and this document
- .github/workflows/check.yml when inspecting or executing the packaging CI step

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
  uv run --no-project --python 3.11 --with ansible-core==2.19.13 --with PyYAML==6.0.3 \
    --with Jinja2==3.1.6 python scripts/test-web-mcp-packaging.py
'
```

The existing drift CI step invokes scripts/run-web-mcp-ci.py inside its disposable
root container. Mounted checkout ancestors may belong to the runner UID, so the
test sources are copied by an exact allowlist into a fresh root-owned
/var/cache/web-mcp-ci-<run-id>-<attempt>/ directory. The launcher validates numeric
run scope and protected container ancestors, asserts HOME/TMPDIR before writes,
and refuses any pre-existing destination, including links. It never changes
checkout ownership or production guards, cleans existing directories, or copies
inventory/secrets/vars/stacks. Test writes stay in that fixture's scratch HOME.
The container discards the fresh directory after the job. A CI-only
WEB_MCP_CI_FIXTURE_PARENT override supports read-only job namespaces; that parent
must exist outside the checkout, with every ancestor a root-owned directory
without group/world write permission. The real CI container defaults to /var/cache.

Tests exercise schema refusals, source/identity/bind/trust constraints,
qualified controls, Gatus credential references, no-follow/mode/parent checks,
route ownership refusal, actual templates, Ansible syntax, disabled no-op roles,
and two-pass template idempotence on a synthetic local inventory. Regressions
also cover fully mocked NSS supplementary groups, HA canonical URLs and read-only
qualification refusal, and the actual enabled route role with no inventory
Traefik variables. That role fixture redirects only its private output directory
and guard EDGE to scratch; tasks, resolver vars, template and guard remain actual
source, including owned route removal and inventory mismatch refusal. Full package
installation, systemd activation, private network/CA access, actual Traefik
loading, Authentik issuance, native clients and physical HA effects remain
runtime gaps. No fixture result is deployment evidence.

Local artifact fixtures additionally run the controller module and real staging
file/copy/stat/binary tasks against a sanitized package path and synthetic CA
file metadata. The job-mounted host CA ownership is not acceptable to the original
guard; the fixture redirects that fixed path and retains the UID0/mode checks.
Tests cover malformed ELF/checksum and protected-source refusals before package creation, fresh check,
apply, second no-op, installed check, omitted-local-source URL no-op, running or
enabled staging refusal, and a forced source mutation between preflight and copy.
Account creation, service facts and config/unit/activation are outside this
staging fixture. The job namespace maps only UID0, named job rather than root;
fixture write tasks use numeric UID/GID0 for the same production ownership.
Wrong source UID and synthetic
ancestor UID metadata are refusal evidence, not qualification of an actual
operator UID or runtime identity. Original production ownership checks are retained.
