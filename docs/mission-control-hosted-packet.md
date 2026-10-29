# Canonical Homelab and Normandy packet

Completed Mission Control canary evidence and limitations:
jocasta:7809f6329c87f8a3c5390221ee4d0d2e@2.
Source package qualification: jocasta:623f53e1c960b0383b2a44b3afcbb981@1.

The H+PC FATE canary selects the verified official host-v0.2.15 archive in
vars/akasha-runtime.yml, SHA
550f5b5c2ff858ed34dcb1de400a8cb2db847e036d88f71f56486cce2a72ab9f,
qualified from source d0bc481. Shared trust remains host-v0.2.2 and the independent
clock runtime remains host-v0.2.12. No fleet host runtime upgrade is selected.

The canonical play retains its always-tagged exact qualified-artifact assertion.
The existing mission_control_laptop_* attestation names identify the qualified
artifact; they do not enable laptop permission. The canary also requires a
boolean selection, enrollment and an exactly attested host of at least 0.2.15.
vars/mission-control-homelab.yml selects akasha_route_canary_enabled only alongside
its existing enrollment/PC flags. It still sets akasha_activate:false, which
REFUSES on an active server. The service phase needs the reviewed true override
and the existing interface/backup acceptance inputs.

The canary appends exactly two service registrations to the current 28:
Homelab route-homelab, issuer chaldea:homelab, kind route_score_snapshot;
PC route-omarchy-pc, issuer chaldea:omarchy-pc, kind route_pick_changed and
route_scores_read:true. Both use source route, profile route-service and project
chaldea. All 28 existing rows, the four Mission Control status sources and the
Operations kinds/reader flag must remain equal. No quota sharing is added.
Source registrations grant nothing without their matching signed ceilings and
helper grants. Root owns those additions using the existing Normandy signer,
H route append and PC route read only, audience akasha. No new signer or publisher
role belongs to this packet. Root handles the existing AA example publisher units.

The published, independently reviewed fleet max-eight prerequisite is
commit dba4e4c7c459ccfe2b433c5cf39ea71c910cb3d4 in fleet PR132.
The installer-role drift check pins that exact public commit. Its full CI and
merge remain deployment gates. Never weaken or skip the exact copied-role guard.
The existing max-eight/ten-release CHECK/REAL/SECOND0 fixture receipt is
89c72ff4-0060-4f80-86c2-9541810dddb7. Current composition checks additionally use
the canonical preserve lists and render the real Akasha configuration template.

Preserve all nine old host releases 022/023/024/026/027/028/029/0210/0214 and their
matching archive/manifest caches. With selected0215, ten releases and twenty cache
files survive each call. The runtime call explicitly keeps eight old022..0210 and
implicitly keeps selected0215 plus newest prior0214. The later shared022 call
uses akasha_trust_preserve_versions, eight old023..0214, plus selected022 and newest
prior0215. These are separate per-call lists. Never override the global runtime
preserve variable through extra vars in the full play.
Before EACH call, obtain fresh release and matching cache membership, root
ownership, no-symlink and directory mtime evidence. Stop on a different set or
newest prior. Never install missing old versions or alter target mtimes.

The following commands are a source runbook for root, not execution by this worker.
Use the reviewed clean infra/fleet heads only after the fleet PIN dependency,
combined review and CI pass. LIVE is an absolute private path to the existing
operator inputs, preserving all current MC permissions/Operations scope and
interface/backup acceptance evidence. It must not override runtime/preserve
selectors or introduce other enrollment. Capture the current 28-row config and
unit/activation state before CHECK; do not print credentials.

```sh
akasha_canary() {
  ansible-playbook -i inventory/hosts.yml playbooks/akasha.yml --limit homelab \
    -e @"$LIVE" -e @vars/mission-control-homelab.yml \
    -e mission_control_laptop_qualified=true \
    -e mission_control_laptop_host_version=host-v0.2.15 \
    -e mission_control_laptop_host_sha256=550f5b5c2ff858ed34dcb1de400a8cb2db847e036d88f71f56486cce2a72ab9f \
    -e akasha_activate=true "$@"
}
akasha_canary --check  # CHECK: review only the expected artifact/config changes.
akasha_canary          # REAL: only after fresh per-call preservation evidence.
akasha_canary          # SECOND0: require changed=0 and the same exact 30 rows.
```

Stop after any refusal, unexpected change or failed actual SYSTEM unit verification.
Runtime-only --tags runtime is a separate artifact phase, not safe full-play staging
with akasha_activate:false. That phase must retain the exact qualified packet and
fresh per-call preservation evidence too. No PC or Normandy play is included above.

Rollback removes the two route registrations first with
-e akasha_route_canary_enabled=false, retaining the exact original 28 and MC scope.
Disable the associated publisher/reader selection and signed/helper route grants
under root's coordinated fleet rollback. Restore the captured matching config if
necessary. For a runtime rollback, the previously selected host-v0.2.14 SHA is
969319cb2dbde4978604edd109ce543711a6960c54d5e45cb1138d2a2f23c0ec.
Override akasha_host_version/sha256 and the matching qualified artifact attestation
together, with canary false and all ten releases/caches still retained.
Selected0214/newest prior0215 plus the runtime's eight explicit old022..0210 keeps
the ten-member set; shared022 retains its separate eight-entry list.
Recheck actual membership/mtime evidence before either call. Retain antirollback
state, keys and backups. Never restore old access databases. An older023 rollback
also requires its matching old registry/config. Root owns all live rollback.

For the Normandy repair, use the canonical playbooks/mission-control.yml with a
clean fleet checkout at exact revision bc3d4c9c1ae3e2f0c88315c3c3d179a3b82944bf.
It imports the canonical mission_control_runtime and mission_control_reader roles.
Pass the new two-key vars/mission-control-repair-runtime.yml packet explicitly;
it is never auto-imported and carries no enablement, activation or permission flags.
After ROOT's official client-v0.4.19 comparison, the operator appends these arguments
to the existing invocation, preserving its current flags and configuration:

```sh
ANSIBLE_LIBRARY="$F/library" ansible-playbook -i inventory/hosts.yml \
  playbooks/mission-control.yml --limit omarchy-normandy \
  -e "mission_control_fleet_checkout=$F" \
  -e mission_control_fleet_revision=bc3d4c9c1ae3e2f0c88315c3c3d179a3b82944bf \
  -e @vars/mission-control-repair-runtime.yml
```

Here F is the absolute clean checkout of that exact dependency. Retain the existing
explicit flags/config arguments in the command above. The packet only changes the
reader and web's existing independent client runtime from client-v0.4.14 to
client-v0.4.19. It selects no fleet site ownership, new grants, Node pin or helper
runtime. ROOT owns review, CI and separately authorized deployment.
Source fixtures do not substitute for actual target SYSTEM verification, namespace/DAC,
BPF, live broker, real thread transition or outage qualification. Root owns combined
reviews, native CI, exact reviewed merges and all separately authorized live actions.
