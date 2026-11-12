# Akasha homelab convergence packet

The current Akasha repair selects official host-v0.2.18 at source
72021bafaff39eb8d03ba1ae115b3e59952bffcc, removing Mission Control and fork-era
features. Archive SHA-256:
34d2fd21fc04cd946e6b3bf586d1e47aad22ae2b31f4cc7a797f4dbb28fc4b7c.
The downloaded archive matches the GitHub release asset digest. Pin this checksum.
This selection upgrades only the central service; all six collectors remain on016.
Deploy only with the post-PR159 registry, which excludes retired MC keys.
The auth optimization and larger client pages still require a fresh full30day
report proof after activation. No report completion is claimed by source fixtures.

Akasha alone uses /opt/chaldea-akasha-host/releases and
/var/cache/chaldea-akasha-host. The existing canonical artifact role receives
these parameters only in its runtime invocation. The Akasha service receives
the matching executable path. Shared trust/job022 and clock012 keep their old
roots, versions, caches and preservation lists. No installer code is changed.

This separate root is required to preserve all ten existing central host
releases022/023/024/026/027/028/029/0210/0214/0215 and their20 cached assets.
The installer supports at most eight explicit prior versions plus selected and
newest prior. Installing016 into that full root would collect old assets.
The independent root must initially contain016/017, with matching archive/manifest
cache pairs and actual newest prior017. The runtime role explicitly preserves016
for collectors, while selected018 plus implicit newest prior017 retains service
rollback. After staging require exactly016/017/018 and all six cached files.
Before EACH install require017 to remain the actual newest prior excluding018.
STOP on another set or newest prior. Do not alter directory mtimes, install missing
old versions, delete rollback files, or expand retention limits to make an install pass.

Before EACH live call, capture fresh old release/cache membership, hashes,
ownership, modes, links and persistent metadata. Require all old assets unchanged
after CHECK/REAL/SECOND0. Source fixtures prove installer scoping, not live state.

The canonical play retains exact qualified-artifact, activation and permission
guards. The akasha_route_host_qualified, akasha_route_host_version and
akasha_route_host_sha256 fields attest exact artifact compatibility. Preserve all existing registration,
Operations, quota and route-canary selections exactly. The repair adds no
registrations, grants, enrollment, keys, signer, profile or Node upgrade.

Run from operator host omarchy-pc, targeting homelab, with pinned Ansible2.19.14 and the existing private operator activation packet.
LIVE below is its absolute private path. It must preserve the current public
configuration and verified interface/backup flags, and must not override runtime
roots, pins or preservation lists.

```sh
akasha_repair() {
  ansible-playbook -i inventory/hosts.yml playbooks/akasha.yml --limit homelab \
    -e @"$LIVE" -e @vars/akasha-homelab.yml \
    -e akasha_route_host_qualified=true \
    -e akasha_route_host_version=host-v0.2.18 \
    -e akasha_route_host_sha256=34d2fd21fc04cd946e6b3bf586d1e47aad22ae2b31f4cc7a797f4dbb28fc4b7c \
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
/opt/chaldea-akasha-host/releases/host-v0.2.17/host/akasha-service.mjs
without running an installer or touching the shared root.
Keep data/access state intact; root owns any subsequent source rollback.
The existing separately authorized route-canary rollback still uses
`-e '{"akasha_route_canary_enabled": false}'` to remove its two route rows while
preserving the 23 remaining non-route registrations. Normal convergence preserves
those 23 rows plus the two enabled FATE route rows, for 25 total; only the five
retired dashboard rows are removed from the former 30-row live selection. This repair does not select that rollback.

Collector rollout is owned by the fleet source play
playbooks/akasha-collector-upgrade.yml. It binds each existing account/host and
continues to select016 through the canonical artifact installer in the
independent root. No collector rollout is needed for this service update. It preserves
the helper, database, spool and lock. Do not use
the enrollment play as an upgrade shortcut.

Historical repair receipts remain in Jocasta:
tooling/handoffs/akasha-final-runtime-acceptance-20261024.md.
