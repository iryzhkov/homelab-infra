# Retained InfluxDB isolation

This explicitly coordinated cold transition separates the existing InfluxDB
2.9.1 database from Loki/Grafana. The 8087 ingress endpoint, existing
organization, buckets, retention policies, authorizations, image digest and
SOPS secrets remain the same. Grafana keeps `http://influxdb:8086` on the
existing nonattachable `logging_default` overlay. Both stacks declare that
network external; neither creates a replacement overlay.

Ordinary logging convergence refuses until the accepted isolation proof and
a running `legacy-influx_influxdb 1/1` exist. Ordinary legacy convergence
refuses empty, unowned, unaccepted or failed state, and refuses the original
service even at zero replicas because it still reserves port 8087. The transition is never called by normal
convergence, and defaults to refusing without
`legacy_influx_transition_confirm=true`.

Before the cold window, copy the two role-owned Python helpers to the private
root-owned `/var/lib/legacy-influx-transition/` on homelab and run
`sudo python3 /var/lib/legacy-influx-transition/influx-native-proof.py`.
This reads the running database, creates a native consistent backup, and
restores nonempty historical bucket samples into a unique scratch server
using the identical image, network none, zero published ports, 1 GiB memory
and half a CPU. It prints record counts and digests only. Tokens remain in
process memory/environment or mounted Docker secrets; no secret argv, source
file, response body or log output. Scratch setup and restore credentials are
independent of the live operator identity. A failed native restore is a
precondition failure and causes no live stop.

The manifest at `/var/lib/legacy-influx-transition/proof.json` must be owned
by root, mode0600, less than24h old. Copy its actual native archive with the
existing `/etc/swarm-sync/config.json` restricted rsync key to
`legacy-influx/snapshots/native/<filename>` on the peer. Read its SHA256
from that peer through normal operator SSH; add the verified absolute peer
path as `peer_archive` in the private manifest. The play hashes both actual
archives again and requires equality with the independently restored archive.
This does not fabricate an hourly sync stamp. A native archive previously
created by this helper can be retested with `INFLUX_PROOF_ARCHIVE=/absolute/path`;
archive members must be regular files/directories beneath `influxdb-backup`.

InfluxDB2.9 hashes API tokens by default. Its stock CLI exposes a
`--operator-token` flag for full restore but no environment equivalent.
To avoid putting an operator token in argv, this proof performs native bucket
data restores using an isolated token via stdin-to-environment. It proves
historical data recovery; it does **not** claim full native identity/token
restore. The cold transition copies the original bolt/config/data metadata
unchanged and compares organization, bucket retention and authorization
identity digests before/after. See
[official restore documentation](https://docs.influxdata.com/influxdb/v2/reference/cli/influx/restore/).

After the lead reviews the proof and clears the HA/Docker window, run:

```text
ansible-playbook playbooks/legacy-influx-transition.yml -e legacy_influx_transition_confirm=true
```

The play checks spare disk, source owner/task, an unused new owner, the original
deployed logging rendering and all fixed history samples. It retains that
exact rendering for recovery evidence, derives an Influx-only rollback with
the local log driver, and retains the native archive in the existing daily
native archive tier. A root-owned `native-backups` symlink exposes that tier
beneath the independently owned state to the standard hourly peer/offsite
mirror; the existing native daily job and Backblaze destination stay intact.
The backup selector refuses two running database tasks and supports both old
and new service names without putting its token in Docker argv. The retained
tier copy belongs to the backup login user with mode 0600 and a date-sortable
filename, so the existing user rclone job can read it and seven-archive
retention still expires it normally.

The play precopies while the original Influx task runs, checks the household
routes, sets only that service to zero with60s shutdown grace, waits for no
running task, then copies the final stopped delta with numeric ownership.
It removes only `logging_influxdb` to release its reserved8087 ingress port
and DNS alias, assigns the new owner and deploys only `legacy-influx`. Loki
and Grafana are not converged by the successful transition. It accepts only
identical fixed historical data, unchanged identities/retention, fresh real
HA writes after cutover, Grafana overlay alias health and healthy household
routes. Only then does it write the acceptance marker; subsequent logging
convergence can prune the old source entry safely.

On failure, rollback invalidates both acceptance markers, writes a persistent
failed-attempt barrier, removes the new service and confirms its tasks are
gone before deploying only the original InfluxDB definition without pruning.
The derived rollback uses local logs, retains the original Influx data mounts
and secret identities, and excludes Grafana/Loki, so their newer settings and
running tasks cannot be reverted by database recovery. The
original cold state, new state, native archive and peer archive are retained.
If the new database already accepted writes, those records remain in its
retained new directory even when the old directory is restored to service:
review and reconcile that bounded failed-window history before any retry.
The attempt marker is written before the first live stop; completed, failed
or interrupted markers make retries fail before any copy. Neither a cleared
owner marker nor a zero-replica service permits an automatic retry. An
operator must reconcile retained writes before clearing the barriers.
There is never permission for two active8087 writers. A rollback that cannot
establish exclusivity stops rather than starting the old writer blindly.

The independent legacy stack currently remains pinned to homelab, matching
the native daily job. Logging can move separately. Do not move legacy-influx
until its daily native backup scheduling and all direct HA/SMART/PVE client
endpoint assumptions have also been reviewed for owner changes. This role
does not delete buckets, revoke tokens, change writers or stop application
services. Existing bucket retention still expires records on its existing
schedule; all history retained at transition is preserved.

## Native recovery evidence,2026-10-13

The real consistent archive
`influxdb_20261013T172342Z-be593fe1.tar.gz` is940,658,804bytes,
SHA256`6cc8623f437cf5bf513c4a40be608bf1df3ee34d353730eb896cda9d1543a337`.
Isolated native restore matched fixed2026-10-12UTC historical samples exactly:
gatus2340, homeassistant7529, proxmox4859 and smart231 records. Both system
buckets returned no data for that day. Each nonempty sample is up to three
hourly last-values per original field/tag series; this is a recovery sample,
not an assertion that every stored point was exhaustively compared.

The same archive was copied through the existing forced write-only rrsync
transport and read back on normandy at
`/mnt/data/peer-snapshots/homelab/legacy-influx/snapshots/native/influxdb_20261013T172342Z-be593fe1.tar.gz`;
its actual byte count and SHA256 match. No freshness stamp was manufactured.
Scratch container and data were removed; the original InfluxDB container
`3e8fef8c190d` remained running. Preflight identity digest:
`93f02b541b8e0569421fd345f3efab26aa7bff20743c54bffd56df14734ce866`
(one organization, six buckets, three authorizations). The cold transition
has not been executed at this evidence point.

The rollback/retry audit added four production-template regression tests:
only Influx is rendered for rollback with local logging; accepted/failed/
interrupted markers refuse overwriting state; a preexisting zero-replica
service refuses a new transition; ordinary convergence requires true
acceptance and cannot bypass a failed-state barrier. Run these with the deployment venv:
`/home/igor/.local/share/venvs/ansible/bin/python roles/legacy_influx_transition/tests/test_transition.py`.
The SMART/GPU/PVE writer selector now names legacy-influx while preserving
the existing bootstrap ingress address and port.

## Post-cutover backup closure, 2026-10-13

The coordinated transition was accepted (85 ok, 22 changed, zero failures);
subsequent logging/Grafana convergence passed (33 ok, 4 changed, zero
failures). InfluxDB now runs as `legacy-influx_influxdb`, using local logs.
The daily native helper was reconciled on homelab, and the legacy stack's
snapshot definitions on both workers, through focused backup tasks. No
application service was restarted. An unrelated full-role proposal to change
the backup login's `.local/bin` mode was excluded; mode 0700 remains intact.

The first post-cutover native archive attempt exposed Ansible's default
symlink following: assigning root ownership to `native-backups` had changed
its existing archive directory's ownership. The role now sets `follow: false`
when managing the link; only that target directory's original backup-user
ownership was restored. A fifth production-template regression test guards
this behavior. The link remains root-owned; its target remains
`iryzhkov:iryzhkov`, mode 0755. The corrected source commit `e4e4a80`
passed its exact CI run.

The actual deployed native helper SHA256 is
`c72c949587567843ff052261dbf28bbd373dc0262e1a5aac169d10edcf9d88e3`.
It selected the new live service, completed native backup, and produced
`/home/iryzhkov/app/backups/influxdb/influxdb_2026-10-13_1151.tar.gz`:
941,286,930 bytes, 43 regular native backup files including one manifest.
Full gzip/tar validation passed. SHA256:
`8eac890af55cb63b3015464ef7c3f9b1be3e7c922fe76c786d73947b75ddd383`.
The existing seven-archive policy expired two older local archives normally.

A genuine hourly snapshot/mirror run exited zero. The corresponding peer
copy is
`/mnt/data/peer-snapshots/homelab/legacy-influx/files/native-backups/influxdb_2026-10-13_1151.tar.gz`
on normandy. Its actual byte count and SHA256 match the owner archive. Both
the peer `native-backups` directory and its archive are regular filesystem
objects, not copied dangling symlinks. The real transport produced
`1791918110 homelab` in `legacy-influx/sync/last-sync`.

Offsite recovery uses the existing native daily archive tier, whose resolved
directory is the target of `native-backups`. A real checksum upload and
Backblaze object readback verified
`b2:igor-offsite-backup/apps/homelab/backups/influxdb/influxdb_2026-10-13_1151.tar.gz`:
941,286,930 bytes, SHA1
`bba67d653f4ffcc6c88502981cff12c7757c8529`, equal to the local archive.
The hourly offsite job also completed; its existing scope is local native
snapshot directories and the other worker's peer copies. This fresh daily
archive's offsite proof is the daily tier above, not a claim that hourly
rclone directly traverses this owner's live state directory.

Influx health and all eight household acceptance routes returned 200 after
backup closure. The live Influx task remained `2cab5b961612`; no application
restart or bucket deletion was performed. This fresh archive passed structural
and transport validation. The earlier isolated restore evidence above proves
historical sample recovery; no new full identity/token restore is claimed.
