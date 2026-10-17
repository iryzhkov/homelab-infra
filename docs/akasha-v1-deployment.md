# Akasha v1 rollout (source only, 2026-10-17)

Authority: Jocasta tooling/plans/chaldea-rebuild-plan-2026-10-16.md revision 20,
the step-7 brief jocasta:2d21001bcb0769a46f3331f858935cdb@1, and Igor's
2026-10-17 trust/timesyncd decisions. The implementation agent executes none of
the host commands below. The orchestrator performs every host action.

This branch combines PR #11 and PR #12 directly against main, carrying only
Akasha's required NATS foundation from parked PR #10. Garage, NAS mounts, work
and quota streams/consumers/identities are excluded. PR #10 remains parked.

## Hosts and prerequisites

Controller: **omarchy-pc**. Signing/publishing host: **omarchy-normandy**, the
always-on Arch machine, also named normandy in inventory, at 192.168.70.234.
Managed service host: **homelab**, owned
by homelab-infra, never fleet-config. **agent-a** is the sole issuer and initial
producer; its issuer identity/key and token helper are separately provisioned.
No changes to agent-b, laptop, Normandy's other roles, NAS or either HTTPS edge are implied.
All participating hosts retain systemd-timesyncd. Chrony remains supported;
there is no time-service migration.

Use the repository's existing private Ansible environment and verified SSH host
aliases on omarchy-pc. Prerequisites there: Ansible dependencies/collections, gh,
git, curl, openssl, tar/xz, Python/YAML and an operator NATS CLI context. Homelab
needs Python3, git, openssh-client, tar/xz, systemd/busctl and independent external
time sources. The playbook never reads, generates, copies or resets private keys.

Runtime and path pins are in vars/chaldea-runtime.yml, imported from the reviewed
fleet inventory. Keep them identical when updating. Node is the official
26.10.0 Linux x64 archive, SHA-256
ca70e9e349de048b9522abb3adc05b3bd6f43c5ffd3ec57916c7da292f59f022.
The archive is verified before extraction into root-owned /opt/chaldea/node.
Host code lives in /opt/chaldea-host/releases/VERSION/host and includes production
dependencies. No npm or source compilation runs on homelab.

The current matching host-v0.2.0 pin is the published timesyncd/Akasha release.
For later changes, the orchestrator tags reviewed main, waits for release CI,
then updates both inventories to that exact version and SHA-256. The publisher
procedure checks the installed Akasha capability before key creation. Never use
a floating ref, unpublished candidate or invented checksum.

The new deployment anchor is kid deployment-2026-10b. Its public JWK and SPKI
SHA-256 placeholders must be replaced identically in fleet inventory and
vars/chaldea-runtime.yml after generation on Normandy. Akasha config and the
publisher consume chaldea_access_trust_anchors. The mandatory check-trust-pins.py
guard compares the two inventories and rejects placeholders before activation.
The agent-a-2026-10 public issuer and three append-only ceilings are in fleet
access-trust/issuer-grants.json; vars/akasha-registry.yml fixes the corresponding
profiles/projects/source names and permitted event kinds. The proposed 30-day
issuance window is 2026-10-17T00:00:00Z <= iat < 2026-11-16T00:00:00Z;
verify_until is 2026-11-16T00:13:30Z. Do not extend an existing kid's deadlines.

The signed bundle lives at the root of private iryzhkov/chaldea-trust. Fleet main
is root-executed by ansible-pull and cannot be branch-protected on this GitHub
plan. Normandy therefore has write access only to the data repository, plus a
different read-only fleet-config credential for grants. The publisher's guard
allows only bundle.jws to change. Neither consumers nor publisher execute data
repository content. Both inventories pin chaldea_access_trust_repository and
chaldea_access_trust_bundle_path; check-trust-pins.py compares and validates them.
The trust role installs its reviewed downloader over host-v0.2.0's old transport
program, leaving the cryptographic runtime release pin unchanged.

## Exact ordered orchestrator commands

Run each numbered block only after its preceding checks succeed. Keep the same
shell on omarchy-pc for controller commands. The linked publisher procedure
creates a new deployment key only on Normandy. Keep the existing agent-a issuer
key; never regenerate it. No private key moves between hosts.

1. Complete [Normandy publisher steps 1–6](normandy-publisher.md#ordered-orchestrator-commands)
   in order. They stage the locked publisher on **omarchy-normandy**, generate a
   new anchor there, compare fleet/infra public pins, provision its unique write
   deploy key, initialize durable state once, publish and verify the first two
   envelopes, enable six-hour renewal plus hourly 12-hour age alerts, and retire
   the old omarchy-pc key. All commands are operator-only.

2. Require successful publication at chaldea-trust main:bundle.jws, kid deployment-2026-10b,
   increasing sequence and 86400-second lifetime. Record feed availability under
   the publisher UID. Alerts use the existing feed CLI if usable; otherwise
   journal only. Verify journal monitoring before proceeding in that case.

3. In the controller shell retain INFRA, FLEET, TMPDIR,
   CHALDEA_ACCESS_NODE and CHALDEA_ACCESS_RUNTIME from the publisher procedure.
   Freeze the reviewed Chaldea main commit as before and check out CHALDEA.
   Re-run the public-pin guard before any verifier staging:

```sh
: "${CHALDEA:?set reviewed Chaldea checkout}"
: "${CHALDEA_COMMIT:?set reviewed merged Chaldea commit}"
test -z "$(git -C "$CHALDEA" status --porcelain)"
git -C "$CHALDEA" checkout --detach "$CHALDEA_COMMIT"
python3 "$INFRA/scripts/check-trust-pins.py" "$FLEET" "$INFRA"
```

4. Stage Node/host runtime on **homelab** first, so explicit initialization can
   use the installed reviewed command. The runtime tag does not enable trust
   timers or Akasha. Then create homelab's unique root-owned transport key.
   These are operator actions; the playbooks never generate credentials.
   For existing key 166781554, first use the Normandy runbook's
   [transport move](normandy-publisher.md#move-an-already-staged-transport-to-chaldea-trust)
   to delete its fleet-config registration and add its public half read-only to
   chaldea-trust. Skip key generation/registration below when already provisioned.
   Preserve all initialized state; skip initialization in steps 5 and 8 if files exist.

```sh
cd "$INFRA"
ansible-playbook playbooks/akasha.yml --limit homelab --tags runtime --check --diff
ansible-playbook playbooks/akasha.yml --limit homelab --tags runtime
ansible homelab -b -m ansible.builtin.file -a 'path=/etc/chaldea-access-trust state=directory owner=root group=root mode=0755'
ansible homelab -b -m ansible.builtin.command -a 'ssh-keygen -t ed25519 -N "" -C chaldea-trust-homelab -f /etc/chaldea-access-trust/deploy-key creates=/etc/chaldea-access-trust/deploy-key'
ansible homelab -b -m ansible.builtin.file -a 'path=/etc/chaldea-access-trust/deploy-key owner=root group=root mode=0600'
# Fetch ONLY the public half to controller scratch; never fetch the private key.
ansible homelab -b -m ansible.builtin.fetch -a "src=/etc/chaldea-access-trust/deploy-key.pub dest=$TMPDIR/homelab-trust-deploy.pub flat=true"
gh repo deploy-key add "$TMPDIR/homelab-trust-deploy.pub" -R iryzhkov/chaldea-trust --title homelab-chaldea-access-trust
```

   Omit --allow-write: GitHub deploy keys are read-only by default. Confirm only
   chaldea-trust alone is granted. Do not use the operator's SSH identity.
   Homelab needs no fleet-config access for trust.

5. Pin GitHub's published Ed25519 host key and verify its fingerprint, then
   provision known_hosts and explicitly initialize root updater state on homelab:

```sh
printf '%s\n' 'github.com ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIOMqqnkVzrm0SdG6UOoqKLsabgH5C9okWi0dh2l9GKJl' > "$TMPDIR/github-known_hosts"
test "$(ssh-keygen -lf "$TMPDIR/github-known_hosts" -E sha256 | awk '{print $2}')" = SHA256:+DiY3wvvV6TuJJhbpZisF/zLDA0zPMSvHdkr4UvCOqU
ansible homelab -b -m ansible.builtin.copy -a "src=$TMPDIR/github-known_hosts dest=/etc/chaldea-access-trust/known_hosts owner=root group=root mode=0644"
ansible homelab -b -m ansible.builtin.file -a 'path=/var/lib/chaldea-access-trust state=directory owner=root group=root mode=0700'
ansible homelab -b -m ansible.builtin.command -a "$CHALDEA_ACCESS_NODE $CHALDEA_ACCESS_RUNTIME initialize-state --state-file /var/lib/chaldea-access-trust/updater.sqlite"
```

   Fingerprint/key source:
   [GitHub's published SSH fingerprints](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/githubs-ssh-key-fingerprints).
   Never trust unverified ssh-keyscan output. Initialization must fail if state
   exists; diagnose and preserve it. Never share updater state with Akasha.

6. Apply trust plus inactive Akasha staging from omarchy-pc. This enables root
   clock (10s) and updater (120s) timers only after bootstrap checks pass.
   Keep chaldea_access_trust_enabled=false in **fleet** inventory.

```sh
ansible-playbook playbooks/akasha.yml --limit homelab -e chaldea_access_trust_enabled=true --check --diff
ansible-playbook playbooks/akasha.yml --limit homelab -e chaldea_access_trust_enabled=true
ansible homelab -b -m ansible.builtin.command -a 'systemctl start chaldea-clock-health.service'
ansible homelab -b -m ansible.builtin.command -a 'systemctl start chaldea-access-trust.service'
ansible homelab -b -m ansible.builtin.command -a 'systemctl --no-pager status chaldea-clock-health.timer chaldea-access-trust.timer'
ansible homelab -b -m ansible.builtin.command -a 'journalctl --no-pager -u chaldea-clock-health.service -u chaldea-access-trust.service -n 40'
ansible homelab -b -m ansible.builtin.command -a 'stat -c %U:%G:%a /var/lib/chaldea-access-trust/updater.sqlite /var/lib/chaldea-access-trust-public/bundle.jws /run/chaldea-access-trust/clock-health.json'
ansible-playbook playbooks/akasha.yml --limit homelab -e chaldea_access_trust_enabled=true --check --diff
```

   Require healthy fresh <=30s clock evidence, the expected published sequence and
   expected signed expiry, root:root 0600 updater DB and root:root 0644 public
   files. The second check must report changed=0. Dry runs render/predict files
   and check bootstrap metadata but defer systemd reload/start until real apply. Unit ExecStartPre rejects
   unsafe runtime ownership, symlinks or writable ancestors. Clock supports
   timesyncd D-Bus; -/run/chrony is optional for hosts without chrony.

7. Provision the three required **public** NKeys in inventory/group_vars/swarm/nats.yml,
   with private seeds created and retained only by their owners: SYS sys-admin
   and CHALDEA chaldea-admin on omarchy-pc, CHALDEA akasha on homelab.
   The operator provisions /etc/akasha/nats.seed as akasha:akasha 0600 and the
   root:root 0600 /etc/akasha/runtime.env (empty if no extra environment is
   needed). No seed goes in Git, command output or a worker. Confirm
   nats_chaldea_hosts=[]; no host identities are supported in the shared account.
   After separately reviewing/committing the public NKeys:

```sh
ansible-playbook playbooks/swarm.yml --tags stacks --limit homelab -e only=nats --check --diff
ansible-playbook playbooks/swarm.yml --tags stacks --limit homelab -e only=nats
NATS_CONTEXT=chaldea-admin bash scripts/nats-jetstream-apply.sh --stream AKASHA_EVENTS --check
NATS_CONTEXT=chaldea-admin bash scripts/nats-jetstream-apply.sh --stream AKASHA_EVENTS
NATS_CONTEXT=chaldea-admin bash scripts/nats-jetstream-apply.sh --stream AKASHA_EVENTS --check
```

   Only AKASHA_EVENTS and akasha-ingest may be created. --all includes only
   that subset on this branch. Stream: file/Limits/discard-old, replicas 1,
   14d/1 GiB, 327680-byte maximum, max 8 consumers, 10m dedup. Consumer:
   explicit ACK, 60s ack wait, 10 deliveries, 1000 pending, max batch 500.
   Akasha publishes through its gateway registry; host JetStream API replies
   cannot bypass authorization because host identities are absent.

8. Initialize Akasha's **separate** verifier and event store once, as akasha.
   Staging created service-owned 0700 /var/lib/akasha. Do not initialize or
   restore over existing state. The config takes bundle/clock/verifier paths
   from the single chaldea_access_paths map and pins the public anchor.

```sh
ansible homelab -b --become-user akasha -m ansible.builtin.command -a "$CHALDEA_ACCESS_NODE $CHALDEA_ACCESS_RUNTIME initialize-state --state-file /var/lib/akasha/access-state.db"
ansible homelab -b --become-user akasha -m ansible.builtin.command -a "$CHALDEA_ACCESS_NODE /opt/chaldea-host/releases/$CHALDEA_HOST_VERSION/host/akasha-service.mjs init --config /etc/akasha/config.json"
```

9. Verify off-host backup packaging, runtime/ACL/clock contracts and alert
   routing below; record the evidence before setting the two verification
   flags. Then check and activate only homelab's reviewed service:

```sh
ansible-playbook playbooks/akasha.yml --limit homelab -e chaldea_access_trust_enabled=true -e akasha_activate=true -e akasha_interfaces_verified=true -e akasha_backup_packaging_verified=true --check --diff
ansible-playbook playbooks/akasha.yml --limit homelab -e chaldea_access_trust_enabled=true -e akasha_activate=true -e akasha_interfaces_verified=true -e akasha_backup_packaging_verified=true
ansible homelab -b -m ansible.builtin.command -a 'systemctl --no-pager status akasha.service akasha-health.timer akasha-backup.timer'
ansible homelab -m ansible.builtin.uri -a 'url=http://127.0.0.1:1935/healthz return_content=true'
ansible homelab -b -m ansible.builtin.command -a 'systemctl start akasha-backup.service'
ansible homelab -b -m ansible.builtin.command -a 'systemctl start akasha-health.service'
ansible-playbook playbooks/akasha.yml --limit homelab -e chaldea_access_trust_enabled=true -e akasha_activate=true -e akasha_interfaces_verified=true -e akasha_backup_packaging_verified=true --check --diff
```

10. Run disposable trust/clock negative canaries and real local admission
    canaries. Never change production time, delete high-water state, re-sign
    sequence 1 or edit the installed bundle for a negative test.

```sh
cd "$CHALDEA"
CHALDEA_STATE_DIR="$TMPDIR/exec-state" chaldea exec --host agent-a -- sh -c 'set -eu; export HOME="$PWD/.scratch-home" TMPDIR="$PWD/.scratch-tmp"; test "$HOME" = "$PWD/.scratch-home"; mkdir -p "$HOME" "$TMPDIR"; npm ci --ignore-scripts; scripts/with-scratch node --test test/unit/access-maintenance.test.ts test/unit/access-token.test.ts test/unit/timesyncd-health.test.ts'
cd "$INFRA"
ansible homelab -m ansible.builtin.uri -a 'url=http://127.0.0.1:1934/healthz status_code=200'
ansible homelab -m ansible.builtin.uri -a 'url=http://127.0.0.1:1935/api/v1/events status_code=401'
```

    The disposable suite covers signature tamper, same/lower sequence,
    removal/overlap rotation/restart, stale clock/offset >30s/rollback, and
    publisher loss to exact signed expiry. For live append, the issuer owner
    supplies a fresh short-lived append-only report bearer through an approved
    private file/channel, never a command argument or transcript. Use the local
    canary helper below on homelab, then repeat after restarting only Akasha.
```sh
# The orchestrator provisions a fresh report bearer at this service-private path.
# Use one stable key for both invocations and a new key for a different canary run.
(
  cleanup_canary() {
    ansible homelab -b -m ansible.builtin.file -a 'path=/var/lib/akasha/canary-token state=absent'
    ansible homelab -b -m ansible.builtin.file -a 'path=/usr/local/libexec/akasha-canary state=absent'
  }
  trap cleanup_canary EXIT HUP INT TERM
  # Copy as root, then use pipelined command; no unprivileged script/ACL dependency.
  ansible homelab -b -m ansible.builtin.copy -a 'src=scripts/akasha-canary.py dest=/usr/local/libexec/akasha-canary owner=root group=root mode=0755'
  ansible homelab -b --become-user akasha -m ansible.builtin.command -a '/usr/local/libexec/akasha-canary --token-file /var/lib/akasha/canary-token --key rollout-2026-10-17-1'
  ansible homelab -b -m ansible.builtin.command -a 'systemctl restart akasha.service'
  ansible homelab -b --become-user akasha -m ansible.builtin.command -a '/usr/local/libexec/akasha-canary --token-file /var/lib/akasha/canary-token --key rollout-2026-10-17-1'
)
```

    The existing issuer must bind the exact reviewed profile/project/source;
    no guessed token-helper executable is installed by this rollout.
    A successful PubAck alone is insufficient: require a visible durable receipt,
    then same-key retry => same event ID, changed payload =>409, malformed/
    tampered token =>401/403, report read =>403. Fleet/toolfeedback canaries
    must reject another source and forbidden kind. Record restart high-water
    persistence, fresh health and 24h expiry behavior. Agent-a producer cutover
    waits for separately reviewed issuer/helper and authenticated HTTPS ingress;
    this playbook binds only 127.0.0.1:1935 and changes no edge.

## Backup, RPO and restore

Proposed disaster RPO is **<=24h**, subject to Igor's confirmation and a
measured off-host shipping schedule/restore. No HA or zero-loss claim.
NATS state is `/srv/swarm/nats/data/jetstream` on homelab local disk
(container `/data/jetstream`); VM backups exclude it. Stream retention does
not protect against homelab disk loss.

The hourly backup unit uses SQLite's online backup API against events.db,
checks integrity and fsyncs a single 0600 snapshot at
`/var/lib/akasha/backup/events.db`. The snapshot includes events,
projections, receipts, cursors and outbox in the same database. It never
copies a live DB/WAL pair. It has 5min/4 GiB bounds and local disk reserve.

At the 4 GiB limit, retaining old + staging + live DB would exceed 10 GiB.
The helper therefore removes the prior local generation before making the
next. A failed/interrupted snapshot can leave no local snapshot: health
fails and the last off-host packaged generation is the fallback. Do not
enable this schedule without that verified fallback and backup-failure
alerts. Snapshot work is serialized by its systemd oneshot unit. First
snapshot is due within 10 minutes of activation; initial missing-backup
health is expected until it completes.

Integrate the snapshot path into homelab's **existing backup packaging**
with its owner, preserving 0700/0600 and encryption/access restrictions;
do not add a NAS mount or run the broad backup role. The backup package
must acquire a stable snapshot between hourly runs (stop the timer and wait
for the oneshot, or use an equivalent exclusion lock), copy/verify it and
resume the timer. Never package live events.db, SHM/WAL or temp files.
Prove that at least one off-host copy is <24h old and that failed/stale
packaging and `akasha-health.service`/`akasha-backup.service` failures reach
the existing operator alert channel. The local probe checks snapshot age,
HTTP and physical disk bounds; it cannot prove off-host shipping or detect
all ingest gaps. No new global Gatus/edge deployment is implied.

Restore in isolation first: stop intake/consumer and timers; restore a
consistent snapshot to private local disk; check integrity, receipt
idempotency, legacy IDs/allocator and cursors. Preserve independently
verified current trust/revocation/clock high-water state and refresh the
signed bundle before admitting requests. Replay the available JetStream
tail with SQL uniqueness; expose missing sequences as gaps. Restore tests
must include receipts and cursors, loss after PubAck and redelivery after
SQL commit. The lane-I test proves consistent WAL backup of receipts/cursors;
C owns end-to-end admission/ACK/crash recovery evidence.

## Parity and rollback

Keep feedd and its shipper through at least 7d measured parity and 7d
rollback observation. Step-9 retirement and shared-key rotation require
separate approval and a complete consumer inventory. To roll back this
service, stop/disable only akasha and its timers, retain its state and keep
feedd on :1934. Never delete streams, receipts or imported IDs as rollback.
Reverting the gateway ACL to host-publish permissions is not a safe rollback.
