# OV HTTP reader Compose source

The standalone playbook playbooks/ov-http.yml and ov_http_compose role package
the reviewed iryzhkov/ov-mcp HTTP reader interface at
5f5c52c2171a2fef9ebe1439342921b1e58169bd. The accepted source includes
the complete requirements-http.lock closure. Build the Python 3.13 image with
that repository's Dockerfile using a reviewed immutable official Python base.
No image reference or production input is supplied here.

The full Jocasta authoring guide was fetched at
jocasta:38a63cf8918eebae2dd7c8911eacd89c@1, content SHA256
b68c6ba2ca4ddc8d2293dc349cd9e3b95b87a00172b3820938f2ae86a0fa1ca4.
Its tested Jocasta/native-client results do not qualify this reader.

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

image.reference requires an explicit image@sha256 digest and refuses latest.
source_revision is the actual reviewed 40-hex ov-mcp build commit.
contract_revision must equal 5f5c52c2171a2fef9ebe1439342921b1e58169bd.
provenance is an explicit HTTPS reviewed build-receipt URL. That receipt must
map the image digest to the reviewed ov-mcp commit/tree, lock blob, platform,
base digest and successful build. Inventory values and OCI labels assert that
mapping; they do not prove it. Review the receipt before selecting the image.
No registry publishing or credentials are implemented by this source.

Config fields exactly match ov_http/config.py. Resource is canonical public
HTTPS /mcp, issuer has its exact trailing slash, JWKS is fixed HTTPS and backend
URL ends exactly /api/v1. backend_secret_file is /etc/ov-http/backend-key,
listen_host is 0.0.0.0 and listen_port is 8098 inside the container.
The role renders TOML using JSON-compatible string/int literals.
Semantic settings are deliberately unsupported by this packaging slice and
remain OFF. Do not upgrade the backend or treat an inventory assertion as
runtime/root-type qualification. The reader's separate opt-in profile at exact
68e66ec8998957e1af3cae8a661947fe77cd9578 still requires admitted namespace
directories and the documented native backend and immutable-root-type gates.

Supply credential.source as an existing absolute protected controller file
outside this repository, snapshots and fixtures. The existing credential guard
checks every ancestor, final no-follow/regular/link-count/mode0600 and actual
source_uid before managed writes. Config, key copy and checksums use no_log
and diff:false for sensitive operations. Never read /srv/swarm/openviking/ov.conf
to manufacture this input. The destination key and TOML must actually be owned
by 8098:8098; root-owned mode0600 is refused by the nonroot reader.

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
objects and any leftover check container, pulls only the selected digest, then
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
No test calls an actual Docker API or issuer/backend. In a single-ID Chaldea
namespace, changing ownership to UID8098 fails EINVAL; that case explicitly
simulates only the two regular leaves' numeric UID/GID metadata. Ancestor,
link/type/mode and root-owned-key refusal checks still use real filesystem data.
This branch is source guard coverage, never nonroot runtime proof.
Image build/nonroot execution, host network isolation, Traefik loading, issuer
enrollment and actual native-client/renewal behavior require later authorization
and qualification. A missing job daemon is reported, never installed here.
