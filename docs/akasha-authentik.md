# Akasha Authentik coexistence deployment

This packet is source-only until the root executor deploys it. P3.3 step 2 producers stay on PKI.
The new resource uses client_id/application slug `chaldea-akasha`, scope `akasha.ingest`,
and resource/audience `https://akasha.ryzhkov.dev`. Issuer is exactly
`https://auth.ryzhkov.dev/application/o/chaldea-akasha/`, JWKS is
`https://auth.ryzhkov.dev/application/o/chaldea-akasha/jwks/`, cache TTL 60 seconds.
The provider uses the existing protected RS256 signer pin, client_credentials only,
and access_token_validity seconds=300. Both Akasha and rotation explicitly pin
sub_mode hashed_user_id, encryption_key null, include_claims_in_id_token false,
and refresh_token_validity seconds=0. These are writable/readable fields in the
pinned Authentik serializer; refresh grants and ID-token claims are unnecessary
for this client_credentials-only resource. Exact readback checks every field. Akasha enforces the 300-second lifetime and
60-second skew. R1 accepts the residual validity and Authentik outage trade-offs.

The sole scope mapping overrides aud and emits host from a fixed reviewed map:
laptop maps to omarchy-macbook-air-igor, normandy to omarchy-normandy, and the
other four agent-host names map to themselves. preferred_username is chaldea-
plus that registry host; stable run_id remains the account username chaldea-HOST. Authentik supplies sub, iss, iat and exp. No custom nbf
or source selector is emitted. No API scope or grants are attached. All six existing
producer hosts are eligible; their exact sources, kinds, roots and read permissions
remain the Akasha registry's authority. Normandy gets a new chaldea-normandy account
with no Jocasta, OV, HoloNet, Shizune, HA, observer or rotation grant. Rotation
membership and client attributes are restricted to laptop, omarchy-pc, agent-a,
agent-b and homelab. It uses the normal
host credential custody path /home/igor/.config/chaldea/authentik/host.app-password,
mode 0600 with private parents and exact node/UID binding. No producer consumes it yet.

Keep the complete existing protected host cohort, owner, receipt and baselines.
Append Normandy's explicit node/UID, generation 1 and UTC expiry within 60 days.
Set authentik_agent_akasha_signing_key to the existing resource-provider RS256 key UUID,
equal to authentik_agent_rotation_signing_key. Confirm this against current protected
resource-provider ownership evidence. This creates receipt-owned objects; name
collisions and modified bodies refuse adoption. A returned provider PK is persisted
before response comparison. A pending provider can be recovered only by that exact
PK and a live body matching every compared field, including empty
jwt_federation_sources and jwt_federation_providers. CHECK does not write the
journal; REAL commits the verified provider before further writes. A pending write
without a returned PK requires operator reconciliation and is never retried.
Subsequent runs retain the pin from the receipt. The account attributes and existing
resource mappings do not gain Akasha permissions. Admission policy pins the exact
active cohort and uses the existing host role/group/revocation guard.

## Check-first sequence

Run from omarchy-pc at the reviewed PR head with existing protected inputs.
Do not run these commands as part of the builder task.

1. Capture private rollback backups of Authentik database/config, the host enrollment
   journal and protected input; take the existing WAL-aware Akasha backup. Preserve
   events.db, access/trust state, current config/unit, credentials and broker state.
   Verify backup readability and record private locations without logging secrets.
2. Run the Authentik check below, review all predicted changes, then apply, repeat
   for zero changes, and check again. Retain the existing legacy-writer declaration.
   Deliver Normandy's credential through the existing guarded delivery tasks, never
   through stdout or a second persistent password file.

```sh
ansible-playbook -i "$PROTECTED_INVENTORY" playbooks/authentik-agent-hosts.yml -e @"$PROTECTED_AGENT_HOSTS" --check
ansible-playbook -i "$PROTECTED_INVENTORY" playbooks/authentik-agent-hosts.yml -e @"$PROTECTED_AGENT_HOSTS"
ansible-playbook -i "$PROTECTED_INVENTORY" playbooks/authentik-agent-hosts.yml -e @"$PROTECTED_AGENT_HOSTS"
ansible-playbook -i "$PROTECTED_INVENTORY" playbooks/authentik-agent-hosts.yml -e @"$PROTECTED_AGENT_HOSTS" --check
```

3. Verify current artifact roots and caches before each Akasha invocation.
   The independent runtime root must contain 017/018 with matching cache pairs,
   newest prior018 excluding selected020. After installation expect017/018/020.
   Explicit018 keeps the fleet collectors (akasha_collector_version runs from this
   root) and is also the newest prior release; explicit017 keeps the older
   rollback. 016 was pruned before this packet and is not required. Never change
   mtimes or install missing versions to pass retention. Shared022 and clock012 stay pinned.
   Use the existing reviewed activation packet and exact artifact attestation:

```sh
akasha_dual() {
  ansible-playbook -i "$PROTECTED_INVENTORY" playbooks/akasha.yml --limit homelab \
    -e @"$PROTECTED_AKASHA" -e @vars/akasha-homelab.yml \
    -e akasha_route_host_qualified=true \
    -e akasha_route_host_version=host-v0.2.20 \
    -e akasha_route_host_sha256=ed967a7d018b4b46e9578fe0b272da9977e04b8f2c81a1fbdfe11cfbaa6a9063 \
    -e '{"akasha_activate":true,"akasha_authentik_enabled":false}' "$@"
}
akasha_dual --tags runtime --check --diff
akasha_dual --tags runtime
akasha_dual --check --diff
akasha_dual
akasha_dual
```

4. Require zero changes on the repeat, health and an authenticated existing PKI
   producer append/read canary with JWT acceptance still off. Preserve registry
   selections from the protected packet. An active service needs activate=true.
5. Enable only the verifier through a JSON boolean override, check then apply:

```sh
akasha_dual -e '{"akasha_authentik_enabled":true}' --check --diff
akasha_dual -e '{"akasha_authentik_enabled":true}'
akasha_dual -e '{"akasha_authentik_enabled":true}'
```

6. Verify PKI still works. Exchange one enrolled host's app password in memory
   using client_credentials, its chaldea-HOST username, client_id chaldea-akasha,
   scope akasha.ingest and resource https://akasha.ryzhkov.dev. Verify signature,
   pinned issuer, exact audience, host, preferred_username, stable run_id and
   exp minus iat at most 300, without logging the bearer/password.
   Submit a disposable event with X-Akasha-Source selecting that host's registered
   source and an allowed kind. Require acceptance and registry-derived provenance.
   Try an unregistered kind on that same source and another host's source; require
   authorization refusal with no event persisted. Confirm the provider refuses
   goauthentik.io/api and a revoked/inactive host. These are root-owned live checks,
   not claims made by source tests.

Rollback first disables akasha_authentik_enabled using the same check/apply path.
Disabled rendering omits every authentik key, preserving the 018 config schema.
Existing PKI producers keep working. If runtime rollback is needed, restore the
captured config/unit and host-v0.2.18 executable without running GC or changing
the shared root; preserve data and trust state. Do not revoke or delete PKI keys.
