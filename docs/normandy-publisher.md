# Normandy trust publisher

Source only. Igor's 2026-10-17 decision moves publishing from the gaming PC to
the always-on Arch x86_64 machine **omarchy-normandy**, user igor, timesyncd active.
It is the same machine as the inventory's existing **normandy** host (VM 202,
192.168.70.234); the separate inventory name only scopes the signer play.
The signer play targets only chaldea_publishers/omarchy-normandy and installs no
agent workload. The orchestrator runs every command here and creates every key.

The locked system UID chaldea-publisher has no login, extra groups or sudo.
Its home, keys and state directories are 0700 under /var/lib/chaldea-publisher.
Root installs the program and independent public pin and GitHub known_hosts in /etc/chaldea-publisher.
Private keys never leave Normandy and have no backup anywhere. The fleet deploy key is writable only for
iryzhkov/chaldea-fleet-config. A write deploy key can technically write other
paths in that repository; the program's path guard constrains normal operation,
not an attacker who has stolen that credential.

Every six hours the oneshot serializes under state/publisher.lock, fetches main
into a bare Git repository, reads public grants as bytes, samples timesyncd and
signs the next sequence. It authenticates fetched bundle high water against the
independent anchor, takes the maximum with durable signer state, then adds one.
Malformed, nonregular or unauthenticated remote envelopes cannot advance state
or suppress age alerts, and a new valid publication replaces them. A separate durable verifier authenticates the
new envelope against the root-installed anchor. Git plumbing constructs exactly
one child commit from fetched main, with only access-trust/bundle.jws changed.
The path/parent guard runs immediately before an ordinary fast-forward SSH push.
No repository checkout, hook, filter or fetched program runs. A concurrent main
update fails the push; the next timer fetches again and uses a higher sequence.

Lifetime is 86400 seconds. The 6-hour cadence leaves 18 hours of outage tolerance
after a missed renewal. An hourly independent timer reports missing publication
or published_at older than 12 hours once per stale episode. Fetch/clock/sign/
verification/guard failures preserve the previous remote envelope and confirmed
local published.jws. Never reset either SQLite file after a failure. A push
transport timeout is ambiguous, so inspect fetched main before diagnosing it as
unpublished; the next run advances above either high water.

Alerts invoke the existing `feed post` CLI if it is available and usable by the
publisher UID through /usr/local/bin:/usr/bin:/bin. Every alert also goes to the
unit journal. If the CLI is absent or fails, the route is journal only and logs
`alert route=journal`. No feed credentials or igor home access are provisioned.
The implementation has not inspected Normandy. The operator must record the
actual route at deployment, and arrange journal monitoring if feed is unavailable.
Do not enable verifiers until renewal and the alert route have been checked.

## Ordered orchestrator commands

Run from the existing protected Ansible environment on the controller. Normandy
must already have Python3, git, OpenSSH, openssl, acl, systemd/busctl and timesyncd.
Verify its SSH host identity independently first. The signer alias scopes this play.
Freeze reviewed merged INFRA_COMMIT and FLEET_COMMIT, with successful CI.
The current matching runtime is host-v0.2.0 with the exact digest in both inventories.

1. Stage the runtime, locked UID, directories, program and inactive timers.
   The first apply accepts public placeholders because it cannot start renewal.

```sh
set -eu
export TMPDIR=/home/igor/.cache/chaldea-scratch/publisher
mkdir -p "$TMPDIR"
INFRA=/home/igor/Work/homelab-infra-normandy-publisher
FLEET=/home/igor/Work/chaldea-fleet-config-normandy-publisher
: "${INFRA_COMMIT:?set reviewed merged infra commit}"
: "${FLEET_COMMIT:?set reviewed merged fleet commit}"
test -z "$(git -C "$INFRA" status --porcelain)"
test -z "$(git -C "$FLEET" status --porcelain)"
git -C "$INFRA" checkout --detach "$INFRA_COMMIT"
git -C "$FLEET" checkout --detach "$FLEET_COMMIT"
cd "$INFRA"
ansible-playbook playbooks/chaldea-signer.yml --limit omarchy-normandy --check --diff
ansible-playbook playbooks/chaldea-signer.yml --limit omarchy-normandy
ansible omarchy-normandy -b -m ansible.builtin.command -a 'id chaldea-publisher'
ansible omarchy-normandy -b -m ansible.builtin.command -a 'passwd -S chaldea-publisher'
ansible omarchy-normandy -m ansible.builtin.command -a 'systemctl is-active systemd-timesyncd'
CHALDEA_HOST_VERSION=$(python3 -c 'import yaml,sys; print(yaml.safe_load(open(sys.argv[1]))["chaldea_host_version"])' "$INFRA/vars/chaldea-runtime.yml")
CHALDEA_ACCESS_NODE=/opt/chaldea/node/bin/node
CHALDEA_ACCESS_RUNTIME=/opt/chaldea-host/releases/$CHALDEA_HOST_VERSION/host/access-maintenance.mjs
ansible omarchy-normandy -m ansible.builtin.command -a "$CHALDEA_ACCESS_NODE --version"
ansible omarchy-normandy -m ansible.builtin.command -a "$CHALDEA_ACCESS_NODE /opt/chaldea-host/releases/$CHALDEA_HOST_VERSION/host/akasha-service.mjs --help"
```

2. Create a NEW deployment key as the publisher UID on Normandy, with umask 077
   and exclusive creation. Never copy the old gaming-PC key. Never export or
   back up this new private material to another host or password manager.

```sh
ansible omarchy-normandy -b --become-user chaldea-publisher -m ansible.builtin.shell -a 'set -euC; umask 077; openssl genpkey -algorithm ED25519 > /var/lib/chaldea-publisher/keys/deployment-2026-10b.pem'
ansible omarchy-normandy -b --become-user chaldea-publisher -m ansible.builtin.command -a '/opt/chaldea/node/bin/node --input-type=module -e '"'"'import {readFileSync} from "node:fs"; import {createPublicKey} from "node:crypto"; const {kty,crv,x}=createPublicKey(readFileSync(process.argv[1])).export({format:"jwk"}); console.log(JSON.stringify({kty,crv,x}));'"'"' /var/lib/chaldea-publisher/keys/deployment-2026-10b.pem'
ansible omarchy-normandy -b --become-user chaldea-publisher -m ansible.builtin.shell -a 'set -eu; openssl pkey -in /var/lib/chaldea-publisher/keys/deployment-2026-10b.pem -pubout -outform DER | sha256sum'
```

Only public JWK and SPKI SHA-256 are printed. Independently compare them.
Through guarded Huyang edits, set these same three variables in
**fleet inventory/group_vars/all.yml** and **infra vars/chaldea-runtime.yml**:

- chaldea_deployment_anchor_kid = deployment-2026-10b.
- chaldea_deployment_anchor_public_jwk = the public {kty, crv, x} object.
- chaldea_deployment_anchor_spki_sha256 = lowercase hex SHA-256 of public SPKI DER.

Keep chaldea_access_trust_anchors as the variable flow in both files. Akasha's
roles/akasha/templates/config.json.j2 already takes that variable, rendering
/etc/akasha/config.json anchors. The publisher's config takes the same resolved
anchor, independent of fetched main. Its bootstrap verifies the SPKI digest.
Review, commit, push and confirm CI for the public pin updates in both repos.
Freeze the new merged commits, check them out, and run this mandatory comparison:

```sh
python3 "$INFRA/scripts/check-trust-pins.py" "$FLEET" "$INFRA"
ansible-playbook playbooks/chaldea-signer.yml --limit omarchy-normandy --check --diff
ansible-playbook playbooks/chaldea-signer.yml --limit omarchy-normandy
```

The comparison checks runtime, paths, public JWK, kid, fingerprint and anchor
mapping, and refuses placeholders. Fleet trust enrollment stays false.

3. Generate the unique write deploy key on Normandy and register ONLY its public
   half with this one GitHub repository. Do not reuse an operator SSH identity.

```sh
ansible omarchy-normandy -b --become-user chaldea-publisher -m ansible.builtin.command -a 'ssh-keygen -t ed25519 -N "" -C normandy-chaldea-publisher -f /var/lib/chaldea-publisher/keys/fleet-deploy-key creates=/var/lib/chaldea-publisher/keys/fleet-deploy-key'
ansible omarchy-normandy -b --become-user chaldea-publisher -m ansible.builtin.fetch -a "src=/var/lib/chaldea-publisher/keys/fleet-deploy-key.pub dest=$TMPDIR/normandy-fleet-deploy.pub flat=true"
gh repo deploy-key add "$TMPDIR/normandy-fleet-deploy.pub" -R iryzhkov/chaldea-fleet-config --allow-write --title normandy-chaldea-publisher
gh repo deploy-key list -R iryzhkov/chaldea-fleet-config
ansible omarchy-normandy -b -m ansible.builtin.command -a 'ssh-keygen -lf /etc/chaldea-publisher/known_hosts -E sha256'
```

Require GitHub fingerprint
SHA256:+DiY3wvvV6TuJJhbpZisF/zLDA0zPMSvHdkr4UvCOqU, independently compared with
[GitHub's published fingerprints](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/githubs-ssh-key-fingerprints).
The role pins the matching key, never ssh-keyscan output. Repository rules must
permit this deploy key's single-path direct main push. If they reject it, stop
and resolve the policy explicitly; do not bypass the guard or use an operator key.

4. Initialize both durable state files ONCE, then manually start the same locked
   service used by the timer. Existing files cause initialization to fail.
   Never delete/reinitialize state or use sequence 1 to recover. Confirm no
   verifier has been provisioned before this first enrollment. A retired-kid
   envelope is unauthenticated under the new pin; only this first enrollment may start at 1.

```sh
CHALDEA_HOST_VERSION=$(python3 -c 'import yaml,sys; print(yaml.safe_load(open(sys.argv[1]))["chaldea_host_version"])' "$INFRA/vars/chaldea-runtime.yml")
CHALDEA_ACCESS_NODE=/opt/chaldea/node/bin/node
CHALDEA_ACCESS_RUNTIME=/opt/chaldea-host/releases/$CHALDEA_HOST_VERSION/host/access-maintenance.mjs
ansible omarchy-normandy -b --become-user chaldea-publisher -m ansible.builtin.command -a "$CHALDEA_ACCESS_NODE $CHALDEA_ACCESS_RUNTIME initialize-state --state-file /var/lib/chaldea-publisher/state/publisher.sqlite"
ansible omarchy-normandy -b --become-user chaldea-publisher -m ansible.builtin.command -a "$CHALDEA_ACCESS_NODE $CHALDEA_ACCESS_RUNTIME initialize-state --state-file /var/lib/chaldea-publisher/state/verify.sqlite"
ansible omarchy-normandy -b -m ansible.builtin.command -a 'systemctl start chaldea-publisher.service'
ansible omarchy-normandy -b -m ansible.builtin.command -a 'journalctl --no-pager -u chaldea-publisher.service -n 40'
gh api repos/iryzhkov/chaldea-fleet-config/commits/main --jq '.sha, (.files[].filename)'
ansible omarchy-normandy -b -m ansible.builtin.command -a 'stat -c %U:%G:%a /var/lib/chaldea-publisher /var/lib/chaldea-publisher/keys /var/lib/chaldea-publisher/state /var/lib/chaldea-publisher/keys/deployment-2026-10b.pem /var/lib/chaldea-publisher/keys/fleet-deploy-key /var/lib/chaldea-publisher/state/publisher.sqlite /var/lib/chaldea-publisher/state/verify.sqlite'
```

Require a successful push with only access-trust/bundle.jws changed, header kid
deployment-2026-10b, increased sequence and a 24h lifetime. The program has already
authenticated the exact bytes using install-bundle and the independent pin.
Inspect the public bundle via GitHub, with no private file in the transcript.
Record Normandy's actual alert route as the locked UID:

```sh
ansible omarchy-normandy -b --become-user chaldea-publisher -m ansible.builtin.command -a '/usr/local/libexec/chaldea-publisher --test-alert'
```

Require alert route=feed and the test record in the feed, or alert route=journal
and verified monitoring of chaldea-publisher unit journals before activation.
The interactive test prints stderr through Ansible; service failures and age
alerts use the same notifier inside their journal-connected units.

5. Enable both timers, run an immediate second renewal and the age check, then
   verify increasing published sequence, next timer deadlines and journals.

```sh
ansible-playbook playbooks/chaldea-signer.yml --limit omarchy-normandy -e chaldea_publisher_enabled=true --check --diff
ansible-playbook playbooks/chaldea-signer.yml --limit omarchy-normandy -e chaldea_publisher_enabled=true
ansible omarchy-normandy -b -m ansible.builtin.command -a 'systemctl start chaldea-publisher.service'
ansible omarchy-normandy -b -m ansible.builtin.command -a 'systemctl start chaldea-publisher-age.service'
ansible omarchy-normandy -b -m ansible.builtin.command -a 'systemctl list-timers chaldea-publisher.timer chaldea-publisher-age.timer --no-pager'
ansible omarchy-normandy -b -m ansible.builtin.command -a 'journalctl --no-pager -u chaldea-publisher.service -u chaldea-publisher-age.service -n 60'
```

Before ending rollout, persist chaldea_publisher_enabled=true in reviewed
inventory/host_vars/omarchy-normandy.yml through Huyang, merge and confirm CI,
then check out that reviewed source and repeat the play without -e. This keeps
later routine runtime updates from stopping both timers. A staging apply without
the persisted flag deliberately stops timers. Normandy's own outage requires
external host/journal monitoring, since no local timer can alert while it is off.
There are no active verifiers yet, so replacing the old pin is safe. Continue
Akasha rollout step 4 only after new publication and pin checks succeed.
For later loss or compromise, follow Re-issue the deployment anchor below; do not restart at 1.

6. After the new anchor is live, stop any old omarchy-pc renewal automation.
   On **omarchy-pc**, verify the retired key's public fingerprint and delete its
   key file. Igor removes the retired deployment-2026-10 entry from his password
   manager. Never transfer the new Normandy private key.

```sh
# Run only on omarchy-pc, after the new publisher and public pins are verified.
: "${RETIRED_SIGNING_KEY:?set the existing protected deployment-2026-10 PKCS8 path}"
test "$(openssl pkey -in "$RETIRED_SIGNING_KEY" -pubout -outform DER | sha256sum | cut -d' ' -f1)" = 222450438cdcae5bda6c498f5e39baee5e2479923389c28e518ccf9138aee296
rm -- "$RETIRED_SIGNING_KEY"
```

Retain old public records/history for audit. Do not restore the retired anchor.
The new key and deploy key stay solely in Normandy's protected publisher home.

## Re-issue the deployment anchor

The new private key has **no backup anywhere**. Loss means generating a new kid,
not restoring the old key. Compromise means the old kid is hostile, with no
overlap period. These are operator actions over independently verified SSH.
For compromise, rebuild or resecure Normandy before generating another key,
and revoke its old GitHub write deploy key, then repeat deploy-key enrollment
above with a new Normandy-only key. A replacement on a still-compromised host
does not restore trust. A rebuilt Normandy also needs a new SSH deploy key;
revoke its old GitHub entry and repeat the initial deploy-key enrollment before
resuming publication, for loss as well as compromise.

The runtime has no automatic anchor epoch reset. Its SQLite `trust.sequence`
is global across kids. New anchor signatures with a lower/equal sequence are
rejected. Preserve every updater/Akasha verifier DB, clock history and issuer
tombstone. Never reset those databases to make a new anchor work. The publisher
supports a reviewed `chaldea_publisher_sequence_floor`: every publication is
greater than that floor, its durable local sequence and authenticated remote
sequence. A new publisher DB can therefore recover without rolling consumers
back. If any retained sequence reaches 9007199254740991, park the cohort and
obtain a separately reviewed verifier-state migration. This runtime cannot
advance past that limit, and this helper will refuse it.

1. Freeze reviewed source, set a NEVER-used next kid, and stop renewal first.
   For both loss and compromise, park every enrolled verifier and updater before
   measuring state. No host may keep accepting the old anchor during compromise.
   The current cohort is homelab's root updater and Akasha; include any later
   enrolled fleet verifier/issuer before continuing. An unreachable member stays
   parked until its state is measured and its new pin is installed over SSH.

```sh
cd "$INFRA"
NEW_KID=deployment-2026-10c
# Existing Normandy only: skip these two stops if a rebuilt host has no units yet.
ansible omarchy-normandy -b -m ansible.builtin.command -a 'systemctl disable --now chaldea-publisher.timer chaldea-publisher-age.timer'
ansible omarchy-normandy -b -m ansible.builtin.command -a 'systemctl stop chaldea-publisher.service chaldea-publisher-age.service'
# If a prior recovery installed the emergency unit, stop it before measuring too.
ansible omarchy-normandy -b -m ansible.builtin.shell -a 'if systemctl cat chaldea-publisher-emergency.service >/dev/null 2>&1; then systemctl stop chaldea-publisher-emergency.service; fi'
ansible homelab -b -m ansible.builtin.command -a 'systemctl disable --now akasha.service chaldea-access-trust.timer'
ansible homelab -b -m ansible.builtin.command -a 'systemctl stop chaldea-access-trust.service'
# On a rebuilt Normandy, stage the locked UID/runtime/helper first with timers OFF.
ansible-playbook playbooks/chaldea-signer.yml --limit omarchy-normandy -e chaldea_publisher_enabled=false --check --diff
ansible-playbook playbooks/chaldea-signer.yml --limit omarchy-normandy -e chaldea_publisher_enabled=false
ansible homelab -b -m ansible.builtin.copy -a "src=$INFRA/scripts/chaldea-anchor-reissue dest=/usr/local/libexec/chaldea-anchor-reissue owner=root group=root mode=0755"
ansible homelab -b -m ansible.builtin.command -a '/usr/local/libexec/chaldea-anchor-reissue high-water --state-file /var/lib/chaldea-access-trust/updater.sqlite'
ansible homelab -b --become-user akasha -m ansible.builtin.command -a '/usr/local/libexec/chaldea-anchor-reissue high-water --state-file /var/lib/akasha/access-state.db'
```

   If Normandy state remains usable and trustworthy, also read both receipts:

```sh
ansible omarchy-normandy -b --become-user chaldea-publisher -m ansible.builtin.command -a '/usr/local/libexec/chaldea-anchor-reissue high-water --state-file /var/lib/chaldea-publisher/state/publisher.sqlite'
ansible omarchy-normandy -b --become-user chaldea-publisher -m ansible.builtin.command -a '/usr/local/libexec/chaldea-anchor-reissue high-water --state-file /var/lib/chaldea-publisher/state/verify.sqlite'
```

   Record the maximum of ALL trusted receipts as FLOOR. Do not derive FLOOR from a remote old-kid envelope during compromise.
   Keep the cohort stopped so that old-anchor high water cannot race this
   measurement. A missing consumer DB needs diagnosis, not initialization.

2. Generate the next key on Normandy as the locked UID. The helper refuses an
   existing destination or symlink and refuses the currently configured kid.
   Never reuse ANY retired kid, even when its key file is gone. The helper
   only knows the currently configured kid and existing files; checking Git's
   public history for every older kid remains an operator requirement.
   A failed genpkey may leave an incomplete file. Do not overwrite it; diagnose
   it as the publisher UID and use another never-used suffix.
   It prints only the three public pin variables. No key is transferred,
   backed up, placed in a password manager or printed.

```sh
ansible omarchy-normandy -b --become-user chaldea-publisher -m ansible.builtin.command -a "/usr/local/libexec/chaldea-anchor-reissue generate --kid $NEW_KID"
```

3. Through reviewed PRs, update kid, public JWK and SPKI SHA-256 identically in
   fleet `inventory/group_vars/all.yml` and infra `vars/chaldea-runtime.yml`.
   Update any test expectation of the previous concrete kid in those pin PRs.
   In infra's publisher-only host vars, persist integer
   `chaldea_publisher_sequence_floor: FLOOR` and keep
   `chaldea_publisher_enabled: false` during recovery. Neither variable changes
   consumer state. Merge the pin PRs, confirm CI and check out those exact
   commits on the controller before applying. Compromise removes the old anchor
   entirely from every deployed anchor list, including Akasha's config.

```sh
python3 "$INFRA/scripts/check-trust-pins.py" "$FLEET" "$INFRA"
ansible-playbook playbooks/chaldea-signer.yml --limit omarchy-normandy -e chaldea_publisher_enabled=false --check --diff
ansible-playbook playbooks/chaldea-signer.yml --limit omarchy-normandy -e chaldea_publisher_enabled=false
```

   Keep usable publisher.sqlite and verify.sqlite. Only if a file is missing,
   corrupt or untrustworthy after compromise, quarantine that unusable file on
   Normandy while services are stopped, then run the initial procedure's
   `initialize-state` command for that file once as chaldea-publisher. Never
   overwrite or initialize a healthy existing file. No private-key backup is
   involved. The measured, deployed FLOOR is required even with new empty state.

4. Publish once while timers and consumers remain stopped. Loss uses normal
   grants. Compromise MUST publish the emergency empty-grants envelope first.
   The helper checks the requested kid and measured floor against deployed
   config; it does not replace the operator's merged-PR/CI check.
   Use only one of these commands, with FLOOR set to the measured integer:

```sh
: "${FLOOR:?set maximum trusted retained high-water receipt}"
# Loss:
ansible omarchy-normandy -b -m ansible.builtin.command -a "/usr/local/libexec/chaldea-anchor-reissue resume --kid $NEW_KID --sequence-floor $FLOOR"
# Compromise instead:
ansible omarchy-normandy -b -m ansible.builtin.command -a "/usr/local/libexec/chaldea-anchor-reissue resume --kid $NEW_KID --sequence-floor $FLOOR --emergency-empty"
```

   If replacement enrollment takes over 24h, the emergency envelope expires and
   consumers refuse everything. Re-run the same emergency resume command to
   renew the empty envelope at a higher sequence while keeping normal timers
   off. This never reactivates old issuers.

   Emergency mode uses the same flock, fresh clock, independent verification and
   single-path push guard, with keys=[] and 24h lifetime. Normal renewal still
   rejects empty grants. Keep timers off. Empty publication permanently
   tombstones every old issuer in preserved consumer state. Before later normal
   renewal, review/merge replacement issuer kids AND public key material in
   fleet main's grants; never reintroduce the old issuer identities or material.

5. Install the new pin out of band on every parked consumer. On homelab, apply
   the trust role first, fetch the new publication, then update/restart Akasha.
   Reuse the previously accepted activation flags only after their original
   runtime/backup prerequisites still hold. No database is initialized here.

```sh
ansible-playbook playbooks/akasha.yml --limit homelab --tags runtime,trust -e chaldea_access_trust_enabled=true --check --diff
ansible-playbook playbooks/akasha.yml --limit homelab --tags runtime,trust -e chaldea_access_trust_enabled=true
ansible homelab -b -m ansible.builtin.command -a 'systemctl start chaldea-clock-health.service'
ansible homelab -b -m ansible.builtin.command -a 'systemctl start chaldea-access-trust.service'
ansible-playbook playbooks/akasha.yml --limit homelab -e chaldea_access_trust_enabled=true -e akasha_activate=true -e akasha_interfaces_verified=true -e akasha_backup_packaging_verified=true --check --diff
ansible-playbook playbooks/akasha.yml --limit homelab -e chaldea_access_trust_enabled=true -e akasha_activate=true -e akasha_interfaces_verified=true -e akasha_backup_packaging_verified=true
ansible homelab -b -m ansible.builtin.command -a 'systemctl restart akasha.service'
ansible homelab -b -m ansible.builtin.command -a 'journalctl --no-pager -u chaldea-access-trust.service -u akasha.service -n 60'
```

   Require the new header kid, sequence > FLOOR, expires_at-published_at=86400,
   successful updater receipt, and Akasha health after restart. Read high-water
   again from BOTH homelab DBs and every other cohort member; require each to
   match the new publication. Restart reloads Akasha's configured anchors, which
   its 120s bundle poll alone does not do. Test an old-anchor envelope in
   disposable state to confirm rejection. On compromise, confirm all old
   issuer bearers are refused before introducing replacement issuers.

   The homelab helper copy is temporary and used only for these read-only
   receipts. After every consumer receipt is recorded, remove that one-off copy
   with Ansible file state=absent; keep Normandy's role-managed helper installed.

   Only after all receipts and, for compromise, replacement issuer enrollment,
   persist publisher enabled=true through a reviewed PR and reapply the signer
   play. Run a second renewal and verify increasing sequence, expiry, timers
   and the alert route as in the initial procedure. Delete the retired key file
   on Normandy if it still exists. Never retain or restore it as a backup.
