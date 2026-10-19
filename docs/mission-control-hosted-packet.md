# Canonical Homelab and Normandy packet

Completed canary evidence and limitations:
jocasta:7809f6329c87f8a3c5390221ee4d0d2e@2.
Source package qualification: jocasta:623f53e1c960b0383b2a44b3afcbb981@1.

vars/akasha-runtime.yml selects published Homelab host-v0.2.9,
SHA37a30cf6b3fb2a05180b2c288d07131a86da12ed35ab2dae19d9a32e87ae6bcc.
Archive657522711/run39329948376; manifest657522709
SHA79dc7a05d3e356968719335a987632a1956b2d698e5f632c783ac86a57bd3550.
Actual download/native qualification: jocasta:cd2e425300544d65e90ba99c4d4bbc2e@1.
vars/akasha-runtime.yml supplies separate per-call preserve lists: the runtime call
uses chaldea_host_artifact_preserve_versions and the shared-trust include uses
akasha_trust_preserve_versions. Global vars/chaldea-runtime.yml/shared trust remain022.
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

For Normandy, follow mission-control-hosted.md. The composition requires the exact
qualified fleet dependency selected by roles/mission_control/defaults/main.yml.
The old live dependency
remains valid for the deployed old composition until a reviewed source update.
The qualified client-v0.4.9 archive SHA is
4029833d7b013571ff375a8bb58d355fcab3ab54e682038a2a65a598751a1535.
Its explicit runtime selection remains independent of helper040/Node26.12 and user clients.
Source fixtures do not substitute for actual target SYSTEM verification, namespace/DAC,
BPF, live broker, real thread transition or outage qualification. Root owns combined
reviews, native CI, exact reviewed merges and all separately authorized live actions.
