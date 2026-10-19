# Canonical Homelab and Normandy packet

Completed canary evidence and limitations:
jocasta:7809f6329c87f8a3c5390221ee4d0d2e@2.
Source package qualification: jocasta:623f53e1c960b0383b2a44b3afcbb981@1.

vars/akasha-runtime.yml now selects the already qualified Homelab host-v0.2.4,
SHA531710066a7f67ac68f82ad1234d8f9a32e3311c839a894f86125c7d4f170592.
Release416722339/archive654523759/run39167630357; manifest654523754
SHA0fd697257963498e1f946e4fb5274a0c141cf5287120b6d6617721aa0e788a72.
vars/akasha-runtime.yml supplies preserve_versions:[host-v0.2.2,host-v0.2.3] to
both artifact calls in the Homelab play, including the shared-trust include. Global vars/chaldea-runtime.yml/shared trust remain022.
vars/mission-control-homelab.yml retains explicit existing permission and activation
gates without duplicating the runtime pin or artifact preservation.

The existing always-tagged guard rejects host023 when the effective registry contains
mission_control_read_sources. Keep it. Restore matching old registry/config before a
023 rollback and retain antirollback state, keys and backups. Never restore old access
databases. The packet's akasha_activate:false still refuses an active server; runtime
tags are a separate artifact phase. No Akasha lifecycle change is implied.

Existing enrollment additions preserve old23 records by key/content plus the two
scoped MC records. The SAME qualified native render supplied registry/ceiling/helper
equality. The separate proposal remains empty/unused; do not request its validator
option or invent grants. No signing/publication/publisher work belongs to this cleanup.

For Normandy, follow mission-control-hosted.md. The new composition requires the exact
qualified fleet source f7acedfed358f398d1e9f8f2ce6e73c0a2d9ce71 from fleet PR68.
The old live dependency
remains valid for the deployed old composition until a reviewed source update.
client-v0.4.4 SHAda4b457e7885ffea3dde51c7e93160e90f2e81566d2e33fb9211fb07ca146ca4
remains independent of helper040/Node26.12 and user clients.
Source fixtures do not substitute for actual target SYSTEM verification, namespace/DAC,
BPF, live broker, real thread transition or outage qualification. Root owns combined
reviews, native CI, exact reviewed merges and all separately authorized live actions.
