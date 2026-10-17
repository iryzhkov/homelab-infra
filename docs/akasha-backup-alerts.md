# Akasha step-9 backup and alerts

Implementation only; these commands are for the orchestrator after review and
merge. No live configuration or token has been changed by this PR.

The owner is Igor, through `roles/backup`; the existing backup identity is
`iryzhkov` on homelab. `playbooks/akasha-backup.yml` includes only
`roles/backup/tasks/akasha.yml`, reusing
`/home/iryzhkov/.config/rclone/rclone.conf` and
`b2:igor-offsite-backup/apps/homelab/backups/akasha`. It neither runs the
broad backup role nor touches the NAS, Normandy or another backup tier.

The hourly snapshot and packager hold the same persistent
`/var/lib/akasha/backup.lock` through snapshot replacement or copy/hash
verification. SQLite integrity checking then uses the private copy outside
the lock, so it cannot delay the next snapshot. The hourly timer stays active. Never unlink or replace
the lock inode while either can run. Only the completed
`/var/lib/akasha/backup/events.db` is read. The live DB, WAL, SHM, partials,
seeds and independent verifier state never enter this package.

Packaging runs at 00:25 and 12:25 with up to five minutes jitter: nominal
disaster loss is <=12h05m + snapshot age (accepted <=2h) + transfer time,
within the proposed <=24h RPO.
Before upload, the job queries B2 using the existing key, requiring an
`allPrivate` bucket and default `SSE-B2/AES256`. Missing
`readBucketEncryption` permission or absent encryption fails closed. Stop
and ask the backup owner for a scoped proposal if that gate fails; this
procedure never edits bucket policy, encryption, credentials or lifecycle.
[Backblaze documents the encryption setting and read permission](https://www.backblaze.com/docs/cloud-storage-server-side-encryption).

The job copies to root:root 0700/0600 staging, uploads only `events.db` and
`manifest.json`, fetches both, verifies SHA-256 and SQLite integrity, then
reports success. The manifest records the **snapshot's original mtime**,
size and hash. Verification rejects snapshots >=24h old or from the future.
A local completion timestamp alone never proves off-host recovery. At 4 GiB,
packaging needs another 8 GiB plus 128 MiB free outside Akasha's state budget
for staging/readback. Work is removed after success/failure; the next run
cleans interrupted staging first. The existing bucket lifecycle retains
overwritten versions; this job never prunes them. At two changed 4 GiB
uploads/day, 30 days of hidden versions can approach 240 GiB.

Gatus on homelab owns `chaldea/akasha-backup` (2h silence),
`chaldea/akasha-offsite` (13h) and `chaldea/akasha-health` (3m, for its
1m timer). Each failed run immediately alerts Discord; success resolves it.
Offsite success requires the full readback and age proof; a stale source,
remote mismatch, failed transfer or encryption check reports failure.
A stopped timer/host is detected by heartbeat silence. The three tokens follow
PR #21: encrypted in `secrets/gatus-alerting.sops.yaml`, forwarded through
Gatus's existing private environment, and read from files on homelab.
`ExecStopPost=-` reports `SERVICE_RESULT`, including timeouts/signals;
delivery failure never changes unit status. The reporter disables redirects
and environment proxies and never logs exception text, tokens or records.
Homelab/Gatus/Discord outages can prevent delivery, as with the publisher.
A near-timeout 65min transfer can exceed the 13h silence window after a fast
previous run and cause an early alert; real success resolves it. This alerts
conservatively before the 24h RPO rather than hiding a missed shipment.

## Ordered orchestrator commands

1. Pin the reviewed merged infra checkout as `INFRA`. Complete rollout
   steps 1–8 first, leaving Akasha inactive. Keep shell tracing off and scratch
   on disk. Generate **no tokens during implementation/review**.

```sh
set -eu
set +x
: "${INFRA:?set reviewed merged homelab-infra checkout}"
export TMPDIR=/home/igor/.cache/chaldea-scratch/akasha-backup
mkdir -p "$TMPDIR"
chmod 700 "$TMPDIR"
cd "$INFRA"
test -z "$(git status --porcelain)"
```

2. With Huyang `workspace_open` for `$INFRA`, copy the encrypted
   `secrets/gatus-alerting.sops.yaml` to
   `$TMPDIR/gatus-alerting.sops.yaml` using `edit_apply kind=copy_file`
   in a documents workspace (destination outside the repository).
   Then run this editor against **that scratch ciphertext**; it preserves
   existing keys and generates only missing tokens in memory. Use the existing
   Gatus SOPS age identity; never put a token in argv or print decrypted YAML.

```sh
SOPS_EDITOR="python3 -c 'import pathlib,secrets,sys,yaml; p=pathlib.Path(sys.argv[1]); d=yaml.safe_load(p.read_text()); [d.setdefault(\"AKASHA_\"+n+\"_HEARTBEAT_TOKEN\", secrets.token_hex(32)) for n in (\"BACKUP\",\"OFFSITE\",\"HEALTH\")]; p.write_text(yaml.safe_dump(d))'" sops "$TMPDIR/gatus-alerting.sops.yaml"
```

   Import the resulting **ciphertext only** using Huyang's revision-guarded
   edit of `secrets/gatus-alerting.sops.yaml`. Review/commit/open the secret
   PR, merge and confirm CI. Check out that reviewed merged ciphertext before
   deployment. No token value belongs in the implementation PR.

3. Stage Akasha's updated maintenance helper, persistent lock and reporter.
   Provision homelab token files, then only its Gatus stack and narrow backup
   job. Do not run `playbooks/backup.yml`.

```sh
ansible-playbook playbooks/akasha.yml --limit homelab -e chaldea_access_trust_enabled=true --check --diff
ansible-playbook playbooks/akasha.yml --limit homelab -e chaldea_access_trust_enabled=true
ansible-playbook playbooks/akasha-heartbeat.yml --limit homelab --check --diff
ansible-playbook playbooks/akasha-heartbeat.yml --limit homelab
ansible-playbook playbooks/site.yml --limit homelab -e only=gatus --check --diff
ansible-playbook playbooks/site.yml --limit homelab -e only=gatus
ansible-playbook playbooks/akasha-backup.yml --limit homelab --check --diff
ansible-playbook playbooks/akasha-backup.yml --limit homelab
ansible homelab -b -m ansible.builtin.command -a 'stat -c %U:%G:%a /var/lib/akasha/backup.lock /var/lib/akasha-offsite /etc/akasha/heartbeat-backup /etc/akasha/heartbeat-offsite /etc/akasha/heartbeat-health'
```

   Require lock akasha:akasha:600 and staging root:root:700. The backup/health
   tokens are root:akasha:640; the independent offsite token is root:root:600. Provisioning is no_log/diff=false, including check mode.
   Routine Gatus convergence omits each unprovisioned endpoint. Before activation,
   health's 3m silence alert is expected; it resolves after the first real healthy
   run. Backup's 2h and offsite's 13h silence alerts are also expected if manual
   bootstrap is delayed that long; steps 4–5 resolve them with real runs. An
   already-failing silence state is not evidence of unit-failure delivery.

4. Make the first snapshot from the initialized events store, package it,
   and independently fetch/verify the off-host copy. No activation flags
   are set yet; neither helper needs the HTTP listener.

```sh
ansible homelab -b -m ansible.builtin.command -a 'systemctl start akasha-backup.service'
ansible homelab -b -m ansible.builtin.command -a 'systemctl start akasha-offsite.service'
ansible homelab -b -m ansible.builtin.command -a '/usr/local/sbin/homelab-akasha-offsite --verify'
ansible homelab -b -m ansible.builtin.command -a 'journalctl --no-pager -u akasha-backup.service -u akasha-offsite.service -n 40'
ansible homelab -b -m ansible.builtin.command -a 'stat -c %U:%G:%a /var/lib/akasha/backup/events.db'
```

   Require akasha:akasha:600 snapshot, successful units and a fetched
   `snapshot_time`, `age_seconds < 86400`, SHA-256 and integrity check.
   Record UTC time and the verification receipt. A failure blocks step 9.

5. Define the operator canary below. It stops the selected timer, requires
   a **real successful run and confirmed healthy Gatus state first**, then
   overrides only ExecStart under /run to fail. Confirm the new failure and
   recovery separately in Discord. This distinguishes unit failure from an
   earlier silence alert. No fabricated success heartbeat is sent.

   Run backup/offsite canaries now. Health cannot pass before the HTTP service
   is active; run the same health canary immediately after first activation in
   step 6, once the expected pre-activation health silence has resolved.
   Igor runs the canary interactively in **Bash**, in the same controller shell
   so the function remains defined; an agent hands these receipt prompts to him.
   For health's short 3m interval, an open-ended operator pause can itself cause
   silence. Refresh real success after the first prompt, force failure within
   2m, and accept only a Discord message timestamp after the override and within
   that 2m window. If any silence alert appears, discard the evidence and restart
   the canary from its real-success step; never count silence as unit failure.

```sh
printf '%s\n' '[Service]' 'ExecStart=' 'ExecStart=/usr/bin/false' > "$TMPDIR/akasha-rollout-canary.conf"
akasha_alert_canary() (
  UNIT="$1"
  RESTART_TIMER="${2:-false}"
  TIMER="${UNIT%.service}.timer"
  CANARY="/run/systemd/system/$UNIT.d/akasha-rollout-canary.conf"
  ansible homelab -b -m ansible.builtin.shell -a "test ! -e $CANARY"
  cleanup() {
    ansible homelab -b -m ansible.builtin.file -a "path=$CANARY state=absent"
    ansible homelab -b -m ansible.builtin.command -a 'systemctl daemon-reload'
    if [ "$RESTART_TIMER" = true ]; then
      ansible homelab -b -m ansible.builtin.command -a "systemctl start $TIMER"
    fi
  }
  trap cleanup EXIT HUP INT TERM
  ansible homelab -b -m ansible.builtin.command -a "systemctl stop $TIMER"
  ansible homelab -b -m ansible.builtin.command -a "systemctl start $UNIT"
  ansible homelab -m ansible.builtin.uri -a 'url=http://{{ ansible_host }}:3002/api/v1/endpoints/statuses return_content=true'
  read -r -p "Confirm healthy in Gatus (and resolved in Discord if previously alerted); press Enter: " RECEIPT
  # Refresh after the unbounded human pause; the following section has no prompts.
  ansible homelab -b -m ansible.builtin.command -a "systemctl start $UNIT"
  CANARY_SUCCESS_AT=$(date +%s)
  ansible homelab -b -m ansible.builtin.file -a "path=/run/systemd/system/$UNIT.d state=directory owner=root group=root mode=0755"
  ansible homelab -b -m ansible.builtin.copy -a "src=$TMPDIR/akasha-rollout-canary.conf dest=$CANARY owner=root group=root mode=0644"
  ansible homelab -b -m ansible.builtin.command -a 'systemctl daemon-reload'
  CANARY_OVERRIDE_AT=$(date +%s)
  test "$((CANARY_OVERRIDE_AT - CANARY_SUCCESS_AT))" -lt 90
  if ansible homelab -b -m ansible.builtin.command -a "systemctl start $UNIT"; then exit 1; fi
  test "$(($(date +%s) - CANARY_SUCCESS_AT))" -lt 120
  ansible homelab -m ansible.builtin.uri -a 'url=http://{{ ansible_host }}:3002/api/v1/endpoints/statuses return_content=true'
  printf 'Accept only a NEW Discord unit-failure message after %s and before %s (UTC).\n' \
    "$(date -u -d "@$CANARY_OVERRIDE_AT" +%FT%TZ)" "$(date -u -d "@$((CANARY_SUCCESS_AT + 120))" +%FT%TZ)"
  read -r -p "Paste that Discord message UTC timestamp (YYYY-MM-DDTHH:MM:SSZ): " RECEIPT
  RECEIPT_AT=$(date -u -d "$RECEIPT" +%s)
  test "$RECEIPT_AT" -ge "$CANARY_OVERRIDE_AT"
  test "$RECEIPT_AT" -lt "$((CANARY_SUCCESS_AT + 120))"
  cleanup
  trap - EXIT HUP INT TERM
  ansible homelab -b -m ansible.builtin.command -a "systemctl start $UNIT"
  ansible homelab -m ansible.builtin.uri -a 'url=http://{{ ansible_host }}:3002/api/v1/endpoints/statuses return_content=true'
  read -r -p "Confirm this endpoint resolved in Discord; press Enter: " RECEIPT
)
akasha_alert_canary akasha-backup.service
akasha_alert_canary akasha-offsite.service
```

6. With backup/offsite failures and resolutions received in Discord and the
   <24h remote receipt recorded, enable shipping. Complete step 9's remaining
   runtime/ACL/clock checks, then use its explicit activation commands.
   Run real health after activation, confirm its silence alert resolves, then
   exercise its failure/recovery route with the same canary and restart its timer.
   Do not advance rollout beyond this initial activation until that proof passes.

```sh
ansible-playbook playbooks/akasha-backup.yml --limit homelab -e akasha_offsite_enable=true --check --diff
ansible-playbook playbooks/akasha-backup.yml --limit homelab -e akasha_offsite_enable=true
ansible-playbook playbooks/akasha.yml --limit homelab -e chaldea_access_trust_enabled=true -e akasha_activate=true -e akasha_interfaces_verified=true -e akasha_backup_packaging_verified=true --check --diff
ansible-playbook playbooks/akasha.yml --limit homelab -e chaldea_access_trust_enabled=true -e akasha_activate=true -e akasha_interfaces_verified=true -e akasha_backup_packaging_verified=true
ansible homelab -b -m ansible.builtin.command -a 'systemctl start akasha-health.service'
akasha_alert_canary akasha-health.service true
ansible homelab -b -m ansible.builtin.command -a 'systemctl list-timers --all akasha-health.timer akasha-backup.timer akasha-offsite.timer'
ansible homelab -b -m ansible.builtin.command -a '/usr/local/sbin/homelab-akasha-offsite --verify'
ansible homelab -m ansible.builtin.uri -a 'url=http://{{ ansible_host }}:3002/api/v1/endpoints/statuses return_content=true'
ansible-playbook playbooks/akasha-backup.yml --limit homelab -e akasha_offsite_enable=true --check --diff
```

   Require all three Gatus endpoints healthy, all three timers active,
   remote age <24h, new Discord health failure plus resolution and changed=0
   on second check.
   Record the next scheduled offsite readback and its receipt within 13h.
   A stopped offsite timer alerts by 13h silence, before the 24h RPO even
   with the permitted 2h source age; a running job with a stale
   source/remote fails immediately. Operator receipt of silence alerts can be
   tested by stopping just the selected timer and waiting its heartbeat
   interval, then restarting it and running its service; record this only
   after a real test, never claim it from configuration alone.

## Isolated restore and rollback

Fetch `events.db` and `manifest.json` into a 0700 isolated scratch HOME with
umask 077 and the backup owner's existing rclone credentials. Run
`homelab-akasha-offsite --verify` first for current off-host age/hash/integrity.
For an isolated retained restore artifact, use these commands **on homelab as
root**, only after proving `/var/lib/akasha-restore-drill` does not exist:

```sh
test ! -e /var/lib/akasha-restore-drill
umask 077
mkdir -m 700 /var/lib/akasha-restore-drill
export HOME=/var/lib/akasha-restore-drill TMPDIR=/var/lib/akasha-restore-drill
test "$HOME" = /var/lib/akasha-restore-drill
rclone copyto b2:igor-offsite-backup/apps/homelab/backups/akasha/events.db "$HOME/events.db" --config /home/iryzhkov/.config/rclone/rclone.conf
rclone copyto b2:igor-offsite-backup/apps/homelab/backups/akasha/manifest.json "$HOME/manifest.json" --config /home/iryzhkov/.config/rclone/rclone.conf
python3 - <<'PY'
import hashlib, json, os, pathlib, sqlite3, time
root = pathlib.Path(os.environ["HOME"])
assert root == pathlib.Path("/var/lib/akasha-restore-drill")
manifest = json.loads((root / "manifest.json").read_text())
assert 0 <= time.time() - manifest["snapshot_time"] < 86400
assert (root / "events.db").stat().st_size == manifest["size"]
with (root / "events.db").open("rb") as db:
    assert hashlib.file_digest(db, "sha256").hexdigest() == manifest["sha256"]
with sqlite3.connect((root / "events.db").as_uri() + "?mode=ro&immutable=1", uri=True) as db:
    assert db.execute("PRAGMA integrity_check").fetchall() == [("ok",)]
    tables = [row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")]
    for kind in ("receipt", "cursor"):
        matching = [name for name in tables if kind in name.lower()]
        assert matching, "missing receipt/cursor schema"
        for name in matching:
            quoted = '"' + name.replace('"', '""') + '"'
            assert db.execute("SELECT COUNT(*) FROM " + quoted).fetchone()[0] > 0
    # Require populated receipts/cursors after C's admission canaries; no contents printed.
    print("isolated restore integrity/hash/size/age and populated receipt/cursor tables: ok")
PY
```

The helper removes its transient readback; the retained drill above is separate
and must stay private. Run this drill after C's admission canaries have populated
receipts and cursors; an empty initialized store deliberately fails that check.
After recording the isolated restore evidence, remove only the drill's files:

```sh
test "$HOME" = /var/lib/akasha-restore-drill
rm -- "$HOME/events.db" "$HOME/manifest.json"
rmdir -- "$HOME"
```
 Re-run lane-I and C's admission/ACK/crash evidence for
receipt idempotency, imported IDs/allocator and cursors against the isolated
copy before any actual restore. Preserve current independently verified
trust/revocation/clock high-water state; never restore access-state.db from
this event package. Stop intake/consumer, all Akasha timers and services before
installing a restored events DB; remove stale WAL/SHM only in that separately
approved cold restore. Refresh signed trust before admitting requests, then
replay JetStream with SQL uniqueness and expose sequence gaps.

If the current remote DB and manifest disagree after interrupted packaging,
do not use either unchecked. Fetch a matched previous pair with
`rclone --b2-version-at <RFC3339-before-failed-upload> copyto ...`, verify
the manifest hash/size/integrity and record its actual age; diagnose any >24h
RPO breach. Never hard-delete B2 versions or prune the bucket for this rollout.

Rollback stops/disables `akasha-offsite.timer` and waits for
`akasha-offsite.service` alongside Akasha's existing consumer/health/snapshot
units. Retain all local state, remote generations, lock and tokens; keep
feedd on :1934. Three days of shadow parity without differences are required;
the rollback observation remains seven days.
