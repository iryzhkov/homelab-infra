# Homelab / Normandy held Mission Control packet

SOURCE ONLY; no live host/grant/account/unit/publisher change. See fleet docs/mission-control-hosted-packet.md for the SAME canonical render, actual release digests, phase gates and rollback. Qualified report jocasta:623f53e1c960b0383b2a44b3afcbb981@1; actual packages came from reviewed Chaldea main6f3aa16a7c2b6d2cf517d7e8bb3f4b5e3ab7f5c2. ROOT owns new security-packet reviews, exact CI, merges and deployment release.

vars/akasha-enrollment-registry.yml contains the two scoped registrations in its existing additions list. Existing playbooks/akasha.yml loads them only when existing akasha_enrollment_registry_enabled is true. Existing default gates stay false. The old23 records remain exact by key/content; new additions precede existing PC rows. The separate vars/mission-control-proposal.yml stays empty/unused. Validate the actual qualified fleet generator render with scripts/check-enrollment-registry.py native.json --pc-reader --pc-quota, WITHOUT --mission-control (that option still refuses the empty unused proposal). Preserve six issuer keys and every old ceiling/grant; do not duplicate a complete helper-grant list into this repo.

## Homelab server only

I38 is MERGE-HELD together with F67 for ROOT's exact reviewed permission packet and coordinated Homelab host-v0.2.4 service phase. The enrollment gate is already true on the live server. Every future akasha.yml run whose effective registry contains mission_control_read_sources MUST select exact qualified host-v0.2.4; the first always-tagged pre-task refuses before composition, runtime artifacts or configuration writes, including --tags runtime. Unused additions with enrollment disabled and a legacy base registry stay inert.

A downgrade to host-v0.2.3 MUST first restore the previous registry and matching configuration without the new reader field, then perform the canonical restart. Keep access/antirollback databases and high-water state: never restore old access-state databases to bypass antirollback. Both current shared2.2 and server/rollback2.3 must remain available throughout staging/cutover.

vars/mission-control-homelab.yml is NOT imported by any play. It is a held explicit EXTRA-VARS packet selecting actual host-v0.2.4/SHA531710066a7f67ac68f82ad1234d8f9a32e3311c839a894f86125c7d4f170592 and preserve_versions:[host-v0.2.2,host-v0.2.3]. Archive654523759/manifest654523754, manifestSHA0fd697257963498e1f946e4fb5274a0c141cf5287120b6d6617721aa0e788a72, release416722339/run39167630357. Current vars/akasha-runtime.yml remains0.2.3 and vars/chaldea-runtime.yml remains2.2. No trust upgrade or fleet rollout.

Future command shapes, NOT authorized/executed:
```sh
ansible-playbook playbooks/akasha.yml --limit homelab --tags runtime -e @vars/mission-control-homelab.yml --check --diff
# After ROOT releases inspected runtime-only real/second-real phases:
ansible-playbook playbooks/akasha.yml --limit homelab -e @vars/mission-control-homelab.yml -e akasha_activate=true --check --diff
```
The file deliberately says akasha_activate:false. A full play with false on the CURRENT ACTIVE server REFUSES; it is not a staging mode. Runtime-only --tags runtime is a separate artifact/retention phase, with trust2.2 AND running/rollback server2.3 preservation checked BEFORE every GC include. Privately back up the existing host0.2.1 rollback tree/cache before GC; do not add an all-host preserve policy. The separately reviewed service phase requires explicittrue only after new host package, registry/permission equality, existing runtime/DAC/backups and live broker gates. Source qualification used an external projector fixture, not the real NATS broker. Existing PCreader+sevenquota grants remain true in the explicit packet; separate sharing stays false. Do not widen or replace them.

## Normandy / agent-a only

Use existing playbooks/mission-control.yml with literal target omarchy-normandy and --limit omarchy-normandy, exact clean canonical F checkout0a47aa2eee09901e7544ca618537f80fb2706e22 and ANSIBLE_LIBRARY=ABS_F/library. That immutable commit remains valid; do not repin because later fleet commits changed tests/data. Controller Git HEAD/clean guards remain mandatory, snapshots without .git do not prove the pin.

Pass qualified client-v0.4.4 inputs as explicit extra-vars from the reviewed fleet deployment packet; every runtime/reader/web/ingress/route flag remains false until its separate released phase. Existing play vars_files outranks inventory: hostvars are insufficient. Canonical roles enable/start services when their flags are true. Prepare exact private staged units and qualify them first; do not assume a stage-only flag exists.

UID940 is selected in the public packet contingent on immediate NSS/name/UID collision recheck. Required reader sudo ONLY exact issuer command, issuer-only keys/state DAC, unit namespace ability with /var/lib/chaldea-issuer/access writable to the issuer but not reader directly, cache2750 SGID/file0640 atomic replacement and web read/no-write checks. Web has no sudo/helper/token/T3; NNPyes, strictsystem/home and loopback7392.

Normandy SYSTEM-mode verification of EXACT rendered mission-control.service, mission-control-reader.service, mission-control-ingress.socket and mission-control-ingress.service is mandatory BEFORE any enable/start. Preserve DynamicUser/hardening; user-mode/job /run limitations are not a pass. Source compile +BPF is not actual enforcement proof. Qualify exact peer-only7393 separately; no Akasha1936 change. Any missing UDM rule must be edges20.33/.4 ->Normandy70.234 TCP7393 only. LAN actual-peer ranges20/70/90/130, no trusted arbitraryXFF/Cf-Ray bypass, existingcatchall302, Host/Origin, SSE and natural non-PC<90s/outage checks remain live gates. Existing wildcard DNS/TLS requires no DNS change.

Agent-a capture and optional collector host0.2.4/snapshot input are separate reviewed fleet phases, not an all-host rollout. Preserve normal T3/quota services, token custody and existing30s+5random collector cadence. Public issuer source merging may feed the existing Normandy publisher's natural fetch_grants: ROOT must gate merge/publication order, not assume draft source authorizes signing.

Record operator ownership, paths/modes/PIDs/timer state and collision guards before each phase. Backup/restore only exact owned files with current-content guards; explicitly stop/disable owned MC services/route for rollback, retain keys/state/history and host2.2, restore held pull timers in finally. ROOT owns homelab registry/server/bundle rollback and all live operations.
