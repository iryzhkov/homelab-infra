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
