# Canonical Homelab and Normandy packet

Completed canary evidence and limitations:
jocasta:7809f6329c87f8a3c5390221ee4d0d2e@2.
Source package qualification: jocasta:623f53e1c960b0383b2a44b3afcbb981@1.

Current Homelab version and archive SHA come from vars/akasha-runtime.yml.
Use the current qualified publication packet and fresh preservation sets for any
upgrade. The following immutable receipts describe the previous host-v0.2.9,
SHA37a30cf6b3fb2a05180b2c288d07131a86da12ed35ab2dae19d9a32e87ae6bcc.
Archive657522711/run39329948376; manifest657522709
SHA79dc7a05d3e356968719335a987632a1956b2d698e5f632c783ac86a57bd3550.
Actual download/native qualification: jocasta:cd2e425300544d65e90ba99c4d4bbc2e@1.
vars/akasha-runtime.yml supplies separate per-call preserve lists: the runtime call
uses chaldea_host_artifact_preserve_versions and the shared-trust include uses
akasha_trust_preserve_versions. Global vars/chaldea-runtime.yml/shared trust remain022.
The artifact role accepts at most seven unique exact preserved versions. For the
nine-member set 022/023/024/026/027/028/029/0210/013, runtime013 needs explicit
022/023/024/026/027/028/029 plus newest prior0210. The subsequent shared022 call
needs explicit 023/024/026/027/028/029/0210 plus newest prior013. Seven is the actual
maximum and the minimum needed here; selected plus newest prior remain implicit.
Before EACH call, obtain fresh release and matching archive/manifest cache
membership, root ownership, no-symlink and directory mtime evidence. Stop on a
different set or newest prior. Preserve all eight old releases and their cache
entries. Never install missing old versions or alter target mtimes.
ROOT must compare the official host-v0.2.13 publication against the qualified
candidate before source activation, merge or apply.
vars/mission-control-homelab.yml retains explicit existing permission and activation
gates without duplicating the runtime pin or artifact preservation.

The always-tagged guard requires the exact qualified version/SHA packet for a new
backend when the registry contains mission_control_read_sources. The qualification
attestation retains its existing mission_control_laptop_* names; it does not select
laptop permission. Old024 remains compatible without laptop selection. Restore
matching old registry/config before a
023 rollback and retain antirollback state, keys and backups. Never restore old access
databases. The packet's akasha_activate:false still refuses an active server; runtime
tags are a separate artifact phase. No Akasha lifecycle change is implied.

The qualified composition preserves all28 existing registry keys; Operations opt-in
changes only the existing reader flag/kinds, keeping its four status sources. The SAME qualified native render supplied registry/ceiling/helper
equality. The separate proposal remains empty/unused; do not request its validator
option or invent grants. No signing/publication/publisher work belongs to this cleanup.

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
