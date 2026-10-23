# Disabled Garage object store storage

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
  evidence pending null. Metadata hard-cap facts remain unknown. No
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
The data directory and `meta` must be owned by the Garage uid with no group or
other access. Before privileged configuration rendering, the metadata parent,
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

## Capacity

The approved blob data budget is **2 TB decimal (2,000,000,000,000 bytes)
combined across artifacts and results**. This is one aggregate operational
budget, not 2 TB per bucket. It supersedes the historical 1 TiB / 400 GiB
proposal. Local metadata still requires its own physical byte and inode
bounds; its allocation remains unselected.

Data has two acceptance modes:

- `data.acceptance_mode: physical_cap` requires the original physical-cap
  evidence described below. Omitting `acceptance_mode` retains this mode for
  existing configurations. Missing or invalid cap evidence never selects a
  budget implicitly.
- `data.acceptance_mode: operational_budget` explicitly accepts unquotaed
  data. It requires `data.operational_budget` with exactly `bytes`, a positive
  integer aggregate budget across artifacts and results, and
  `risk_acceptance`, a nonblank string referencing the operator's acceptance
  of the unbounded physical-data risk. Booleans, structured acknowledgments
  and unknown budget keys refuse. `data.physical_cap` must be absent or null;
  even an empty cap object refuses because it mixes incompatible claims.
  The canonical selection records the approved value but leaves the
  acceptance reference null. It remains disabled and cannot pass enabled
  validation with unknown facts.

An operational budget does **not** enforce physical data bytes or inodes,
reserve NAS space, or constrain multipart uploads. It adds no admission
controller, quota, cleanup process or runtime monitor. Multipart data,
overhead, unrelated consumers and ordinary writes can exhaust the shared NAS.
A successful budget preflight reports `data_acceptance.mode` and
`aggregate_budget_bytes`, plus these limitations; it never reports physical
enforcement.

`physical_cap` mode refuses any `operational_budget` field. Unknown modes,
null modes and contradictory cap claims refuse before host probing.
Metadata always requires a physical cap and refuses data acceptance fields.
To change modes, explicitly select the new mode, remove the incompatible
declaration, supply its required evidence and requalify. The existing digest
covers the mode, aggregate budget and risk-acceptance reference as well as all
other storage settings. Adding an explicit mode to an old config changes its
digest; leaving an old config unchanged preserves both its digest and
hard-cap validation semantics. No migration or host change happens here.

A physical cap with positive bytes and inodes, an evidence reference and a
mechanism that bounds physical allocation is required for metadata in every
mode, and for data in `physical_cap` mode:

- `dedicated_volume` or `dedicated_filesystem`, scoped to the mount target.
  The store must be an immediate child of the mount target. The preflight
  lists the mount root and refuses if it holds anything except the store
  directory and `lost+found`. This prevents a nested metadata path such as
  `/disk/swarm/objectstore` from claiming all of `/disk` while another stack
  uses `/disk/swarm/other-stack`. Nested metadata needs a directory-scoped
  hard quota. Claiming a whole shared mount refuses.
- `nas_directory_hard_quota` or `filesystem_project_hard_quota`, scoped to
  exactly the store path.

Free space, df figures, logical or Garage bucket quotas and cleanup scans are
not physical caps and are refused. The cap's bytes, inodes and enforcement are
evidence the preflight requires but cannot measure. A read-only survey found no
existing physical byte or inode enforcement for any candidate location, so no
cap is qualified yet. Choosing operational data acceptance does not qualify
metadata, identity, custody, mount behaviour, durability, retention, restore
or activation. All existing live gates and deployment environment binding
still apply.

## The existing ML export

The homelab NAS mount is `/mnt/nas/ml` from
`192.168.40.40:/var/nfs/shared/machine_learning`, NFSv3, soft, `sec=sys`, root
owned 988:988 mode 0770. Existing folders there (jobs, datasets, registry, big
data, runs, dsh-workspace) are unrelated, and no blob directory exists. The
preflight does not ban this export by name. A blob namespace there can pass
only as a dedicated top-level directory (for example `/mnt/nas/ml/garage-blobs`)
owned by the Garage uid with no group or other access. Data acceptance must
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
RPC secret, establish and evidence the metadata physical cap, qualify the
selected data acceptance mode and its evidence, run the live
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
