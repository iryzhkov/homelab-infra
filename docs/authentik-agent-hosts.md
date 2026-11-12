# M3 host identities: creation and coexistence

The sole creation entrypoint is `playbooks/authentik-agent-hosts.yml`. It runs
the host tasks of `authentik_machine_identities`, bypassing the legacy blueprint
writer. Legacy playbooks, accounts, credentials, applications, human identities
and PKI remain in place. Normandy is reserved for P3.3 and is refused here.

The closed contract in `module_utils/agent_host_contract.py` is the target
matrix from survey `jocasta:bb2fc17fa5ceaa265efbdb7c4c3d9ac0@1`.
It includes the live seven HoloNet recipient IDs, rather than optional public
declaration defaults. Protected input selects hosts and lifecycle metadata; it
cannot add grants. The optional separate rotation provider and its reserved
attribute slot are described in [host credential rotation](host-credential-rotation.md).

| Host | Grants |
| --- | --- |
| laptop | Jocasta operator read/write; OV resources read/write/remove/maintain and default/default memory.read, memory.write, session.read, session.create, session.append; HoloNet catalog, writer/history/series, importer/custody read, torrent read, seed-join read; Shizune owner igor/use |
| omarchy-pc | Jocasta operator read/write; identical OV resources/personal grants; Shizune owner igor/use |
| agent-a | Jocasta operator read/write |
| agent-b | Jocasta operator read/write |
| homelab | HoloNet Movies catalog/read; HA state.read on sensor.zigbee2mqtt_bridge_version; observer host homelab, no services |

HoloNet catalog is library 3, server 3c86e6d7ff6c4a3296522cdf03d22bb0,
catalog user 54b7d36cef804d3c8478474553ed3a82, namespace movies-catalog, empty
history/targets. Writer is libraries 3/615/1132, namespace
managed-recommendations, and the exact eight actions and seven recipients in
the contract. Janitor purposes are separate scope tuples, library 3,
namespace managed-janitor, empty catalog/history/target users. Combined or
foreign-purpose requests emit no host claims. One mapping emits arrays once;
other selected mappings return empty dictionaries. No resource token receives
another resource's claims.

## Protected input and ownership

Prepare a private controller receipt directory (0700, owned by the invoking
controller user, with non-writable and unlinked ancestors). Use a new UUID
owner and a protected inventory containing the exact selected host aliases.
The private input file has this shape:

```yaml
authentik_agent_host_owner: RANDOM-UUID
authentik_agent_host_receipt_dir: /home/igor/.cache/PRIVATE-OPERATION/receipt
authentik_agent_hosts:
  - host: agent-b
    generation: 1
    expires: "EXPLICIT-UTC-DATE-WITHIN-60-DAYS"
    revoked: false
    destination:
      hostname: ACTUAL-NODE-NAME
      uid: ACTUAL-NUMERIC-UID
authentik_agent_host_baselines:
  - kind: mapping
    pk: EXACT-EXISTING-SCOPE-MAPPING-UUID
    sha256: SHA256-OF-EXACT-ORIGINAL-EXPRESSION
    service: jocasta
    scope: jocasta.read
    ownership:
      receipt_dir: /home/igor/.cache/EXISTING-MANAGED-ENROLLMENT/receipt
  # Include every scope listed below, plus exactly one managed Jocasta policy:
  - kind: policy
    pk: EXACT-MANAGED-JOCASTA-POLICY-UUID
    sha256: SHA256-OF-EXACT-ORIGINAL-POLICY-EXPRESSION
    service: jocasta
    scope: ""
    ownership:
      receipt_dir: /home/igor/.cache/EXISTING-MANAGED-ENROLLMENT/receipt
```

Required mapping baseline scopes are jocasta.read; all four ov resource
scopes plus the five ov.personal actions; holonet.read/curate/home/janitor/
torrents/seedjoin; shizune.use; ha.read; homelab.read. Supply exact current
mapping IDs and hashes from the existing owned applications, using protected
operator discovery. Hash the expression bytes, without normalizing whitespace.
Every non-Jocasta mapping also requires an ownership anchor:
`ownership: {blueprint_pk: EXACT-UUID, content_sha256: EXACT-CONTENT-HASH}`.
The stored blueprint must still be successful, match that hash and list both
the exact mapping name and its resource group as present-owned objects.
Jocasta anchors point to the original private managed enrollment receipt,
which must have no pending operation and bind the exact object ID/body.
Expression hashes alone never establish ownership.
Public provider, application and binding inventories must match the owned resources and complete scope-mapping sets; API mappings are refused. No client secret is read.

The journal records API-returned IDs and original/rendered expressions.
Name collisions, missing ownership, changed grants/groups, invalid managed roles,
incomplete baseline sets and pending API mutations refuse convergence.
An uncertain API write remains journaled and is never automatically replayed.
Reconcile that exact operation before a retry. Keep every previously declared
host in subsequent cohorts. Bootstrap generation is immutable after enrollment;
later rotation belongs to P3.2.

Freeze the old resource blueprint writers during coexistence: they do not own
new host accounts and will reject the new group members. Do not run them to
reset group membership or mappings. Content-stored legacy blueprints are not re-applied on a schedule, and a deliberate re-apply fails closed. For the managed Jocasta role, supply
`webmcp_managed_host_users` with exact `public_host_users` readbacks from the
host writer when deliberately reconciling its protected receipt. Its expressions
accept both generations; it does not create or modify host accounts. Changes to
the coexistence cohort require reconciliation of the existing expression
receipts, never deletion/reconstruction of an uncertain ownership journal.

## Operator deploy sequence

This source change does not deploy. After review and merge, root pins the
reviewed revision and supplies the existing protected inventory and enrollment
packet. In that checkout, run exactly:

```sh
ansible-playbook -i "$PROTECTED_INVENTORY" playbooks/authentik-agent-hosts.yml -e @"$PROTECTED_AGENT_HOSTS" --check
# Inspect the complete check result and reconcile all ownership refusals.
ansible-playbook -i "$PROTECTED_INVENTORY" playbooks/authentik-agent-hosts.yml -e @"$PROTECTED_AGENT_HOSTS"
ansible-playbook -i "$PROTECTED_INVENTORY" playbooks/authentik-agent-hosts.yml -e @"$PROTECTED_AGENT_HOSTS"
ansible-playbook -i "$PROTECTED_INVENTORY" playbooks/authentik-agent-hosts.yml -e @"$PROTECTED_AGENT_HOSTS" --check
```

CHECK decrypts the existing controller credential only for authenticated GET
requests. It performs no mutation, key retrieval, receipt write, lock creation or
host write. It reports missing-account changes without predicting PKs and
compares existing accounts/expressions and destination custody. REAL reads
back exact API responses and rechecks authoritative user state before delivery.
An identical repeat should report no changes.

Creation is separate from consumer cutover: retain old accounts and credentials.
Root must install consumer coexistence, observe verified subjects, and qualify
every granted resource/purpose before switching host consumers. This PR does
not claim native-client acceptance or update OV personal subject pins, HoloNet
worker subject sets, fleet helpers, Akasha or signing-key publication.


## Original legacy writer acquisition read projection

The maintained playbook also accepts one default-off
`authentik_agent_original_writer_read` declaration. It only reconciles owned
scope expressions through the existing `hosts.json` journal. It does not patch
the legacy user, token, expiry, membership or credential. It refuses any host
or rotation change in the same run and suppresses host credential delivery.
Retain the complete existing M3 host cohort, owner, rotation input and journal.

After source review and CI, the parent must establish an exclusive issuer write
window and fresh protected existing-only GET readbacks. Neither source tests nor
an old subject receipt qualify live use. Do not run
`playbooks/authentik-holonet-catalog.yml` or rebuild ownership journals.

Read the complete users and token metadata inventories through existing approved
administrative custody, without requesting token keys. Exactly one user must
match username `chaldea-laptop-holonet-recommendations`, its approved original
hashed subject and PK. Require service_account, active, non-superuser, legacy
path `homelab-infra/machine-identities`, existing HoloNet group, and no host
attributes. Require the existing application `chaldea-holonet` provider to use
`hashed_user_id`, so the user's API `uid` equals the approved original subject.
A duplicate PK, username or uid is a refusal, not an account to adopt.

Read exactly its existing app-password metadata, identifier
`chaldea-laptop-holonet-recommendations-g1`, generation 1, existing unmodified
expiry and token PK. There must be no second app password for that principal.
Never fetch, log or copy its key. Compare the readback to the approved protected
existing-writer evidence before preparing this input:

```yaml
authentik_agent_original_writer_read:
  username: chaldea-laptop-holonet-recommendations
  pk: EXACT-EXISTING-USER-PK
  subject: EXACT-APPROVED-ORIGINAL-64-HEX-SUBJECT
  generation: 1
  expires: EXACT-EXISTING-UTC-EXPIRY
  user_sha256: SHA256-OF-NORMALIZED-PUBLIC-USER-READBACK
  token_sha256: SHA256-OF-NORMALIZED-TOKEN-METADATA-READBACK
```

Compute hashes with the maintained contract functions
`readback_sha256(writer_user_readback(user))` and
`readback_sha256(writer_token_readback(token))`. User fields are pk, uid,
username, path, type, is_active, is_superuser, attributes, sorted string group
IDs and sorted string role IDs. Token fields are pk, identifier, user, intent,
expiring and expires normalized by datetime.fromisoformat to isoformat.
The hash encoder is UTF-8 JSON with sorted keys and separators comma/colon.
Store only protected metadata inputs, never credentials, in the operation packet.
The module compares these against fresh authoritative inventories on each run
and after reconciliation. Issuance also checks the pinned public user state.

Supply the complete normal mapping/policy baseline set plus exactly the existing
`holonet.acquire` scope mapping. Pin original expression SHA256 values and
existing ownership anchors, while retaining the current rendered bytes in
`hosts.json`. The existing resource provider must already attach the exact
complete mapping set. Missing acquisition mapping/ownership, extra mappings,
pending operations or changed rendered bytes stop before writes. This path
does not create or adopt a missing mapping or reset a provider. If fresh
discovery finds that prerequisite absent, retain it as a parent-owned source
or operational dependency; do not run the frozen legacy blueprint to fill it.

Run the CHECK, one REAL, unchanged REAL, CHECK sequence above with this complete
protected packet. Inspect the first CHECK before REAL. The only intended REAL
changes are scope expressions and their existing journal records. Unchanged
REAL and final CHECK must report zero changes. Preserve all 23 legacy identities,
both coexisting subjects and M3 account/rotation state. Do not refresh hashes to
accept drift without reconciling its cause.

Only the exact request `holonet.read holonet.janitor holonet.acquire` emits
library 3, server 3c86e6d7ff6c4a3296522cdf03d22bb0, namespace managed-janitor,
action janitor.acquire.read, and empty catalog/history/target users. The
acquisition mapping emits once. Other mappings return empty dictionaries for
that request. Old writer requests retain the original expression bytes.
Acquisition unions, start/media, sibling purposes and other subjects receive no
acquisition claims. No new M3 grants are added. Start/media and SR05 remain
withheld; native read acceptance and denied sibling probes belong to the parent
after the separately qualified write window.

## Credential custody and revocation

Each selected account is `chaldea-HOST`. Resource memberships are exactly the
existing non-superuser resource groups; Jocasta uses its existing expression
policy. Account attributes contain only the closed host services map and owner.
The only allowed role is Authentik's own managed per-user role, with exact
name/owner, no global permissions and only object-level view_token_key
on that user's own tokens. Membership and permission shape are checked during
enrollment and again inside issued-claim and Jocasta policy expressions. The
ORM names follow Authentik 2026.8.5's guardian RoleModelPermission and
RoleObjectPermission models; see the pinned Authentik core model source.

The remote module checks actual node, UID and passwd home. Homelab uses
/home/iryzhkov; the other hosts use /home/igor. The sole password file is
`~/.config/chaldea/authentik/host.app-password` (0600). The directories are
private; links, hardlinks and foreign ownership are refused. No enrollment.json
password copy is produced.

`host.generation.json` is public metadata under the private directory, with host,
UID, generation, status and key SHA-256. Delivery holds `host.lock`, records a
pending marker, atomically replaces the key, then commits the marker with fsync.
A pending/crashed operation refuses replay. P3.2's rotation writer must use this
same lock and marker contract. A committed equal/newer generation prevents key
retrieval and overwrite; bootstrap never advances an existing local generation.
An orphan key, missing committed key or mismatched digest requires reconciliation.

Inventory revoked=true or observed administrative is_active=false latches
revocation in hosts.json, keeps the account disabled, refuses enrollment/key
delivery and leaves a local revocation tombstone while removing the password.
A stale revoked=false inventory cannot re-enable it. Existing roles are never
reset and existing users are never patched with is_active=true.

Re-enable is a deliberate manual operator operation: reconcile the exact disabled
account and controller journal, choose a new credential generation, remove only
that host's controller revocation latch and immutable bootstrap token record,
and reconcile/remove its local tombstone under the same lock before explicitly
reactivating the account. Record actual IDs and revoke obsolete tokens. There is
no automatic re-enable. The explicit administrative reenable_host operation is
documented in [host credential rotation](host-credential-rotation.md). Do not clear pending operations as a shortcut.

Offline resource JWTs remain valid through their existing bounded expiry;
deactivation prevents subsequent issuance. S1's native JWKS overlap limitation
and the later P3.2 edge-JWKS work remain unchanged.
