# Canonical Homelab and Normandy packet

The current Akasha repair selects official host-v0.2.17 at source fe6d5cfd4c4a6fdb2d8530e7c5f2f54894edc0ed:
archive SHA-256 568aa56c1b28e013e6c3d7c5813795428943b6cc1c2ba78cfd17e8becf9e3747.
Workflow41802158484 passed all four jobs. Package verification checked all11
safe regular members against the manifest and GitHub asset digests. The release
is not GitHub-immutable; pin the verified checksum. All four jobs passed on
attempt1. The tag is annotated and unsigned; source is in main148b9cb.
Service SHA-256: 169c212924fc0dd21164e6fcdfe35b45f6ec8bf0b341198c784adcf57329f3bf.
Collector SHA-256: 19e562f5c0d3c5df16c0c219effe7cd9817ab06e023d211dedfdd9445a4ce8c7,
identical to016. This selection upgrades only the central service; all six
collectors remain on016. The auth optimization and larger client pages still
require a fresh full30day report proof after activation. No report completion
is claimed by these source fixtures.

Akasha alone uses /opt/chaldea-akasha-host/releases and
/var/cache/chaldea-akasha-host. The existing canonical artifact role receives
these parameters only in its runtime invocation. The Akasha service receives
the matching executable path. Shared trust/job022 and clock012 keep their old
roots, versions, caches and preservation lists. No installer code is changed.

This separate root is required to preserve all ten existing central host
releases022/023/024/026/027/028/029/0210/0214/0215 and their20 cached assets.
The installer supports at most eight explicit prior versions plus selected and
newest prior. Installing016 into that full root would collect old assets.
The independent root currently contains016. Before EACH install require016 to
be the actual newest prior in both release and cache membership. The empty
explicit preserve list then retains selected017 plus newest prior016, including
the current collector and actual service rollback bytes. STOP on another set or
newest prior. Do not alter directory mtimes, install missing old versions, delete
rollback files, or expand retention limits to make an install pass.

Before EACH live call, capture fresh old release/cache membership, hashes,
ownership, modes, links and persistent metadata. Require all old assets unchanged
after CHECK/REAL/SECOND0. Source fixtures prove installer scoping, not live state.

The canonical play retains exact qualified-artifact, activation and permission
guards. The existing mission_control_laptop_* names attest artifact compatibility;
they do not enable a laptop or grant access. Preserve all existing registration,
Operations, quota and route-canary selections exactly. The repair adds no
registrations, grants, enrollment, keys, signer, profile or Node upgrade.

Run with pinned Ansible2.19.14 and the existing private operator activation packet.
LIVE below is its absolute private path. It must preserve the current public
configuration and verified interface/backup flags, and must not override runtime
roots, pins or preservation lists.

```sh
akasha_repair() {
  ansible-playbook -i inventory/hosts.yml playbooks/akasha.yml --limit homelab \
    -e @"$LIVE" -e @vars/mission-control-homelab.yml \
    -e mission_control_laptop_qualified=true \
    -e mission_control_laptop_host_version=host-v0.2.17 \
    -e mission_control_laptop_host_sha256=568aa56c1b28e013e6c3d7c5813795428943b6cc1c2ba78cfd17e8becf9e3747 \
    -e akasha_activate=true "$@"
}
akasha_repair --tags runtime --check --diff
# Review expected artifact-only changes, then stage with --tags runtime.
akasha_repair --check --diff
# Review exact unit/config changes before REAL.
akasha_repair
akasha_repair
```

Require SECOND0 changed=0, failed=0, unreachable=0 and actual SYSTEM verification.
An already active service requires akasha_activate=true; false refuses safely.
Runtime-only staging must retain the exact qualification packet and old-asset
evidence. Before activation take the existing WAL-aware private rollback backup;
preserve access/trust state, credentials, broker and all existing pending data.

Verify health, authenticated retained tool_call pagination, fresh occurrence
timestamps, status-reader readiness and existing public feed after activation.
A server restart resets in-flight cursors through410. New traversals must work.
Do not assert complete historical coverage: real pruning and missing telemetry
remain gaps. Rollback restores the captured prior service unit and existing
/opt/chaldea-akasha-host/releases/host-v0.2.16/host/akasha-service.mjs
without running an installer or touching the shared root.
Keep data/access state intact; root owns any subsequent source rollback.
The existing separately authorized route-canary rollback still uses
`-e '{"akasha_route_canary_enabled": false}'` to remove its two route rows while
preserving the original28 registrations. This repair does not select that rollback.

Collector rollout is owned by the fleet source play
playbooks/akasha-collector-upgrade.yml. It binds each existing account/host and
continues to select016 through the canonical artifact installer in the
independent root. No collector rollout is needed for this service update. It preserves
the helper, database, spool, lock and Mission Control snapshot inputs. Do not use
the enrollment play as an upgrade shortcut.

Normandy reader/web remains on the existing qualified client-v0.4.19 repair
packet vars/mission-control-repair-runtime.yml. This task changes neither that
runtime nor its permissions. Canonical installer drift checks remain required.
Historical repair receipts remain in Jocasta:
tooling/handoffs/akasha-final-runtime-acceptance-20261024.md.
