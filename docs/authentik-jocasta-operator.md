# Jocasta interactive operator OAuth

This source adds a separate opt-in Authentik application for Igor's laptop.
It does not activate Jocasta trust, change a backend or route, deliver client
credentials, enroll a native client, or log anyone in. The ordinary machine
identity declarations, grants, providers and private paths are unchanged.

## Entry point and required input

Only `playbooks/authentik-jocasta-operator.yml` includes
`authentik_jocasta_operator`. No global or site playbook includes it.
The default is disabled. The operator supplies both
`jocasta_operator_enabled: true` and an integer `jocasta_operator_user_pk`
in an operator-owned input file. There is no production default PK, username
lookup, guessed account, or test-fixture fallback. Confirm that PK belongs to
Igor through the existing controlled administration path before deployment.

The only credential pointer is the existing
`secrets/authentik-api.sops.yaml`, consumed by `community.sops.load_vars`
under `no_log`. Values stay in controller custody. The entrypoint pins its
transport to `https://auth.ryzhkov.dev/api/v3` before any API request. The role
accepts the maintained canonical HTTPS API syntax so its real tasks can also
be exercised on the isolated HTTPS fixture. Production uses this entrypoint.

With that public input file, the offline plan is:

```sh
ansible-playbook playbooks/authentik-jocasta-operator.yml --check --diff \
  -e @/operator-owned/jocasta-operator-input.yml
```

Check mode does not load SOPS or make requests by default. Explicit
`jocasta_operator_online_check: true` loads the existing controller credential
for read-only collision, account and key metadata checks. Neither check mode
proves actual issuance or verifies a new provider's public signer. An authorized
real convergence uses the same entrypoint without `--check`. It makes no
host, service, user-account, native-client or login changes.

## Closed public contract

| Field | Value |
| --- | --- |
| Client ID and application slug | `jocasta-laptop-interactive` |
| Issuer | `https://auth.ryzhkov.dev/application/o/jocasta-laptop-interactive/` |
| JWKS | Issuer plus `jwks/` |
| Resource and mapped audience | `https://jocasta.mcp.ryzhkov.dev/mcp` |
| Signing key UUID | `8bbd6516-af33-4e64-a73b-caa39e692c29` |
| Client and grants | Public; exactly `authorization_code`, `refresh_token` |
| Access token / authorization code | 300 seconds / 60 seconds |
| Refresh | 30 days per token; `refresh_token_threshold: seconds=0` |
| Scopes attached | Owned `jocasta.read`, owned `jocasta.write`, built-in `offline_access` |

The public client has no delivered secret. No DCR, client-credentials,
password, token-exchange, profile, email, API or administrative grant is added.
The built-in offline mapping's managed identity, scope and exact pinned
expression are checked; the role neither owns nor edits it.
RSA certificate metadata and private-key availability are checked before
apply. After apply and on a real no-op run, the maintained bounded public
JWKS module verifies the actual signing entry is RSA/RS256. It uses TLS, no
redirects, no administrative header, an eight-second deadline and a 64 KiB
body limit. Certificate metadata alone is insufficient. A failed signer gate
leaves the partial application for explicit reconciliation, never native login
or automatic rollback.

Strict authorization redirects are exactly:

- `http://127.0.0.1:43181/callback/bbdouGp56E53`
- `http://localhost:43182/callback`
- `http://127.0.0.1:43183/callback`

No regex, wildcard or fallback port is accepted. Check availability of these
ports before onboarding. If occupied, resolve that conflict before login.
Changing the fixed callback contract requires new source qualification.

Application mode is ALL. Its two owned policy bindings require the dedicated
non-superuser `jocasta-operators` group and the expression policy together.
The group has no roles or parents and contains only the explicit PK.
The policy independently requires an active internal human with that exact PK
and direct membership in this group. It checks the pinned authorization
context fields for authorization_code, response type code, S256, a canonical
43-character SHA256 base64url challenge, a strict redirect and the closed scope
set including read. OAuth state remains the native client's responsibility.
There is no imaginary `oauth_state` context check.

The read mapping rechecks current active/type/PK/group/provider/client/app state
on each evaluation and returns only fixed `aud` and boolean
`jocasta_operator: true`. The write mapping returns an empty dictionary; its
attached scope carries write authority. No mapping overrides `scope` or
`azp`, emits project arrays or reads identity attributes as authority.
`include_claims_in_id_token: true` is required by this Authentik pin's
access-token construction, not permission to accept ID tokens at Jocasta.

## Ownership, removal and recovery

The single fixed blueprint name is
`homelab-infra Jocasta laptop interactive operator`. Its versioned marker
binds the explicit PK and state. The stored content must exactly reproduce
this version's rendered blueprint and have a successful matching SHA512
receipt. Unknown, pending, failed, edited or foreign records stop before writes.
Future source changes that alter rendering need an explicit compatible ownership
migration; do not replace the ownership check with automatic adoption.

UUID-backed objects have deterministic role-specific UUIDs. Provider identity
uses the fixed name/client ID and the owned application's provider reference.
Complete bounded collections establish name/UUID collisions and cross-object
references. Pagination, missing reference fields, foreign bindings, a shared
provider/mapping, or group privilege/member drift stop the run. Collections
larger than 500 require a separately qualified bounded pagination change.
The role creates no user or Authentik API token.

A changed request is stored once. Its post-store last_applied baseline,
exact content and fresh worker SHA512 receipt bind completion. Only reads are
retried while awaiting the worker. Exact object fields and references, public
signer and a final terminal ownership receipt are checked. Identical content
with exact state performs no write or apply. Actual object drift is a refusal
on a no-op run, not a claim of convergence. Serialize administration of these
fixed objects because Authentik's blueprint API has no compare-and-swap write.

For retirement, first withdraw resource-server operator trust and revoke this
operator's refresh grants/application authorization under root's deployment
procedure. Then use the same input PK with `jocasta_operator_state: absent`.
Removal deletes only the exact owned bindings, application, provider, policy,
group and two mappings in dependency order. It retains a successful absent
blueprint as a tombstone. A later foreign object with a colliding name or UUID
is refused even on repeated removal. The user account, other groups, built-in
mapping and limited service applications remain untouched.

Any lost response, timeout, failed worker, failed postcondition or signer
refusal is an unconfirmed/partial operation. Preserve the original blueprint
PK, rendered content, last_applied, last_applied_hash and exact observed object
state privately. Read the original operation and reconcile its outcome before
a new run. Do not replay an apply, blindly overwrite the marker, or claim
that reverting Git undoes an issuer-side operation.

## Refresh withdrawal and native acceptance

Authentik 2026.8.4 does not repeat the initial application policy for refresh.
Its refresh path calls IDToken.new and reevaluates attached scope mappings.
The current-user check therefore removes the useful operator claim after group
removal, deactivation or a change to service-account type, even if the same
refresh grant remains. User attributes cannot restore it.

Withdrawing membership or user access MUST also revoke every refresh grant
for the exact user PK and this provider, including rotated grants. Use the
existing private administration path to enumerate the complete
`/api/v3/providers/oauth2/refresh_tokens/?user=<PK>&provider=<provider-PK>`
collection and DELETE each exact returned grant PK through its detail endpoint;
re-read until none remain. The pinned viewset supports user/provider filters
and deletion. Never log the returned token records or ID-token claims. Revoke
outstanding authorization codes for the same pair and remove this application's
saved consent/authorization through supported administration. Do not modify
other applications or copy tokens into a test job.

Rotation is performed on every refresh and marks the old token revoked.
The replacement expires 30 days from that refresh. This is a sliding lifetime,
not an absolute 30-day maximum session. Actual rotation, concurrent native
refresh behavior, restart reuse and revoked-token refusal remain live
acceptance requirements. Existing signed access JWTs can remain useful for
300 seconds plus Jocasta's 30-second leeway; removing the optional trust ends
acceptance sooner.

Jocasta operator trust stays disabled in inventory. The matching deployment
source now validates the exact JOCASTA_OPERATOR_OIDC_ISSUER,
JOCASTA_OPERATOR_OIDC_JWKS_URL and JOCASTA_OPERATOR_OIDC_CLIENT_ID trio,
as documented in [jocasta.md](jocasta.md). No operator image source is accepted:
the allowlist is empty while root fixes the blocked initial server candidate.
Root must supply the replacement reviewed source, checked image publication
and Auth readiness gates before convergence.

After source deployment, Igor performs each client's supported native one-time
MCP OAuth login as a user action. Ansible does not launch a browser or native
login. Codex 0.163.0 and Claude 2.1.276 need independent actual login,
full-store read/write/conflict and refresh/revocation qualification under
official T3. OpenCode is outside this slice's acceptance scope; its unused
strict redirect is retained only for possible future work. Existing limited
identities must still receive only their existing project read claims.
Synthetic tests are not native or live-issuer evidence.

## Pinned source and focused checks

The adopted design is the source-only laptop interactive design at
`interactive-design/RESULT.md`, with authoring guide revision 32.

- Authentik 2026.8.4, commit
  [410f46e5](https://github.com/goauthentik/authentik/tree/410f46e5cf18f97a1f0056b30984a145242df55d).
  `providers/oauth2/models.py` defines grants, strict redirect records and
  refresh_token_threshold; `views/authorize.py:447-455` exposes the actual
  policy context. `policies/views.py` explicitly disables policy cache reads
  for this authorization check. `views/userinfo.py:get_claims` supplies current
  user/provider and deepmerges mappings. `views/token.py:create_refresh_response`
  rebuilds mapped claims and rotates/revokes. `api/tokens.py` and `urls.py`
  define exact refresh/code administration routes.
- Codex
  [83086188 oauth_callback.rs](https://github.com/openai/codex/blob/83086188ae0614bd882818ff1aa23f3194962012/codex-rs/rmcp-client/src/oauth_callback.rs)
  hashes the full resource URL with SHA256, taking nine bytes for the suffix.
  Its pinned rmcp revision
  [4048ac40 auth.rs](https://github.com/modelcontextprotocol/rust-sdk/blob/4048ac40e958fb1a7d614617a2ae8d26666ee2fa/crates/rmcp/src/transport/auth.rs)
  uses S256 PKCE.
- The design's public Claude 2.1.276 embedded-source excerpts at offsets
  221410160 and 221418100 establish S256, and 221425900 gives the literal
  localhost callback helper. Its 221491205 excerpt selects the configured
  client ID. OpenCode 2.1.3 excerpts at 169484800/169495000 establish S256;
  169557600 shows explicit redirect_uri/callback_port and the literal
  127.0.0.1 callback. The 167271400 compatibility conversion preserves those
  explicit fields. These excerpts were read before fixing the three redirects.
  They are source evidence, not a successful laptop login.

Use the repository's pinned ansible-core 2.19.13 and deepmerge 2.1.0. Focused
checks run only on agent-a with an asserted scratch HOME and short disk TMPDIR:

```sh
python -B -m unittest discover -s roles/authentik_jocasta_operator/tests -p test_operator.py -v
```

The tests reuse the existing HTTPS/blueprint fixture. They cover composed
mapping orders, exact PK/type/provider/membership, current-state claim
withdrawal, authorization and strict PKCE negatives, public provider/grants,
ownership, incomplete reads, check mode, foreign references, absence,
idempotency and failed-apply non-replay. The actual entrypoint gets offline
syntax/check coverage with a SOPS sentinel that must remain untouched.
The copied JWKS module is byte-identical to the maintained machine role's gate;
its production signing entry is also exercised against fake HTTPS. There are
no real credentials, issuer requests, native logins or server changes in tests.
