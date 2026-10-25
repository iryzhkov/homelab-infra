# Managed interactive Web MCP enrollment source

This capability is disabled and has isolated source/fixture evidence only.
Root owns live effects, review, merge/publication and deployment. Adopted hosts
are laptop, agent-a, agent-b and omarchy-pc/GamingPC. OpenCode is excluded.
Actual laptop qualification gates other-host enrollment and deployment.

playbooks/authentik-webmcp-interactive.yml is a separate explicit entrypoint,
never imported by authentik.yml or ordinary convergence. It decrypts only the
existing secrets/authentik-api.sops.yaml under no_log, as existing entrypoints do.
No actual new credentials, expiry or accounts are committed or fabricated.

## Closed contract

One service identity webmcp-interactive-HOST has no groups, roles or superuser
privileges and one separately revocable app password per host/generation.
Future explicitly granted services reuse this identity/credential. Igor's
credential and limited tooling accounts are untouched.

The separate confidential application/provider/client is jocasta-managed-interactive.
It has client_credentials only, no redirects, refresh grant or offline scope,
exactly jocasta.read+jocasta.write mappings and 300-second RS256 tokens.
It uses existing keypair 8bbd6516-af33-4e64-a73b-caa39e692c29.
include_claims_in_id_token stays true: pinned Authentik also derives access JWT
claims from IDToken.to_dict, so false would omit the resource/operator mappings.
Issuer is https://auth.ryzhkov.dev/application/o/jocasta-managed-interactive/.
Resource is https://jocasta.mcp.ryzhkov.dev/mcp. The read mapping emits
jocasta_operator=true and signed webmcp_identity=username, with no project claim.
Write mapping emits {} to avoid duplicate merged claims.

The policy pins actual receipt-bound user PK/username, active service account,
path/owner marker, no privileged groups/roles, exact POST client/resource,
username/password client_credentials and the two scopes. Generated-client,
client-secret and assertion shortcuts fail. The pinned Authentik2026.8.4 source
accepts username/app-password then checks application policy access.
Public human provider, limited accounts/issuer/session helper gate and existing
receipt-bound operator recovery role remain unchanged. Jocasta server code is
unchanged; the existing single operator verifier can select the managed trio.

Bootstrap creates a deny policy and provider without resource mappings before
application/binding. Only after binding does it attach mappings and the allowlist.
Credentials are retrieved last, after readback and public RSA/RS256 JWKS checks.
No access token is issued by enrollment. Public signing metadata alone does not
verify an issued token; the later native helper verifies actual RS256 signatures.

## Ownership and private delivery

Root supplies a random owner UUID and pre-existing controller-UID 0700 receipt
directory under safe nonlinked ancestors. The module holds directory FDs, locks
a private single-link file and atomically/fsync writes receipt.json.
Each mutation first records a pending operation; only an exact successful response
with an API-returned PK is accepted. No blueprint or UUID importer is used.
Receipt fields are declared public attributes and IDs, never app passwords or
returned client secrets.

The API actor must report user.is_superuser as the JSON boolean true through
/core/users/me/ before each application collection and each mapping used_by read.
A matching filtered pagination count is not sufficient. Reference authority is
checked directly, independently of any previous application collection load.

No name-only adoption occurs. Matching names without receipts refuse; existing
objects must match original IDs/attributes. Privilege and foreign-reference drift
refuse. Each recorded scope mapping is inspected through
/propertymappings/all/UUID/used_by/ on the concrete ScopeMapping. Pinned Authentik
2026.8.4 enumerates reverse managers without pagination; verified superuser access
is required for unfiltered evidence. This covers base and subclass relations,
including DCR, Source user/group, provider group and RAC consumers, without
enumerating those endpoint collections in the controller.

Only the exact common Provider row and ProviderPropertyMapping through row for
the receipt-bound OAuth2 provider/mapping IDs are accepted for an attached mapping;
an unattached mapping must have no references. The through row's fixed model
string encodes the two FK IDs; it is not an editable name or name-based adoption.
Foreign, missing, malformed, duplicate, oversized or unavailable evidence refuses
before convergence writes and is freshly checked before credential retrieval.
Mapping mutations remain confined to the recorded OAuth2 scope/provider endpoints.

The offline Django5.2.17/DRF3.17.1/SQLite seam executes pinned UsedByMixin and
extracts relevant actual model declarations from hash-checked upstream snapshots.
It qualifies producer shape and consumer refusal, including direct ScopeMapping
DCR relations. Its guardian stand-in requires a strict superuser and returns the
real queryset; it does not qualify live RBAC or a full Authentik runtime.
An uncertain write leaves pending set. Further runs refuse before API
requests. Root must reconcile that original operation separately. Never delete
the receipt, invent an acknowledgement or replay a POST. No automatic adoption
or recovery command is provided.

Runtime output is HOST.json beside receipt.json, private0600/single-link, with
exactly version=1, owner, host, username, password and generation. Fleet imports
that protected file through held-FD/no-follow checks. Administrative credentials
never leave the controller. Runtime values never enter argv/env/source/results/
ordinary logs/diffs. Keep this directory outside job snapshots. Root preserves
durable receipts and private custody, removing only owned staging after delivery.

Exact repeats perform no mutations or credential rewrites. Raise generation to
rotate; lower generations refuse. Existing hosts must not disappear from declarations.
Per-host state=absent deletes recorded app passwords, disables the identity and
removes its staged file. Global state=absent also removes only recorded Jocasta
binding/application/provider/mappings/policy. Inactive account tombstones remain.

## Future root inputs and commands

A private0600 vars file, outside repositories/snapshots, supplies:

    webmcp_managed_enabled: true
    webmcp_managed_owner: ROOT_GENERATED_UUID
    webmcp_managed_receipt_dir: /ABSOLUTE/PRIVATE/0700/DIRECTORY
    webmcp_managed_state: present
    webmcp_managed_laptop_qualified: false
    webmcp_managed_hosts:
      - host: laptop
        generation: 1
        expires: ROOT_SELECTED_FUTURE_UTC_TIME
        state: present

Expiry must remain between one and 180 days from execution. Existing SOPS
administrative input stays separate. Future root commands, not executed here:

    ansible-playbook playbooks/authentik-webmcp-interactive.yml \
      --check --diff --extra-vars @/PRIVATE/enroll-laptop.yml
    ansible-playbook playbooks/authentik-webmcp-interactive.yml \
      --extra-vars @/PRIVATE/enroll-laptop.yml

Check mode validates declarations offline and neither decrypts nor opens receipt/
credential directories. Live requests use fixed verified HTTPS, no proxy/redirects,
eight-second request timeouts and bounded complete collections. Incomplete data
refuses. Errors never quote API response bodies.

The explicit homelab jocasta_managed_interactive_verifier_enabled=true selector
is PREPARED conditional source. Native qualification is OPEN. Keep it off main
until root passes actual laptop Codex AND Claude full managed access, CAS,
idempotency and >330s reuse gates. This source is no native acceptance receipt.
After qualification and merge, ordinary owning stack convergence retains the
managed operator trio. Default false remains the capability fallback elsewhere.
The filter accepts one complete fixed public or managed issuer/JWKS/client trio,
never mixed trios or wildcard origins. Accepted image/resource/limited settings
remain required.

Root delivers HOST.json and the reviewed architecture-specific helper through
fleet's separate opt-in. Only laptop persistence is prepared in the companion
fleet selection. Other hosts stay default off until their accepted enrollment.
The selectors add no Authentik declarations, grants, users or deployment mechanism.

Rotation raises generation in the same declaration/receipt, then uses protected
fleet delivery with the exact prior credential digest supplied privately.
Revocation/rollback sets selected host state=absent in the same declaration;
global withdrawal additionally sets webmcp_managed_state=absent. Preserve existing
hosts. Restore the exact previous verifier/native definitions and only owned files.
Issued JWTs may remain valid through <=300s expiry plus accepted server leeway,
unless managed issuer trust is withdrawn first. Preserve account tombstones.

After actual laptop qualification, root may add agent-a, agent-b and omarchy-pc
with laptop_qualified=true and distinct credentials. This role stages controller
files only; root owns actual host transport and delivery.

A future service needs its own explicit application/resource/scope grant and
registration/helper support, reusing the same identity. URL alone grants nothing.
This module exposes Jocasta only, with no generic wildcard grant parameter.
OS same-UID/root isolation, actual issuer output, official T3/native execution,
renewal beyond330s, restart, live rotation/revocation, pull/no-op and retained
limited sibling/write denial remain root-owned qualification work.
