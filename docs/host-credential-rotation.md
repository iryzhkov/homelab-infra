# Host app credential rotation

Source only. Deploy homelab-infra first, then fleet-config. Do not deploy fleet
rotation before the separate provider exists. Start with agent-b, retain existing
resource providers and signing keys, and extend to laptop, omarchy-pc, agent-a and
homelab only after the canary passes. PR #172 owns consumer cutover independently.

## Provider deployment

Use the existing protected full cohort, receipt and baseline packet. Add
authentik_agent_rotation_signing_key with an existing reviewed RSA signing-key UUID.
Run playbooks/authentik-agent-hosts.yml in check mode, apply, repeat and check.
The host writer journals its separate group/provider/application/binding in
hosts.json.rotation; name collisions or drift refuse adoption. Its provider
client_id is chaldea-host-rotation, issuer is
https://auth.ryzhkov.dev/application/o/chaldea-host-rotation/, grant type is
client_credentials, lifetime is 300 seconds and the only mapping is the built-in
goauthentik.io/api. The only binding admits chaldea-host-accounts. Host attributes'
reserved rotation slot now records the group UUID and client ID. Resource
memberships and claims stay bounded by the existing contract. The resource
preflight still refuses any API-scope mapping. No RBAC powers are assigned.
Only the existing exact Authentik managed per-user object-level view_token_key
role remains allowed.

Changing group membership/rotation metadata re-renders existing owned resource
expressions. Keep legacy blueprint writers frozen during coexistence. A provider
write interrupted before its receipt commit requires administrative reconciliation,
as with enrollment; host rotation itself has automatic recovery.

## Fleet deployment

Converge only the new host_credential_rotation role as the existing fleet user,
with host_credential_rotation_enabled=true. It installs a root-free daily
persistent user timer with up to six hours randomized delay. It is opt-in so
merging the source cannot start a timer before provider/canary deployment.
Set host_credential_rotation_revoked=true in authoritative fleet inventory for
a revoked host; apply the central revoked=true first. Fleet convergence removes
the password and pending journal under host.lock and writes a revocation
tombstone. A stale false inventory cannot clear that tombstone. Administrative
is_active=false independently refuses issuance even before fleet convergence.

The Python standard-library tool shares host.lock and host.generation.json with
enrollment. It checks expiry daily, rotates within 30 days and requests 60 days.
It persists rotation.json with old/new identifiers before POST, reads back the
same deterministic generation after a crash, verifies the replacement exchange,
atomically replaces the 0600 password and marker with fsync, then deletes the old
token. No additional long-lived key is stored. A crash leaves at most current
and pending server tokens; the next run finishes the same generation. Inactive
or expired credentials stop without retries. Failure emits fixed text through
feed post --source feed --severity fail, without exception bodies or secrets.

## Agent-b live canary, performed by root after review

1. Record both reviewed head SHAs, provider/group/binding IDs, account PK and
   baseline scopes without copying keys into logs. Deploy the provider first.
   Verify only the API mapping is attached, only the host group is bound,
   lifetime is at most 300 seconds, and every resource provider excludes API.
2. Install/enable only agent-b's fleet role. Inspect systemctl --user cat
   chaldea-host-rotation.service and systemctl --user list-timers.
   Run systemctl --user start chaldea-host-rotation.service. A token with more
   than 30 days left must remain unchanged.
3. In protected administrative tooling, shorten agent-b's token expiry to less
   than 30 days. Start the service. Check the generation increments once, file
   modes are 0600, directory is 0700, replacement exchanges and old password
   fails. Repeat and confirm no further rotation.
4. Using a disposable enrolled account and scratch HOME with the same code,
   terminate after create/before swap, then after swap/before delete. Repeat.
   Each run must recover the same generation, remove rotation.json and leave
   one live token. Unit tests inject these exact boundaries, including before
   marker commit and after deletion. Never crash the production bootstrap writer.
5. Revoke the disposable account during both pending stages. Apply central
   revoked=true and fleet host_credential_rotation_revoked=true. Check inactive
   account, no server tokens, no local password, revoked marker. Reapply the
   previous false inventory packet: account and marker must remain disabled.
   Attempt user PATCH is_active=true and revoked=false with the old API JWT;
   require refusal and unchanged administrative readback.
6. Leave a disposable credential to expire or administratively expire it,
   simulate the host's offline return and run twice. Require exchange refusal,
   no token creation and no reactivation. Check a failure feed entry and that
   the service has no Restart policy.
7. Replay an actual rotation JWT as Authorization bearer to each protected
   resource endpoint: Jocasta https://jocasta.mcp.ryzhkov.dev/mcp,
   OV https://ov.mcp.ryzhkov.dev/mcp, and HoloNet
   https://media.mcp.ryzhkov.dev/mcp. Use their normal MCP initialize POST and
   protocol headers. Require 401/403, never an accepted MCP response. Inspect
   claims privately: issuer/client audience is rotation-only, API scope only,
   no resource grants. Also verify independently against each configured
   resource audience; reject issuer/audience mismatch. Retain redacted statuses,
   not JWTs. This live replay is a deployment gate, not claimed by fake tests.
8. Wait through the 300-second JWT lifetime plus consumer skew when checking
   offline revocation. Confirm normal resource exchanges still work. Only then
   enable remaining enrolled hosts, one at a time.

## Deliberate manual re-enable

Ordinary pulls never reactivate an account or clear a tombstone. First investigate
why it was revoked, reconcile any uncertain administrative operation, retain the
full cohort and choose a fresh generation greater than the local marker and
bootstrap generation with a new expiry within 60 days. Set the selected host's
central revoked=false and fleet host_credential_rotation_revoked=false, but
retain the tombstone until this explicit administrative command:

```sh
ansible-playbook -i "$PROTECTED_INVENTORY" playbooks/authentik-agent-hosts.yml -e @"$PROTECTED_AGENT_HOSTS" -e reenable_host=agent-b
```

The explicit flag removes only that host's controller latch, reactivates its
owned account, issues a new bootstrap and clears its bound local tombstone under
the same lock before delivery. Then converge its timer. An expired credential
also needs deliberate administrative disable/revoke followed by this operation;
the host cannot bootstrap itself. Preserve receipts. Never delete journals to
bypass an unconfirmed API mutation.

No deployment, live canary, signer change or resource replay is performed by
these source PRs. Edge-JWKS signing-key rotation remains separate P3.2 work.
