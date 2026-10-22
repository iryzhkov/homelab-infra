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
  live fact null. No `swarm_stacks_*` entry names the objectstore source, so
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

On the host it reads only. Every component of every path is `lstat`ed, so a
symlink anywhere refuses. This includes `/srv/swarm`, so metadata must be named
by its real path under `/mnt/docker-data/swarm`. The mount target, the store
path and the `meta` and `config` children must match the expected findmnt
identity and options, and sit on the mount root's device, so a missing mount
or an overmount refuses instead of falling back to the underlying directory.
The data directory and `meta` must be owned by the Garage uid with no group or
other access. The metadata parent and `config` must not be group- or
other-writable. A missing directory refuses; the preflight never creates one.

## Capacity

A physical cap with positive bytes and inodes, an evidence reference and a
mechanism that bounds physical allocation is required for data and metadata:

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
cap is qualified yet.

## The existing ML export

The homelab NAS mount is `/mnt/nas/ml` from
`192.168.40.40:/var/nfs/shared/machine_learning`, NFSv3, soft, `sec=sys`, root
owned 988:988 mode 0770. Existing folders there (jobs, datasets, registry, big
data, runs, dsh-workspace) are unrelated, and no blob directory exists. The
preflight does not ban this export by name. A blob namespace there can pass
only as a dedicated top-level directory (for example `/mnt/nas/ml/garage-blobs`)
owned by the Garage uid with no group or other access, with a NAS-enforced hard
quota scoped to exactly that directory. The export root itself, a folder inside
another consumer's folder, and a whole-share cap all refuse. Whether the NAS
can enforce such a quota, its root-squash behaviour and its physical capacity
are unknown and must be qualified live.

With 0770 at the export root, the Garage gid and the identity running the
preflight need traverse permission there. Under root squash, a root preflight
may get permission denied; that refuses, which is the intended outcome until
the run identity is qualified.

## Limitations

- The preflight is a point-in-time startup check, not continuous enforcement.
  The wrapper runs it at converge time and the prepare example runs it again
  before rendering. Swarm task restarts and rescheduling run neither, and a
  mount lost or replaced after the check is not detected.
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
RPC secret, establish and evidence the physical caps, run the live
full-volume, mount-loss, consistent-restore, fresh-client and last-copy
qualifications, fill `objectstore_storage`, record the digest from `--digest`,
rename the examples, and add a `swarm_stacks_*` entry with `pin` equal to the
storage host, `prepare_tasks: swarm-prepare.yml` and `env_extra` for
`OBJECTSTORE_HOST`, `OBJECTSTORE_UID`, `OBJECTSTORE_GID`,
`OBJECTSTORE_DATA_PATH`, `OBJECTSTORE_METADATA_PATH` and
`OBJECTSTORE_RPC_SECRET`. Routes, buckets and keys are later work.
