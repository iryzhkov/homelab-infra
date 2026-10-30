# Shared Igor personal memory and sessions, source only

This contract extends the existing OV provider. No inventory selects it. No
enrollment, credential read, image publication or deployment was performed.
The implementation follows the full Jocasta authoring guide revision 45,
document 38a63cf8918eebae2dd7c8911eacd89c, SHA256
a2dd0cb0f9d8ca9f57ca49b24e4346526a1de350089f780c3924d7e94a762d51.

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
be 40 lowercase hex characters. It does not establish qualification.

`ov_personal_config` validates this future shape. `ov_http_contract` refuses
every enabled personal package until root binds the qualified backend and HTTP
artifact. All existing closed image bindings remain unchanged. The maintained
sorted TOML template needs no alternate rendering path; the role already stages
it mode0600 with no_log and diff disabled.

HTTP source af26cfcd7a45c188ac6a468092ffa005f7fc0c42 documents the
shared-owner-v1 contract and an older source pin. It refuses append before
dispatch. Root owns the ongoing append qualification, final backend revision,
HTTP source/image identity, complete build context and artifact receipts.
This infra change does not accept that older pin as a final artifact.

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
The runtime also requires the observed subject in protected personal_subjects.
Never infer a grant from requested scopes or send backend owner headers.

Root must bind actual approved subjects and the existing default/default backend
consumer key without exposing credentials. No foreign user, agent namespace,
session commit, extraction, deletion, autoCapture or resource URI expansion is
authorized. Native client qualification and final per-host activation remain
separate work.
