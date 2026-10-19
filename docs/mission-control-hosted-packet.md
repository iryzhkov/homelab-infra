# Canonical Homelab and Normandy packet

Completed canary evidence and limitations:
jocasta:7809f6329c87f8a3c5390221ee4d0d2e@2.
Source package qualification: jocasta:623f53e1c960b0383b2a44b3afcbb981@1.

vars/akasha-runtime.yml selects the qualified Homelab host-v0.2.6,
SHA504326b4d617b8d4f5972f9fab07f6c7c52f6194c1fc149eb9610d5dcba1d74d.
Archive655873917/run39233056206; manifest655873919
SHA3b802e13dd92e1d86719330423b1c04cd97d8ae92c0c4f9a51f6d68ce045350b.
vars/akasha-runtime.yml supplies preserve_versions:[host-v0.2.2,host-v0.2.3,host-v0.2.4,host-v0.2.6] to
both artifact calls in the Homelab play, including the shared-trust include. Global vars/chaldea-runtime.yml/shared trust remain022.
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

Existing enrollment additions preserve old23 records by key/content plus the two
scoped MC records. The SAME qualified native render supplied registry/ceiling/helper
equality. The separate proposal remains empty/unused; do not request its validator
option or invent grants. No signing/publication/publisher work belongs to this cleanup.

For Normandy, follow mission-control-hosted.md. The composition requires the exact
qualified fleet dependency selected by roles/mission_control/defaults/main.yml.
The old live dependency
remains valid for the deployed old composition until a reviewed source update.
The qualified client-v0.4.6 archive SHA is
ffd559aa332b7a6bb13ca59629bce29a57bca11fd9cf597106034fe7ee7ab3b0.
Its explicit runtime selection remains independent of helper040/Node26.12 and user clients.
Source fixtures do not substitute for actual target SYSTEM verification, namespace/DAC,
BPF, live broker, real thread transition or outage qualification. Root owns combined
reviews, native CI, exact reviewed merges and all separately authorized live actions.
