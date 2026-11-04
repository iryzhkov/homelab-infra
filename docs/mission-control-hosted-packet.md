# Canonical Homelab and Normandy packet

The current Akasha repair selects official host-v0.2.16 at source179f465:
archive SHA-256 d9cdc7e86a37b8559a534e99bfa3216a127db5c701c8e86f51291bc52f8cc186.
Workflow41790129018 passed all four jobs. Package verification checked all11
safe regular members against the manifest and GitHub asset digests. The release
is not GitHub-immutable; pin the verified checksum.

Akasha alone uses /opt/chaldea-akasha-host/releases and
/var/cache/chaldea-akasha-host. The existing canonical artifact role receives
these parameters only in its runtime invocation. The Akasha service receives
the matching executable path. Shared trust/job022 and clock012 keep their old
roots, versions, caches and preservation lists. No installer code is changed.

This separate root is required to preserve all ten existing central host
releases022/023/024/026/027/028/029/0210/0214/0215 and their20 cached assets.
The installer supports at most eight explicit prior versions plus selected and
newest prior. Installing016 into that full root would collect old assets.
The new independent root starts with016 only. Do not alter old directory mtimes,
delete rollback files, or expand retention limits to make an install pass.

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
    -e mission_control_laptop_host_version=host-v0.2.16 \
    -e mission_control_laptop_host_sha256=d9cdc7e86a37b8559a534e99bfa3216a127db5c701c8e86f51291bc52f8cc186 \
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
remain gaps. Rollback restores the captured prior service unit and existing015
executable without running the new independent-root installer on the old root.
Keep data/access state intact; root owns any subsequent source rollback.
The existing separately authorized route-canary rollback still uses
`-e '{"akasha_route_canary_enabled": false}'` to remove its two route rows while
preserving the original28 registrations. This repair does not select that rollback.

Collector rollout is owned by the fleet source play
playbooks/akasha-collector-upgrade.yml. It binds each existing account/host and
uses the same canonical artifact installer in the independent root. It preserves
the helper, database, spool, lock and Mission Control snapshot inputs. Do not use
the enrollment play as an upgrade shortcut.

Normandy reader/web remains on the existing qualified client-v0.4.19 repair
packet vars/mission-control-repair-runtime.yml. This task changes neither that
runtime nor its permissions. Canonical installer drift checks remain required.
Historical repair receipts remain in Jocasta:
tooling/handoffs/akasha-final-runtime-acceptance-20261024.md.
