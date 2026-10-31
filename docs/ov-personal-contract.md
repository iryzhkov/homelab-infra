# Shared Igor personal memory and sessions, source only

This contract extends the existing OV provider. No inventory selects it. No
enrollment, credential read, image publication or deployment was performed.
The implementation follows the full Jocasta authoring guide revision 45,
document 38a63cf8918eebae2dd7c8911eacd89c, SHA256
a2dd0cb0f9d8ca9f57ca49b24e4346526a1de350089f780c3924d7e94a762d51.

## Activation order

Root must qualify and activate the HTTP personal artifact, its protected owner
and approved subjects, and its backend binding before enabling issuer personal
grants. Verify that live HTTP deployment before selecting
`ov_http_identities_personal=true`. A deployed resources-only HTTP artifact cannot accept personal claims;
enabling issuance before HTTP personal activation would deny resources access
for those tokens. The replacement source binding
is now closed to [the current HTTP binding](ov-http-compose.md#explicit-local-config-identity);
live activation remains root-owned.

## Issuer installer inputs

The existing isolated playbook accepts a separate default-off
`ov_http_identities_personal=true` only together with
`ov_http_identities_full_interactive=true` and an explicit protected
`ov_http_identities_personal_grant`:

```yaml
account: default
user: default
actions: [memory.read, memory.write, session.read, session.create, session.append]
```

Actions may be reduced to a nonempty unique subset in the shown order.
The role accepts this grant only on the existing `ov-full-interactive`
profile on `omarchy-pc` and `laptop`, with the full resources/actions grant.
It stores the closed `ov_personal` declaration within `chaldea_ov`.
The original three identities, provider/client/resource and destination
bindings remain unchanged. The opt-in adds five scope mappings on that same
provider, one per `ov.personal.<action>`. Scopes alone confer no authority.

The mapping requires exact operator-declared account attributes, service-account
type and group membership. One elected requested mapping emits the complete
grant once, avoiding Authentik 2026.8.4 list concatenation. Requested scopes
reduce each explicit action family independently. No selected personal action
means no `ov_personal` claim. Existing resource claims and `ov.read` remain.

Later enrollment needs the complete reached cohort, original ownership receipts,
operator-confirmed provider/application baseline, bound operator inventory,
finite credential expiry/generation, and a serialized authorized issuer window.
Extra-vars must supply complete matching declarations if they override the
playbook-derived services or identities. No new provider or account is needed.

## HTTP installer inputs and artifact hold

The protected runtime TOML accepts these optional fields. Omission retains the
existing output. `personal_profile: disabled` is explicit off; owner, subjects
and backend revision must then be absent or empty.

```yaml
personal_profile: shared-owner-v1
personal_account: default
personal_user: default
personal_subjects: [APPROVED_PC_OAUTH_SUBJECT, APPROVED_LAPTOP_OAUTH_SUBJECT]
personal_backend_revision: QUALIFIED_BACKEND_SOURCE_REVISION
```

Placeholders are documentation only. Subjects are exact approved OAuth subject
strings, not account names or requested scopes. The validator bounds the list
to 1..16 unique nonempty strings of at most 256 characters with no whitespace
below ASCII 33. Owner values are exactly default/default. Revision input must
be 40 lowercase hex characters and match the qualified personal protocol revision below.
It does not attest deployed runtime identity; the exact backend image/source binding
is recorded separately in [the current backend release](ov-curator-backend-qualified-release.md).

`ov_personal_config` validates the protected shape. `ov_http_contract` permits
personal enablement only with the exact current local HTTP binding and personal
protocol revision 7f3faf6982f81116d6626bc7091f87f786a309c7. Legacy and distribution
bindings cannot enable it. The maintained
sorted TOML template needs no alternate rendering path; the role already stages
it mode0600 with no_log and diff disabled.

Historical source e094cb32a085e9ad3fc9976aba7824db830c8999 implemented
shared-owner-v1 with qualified append. Current artifact identities are recorded
in the [combined release](ov-exact-directory-release.md). The resource interface
contract remains 1c80c90e02a50a168c7f7ef02fd7c9aebaeb8319.

## Fleet helper owner

Keep `webmcp-interactive-headers ov-full HOST` stable for PC and laptop,
shared by Claude and Codex on each host. The separate fleet owner must request
the existing four resource scopes plus:
`ov.personal.memory.read ov.personal.memory.write ov.personal.session.read
ov.personal.session.create ov.personal.session.append`.
After RS256 verification, check the existing issuer, client/resource, lifetime,
resource/action claims and host binding, plus exact signed
`ov_personal = {account: default, user: default, actions: [...]}`.
Validate a nonempty canonical action subset and each corresponding scope.
The fixed expanded request retains all five known personal scopes even when
the operator declares fewer actions. Signed actions alone confer personal
authority; additional known personal scopes are inert when the claim is present.
Do not require a scope/action bijection or narrow issued scopes to manufacture
one. The HTTP af26 personal verifier uses this one-way action-to-scope rule.
The separate helper owner must implement the same rule; this infra fixture
records that intended contract and does not claim to execute the helper.
The runtime also requires the observed subject in protected personal_subjects.
Never infer a grant from requested scopes or send backend owner headers.

Root must bind actual approved subjects and the existing default/default backend
consumer key without exposing credentials. No foreign user, agent namespace,
session commit, extraction, deletion, autoCapture or resource URI expansion is
authorized. Native client qualification and final per-host activation remain
separate work.
