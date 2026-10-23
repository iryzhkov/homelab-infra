# Jocasta working documents

Jocasta runs as a Docker Compose container on homelab, served by the existing
Traefik TLS ingress at https://jocasta.ryzhkov.dev. No Cloudflare Tunnel
hostname is added. The server validates per-host bearer tokens itself; Traefik
does not supply trusted identity. Port 1935 is restricted to the inventory's
edge_proxy mesh address with an owned DOCKER-USER chain. The backend binds
only homelab's Nebula address; Traefik reaches it over the existing encrypted
mesh. Both addresses are declared once in inventory/hosts.yml. The inter-VLAN
LAN service-port allowlist does not include Jocasta, and is left unchanged. Do not remove the firewall
unit while the container is running. jocasta-container.service orders container
startup after Nebula and its firewall, and retries failed binding while the mesh
interface becomes ready. It follows Docker and Nebula restarts without changing
startup ordering for unrelated stacks.

The live SQLite database is local at ~/app/jocasta/data/jocasta.db on homelab.
The NAS is NFSv3, which does not support the WAL shared-memory coordination
needed here. SQLite-aware immutable recovery points live under
/mnt/backup/jocasta instead. No raw live SQLite files are copied to the NAS.
All document content and history are in the database; Docker images and
credentials alone are not a recovery copy.

## Deploy

Build the server image from the exact Jocasta source commit that passed CI,
load it onto homelab with the tag
git.ryzhkov.dev/igor/jocasta:<full-source-commit>, and set jocasta_image in
the homelab inventory to that immutable tag; then
`ansible-playbook playbooks/swarm.yml --tags registry` pushes it to Forgejo
and pre-pulls it on normandy. Use the registry name, not a local-only tag such
as jocasta:<commit>: the weekly image prune removed that one on 2026-10-03.
Alternatively use an immutable registry digest. The playbook refuses mutable
tags.

Run from this repository with the controller's existing SOPS age key:

```sh
python3 scripts/jocasta-credentials.py
ansible-playbook playbooks/site.yml -l homelab -e only=jocasta
ansible-playbook playbooks/traefik.yml
ansible-playbook playbooks/backup.yml -l homelab
```

The credential generator is idempotent: it reuses the encrypted host-token
map on subsequent invocations. It provisions 0600 UpKeeper secret sources
under ~/.config/upkeeper/secrets/jocasta/<host> on laptop, homelab, Normandy
and omarchy-pc. Normandy has the authorized SSH key for omarchy-pc.
UpKeeper deploys the CLI and references these sources; no plaintext tokens
belong in its release manifest. To revoke a host, remove its map entry in
SOPS and deploy the stack configuration again. Rotate by replacing just that
host's token, then run the generator to update host secret sources and
converge UpKeeper. The server rereads the mounted token directory each request.

The backup role deployment installs the existing rclone exclude list changes
so twice-weekly copies do not capture live Jocasta database or token files.

## HTTP MCP (opt-in, off by default)

Jocasta can serve a read-only MCP resource at /mcp, verified against Authentik
with short-lived OIDC tokens (Jocasta's docs/http-mcp.md). It is off: the
jocasta stack's env_extra names only JOCASTA_IMAGE, so prepare.yml renders the
.env exactly as before and the Compose file passes the four MCP variables as
empty strings, which Jocasta treats as unset. Static per-host REST tokens are
unchanged either way, and opaque operator tokens never reach MCP.

The first converge after the Compose file gained those four empty variables
recreates the container once, because its environment changed. That restart
is the only effect while MCP stays off.

The canonical MCP resource is https://jocasta.mcp.ryzhkov.dev/mcp, the
dedicated namespace Igor chose for MCP services. The browser hostname
jocasta.ryzhkov.dev is not an MCP resource.

The only accepted MCP image is built from the reviewed, published Jocasta
source a3e83c55e73661fc50d92a9ca328236a86f291fd (MCP_IMAGE_REVISIONS in
filter_plugins/jocasta.py); its application tree equals the qualified
232c5e3. The deployed 37ce657515092be80c5d49b4a3b30f482d86ed63 image has no
MCP support and is refused. Accepting another commit is a reviewed source
change to that set.

To enable it, an explicitly approved deployment packet:

1. Loads the image built from a3e83c5, for example Jocasta CI's retained
   jocasta-image artifact, as
   git.ryzhkov.dev/igor/jocasta:a3e83c55e73661fc50d92a9ca328236a86f291fd and
   points jocasta_image in inventory/host_vars/homelab.yml at it, optionally
   with @sha256:<digest>, as described under Deploy.
2. Adds all four MCP keys to the jocasta entry's env_extra:

```yaml
  - name: jocasta
    env_extra:
      JOCASTA_IMAGE: "{{ jocasta_image }}"
      JOCASTA_MCP_RESOURCE_URL: https://jocasta.mcp.ryzhkov.dev/mcp
      JOCASTA_OIDC_ISSUER: https://auth.ryzhkov.dev/application/o/chaldea-jocasta/
      JOCASTA_OIDC_JWKS_URL: https://auth.ryzhkov.dev/application/o/chaldea-jocasta/jwks/
      JOCASTA_OIDC_AUDIENCE: <REST audience chosen by the packet>
```

Before anything on homelab is written, prepare.yml refuses:

- some but not all four keys, an empty value, or any other env_extra key;
- a resource other than exactly https://jocasta.mcp.ryzhkov.dev/mcp;
- an issuer or JWKS URL that is not canonical HTTPS on port 443, with no
  escapes or dot segments, an issuer without its trailing slash, or a JWKS URL
  on a different host from the issuer;
- an audience other than 1 to 256 characters from A-Z, a-z, 0-9 and ._~:/-;
- an image that is not jocasta:<commit> for the accepted commit.

The resource audience in MCP tokens is the resource URL itself, which the
Authentik machine-identities mapping issues as the tokens' aud
(docs/authentik-machine-identities.md). JOCASTA_OIDC_AUDIENCE is the separate
REST JWT audience. The packet chooses it and must not widen it to make a test
pass.

3. Installs the MCP route with the existing web_mcp_route role, service
   jocasta, in its own run (docs/web-mcp-endpoints.md, Separate ingress
   contract):

```yaml
web_mcp_route_enabled: true
web_mcp_route:
  service: jocasta
  state: present
  directory_uid: <actual owner UID of the edge file-provider directory>
  resource: https://jocasta.mcp.ryzhkov.dev/mcp
  upstream: http://10.42.0.2:1935
```

The route contract fixes both values for jocasta, so the route cannot send
Jocasta-audience tokens to another backend. It serves only /mcp and
/.well-known/oauth-protected-resource/mcp on jocasta.mcp.ryzhkov.dev, excludes
Cf-Ray requests, and requests an exact-name certificate from the existing
letencrypt resolver, because the *.ryzhkov.dev wildcard does not cover a
nested name. As the Jocasta HTTPS canary qualified, a Jocasta-only middleware
rewrites Host to the backend authority 10.42.0.2:1935 for the MCP SDK's host
protection and leaves Origin untouched; other services keep the public Host.
The backend is homelab's existing Compose port on the mesh, which
jocasta-firewall already restricts to the edge proxies. The jocasta.ryzhkov.dev
browser route is unchanged.

Apply the stack first, then the route, then check metadata and refusal:

```sh
ansible-playbook playbooks/site.yml -l homelab -e only=jocasta
ansible-playbook -i /protected/approved/scope.yml playbooks/web-mcp-route.yml \
  -e web_mcp_edge_target=approved_edge_alias -e @/protected/approved/jocasta-route.yml
curl -fsS https://jocasta.mcp.ryzhkov.dev/.well-known/oauth-protected-resource/mcp
curl -sS -o /dev/null -w '%{http_code}\n' -X POST https://jocasta.mcp.ryzhkov.dev/mcp  # 401
```

DNS for jocasta.mcp.ryzhkov.dev must resolve to the edge, and the exact-name
certificate must issue; neither is managed here. Runtime qualification must
confirm loaded routers, the certificate, metadata, anonymous 401, scope 403,
Origin refusal and project grants.

To turn MCP off again, remove the route first (web_mcp_route state absent),
then remove the four keys and rerun the stack. The container restarts with
MCP and OIDC unset; REST tokens keep working. Rolling
back the image as well needs the previous jocasta_image and its schema: if the
newer image migrated the database, older binaries refuse it, and rollback then
means a selected pre-upgrade recovery point (see Recovery).

## Backups and cold storage

The root system timer jocasta-backup.timer runs hourly. It uses Python's
SQLite online backup API to snapshot locally, checks integrity, compresses,
then fsyncs and atomically publishes a NAS recovery point. It refuses an
absent/non-NFS mount, destination outside that mount, less than 10 GiB NAS
headroom or inadequate local scratch space. A second mount check precedes
publication. Source database and tokens have restrictive permissions; NAS
snapshots are mode0600. Failures trigger the existing phone notification
client and leave a failed systemd unit.

Keep 48 hourly recovery points and 30 daily points (first successful snapshot
of each UTC day). Rotation deletes only older backups. Every retained snapshot
contains all revisions, aliases and receipts in its recovery point. No live
document history is deleted. The server automatically archives documents
after 30 days without an explicit read or update; archived documents remain
retrievable and discoverable by title/path. Reads do not unarchive them.

```sh
sudo systemctl start jocasta-backup.service
sudo systemctl status jocasta-backup.timer jocasta-backup.service
sudo journalctl -u jocasta-backup.service --since yesterday
sudo find /mnt/backup/jocasta/hourly -name '*.sqlite3.gz' -mmin -120
```

An hourly timer targets at most one hour of lost writes while healthy.
An independent hourly jocasta-backup-watch.timer checks freshness at minute45
and notifies when a recovery point is missing or older than two hours.
NAS snapshots are cold recovery copies, not independently browsable document
exports. RTO target is four hours under operator control; report measured
recovery time rather than asserting that the target has been proven.

## Recovery

Stop the service before replacing its database. Preserve the entire damaged
data directory, including WAL/SHM, under a separate local recovery path.
Select and record the backup UTC timestamp and possible write-loss window.
Gunzip the snapshot onto local supported storage, run SQLite integrity_check,
and start an isolated instance of the same pinned image against that copy.
Use the CLI to verify expected IDs/revisions, path aliases, historical content
and search before replacing production. Rebuild search only with the server
stopped, using the server's reindex operation if necessary.

Do not point SQLite at the NAS gzip file or at an NFS-uncompressed database.
Do not overwrite a running database or discard the damaged copy. Reconcile
clients for writes after the selected recovery point before reopening writes.
Successful integrity_check alone does not prove application-level recovery.

Tests in scripts/test-jocasta-backup.py verify a committed live-WAL revision
survives snapshot/compress/restore byte-exactly, backup file permissions,
rotation, and rejection of missing/non-NFS mounts without creating a shadow
local destination. Production restore evidence (2026-09-16): the NAS hourly recovery point
2026-09-16T082912Z was restored into a clean isolated v0.1.1 container on homelab
in 1.38 seconds. Verified document 3133d924af1d2d5a700baa55550c5170,
revisions 1–4, CRLF/Unicode/trailing-space bytes, original path alias, archive
state, original idempotent create receipt after later revisions, and successful
archived-name search after offline index reconstruction. The isolated container
and scratch data were removed; the NAS recovery point was retained.

The final v0.1.2 upgrade (8403174ead5c0c7c4680a5173a48fb310b1e7021) preserved
that fixture and migrated user_version to 2. Its automatic pre-migration database
was retained locally and copied to NAS under jocasta/migrations. The new
2026-09-16T083308Z NAS recovery point passed the same isolated restore checks
against v0.1.2 in 1.49 seconds. Older binaries refuse schema2; rollback must use
a selected schema1 recovery point and explicitly account for later writes.

Live checks also proved trusted HTTPS, unauthenticated API rejection (401),
new per-host token activation and revocation without process restart (200→401),
and refusal of a direct backend connection from a non-ingress mesh host.
The production image version and exact source pin are recorded in inventory.
