# OV HTTP Compose source

The default-off personal owner/subject inputs are in
[the shared personal contract](ov-personal-contract.md). The accepted replacement
artifact is in [Explicit local config identity](#explicit-local-config-identity)
below. Only that exact local binding permits personal tools; inventory does not
enable them. The earlier personal binding document is historical source evidence,
not the current image selection or an activation receipt.

The standalone playbook playbooks/ov-http.yml and ov_http_compose role package
two closed iryzhkov/ov-mcp HTTP interfaces. The legacy reader contract remains
5f5c52c2171a2fef9ebe1439342921b1e58169bd. The full-resource contract is
1c80c90e02a50a168c7f7ef02fd7c9aebaeb8319. Build the Python 3.13 image with
the accepted source's Dockerfile and complete requirements-http.lock closure,
using a reviewed immutable official Python base. No image reference or production
input is supplied here. This extension permits later explicitly selected staging;
it does not select or activate the new profile on installed hosts.

The full Jocasta authoring guide was fetched at
jocasta:38a63cf8918eebae2dd7c8911eacd89c@44, content SHA256
e93cb3050f5e554dddcc92c0417ab68893653acf90077cb0f0c3531007d6ac06.
It grants no new authority. Jocasta, OpenCode and HoloNet retain separate owners
and enrollments. This source changes no credentials, tokens or native registrations.

## Deliberate single-host Compose boundary

The existing OV backend Swarm stack stays untouched. Inventory
group_vars/swarm/stacks-f3.yml:6-11 records why Jocasta stays on Compose:
Swarm's routing mesh publishes all worker interfaces and conflicts with its
private edge/firewall path. This wrapper uses an explicit private or loopback
host IPv4 and unprivileged port. Container listen is explicitly 0.0.0.0:8098.
There is no host networking, Swarm publishing, pin label, backend port/firewall
change or site.yml import. No compose_stacks/swarm_stacks inventory selects it.
Normal converges cannot activate this source.

The role reuses only the existing docker_compose_v2 ownership seam.
Docker access is pinned to the selected host's local Unix socket, including
preflight, one-shot validation and Compose convergence.
It does not invoke compose_stack's path adoption, secret/env or logging logic.
The Compose template has fixed project/service/container name ov-http and just
two protected read-only file binds. No Docker socket, host HOME/SOPS/SSH,
backend data, environment/inline key or writable root enters the container.
UID:GID is fixed 8098:8098, matching the image and mode0600 mounted files.
The role refuses a host NSS UID/GID collision and never creates/adopts an account.
The selected image must support the native host platform and numeric identity.

## Operator input contract

ov_http_compose_enabled defaults false. Enabling the role requires the exact
ov_http_package object below. This document supplies shapes, not an image asset
or production variables.

- release: unique bounded lowercase version ID. Never reuse it with changed inputs.
- listen: explicit private/loopback IPv4:port, with port 1024..65535.
- image: reference, source_revision, contract_revision, provenance.
- config: resource, issuer, jwks_url, backend_url, backend_secret_file,
  listen_host, listen_port.
- credential: protected controller source path and its actual source_uid.

With image.kind absent, image.reference requires an explicit image@sha256
distribution digest and refuses latest. Its existing grammar and semantics stay
unchanged. The only additional selector is image.kind: local-config, described below.
source_revision is the actual reviewed 40-hex ov-mcp build commit.
contract_revision selects exactly one of the two contracts:
- Legacy 5f5c52c2171a2fef9ebe1439342921b1e58169bd retains its existing
  40-hex source_revision acceptance and exact seven-field config.
- Full-resource 1c80c90e02a50a168c7f7ef02fd7c9aebaeb8319 accepts only
  source_revision 1c80c90e02a50a168c7f7ef02fd7c9aebaeb8319 or its published
  identical-tree merge 4cc776bc4f9a9211b1106f2ba3299d92e82690be.
  Both identify tree 651a695611641667e63b9b1d78e27b6b5387dc7b.
  Arbitrary future full-profile sources refuse.

provenance is an explicit HTTPS reviewed build-receipt URL. That receipt must
map the image digest to the reviewed ov-mcp commit/tree, lock blob, platform,
base digest and successful build. Inventory values and OCI labels assert that
mapping; they do not prove it. Review the receipt before selecting the image.
No registry publishing or credentials are implemented by this source.

Config fields exactly match ov_http/config.py. Resource is canonical public
HTTPS /mcp, issuer has its exact trailing slash, JWKS is fixed HTTPS and backend
URL ends exactly /api/v1. backend_secret_file is /etc/ov-http/backend-key,
listen_host is 0.0.0.0 and listen_port is 8098 inside the container.
The existing template renders the validated dictionary as sorted TOML keys using
JSON-compatible literals. Legacy config/output receives no normalization or
injected defaults and its rendered bytes remain unchanged.

The full-resource selector requires the same seven fields plus exactly these
four fields, all together and with these values:

```yaml
semantic_profile: resources-directories-v052-v1
semantic_backend_revision: 7f3ae1af822d936c1d0b070537772b0f1d59122f
namespace_roots: [viking://resources]
mutation_profile: resources-document-mutations-v1
```

Partial or mixed profiles, extra keys, alternate roots and unsupported revisions
refuse in the role's first validation task before managed writes. Legacy config
refuses all four additional fields. There is no generic claims, URL or key escape
hatch, and no separately selectable read-only semantic profile in this package.

The namespace ceiling is not a grant. Signed per-request resources/actions and
the fixed issuer/resource/helper contract remain separate. The backend revision
above identifies the existing search-path contract, not an attestation for a
patched backend artifact. Exact backend artifact qualification remains required.
The new v1 wire checks do not establish all 19 tools' runtime behavior.

Supply credential.source as an existing absolute protected controller file
outside this repository, snapshots and fixtures. The existing credential guard
checks every ancestor, final no-follow/regular/link-count/mode0600 and actual
source_uid before managed writes. Config, key copy and checksums use no_log
and diff:false for sensitive operations. Never read /srv/swarm/openviking/ov.conf
to manufacture this input. The destination key and TOML must actually be owned
by 8098:8098; root-owned mode0600 is refused by the nonroot reader.

## Explicit local config identity

This optional image object admits exactly one measured full-resource HTTP release:

```yaml
kind: local-config
reference: sha256:95cdf45c81564bb231c0c92088ab2d78272f59cb9aa648c60c0bf258d53e36d6
source_revision: d171897f64a839772686c9f6a3b07cfa9900cbf2
contract_revision: 1c80c90e02a50a168c7f7ef02fd7c9aebaeb8319
provenance: https://github.com/iryzhkov/ov-mcp/actions/runs/41162764846
```

All five fields must match together. The contract revision still names the
existing full-resource interface. Accepted publication source
d171897f64a839772686c9f6a3b07cfa9900cbf2 identifies tree
788ef69cb1b82a0a9d6437de157cd53a8c573b4f, equivalent to reviewed source
fe0cc7677b02ef00761d35244c2148dd1d601b5d. The closed object and image label
use the publication revision; neither revision enters the distribution-source
allowlist. This release gives default document writes bounded 60-second backend
I/O and a 70-second operation deadline, still capped by JWT expiry. Completion
is not guaranteed. Dry-run, deferred-write and reader behavior are unchanged.
Private metadata preservation, refusal of unknown dotfiles and directories,
public control-target refusal and global authorization are retained, as are
preflight diagnostics, the non-GET dispatch guard and in-flight authority recheck.
Source PR13 merged as d171897f64a839772686c9f6a3b07cfa9900cbf2 after
495 passing tests and full source CI. These are source checks, not native proof.
The [earlier personal binding record](ov-personal-qualified-release.md)
retains the prior source selection and planned operator procedure, not an
activation receipt or the current accepted HTTP image.
Other kinds, extra fields, canary/reader contracts and arbitrary release triples
refuse. The full config and resource/action/namespace restrictions above are unchanged.

The reference is the Docker image config ID. It commits to the immutable config
and ordered RootFS diff_ids; it is not a registry distribution manifest.
The retained linux/amd64 Docker save is 176904704 bytes, SHA256
72b8cd9c8710f7a781c266215360b26f5366ecbaf80c5e757ff57504fd32fb4b.
The publication ZIP is 176942971 bytes, SHA256
33d29f2585f048381d292ceb61ef652b8ea0f5eac9509d82e42a65ffdb7a894b.
Hosted custody and the independent operator artifact check verified all eleven
source inputs, nine embedded runtime source files, the full saved config and
all ten uncompressed diffIDs. No registry distribution manifest or signature
attestation is claimed. Compressed registry bytes are absent.
Artifact qualification does not establish target loading or runtime acceptance.

Before protected credential inspection, managed writes, check-config or activation,
the role uses docker_image_info on the fixed local socket and exact config ID.
Missing images or mismatched ID, revision label, User, entrypoint, command or
working directory refuse. Docker's immutable config ID supplies the config/RootFS
commitment; this role adds no parallel hashing store or signature authority.
A target store that reports a manifest/index ID instead of this config ID refuses.
Later target qualification must confirm the daemon identity semantics as well as Compose.
The image must already exist. This mode never pulls, tags, imports, installs or
logs into a registry. Offline check-config also uses --pull=never.
The regular activation retains pull:never and recreate:auto.

This bounded-write release retains the qualified personal and resource
profiles, including personal protocol pin 7f3faf6982f81116d6626bc7091f87f786a309c7.
The current source-only backend selection is recorded in
[the backend exact-artifact binding](ov-curator-backend-qualified-release.md).
The earlier backend config ID
sha256:91833db853e6dd1949b2810a2a4fdee6f56751354511a7b2e0b76bb7fa065f32
is historical and does not identify the new backend artifact.
Source tests render and validate the exact image binding; target staging,
activation and native dry-run acceptance remain separate operator checks.
No live refusal cause or successful native acceptance is established here.
It changes no issuer, subjects, grants, credentials, stdio registrations or
backend configuration. A successful dry run is not a successful curator pilot.
Historical canary and earlier backend delivery limitations are retained in the
prior receipts; they are not evidence about this release's live state.

### Historical metadata-preservation binding

The superseded HTTP selection used config ID
sha256:437580b5a2fa582497967c6ac0051214062de763e11a6d8e683f71e51bdb9817,
publication source cd2ad3bf4637d20ffaadb7c7c22105163152b82a, tree
c681ca5766d0d7a51ae656761ab18a0b2f18c42b and reviewed source
5c62ca394ef97019f79ce2e3984eab61c9f77c0b.
Its build receipt is https://github.com/iryzhkov/ov-mcp/actions/runs/41155827874.
Its Docker save was 176904192 bytes, SHA256
7325db9e906bb6e3dd1060842a085c3d215bbf6e82715439fb80cc9ab43c7336,
and its publication ZIP was 176942459 bytes, SHA256
c94503f002b7bff285ff2a43acab24bfd505dec5bc21a7424f1efc211405b311.
This historical custody record does not select that image or record activation.

## Stage, validate, activate and roll back

Use an absolute standalone inventory outside repository inventory, containing
exactly one approved host alias. Both plays check explicit target, enabled flag
and single-host scope, including when --limit skips the controller play.

```sh
ansible-playbook -i /protected/approved/ov-scope.yml playbooks/ov-http.yml \
  -e ov_http_target=approved_alias -e @/protected/approved/ov-vars.yml --check
```

ov_http_state defaults staged. Staging guards root-owned ancestors, exact markers
and allowed files, then creates /opt/web-mcp/ov-http/releases/<release>.
It never overwrites existing release files. It compares exact image/Compose,
TOML and credential checksums and refuses changed inputs under the same release.
An interrupted unmarked directory deliberately refuses adoption. Inspect its
evidence before separately authorized recovery; do not bypass the marker check.

Staging inspects fixed Docker names and project/network labels, refuses unowned
objects and any leftover check container, pulls only the selected distribution
digest in the legacy mode, then
runs --check-config as 8098:8098 with network none, read-only root, dropped
capabilities and the exact mounted files. This validates local schema, ownership
and key format, with no backend/issuer access. It does not start a listening
service or modify an active release. Check mode cannot establish this result.

Activation additionally requires ov_http_state=active and ov_http_activate=true.
The same staging/check sequence runs first. Only the fixed owned service is
converged with recreate:auto, pull:never and no orphan removal. An identical
release leaves it unchanged; a changed release uses new mount paths and
recreates only ov-http. No pip or other host package installation occurs.
The local log driver is bounded to 1MB/one file with compress=false for Docker29.

Before creating a public route, qualify the actual image's nonroot runtime,
DNS/hosts/CA traversal, fixed-JWKS verified TLS, backend reachability, private
health/discovery, canonical Host/Origin and anonymous refusal. No fixture or
inventory assertion proves those properties. Do not enroll issuer credentials
until the image/network gate is satisfied.

Apply ingress separately through playbooks/web-mcp-route.yml using its existing
single-target enabled flags and web_mcp_route.service=ov-http. The existing fixed
edge directory/resolver is unchanged. Upstream must be reachable private IPv4
with explicit port; loopback publishing needs a separately qualified local proxy,
since the route contract refuses loopback upstreams. Only exact /mcp and
/.well-known/oauth-protected-resource/mcp are public. Host/Origin are preserved,
forwarded authority stays ignored by the reader, and /health stays private.
Cf-Ray filtering is not an allowlist. Qualify loaded Traefik routes and network
access separately. OV is accepted only in route enums, never the static-ELF role.

Retain the prior image digest, release ID, source/build receipt, exact config and
protected credential version under operator custody. Rollback selects those
exact prior inputs with active+activation true; immutable files must match.
Do not delete old staged releases or replace the previous credential in place.
There is no inferred automatic rollback or new backend-data backup.

For retirement, first remove and verify the exact owned route in a separate
route run. Only then set ov_http_state=absent and ov_http_route_removed=true.
That flag records the operator's route-removal verification; it cannot inspect
the separate edge host. Removal checks the prior exact Compose bytes and fixed
project/network ownership before Compose down. It retains release/config/key
files, images and all accounts. No unowned paths or accounts are removed.
Protected release disposal is a later explicitly scoped operator action.

## Source validation versus runtime proof

CI's existing scripts/run-web-mcp-ci.py now allowlists this role, playbook,
test and only stacks/ov-http/compose.yml.j2 in addition to the existing web-MCP
sources. It runs both suites in its fresh protected root-owned fixture.
No inventory/, secrets/, vars/ or other stacks enter the fixture.
The tests retain original ownership guards and exercise root-owned-key refusal,
actual UID/GID checks, unsafe parents/links, unowned project refusal, bounded
route access, static-ELF separation, schema refusals, actual Ansible syntax,
template idempotence and disabled no-op/lifecycle refusal.
community.docker 5.4.0 is installed only in the disposable CI test environment.
It uses the documented [Compose module](https://docs.ansible.com/projects/ansible/latest/collections/community/docker/docker_compose_v2_module.html).

For Chaldea, create a minimal Git fixture using Huyang copy_file for the launcher's
exact FILES allowlist plus the launcher itself. Record every source blob and
the source/fixture tree. Set scratch HOME/TMPDIR before writes and install
ansible-core==2.19.13, PyYAML==6.0.3, Jinja2==3.1.6 and community.docker:5.4.0
inside the job. Run both suites via the launcher. Optionally set
OV_HTTP_COMPOSE_PARSE=1 where Docker Compose CLI is present to parse the actual
render without a daemon. YAML/Compose parsing remains source evidence only.
A source-bound job can set OV_HTTP_CONFIG_SOURCE to a separately supplied,
unchanged ov_http/config.py from accepted commit
1c80c90e02a50a168c7f7ef02fd7c9aebaeb8319, tree
651a695611641667e63b9b1d78e27b6b5387dc7b, SHA256
e02fc83629be65ebf40252450306f7787f1bc35b7f8cbfa017c77b73b7646fa9.
The focused suite checks that hash before importing and records the actual import
path/hash/pin. It loads actual Ansible-rendered legacy/full protected TOML and
checks URI policy, namespace admission and mutation selection. A synthetic
owner-only key under scratch HOME exercises secret loading through a temporary
Config copy; the loaded rendered config retains its fixed runtime key path.
Without this external input, ordinary packaging CI omits only the adapter-loader
check. The adapter validator is not vendored into this repository.

No test calls an actual Docker API or issuer/backend. In a single-ID Chaldea
namespace, changing ownership to UID8098 fails EINVAL; that case explicitly
simulates only the two regular leaves' numeric UID/GID metadata. Ancestor,
link/type/mode and root-owned-key refusal checks still use real filesystem data.
This branch is source guard coverage, never nonroot runtime proof.
Image build/nonroot execution, host network isolation, Traefik loading, issuer
enrollment and actual native-client/renewal behavior require later authorization
and qualification. A missing job daemon is reported, never installed here.
