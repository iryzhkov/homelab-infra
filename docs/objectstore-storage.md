# Disabled Garage object store storage

## Native service and maintenance readiness proposal

The smaller candidate is the direct native unit
`stacks/objectstore/garage-objectstore.service.example`, with
`garage-native.toml.example` and `scripts/garage-storage-native.py`.
These files are INERT and uninstalled. No Ansible role, active stack, timer,
pin or current release selection consumes them. Canonical enabled=false and
null live facts remain unchanged. The older Swarm restart example does not
satisfy the every-start requirement and must not be used for this candidate.

The native unit's sole ExecStart runs the guard as existing UID/GID1000:1000.
The guard reads actual trusted storage JSON and TOML, calls the existing
read-only preflight, checks the exact executable SHA256, fsync=true, configured
metadata/data paths, RPC secret custody, local space/inode headroom and
loopback-only RPC/S3, then execs the same Garage binary in place. Unexpected
TOML settings, including an admin listener, refuse. Each explicit start or
restart traverses this path; there is no fallback or automatic Restart policy.
Success is point in time, with no continuous mount-loss, fullness or authority
monitor. It does not establish NFS soft-mount power-loss durability.

The unit limits memory to2GiB, tasks to128 and open files to65536. It grants no
capabilities and suppresses server stdout/stderr rather than exporting potentially sensitive
RPC/config diagnostics. It sends TERM and waits30seconds with SendSIGKILL=no.
The unit deliberately has no filesystem mount-namespace directives: it inherits
the host mount view required by the exact preflight checks. ProtectSystem=strict
with ReadWritePaths would create subtree bind mounts and make the host's valid
mount identities fail. Existing UID1000 filesystem permissions provide access
control; this unit does not claim filesystem confinement beyond those permissions.
A stuck process remains a failure. Neither the unit nor maintenance may KILL and
continue to backup. Automatic OOM termination is not a clean-stop qualification.

Chaldea's `docs/blob-maintenance.md` describes the single finite root-operator
invocation and its separate resume. It enumerates keys, revokes every publisher,
rejects any bucket owner or unexpected writer, confirms exact service exit,
copies and verifies a local stopped pair, restarts the same metadata/config/RPC
identity through this guard, verifies the fence and calls existing cleanup.
No native bootstrap or grant-making convergence is part of startup, restart or
maintenance. The trusted operator exclusively owns RPC and grant administration.
No publisher receives the RPC secret or owner permission.

### Unknown NFS filecount proposal

The native guard still requires at least 1 GiB available data bytes and 2 GiB
available metadata bytes. Normally both stores also need at least 4096 available
file slots. It now validates the complete nonnegative integer
`f_files/f_ffree/f_favail` tuple and `f_favail <= f_ffree <= f_files`.
Missing, malformed, negative, mixed or inconsistent values refuse. An I/O error
still refuses; it is never converted to unknown telemetry.

An optional data-only field, absent from canonical configuration, proposes one
explicit exception:

```json
"unknown_filecount_risk_acceptance": "evidence:separately-reviewed-unknown-filecount-risk"
```

Only explicitly selected `nfs` or `nfs4` data in `operational_budget` mode may
declare this nonblank evidence reference. It permits only the exact
`0/0/0` tuple, reported as `UNKNOWN`, with `available_inodes: null` in
`startup_headroom` from native `check` and a stderr warning that no free-inode
capacity was established. Known counts still need 4096 available slots,
including when the field is present. Metadata, ext4 and local backup destination
inode requirements have no exception. Omitting the field retains refusal.
The preflight reports the selected policy as a limitation without measuring
headroom. Its digest already covers every configuration field except
qualifications, so adding, removing or changing this reference invalidates
existing qualification binding. All five normal startup live gates remain
required, as do exact mount identity, custody, fsync and native TOML checks.

This addresses the observed all-zero NAS filecounts with ample byte headroom.
[RFC 1813 section 3.3.18](https://www.rfc-editor.org/rfc/rfc1813.html#section-3.3.18)
defines file-slot telemetry separately from bytes; it does not define zero as a
pass. Treating this tuple as unavailable telemetry is a narrowly accepted risk,
not proof of available file slots or supported capacity. A deterministic exception
for every NFS operational budget would silently change accepted configurations.
A separate policy framework would add machinery for a single evidence decision;
the optional reference keeps the default strict and the exact change digest-bound.

The frozen first-deployment annex duplicates the old threshold and is unchanged.
A separately reviewed candidate bootstrap must replace only its headroom loop
with `native.check_headroom(storage)`, after its existing exact store probes.
It must retain trusted-source checks, exact storage SHA256 and digest, UID/GID1000,
custody, pinned binary/TOML, and the requirement that all five live gates are null.
It cannot substitute for the qualified normal guard.

Consumer boundaries: `storage-validate.yml`, the Swarm wrapper and inert render
example consume preflight schema/digests and still require all live gates.
Chaldea's `src/exec/blob-maintenance.ts` uses the hash-bound native
`check()` producer, including explicit UNKNOWN semantics. Its
`src/exec/blob_pair.py` checks strict current bytes and available inodes at
the local ext4 backup destination. The managed profile described below also
requires coordinated version 3 pair/consumer source. Neither this configuration
nor source checks establish maintenance or deployment acceptance.

Using this proposal on a host requires separately reviewed replacement guard,
preflight, candidate annex and storage bytes, new hashes/digest and explicit
acceptance of unknown NAS filecount headroom. Existing packet authorization does
not cover them. The 2e12-byte aggregate unquotaed NAS budget, 16 GiB metadata budget,
local backups, manual cleanup/publication, 14-day retention and 1-day multipart
expiry remain unchanged. Fullness, mount-loss, restore and other live gates are
neither supplied nor waived by this source change.


### Inert managed DATA profile

A separately adopted shared-access contract may select this fixed DATA-only
field. It is omitted from canonical inventory. This example is a JSON fragment,
not a usable acceptance record:

```json
"access_profile": {
  "name": "managed_nfs_data_v1",
  "acceptance_reference": null
}
```

A real selection requires a bounded immutable `jocasta:32-lowercase-hex@revision`
reference, `operating_contract: operator_budget_v1`, operational-budget NFS DATA
and the existing explicit `identity_mapping`. Exact DATA and profile keys reject
typos and unknown fields. The preflight checks the DATA root as exactly 0700 or
0770, still owned by the declared mapped uid/gid. It does not walk entries or
normalize ownership/modes. Chaldea maintenance bounds the whole DATA inventory:
directories exactly 0700/0770 and files exactly 0600/0660, no other or special
bits. The first implementation refuses every observed mode/ctime change during
copy or scan, including transitions between allowed modes. Preserve partial
state and obtain a new operator assessment; no automatic retry is implied.

Omission keeps strict private legacy checks. Metadata/config/credentials and
local backups retain their existing private custody requirements. The digest
covers the profile and acceptance reference, but the preflight validates only
reference shape. It neither fetches Jocasta nor establishes that acceptance is
true. This profile is outside the old S2 selector-plus-UNKNOWN-reference
difference allowlist. A future immutable composite difference receipt must
mechanically identify the profile/reference addition, owner/mode semantics and
exact qualifying bytes with the existing composite record fields. Old measured
digests and restore path-derived digests remain separate. No production digest,
adopted acceptance or live gate is supplied here.

The proposed boundary is nonsecret, replaceable artifact/result DATA. NAS mode
actors, ACLs and underlying client restrictions remain UNKNOWN. Authorized
share clients may read, alter, delete or replace DATA regardless of S3 grants.
Hashes cannot authenticate simultaneous object and manifest replacement. The
2e12-byte budget is TOTAL across artifacts/results and is not a quota. Separate
16 GiB local metadata, UNKNOWN all-zero filecounts, point-in-time headroom and
manual capacity limits remain unchanged. This source proves no actual NAS
security, shared fullness/outage, power-loss durability or full-scale restore.

### Inert installation and credential mutations

This is an exact proposal to review after filling remaining facts, not a script
to run now. Parent integration and applicable production authority remain
required. No new user, route, issuer, disk, quota, remount or fleet selection
is proposed.

1. Establish the actual enabled target-local storage JSON from canonical source
   in a separate private proposal. Keep canonical source disabled. Bind host
   homelab, process1000:1000, data owner mapping977:988 with accepted evidence,
   actual NFS identity and local ext4 identity, every qualification, risk
   references and its exact digest. Data path is
   `/mnt/nas/ml/garage-blobs`; metadata parent is
   `/mnt/docker-data/swarm/objectstore`. Data's2TB total and metadata's16GiB
   are unreserved operating budgets. Actual mount device/FS identity and live
   durability evidence must be filled truthfully; this text does not supply them.
2. As an authorized operator, create only those private paths. Proposed local
   metadata parent/config directories are root:root0755, meta1000:1000mode0700.
   Proposed backup root `/mnt/docker-data/swarm/objectstore-backups` is
   root:root0700 on the SAME existing local filesystem. NAS data must show
  977:988mode0700 after creation through the already qualified1000:1000
   identity. Do not chown the shared export or assume NAS owner maps locally.
   Verify actual traversal/access before any start.
3. Stage the SAME Garage2.4.1 executable from immutable OCI digest
   `sha256:9c96caa2612d3411acc5b0e6701fb238dbfba33e533a6d7d3d811a4b12d0d020`.
   Verify accepted executable SHA256
   `ae49f8de4aaee6b5ca305f7cbdccc8cd8a29b2804aac074649f18bfb83d0a2b6`.
   Install it root:root0755 at `/usr/local/lib/garage/2.4.1/garage`, with
   root-owned non-writable ancestors. Existing fixture extraction code supplies
   the qualified extraction method; this proposal does not fetch on a production
   host or add a background updater.
4. Install reviewed `garage-storage-native.py` and
   `garage-storage-preflight.py` as root:root0644 under
   `/usr/local/libexec/`, not executable shell hooks. Install the filled storage
   JSON root:1000mode0640 at `/etc/garage/storage.json`, root-owned directory0755.
   Install the exact native TOML root:root0644 at
   `/mnt/docker-data/swarm/objectstore/config/garage.toml`. Native Python
   requires3.11+ for tomllib; confirm the actual target version before install.
5. Generate one RPC secret LOCALLY with the existing supported custody method,
   retaining it only as `/etc/garage/rpc.secret`,1000:1000mode0400. Do not send
   it through command arguments, clipboard, jobs, logs or exported results.
   Bootstrap exact bucket aliases/IDs, quotas and synthetic-free production keys
   through trusted local RPC only after authority. Retain publisher and reader
   secrets in their own qualified host-local custody. Project the separate
   cleanup read/write/no-owner credential only into root:root0600
   `/etc/chaldea/blob-cleanup-<bucket>.json`. Enumerate ALL keys and revoke the
   one-time bootstrap bucket owner/write privileges before maintenance can pass.
   Freeze any grant-making bootstrap/converger for the maintenance interval.
6. Install the exact reviewed example unit, after reviewing its fixed paths,
   root:root0644 at `/etc/systemd/system/garage-objectstore.service`. No
   drop-ins, Install section, enable, timer or automated start. Record the
   actual reviewed unit, guard, preflight, storage and TOML SHA256 values into
   root:root0600 `/etc/chaldea/blob-maintenance.json` using Chaldea's inert
   schema. Install the independently qualified Chaldea source with root-only
   modification rights, including the finite pair helper. No current client
   release selection or fleet pin changes are part of this proposal.
7. Only with separate activation authority, run exactly
   `systemctl daemon-reload` and `systemctl start garage-objectstore.service`.
   No blanket stack deploy/restart is needed. Qualify actual process exe/UID,
   loopback endpoint, node identity, grants, mount/custody, adverse storage
   behavior and clean-stop refusal. Do not enable publication automatically.
   A manual future maintenance is `chaldea blob-maintenance run`; only a
   verified successful result supplies an ID eligible for the separate
   `chaldea blob-maintenance resume <id>` command.

Backup inventory/copy currently refuses above4096entries or512MiB allocated/read
per tree. It preserves sparse logical files but does not offer full2TB backup
support. Local available capacity can reduce usable data far below the budget.
No silent NAS backup relocation or added disk is allowed. Larger-volume operation
requires a separately reviewed capacity/method change. All pairs, partial copies,
pending local job bytes, journals and original UUIDs are retained; no rotation or
replay is implicit.

Rollback after any later authorized installation keeps publication fenced and
preserves metadata, NAS data and local backups. Stop only this exact newly owned
service, require confirmed exit, and remove no last good copy. A timeout remains
a recovery decision, not permission to force-stop and back up. Disable only new
publication configuration; do not touch ML folders, NATS, Akasha, or ordinary
SSH job execution.

Reusable proof references remain Q2
`jocasta:75988be5921944746f349a2adee821b9@26` and revocation experiment
`jocasta:56e6d40d605c7a3181aef45747357087@1`. That experiment required fixture
KILL for an incomplete PUT. New local contracts qualify source guards and
ordering only. Installed target service, production credentials, full-scale backup, root read of
mode0700 NAS data under the actual export's squash policy, and
continuous/adverse-storage behavior remain unqualified. The accepted NAS copy
proof ran as1000:1000, not root; a root read refusal must not trigger export,
ownership or identity changes. Each retained pair also retains its RPC-secret
copy after a later live-secret rotation; continuing private custody belongs to
the operator.


The objectstore (Garage) source is disabled. This change adds no directory,
mount, service, bucket, secret, route, host pin, quota or backup. It adds a
canonical disabled configuration, a read-only storage preflight that refuses
unknown or unsafe storage, and inert examples for a later, separately approved
activation. Live full-volume behaviour, mount loss, a consistent restore and a
fresh-client round trip remain unqualified.

## Source

- Canonical configuration: `objectstore_storage` in
  `inventory/group_vars/swarm/objectstore.yml`, with `enabled: false` and every
  measured live fact null. Data explicitly selects `operational_budget` at
  2 TB decimal total across artifacts and results, with risk-acceptance
  evidence pending null. Metadata explicitly selects the same mode with a
  proposed 16 GiB budget and null risk evidence on the existing shared
  service-state disk. All measured facts remain unknown. No
  `swarm_stacks_*` entry names the objectstore source, so
  the stack wrapper never selects it.
- Guard: `roles/swarm/tasks/stack.yml` includes
  `stacks/objectstore/storage-validate.yml` for the objectstore source (also
  under another stack name) before lint, deployment directories, stack or
  secret writes and pin labels, as it does for NATS messaging. The guard
  refuses unless storage is enabled and names a host equal to the stack pin.
  Before probing or deployment effects, it also requires the five storage
  values in `env_extra` (host, uid, gid, data path and metadata path) to match
  `objectstore_storage` exactly as strings. It then runs the preflight on
  that host. The qualification digest alone does not bind independent
  deployment overrides.
- Preflight: `scripts/garage-storage-preflight.py`. Exit 0 passed, 2 refused,
  3 disabled. `--digest` prints the configuration digest and inspects nothing.
- Inert examples: `stacks/objectstore/stack.yml.example` (Garage v2.4.1 pinned
  by digest, no published port) and `stacks/objectstore/swarm-prepare.yml.example`
  (validates again, then renders `templates/garage.toml.j2`). Neither name is
  `stack.yml` or `swarm-prepare.yml`, so nothing includes them.
- Tests: `scripts/test-garage-storage-preflight.py`, run in CI beside the NATS
  messaging tests.

## Optional operator-budget qualification contract

Source support is opt-in with top-level `"operating_contract": "operator_budget_v1"`.
Both data and metadata must explicitly select `operational_budget`. Unknown
selectors refuse, including on a disabled configuration. The canonical inventory
remains disabled, all qualification values null, and has no selector. Without the
selector, legacy nonblank gate strings, defaults, output and digest are unchanged.
The separate all-null qualification-only starter is unchanged.

The selected contract retains `qualifications.configuration` and exactly the
five gate keys. Normal startup requires all five nonnull typed records.

| Gate | Permitted type |
| --- | --- |
| full_volume, mount_loss | accepted_risk only |
| consistent_restore, fresh_client_roundtrip | measured or composite |
| last_copy_policy | measured only, for actual custody/policy evidence |

Measured and accepted-risk records have exactly
`{type, configuration, references, limitation}`. Their configuration must equal
the current computed digest. Composite records have exactly
`{type, measured_configuration, qualifying_configuration, difference_reference, references, limitation}`.
Their qualifying digest must equal the current digest; their measured digest must
have canonical `sha256:` plus 64 lowercase hexadecimal digits syntax.
References contain 1..8 immutable Jocasta references, each at most 96 ASCII bytes
and exactly `jocasta:` plus 32 lowercase hexadecimal digits, `@`, and a positive
decimal revision without leading zeros. The difference reference uses the same
syntax and bound. A limitation must contain non-whitespace text and fit in 4096
UTF-8 bytes. Extra/missing fields, wrong types and gate classes refuse before
host inspection. All-null gates refuse normal startup.

Preflight output preserves `passed-startup-preflight` and adds the contract,
typed qualifications and `qualification_validation` in selected mode. These
report accepted-risk, measured and composite distinctions plus each limitation.
Startup verifies record shape and current qualifying bindings. It never fetches
Jocasta, checks historical configuration bytes, verifies evidence truth, or
enforces an allowed-difference receipt. Native `check` retains these fields and
the existing `startup_headroom` shape. Headroom/custody/mount checks and UNKNOWN
policy are unchanged; there is no reservation, numeric data quota, live mount
fence or combined host/NAS-loss guarantee.

Adding the selector or changing the UNKNOWN-filecount risk-acceptance string
changes the digest. A reviewed operator preparation must compare the actual old
and new JSON locally and record an immutable allowed-difference receipt. S2
client-proof reuse permits only the selector addition and the
`data.unknown_filecount_risk_acceptance` reference-string change. Paths, mount
options, identities/mapping, fsync and budgets must stay identical. Removing both
fields does not reconstruct the old configuration: its risk string was nonempty.
The composite record must retain the actual measured digest separately from the
qualifying digest. Generic source does not embed a deployment digest.

A restore composite uses the actual restored instance's path-derived measured
digest. Its referenced receipt must disclose source, restored and qualifying
digests, exact path mapping and observed results. The adopted preparation route
is one current namespace/key restore with deleted-probe 404, combined explicitly
with the earlier artifact-restore proof. Current copied state is not a restore,
and that composition does not prove current artifact recovery, shared fullness,
live mount loss or production publisher/reader access. The last-copy measured
record references the actual custodian, retained last-good-pair inventory and
no-deletion/recovery policy; it makes no capacity or combined-loss claim.
Actual first publication and original-UUID retrieval through production
principals remain later requirements.

No live records or pass references are populated by this source change.
Qualification, gate population, restored-copy starts, credentials and maintenance
use need their separately reviewed exact operator authority.

## What the preflight checks

With `enabled: false` it inspects nothing and exits 3. Otherwise every declared
fact is checked before the host is touched: explicit host, non-root uid and
gid, boolean `data_fsync` and `metadata_fsync`, an evidence reference for every
live gate, and a `qualifications.configuration` digest equal to the digest of
the rest of the configuration, so evidence for one configuration cannot
qualify another.

For each store it requires an explicit mount identity (target, source, fstype,
fsroot `/`, expected options including `rw`). Data must be NFS with a
`server:/export` source and exactly one of `hard` or `soft` declared. Metadata
and journals must be on local ext4, xfs or btrfs. Paths must be absolute and
canonical, strictly below their mount target, and disjoint. Paths and the
host must contain no `$`: Docker interpolates again at deployment, so even
an existing literal directory named `${BLOB_DIR}` is unsafe. Data and metadata
must be on separate mounts. The data path must be a top-level directory of its
mount, a sibling of other consumers' folders rather than nested inside one.
This is a conservative repository policy for namespace isolation, not a user
requirement that all blob storage layouts use a top-level directory.

On the host it reads only. Every component of every path is `lstat`ed, so a
symlink anywhere refuses. This includes `/srv/swarm`, so metadata must be named
by its real path under `/mnt/docker-data/swarm`. The mount target, the store
path and the `meta` and `config` children must match the expected findmnt
identity and options, and sit on the mount root's device, so a missing mount
or an overmount refuses instead of falling back to the underlying directory.
The data directory must be owned by the Garage uid, or by both numeric IDs
in an explicit qualified data identity mapping as described below. It must
have no group or other access. The local `meta` directory always requires
the Garage process uid with no group or other access.
Before privileged configuration rendering, the metadata parent,
`meta`, `config` and every ancestor back through `/` must be directories
owned by root or the selected Garage uid. An unrelated owner refuses even at
0755 because it can chmod the directory and replace its children. The Garage
uid is explicitly trusted for this custody policy; `meta` still requires that
uid alone and no group or other access.

These local directories must not be group- or other-writable. Only strict
ancestors of the metadata parent may use a root-owned sticky directory, such
as a filesystem root at 01777. Sticky unlink/rename restrictions protect the
next component, whose owner is also checked, from replacement by untrusted
users. This exception never applies to the metadata parent, `meta` or
`config`: outsiders could plant entries such as `garage.toml` there even
without replacing the directory. A selected-uid-owned writable sticky
ancestor conservatively refuses too. A missing directory refuses; the
preflight never creates one.

## Optional data owner mapping

NFS can map a process identity to different numeric owners. An optional
`data.identity_mapping` object declares the expected observed owner of the
data directory. It must contain exactly `owner_uid`, `owner_gid` and
`evidence`. Both IDs must be positive non-root integers, excluding booleans.
Evidence must be a nonblank string referencing qualification of the mapping
for the selected process uid/gid and NFS layout.

This inert schema example uses the test fixture's observed IDs. It is not
a production selection or qualification. The null evidence deliberately
refuses enabled validation until actual evidence is supplied:

```yaml
data:
  # Other required data fields omitted from this schema example.
  identity_mapping:
    owner_uid: 977
    owner_gid: 988
    evidence: null
```

The fixture uses process 1000:1000 and observed data ownership 977:988.
With a mapping, the preflight checks both observed IDs and still refuses
any group or other access. A null object, incomplete object, unknown field,
invalid ID or missing evidence refuses before host inspection. Omitting
`identity_mapping` preserves the legacy check of the process uid alone,
without a group-owner comparison. It also preserves the existing
configuration digest. Adding, removing or changing a declaration requires
the existing exact-digest requalification, as does changing the process
identity. Both data acceptance modes support this declaration.

Metadata refuses `identity_mapping`, including null. Its parent, config
directory and ancestors still trust only root or the selected process uid;
`meta` still belongs to the process uid alone. A data mapping grants no
metadata custody. Deployment environment binding still uses the process
uid/gid, never the mapped owner IDs. The selected metadata acceptance mode
and all live qualification gates remain mandatory.

This declaration only checks observed ownership. It does not change NFS
server mapping, exports, ownership, accounts, group membership or ACLs.
Traversal, write behaviour and mapping qualification need their own
evidence. The canonical configuration leaves this declaration absent,
all live facts null, and the source disabled.

## Capacity

The approved blob data budget is **2 TB decimal (2,000,000,000,000 bytes)
combined across artifacts and results**. This is one aggregate operational
budget, not 2 TB per bucket. It supersedes the historical 1 TiB / 400 GiB
proposal.

Metadata is intended to use ordinary private directories under
`/mnt/docker-data/swarm/objectstore` on homelab's existing local ext4
service-state disk, shared with Docker and other services. The proposed
metadata operating budget is **16 GiB (17,179,869,184 bytes)**. It is not an
approved hard cap, physical allocation or reservation. The proposed new
16 GiB virtual disk packet was withdrawn, never approved or executed.
This source does not allocate, format, mount or provision storage. Actual
host, uid/gid, paths and mount identities remain null in the canonical
configuration. Directory preparation, backup and retention preparation,
and activation remain separate work; this source adopts no production
backup or service.

Each store uses the same two acceptance modes:

- `acceptance_mode: physical_cap` requires the original physical-cap
  evidence described below. Omitting `acceptance_mode` retains this mode for
  existing configurations. Missing or invalid cap evidence never selects a
  budget implicitly.
- `acceptance_mode: operational_budget` explicitly accepts an unquotaed
  store. Its `operational_budget` object requires exactly `bytes`, a positive
  integer, and `risk_acceptance`, a nonblank string referencing the
  operator's acceptance of that store's unbounded physical-capacity risk.
  Data bytes are aggregate across artifacts and results. Metadata bytes
  cover its local store. Booleans, structured acknowledgments, inode claims
  and unknown budget keys refuse. `physical_cap` must be absent or null;
  even an empty cap object refuses because it mixes incompatible claims.
  Canonical data records the approved budget, while canonical metadata
  records the proposed budget. Both acceptance references remain null.
  The source stays disabled and cannot pass enabled validation with
  unknown facts.

An operational budget does **not** enforce physical bytes or inodes,
reserve space, or constrain multipart uploads. It adds no admission
controller, automatic capacity controller, quota, headroom reserve, cleanup
process or runtime monitor. Multipart data, overhead, unrelated consumers
and ordinary writes can exhaust the shared NAS. Metadata and other services
can exhaust the shared Docker filesystem. A successful budget preflight
reports `data_acceptance` and `metadata_acceptance` separately, each with
`mode` and, in budget mode, `aggregate_budget_bytes`. The existing data
warnings remain, and the metadata warning explicitly states that it has no
physical byte or inode enforcement or reservation. This is a point-in-time
check, not continuous admission or monitoring, and it proves no backup
safety. Headroom, cleanup outage and fullness behaviour still need the
existing live qualifications; this change defines no new threshold or proof.

`physical_cap` mode refuses any `operational_budget` field. Unknown modes,
null modes and contradictory cap claims refuse before host probing in an
enabled configuration. To change modes, explicitly select the new mode,
remove the incompatible declaration, supply its required evidence and
requalify. The existing digest covers each store's mode, budget and
risk-acceptance reference as well as all other storage settings. Adding an
explicit mode to an old config changes its digest; leaving an old config
unchanged preserves both its digest and hard-cap validation semantics.
No migration or host change happens here.

Budget acceptance changes only the capacity declaration. Metadata still
requires local storage, trusted ancestry and config custody, private
process-owned `meta`, canonical paths, symlink and device checks, exact
mount identity and disjoint data storage. It still refuses
`identity_mapping`, including null. Process identity stays bound to the
deployment environment. A shared local mount with nested metadata is
permitted only when its selected acceptance mode and evidence allow it.

In `physical_cap` mode, either store requires positive bytes and inodes, an
evidence reference and a mechanism that bounds physical allocation:

- `dedicated_volume` or `dedicated_filesystem`, scoped to the mount target.
  The store must be an immediate child of the mount target. The preflight
  lists the mount root and refuses if it holds anything except the store
  directory and `lost+found`. This prevents a nested metadata path such as
  `/disk/swarm/objectstore` from claiming all of `/disk` while another stack
  uses `/disk/swarm/other-stack`. Nested metadata in physical-cap mode
  needs a directory-scoped hard quota. Claiming a whole shared mount refuses.
- `nas_directory_hard_quota` or `filesystem_project_hard_quota`, scoped to
  exactly the store path.

Free space, df figures, logical or Garage bucket quotas and cleanup scans are
not physical caps and are refused. The cap's bytes, inodes and enforcement are
evidence the preflight requires but cannot measure. A read-only survey found
no existing physical byte or inode enforcement for any candidate location,
so no cap is qualified yet. Operational acceptance for either store does not
qualify identity, custody, mount behaviour, durability, retention, restore
or activation. All existing live gates and deployment environment binding
still apply.

## The existing ML export

The original read-only survey recorded the homelab NAS mount at
`/mnt/nas/ml` from `192.168.40.40:/var/nfs/shared/machine_learning`,
NFSv3, soft, `sec=sys`, with export-root ownership 988:988 mode 0770.
The surveyed folders (jobs, datasets, registry, big data, runs,
dsh-workspace) are unrelated. These observations do not populate the
canonical configuration or qualify a production blob namespace. The
preflight does not ban this export by name. A blob namespace there can pass
only as a dedicated top-level directory (for example `/mnt/nas/ml/garage-blobs`)
owned by the Garage uid or the explicitly qualified mapped data uid/gid,
with no group or other access. Data acceptance must
either reference a NAS-enforced hard quota scoped to exactly that directory
or explicitly select operational budget acceptance with its risk evidence.
The export root itself, a folder inside another consumer's folder, and a
whole-share cap all refuse in either mode. This source-only revision makes
no NAS changes. Root-squash behaviour, identity mapping and physical capacity
remain live qualification questions; no quota-console answer is needed to
represent the approved unquotaed-data acceptance.

With 0770 at the export root, the Garage gid and the identity running the
preflight need traverse permission there. Under root squash, a root preflight
may get permission denied; that refuses, which is the intended outcome until
the run identity is qualified. Data ancestry does not inherit the local
metadata custody policy, so the known 0770 shared ML root is not refused
solely for its ownership or mode. NFS ancestor write access, server ACLs,
identity mapping and squash behaviour require separate live qualification
before activation. Passing the synthetic layout is not that evidence.

## Limitations

- The preflight is a point-in-time startup check, not continuous enforcement.
  The wrapper runs it at converge time and the prepare example runs it again
  before rendering. Swarm task restarts and rescheduling run neither, and a
  mount lost or replaced after the check is not detected.
- Custody checks use pathname-based `lstat`, numeric owners and mode bits.
  They do not hold directory descriptors through the later privileged render
  and cannot close check-to-use races. They trust root and the selected Garage
  uid, and do not qualify arbitrary filesystem ACL models or NFS server
  permissions. Custody is a startup refusal policy, not a race-free guarantee.
- Keeping the data path below the mount target means a restart after an
  unmount meets a missing bind source and fails, rather than writing to the
  local directory underneath, as long as nobody creates that directory on the
  underlying filesystem. This is a property of the layout, not fencing.
- A soft NFS mount turns server timeouts into I/O errors. It is not last-copy
  durability evidence, and the preflight reports it as a limitation.
- `data_fsync` and `metadata_fsync` default to true and are configurable. They
  are configuration, not evidence that NFS or the NAS honour fsync.
- One Garage node with `replication_factor = 1` holds one copy. Existing backup
  snapshot tags (including peer and offsite) do not qualify a consistent
  Garage restore, and this change adds no snapshot spec.

## Activation (not part of this change)

Each step needs separate approval: create the dedicated directories and the
RPC secret, qualify both stores' selected acceptance modes and their
required evidence, run the live
full-volume, mount-loss, consistent-restore, fresh-client and last-copy
qualifications, fill `objectstore_storage`, record the digest from `--digest`,
rename the examples, and add a `swarm_stacks_*` entry with `pin` equal to the
storage host, `prepare_tasks: swarm-prepare.yml` and `env_extra` for
`OBJECTSTORE_HOST`, `OBJECTSTORE_UID`, `OBJECTSTORE_GID`,
`OBJECTSTORE_DATA_PATH`, `OBJECTSTORE_METADATA_PATH` and
`OBJECTSTORE_RPC_SECRET`. Before activation, resolve two placement details:
the example's `node.labels.pin.objectstore` constraint must agree with the
wrapper's stack-name label if the stack is renamed, and the inventory name
used for delegation must identify the same node as its Docker hostname used
for placement. The current examples supply no generic name mapping. Routes,
buckets and keys are later work.
