# Authentik machine identities for Chaldea hosts

`roles/authentik_machine_identities` lets a named Chaldea host obtain short-lived
web MCP access tokens from Authentik without holding any administrative
credential. It is off in the integrated playbook: `playbooks/vars/authentik.yml`
sets `authentik_machine_identities_enabled: false`, and `playbooks/authentik.yml`
includes the role only when that is true. The one enabled declaration is the
laptop's Jocasta tooling enrollment, run only by its own entrypoint; see
[Isolated Jocasta tooling enrollment](#isolated-jocasta-tooling-enrollment).
Complete infra identity creation and host credential delivery before enabling the
fleet enrollment.

## What it manages

One OAuth2 provider and application per declared service, and for each declared
host and Chaldea preset (profile):

- an ordinary service account, `chaldea-<host>-<profile>`, with no roles, whose
  only group is the one bound to its service's application;
- one app password for that account, with an explicit owner, intent and expiry;
- one private file on the host, `~/.config/chaldea/authentik/<profile>.app-password`
  of the host's inventory `ssh_user`, mode 0600 in a 0700 directory, holding the
  exact key without a newline.

The default Jocasta provider grants only `client_credentials`, issues 300-second tokens signed by
an existing keypair named by its `kp_uuid`, and carries one scope mapping,
`jocasta.read`. The mapping fixes `aud` to the configured Jocasta resource and
sets `jocasta_projects` to the one project in the account's `chaldea_jocasta`
attribute. Nothing in it comes from the token request. If the account is not a
service account in the identities group, or the attribute is missing, the
mapping returns no claims and the token keeps Authentik's default audience (the
client ID), which Jocasta refuses. The provider's client secret is never read or
delivered; hosts authenticate with `client_id`, `username` and their app
password.

Everything is one stored blueprint, "homelab-infra machine identities", applied
the same way as `authentik_config`'s access blueprint: stored through the API,
applied, waited for, then checked. The role runs after `authentik_config` in the
same play and reuses its API facts, so the admin token is loaded once, on the
controller, and never reaches a host.

## Variables

The role's defaults document them. In short:

```yaml
authentik_machine_identities_enabled: true
authentik_jocasta_machine_access:
  state: present
  slug: chaldea-jocasta
  name: Chaldea Jocasta machine access
  group: chaldea-jocasta-machine-identities
  client_id: <fixed public client ID>
  resource: <canonical Jocasta MCP resource URL>
  signing_key: <kp_uuid of an existing keypair with a private key>
  scope: jocasta.read
  token_seconds: 300
authentik_machine_identities:
  - {host: laptop, profile: jocasta-projectA-reporter, project: projectA,
     generation: 1, expires: "2027-01-15T00:00:00Z", state: present}
```

The host must be an inventory host reached over SSH or the local controller;
its `ssh_user` owns the file. `authentik_machine_identity_home` on the host
overrides `/home/<ssh_user>`. Scope and lifetime are fixed: any other value is
refused. An expiry less than a day away, or more than
`authentik_machine_identity_max_days` (180) ahead, is refused.

The values the Chaldea session policy's enrollment needs follow from these, with
`<auth>` being Authentik's public URL: `issuer`
`<auth>/application/o/<slug>/`, `tokenUrl` `<auth>/application/o/token/`,
`jwksUrl` `<auth>/application/o/<slug>/jwks/`, `clientId`, `username`
`chaldea-<host>-<profile>`, `scope` `jocasta.read`, `resource`, `project`,
`maxTokenSeconds` 300 and `secretFile` the path above. Confirm the issuer and
JWKS URL against the live provider before relying on them.

## Ownership, removal and rotation

The stored blueprint's content is the ownership record: one comment line lists
every object it names and its desired state. Only prior `present` entries establish
ownership. An `absent` declaration never grants authority over an object that
appears later, including after a successful removal. Before storing anything the role looks up every name
involved, read-only, and refuses when:

- an object with one of its names exists but no stored blueprint of this role
  created it (no adoption of a hand-made or foreign object, and no deletion of
  one);
- an object it created is no longer declared at all (declare it `absent`
  first);
- an owned token is no longer an app password of its own account, an owned
  account has gained a superuser group, a role or another group, or the owned
  group has a foreign member, parent or role;
- another provider already uses the client ID, or the signing key is missing or
  has no private key;
- a lookup did not return every candidate or answered in an unexpected shape,
  including a malformed or ambiguous blueprint lookup;
- a foreign application uses the provider being removed, as its primary or
  backchannel provider. The complete application list is checked before mutation.

`state: absent` on an identity deletes its tokens and account (which also
removes its group membership) and then its host file; other identities and the
shared application are untouched. Each service's application can be removed only once all of that service's
identities are absent; deleting the application deletes its binding.
A host named by an absent identity must stay in the inventory until the run
that removes its file has succeeded.

Rotation is a higher `generation`: Authentik creates a new app password under a
new identifier, the old generations are declared absent and deleted, and the
host file is replaced. A later `expires` on the same generation only extends the
existing key; it is not rotation. Deleting an app password stops new token
issuance with it. Tokens already issued are verified offline by the MCP services and stay
valid until they expire, at most 300 seconds.

## Order of a run, and check mode

1. Validate the declaration and the inventory hosts.
2. Render the blueprint; read the stored one and its ownership record.
3. Look up every name; refuse as above.
4. Inspect every destination on its host, without reading or following links:
   the home and each directory must be the user's own, not group- or
   other-writable, and the file, if present, the user's own single-link
   regular file. An unreachable host or unsafe path stops the run before
   Authentik changes.
5. Store the blueprint, capture its `last_applied` after storage, and request
   one apply. Require a newer successful result with the rendered content's
   SHA-512 in `last_applied_hash`, plus the exact instance, name, content,
   empty path/context and enabled state. Poll at most 11 times, three seconds
   apart; an old success alone cannot complete this run.
6. Look everything up again and compare: provider settings, mapping expression,
   application, the single group binding, the group's members, each account and
   each token. Ask `check_access` for every account and every application
   (`superuser_full_list`); each account must pass for its service's application
   and fail for every other one. An application without bindings that admits
   every account therefore stops the run. Repeat this complete read-only
   observation at most six times, three seconds apart, then fail if any
   difference remains. No observation sends another apply. Recheck the
   blueprint correlation before touching host credentials.
7. Only then, for each present identity, read the key with `view_key` on the
   controller under `no_log`, write it to the host as its user (no become,
   `no_log`, no diff) and check the result; for each absent identity remove
   the file.

With machine identities disabled, the integrated playbook keeps the existing
`authentik_config` check and diff preview of groups, application access and MFA,
including its controller SOPS loading and assertions. With machine identities
enabled, check mode skips that role and does not preview access configuration
or decrypt SOPS. Without explicit controller API facts it validates and shows
an offline machine identity blueprint and file plan; it cannot check live
ownership or collisions. When API facts are explicitly supplied, enabled check
mode also performs the read-only preflight in steps 2 and 3. It reads no key,
connects to no host and stores nothing.
Before the new role sends credentials, it requires a canonical HTTPS origin
followed by the fixed `/api/v3` path, without userinfo, query or fragment. Every
credential-bearing request in this role refuses redirects. Every API task is `no_log`, so a failure shows the role's
own message rather than a response. A second real run changes nothing (the
apply call is repeated, as for the access blueprint, and reports no change).

The completion contract was read in the installed version's upstream
[2026.8.4 API](https://github.com/goauthentik/authentik/blob/version/2026.8.4/authentik/blueprints/api.py),
[worker](https://github.com/goauthentik/authentik/blob/version/2026.8.4/authentik/blueprints/v1/tasks.py)
and [model](https://github.com/goauthentik/authentik/blob/version/2026.8.4/authentik/blueprints/models.py).
The API queues the apply without clearing status or returning a request ID.
The worker hashes the UTF-8 content on success; ordinary saves also advance
the model's `last_applied`, which is why the baseline follows storage.
These fields support completion correlation, not exact task attribution under
concurrent writers. Serialize runs and administrative edits to this blueprint.
The final checks are observations, not a transaction with credential delivery.
The limits bound attempts and sleeps; each URI also has Ansible's finite
30-second socket timeout, so elapsed time includes request and controller work.

If an apply times out, disconnects or fails, stop and inspect the original
operation's blueprint logs and the objects changed so far. A timeout is
unconfirmed, and the queued operation may still complete. The role never
replays it within the run and leaves host credentials untouched until both
completion and exact state are verified. Do read-only reconciliation before
deciding whether another apply is needed. Storing the blueprint updates the ownership record before the import
succeeds, and an import can leave partial changes. A retry may therefore refuse
objects whose former `present` record is now `absent`. Keep the last successful
blueprint content and compare it with both the failed content and actual objects.
An operator must reconcile partial changes and restore only a verified ownership
record before retrying. Do not bypass a refusal by marking uncertain objects as
owned. No automatic rollback or recovery is provided.

## Closed service profiles

The role supports exactly `jocasta`, `ov`, `homelab` and `ha`. The default
`authentik_machine_services: {}` keeps the existing
`authentik_jocasta_machine_access` declaration and implicit Jocasta identities.
Jocasta's object names, ownership record version, account names, host paths,
scope mapping and lifecycle remain compatible.

A nonempty `authentik_machine_services` replaces the legacy application input.
It is keyed by service, with a complete application declaration per entry.
Include `jocasta: <the existing mapping>` to keep an existing Jocasta enrollment.
Declare a removed service and its identities absent before dropping them.
Each identity selects one declared `service`; omitted means `jocasta`.
A host/profile pair is unique across services. Reusing a profile for a second
service is refused because it would share an account and secret path.
Use separate presets, accounts, app passwords and files for each service.

OV, homelab and HA applications require the same fields as Jocasta, plus explicit
`issuer` and `jwks_url`. For slug `chaldea-ov` on an example issuer origin
`https://auth.example.test`, these are exactly
`https://auth.example.test/application/o/chaldea-ov/` and
`https://auth.example.test/application/o/chaldea-ov/jwks/`.
Both trailing slashes are required. Online runs require that origin to match
the controller's canonical Authentik API origin. The existing operator-supplied
`signing_key` remains mandatory. OV, homelab and HA require public keypair
metadata `key_type: rsa` and `private_key_available: true`; missing or non-RSA
metadata refuses enrollment before mutation or delivery. In Authentik 2026.8.4,
`key_type` comes from the certificate and does not prove the private key's
algorithm. After exact provider/application verification, and before any app
password read or delivery, the role fetches only the validated provider's public
JWKS over trusted HTTPS without credentials, proxies or redirects. The fetch
has an 8-second deadline and 64 KiB response limit; errors are fixed and hidden
by `no_log`. Native JWKS exposes the signing key and optional encryption key,
so the gate permits 1–2 keys, requires exactly one `use: sig` key and checks
that key's actual `kty: RSA`, `alg: RS256` and public modulus/exponent. An RSA
encryption key or certificate cannot mask an EC/EdDSA signer. No private key
is read and no token is requested. Jocasta retains its existing signing-key check.
This is trusted public signing evidence, not proof from a newly issued token;
live token acceptance remains a qualification step.

| Profile | Fixed resource audience | Fixed scope | Required identity fields |
| --- | --- | --- | --- |
| Jocasta | Existing operator resource | `jocasta.read` | `project` |
| OV | `https://ov.mcp.ryzhkov.dev/mcp` | `ov.read` | `ov_namespaces` |
| Homelab | `https://homelab.mcp.ryzhkov.dev/mcp` | `homelab.read` | `homelab_services`, `homelab_hosts` |
| HA | `https://ha.mcp.ryzhkov.dev/mcp` | `ha.read` initially; explicit `ha.control` allowed | `ha_entities`, `ha_actions` |

The catalog-only HoloNet profile uses fixed resource
https://media.mcp.ryzhkov.dev/mcp and scope holonet.read. Its seven explicit
holonet_* fields match the accepted HoloNet server contract: one hex32 server,
one canonical decimal library ID (positive int64), one hex32 catalog user,
empty history/target user arrays, [catalog.read] actions and an explicit
[a-z][a-z0-9-]{0,31} namespace. Private operator inventory must establish TV
and Movies; names are not inferred from IDs. No UserData, history, curate,
home or combined scopes can be enrolled through this initial profile.
Missing fields, nulls, duplicates, multiple users/libraries or broader actions
are refused before any issuer lookup or secret read.

The existing owned blueprint/account, signer check, delivery and cleanup path
is reused. chaldea_holonet is a separate ordinary service-account attribute.
The scope mapping emits claims only for token.scope exactly [holonet.read],
exact declared user/group/grant equality and the fixed media audience.
Extra scope combinations emit no HoloNet resource claims; no array deepmerge
can widen this one-mapping profile. The synthetic fixture holonet-catalog.json
is shared byte-for-byte with Chaldea's helper tests. The actual-role fixture
proves protected fake-secret delivery, second convergence changed0 and owned
cleanup; no real enrollment or native renewal is established.

All profiles issue 300-second access tokens. OV stores its grant in
`chaldea_ov`, homelab in `chaldea_homelab` and HA in `chaldea_ha`.
Their scope mappings emit only
the fixed `aud` and the service's named grant arrays. They require an ordinary
service account, membership in the service's group, the exact declared
username and the exact operator-owned attribute grant. Missing, modified or
cross-service attributes yield no resource claims. No request parameter supplies
grants. The provider attaches only that service's declared scope mappings.

OV requires 1–16 distinct, non-overlapping canonical roots under
`viking://resources/`, each at most 1024 ASCII characters. Segments admit
letters, digits, underscore, hyphen, period, @, +, comma and equals.
Dot segments, wildcards, percent encoding and repeated/trailing slashes are
refused. Roots authorize descendants, matching the merged reader verifier at
`ov-mcp@599fdda83074c419a3d8170a5217aabb3b92745b`, `ov_http/auth.py`.

Homelab requires both explicit lists, each containing 0–128 unique IDs matching
`[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}`. An empty list grants nothing in that category;
omitted, null or wildcard lists are refused by enrollment. The merged observer
at `homelab-cli@db6f9ead377cded2a7229443c9daecf2c057c3b9`,
`mcp-observer/auth.go` and `config.go`, requires both a service ID and its
configured host ID for service access. Enrollment does not expand a missing
list to inventory or infer hosts from services. These grants name observer
configuration IDs, not the credential destination's inventory host.

HA requires 1–64 distinct entity IDs matching
`(light|switch|sensor|binary_sensor)\.[a-z0-9_]{1,96}`, also bounded to
112 characters, and 1–3 distinct actions chosen exactly from `state.read`,
`light.control` and `switch.control`. These are the merged
`homeassistant-mcp@9b296c6ab56432be34856f733e25843f0ca097ef` rules in
`http-mcp/internal/endpoint/config.go` and `oidc.go`.
No scene, script, area or other selector is supported.

The initial HA enrollment must use `scope: ha.read` and
`ha_actions: [state.read]` until the live entity registry and control paths
are qualified. Only an explicit operator declaration may select `ha.control`
or both `ha.read ha.control`. Each selected scope has a separately named
Authentik scope mapping, and the provider attaches exactly those mappings.
Enrollment refuses an action whose corresponding scope is absent. Authentik
2026.8.4 merges scope mappings with deepmerge 2.1.0, which appends arrays.
For HA, one selected mapping emits both grant arrays exactly once. It uses
Authentik's authenticated `token.scope` list to select the emitter and reduce
actions to the requested scopes: read emits only declared `state.read`, control
emits only declared control actions, and both preserve the complete declared
grant. A narrower scope request never adds an action.
The verifier intersects entities, actions and scopes with its operator registry,
including domain and control qualification; enrollment cannot replace those gates.
Removing a scope mapping from an existing application requires declaring that
service and its identities absent with the old scope set first, completing
cleanup, then enrolling the narrower profile. Undeclared owned mappings are
never silently dropped.

For example, identities for one host could be declared as follows, with complete
operator-owned application mappings supplied separately:

```yaml
authentik_machine_identities:
  - host: laptop
    profile: ov-namespaceA-reader
    service: ov
    ov_namespaces: [viking://resources/namespaceA]
    generation: 1
    expires: "2027-01-15T00:00:00Z"
    state: present
  - host: laptop
    profile: homelab-dns-reader
    service: homelab
    homelab_services: [dns]
    homelab_hosts: [host-a]
    generation: 1
    expires: "2027-01-15T00:00:00Z"
    state: present
```

A read-only HA identity uses the same lifecycle fields, `service: ha`,
a separate `profile: ha-state-reader`, `ha_entities: [sensor.qualified_example]`
and `ha_actions: [state.read]`. This example is synthetic and enables nothing.

The role verifies each provider, mapping, group and binding, then asks
`check_access` for every present account against the complete application list.
Exactly its service's application must pass. Foreign provider consumers and
incomplete collections still refuse removal. Destination inspection, protected
delivery, rotation, ownership refusals and cleanup use the existing lifecycle.
The PR55 asynchronous apply correlation and bounded postconditions remain in
force for the whole blueprint.

### Client enrollment contract still required

The installed Chaldea credential helper and fleet enrollment are Jocasta-only.
This source change does not make those clients support OV, homelab or HA. Before
integration, extend their closed enrollment parser, fleet renderer and private
helper together. They must accept a fixed named preset with `form: app_password`,
`issuer`, `tokenUrl`, `jwksUrl`, `clientId`, `username`, `secretFile`,
`scope`, `resource` and `maxTokenSeconds: 300`; replace the Jocasta
`project` field with exactly `ov_namespaces`, or with both
`homelab_services` and `homelab_hosts`, or with `ha_entities` and
`ha_actions` for HA. Preserve the exact lists and URLs above. `tokenUrl` is the issuer origin plus `/application/o/token/`.
The helper must request only the fixed service scope, verify the signed
audience, issuer, lifetime and exact grant arrays before private header delivery,
and refuse arbitrary claim maps, unqualified scopes and unsupported reductions.
HA control support must require its explicit scopes and action grants; it must
never be inferred from a read-only preset. No profile accepts an admin scope.
Use a separate credential file and ordinary account per host/preset/service.
Do not reinterpret a new service's grant as a Jocasta project.

Production admission also needs the actual protected-policy UID/ownership gate,
native client acquisition/renewal/refusal checks, signed-token acceptance by
the merged verifier, cross-service token denial, alternate API denial,
idempotent enrollment and independent cleanup. The source fixture proves role
logic only. Large grant lists must also fit the verifier's token-size bound in
the issued JWT; count and ID limits alone do not prove that. No inventory entry,
client enrollment or host grant is enabled here.

## Isolated Jocasta tooling enrollment

`playbooks/authentik-jocasta-tooling.yml` enrolls the laptop's two persistent
read-only Jocasta tooling accounts, `chaldea-laptop-jocasta-tooling-codex` and
`chaldea-laptop-jocasta-tooling-claude`, declared in
`playbooks/vars/authentik-jocasta-tooling.yml`. It owns its own blueprint,
"homelab-infra Jocasta tooling laptop", with the application `chaldea-jocasta`
("Chaldea Jocasta tooling access"), client ID `chaldea-jocasta-tooling`, group
`chaldea-jocasta-tooling-identities`, the existing RSA signing key
`8bbd6516-af33-4e64-a73b-caa39e692c29`, scope `jocasta.read` and 300-second
tokens. Each account's only project is `tooling`.

The entrypoint loads the existing controller SOPS API credential the way
`authentik_config` does, refuses any origin other than
https://auth.ryzhkov.dev, and runs only this role. It never converges the
access blueprint, the shared "homelab-infra machine identities" blueprint or
anything else, and grants nothing fleet-wide. Run it on the laptop, which is
the inventory host receiving the files:

```sh
ansible-playbook playbooks/authentik-jocasta-tooling.yml --check --diff
ansible-playbook playbooks/authentik-jocasta-tooling.yml
```

Check mode is the offline plan; add `-e jocasta_tooling_online_check=true` for
the read-only ownership and collision preflight. A second real run changes
nothing. The ownership, refusal and recovery rules above apply unchanged: never
replay an uncertain apply. Both app passwords expire 2026-11-23T00:00:00Z.
Extend `expires` or raise `generation` before then; within a day of expiry the
role refuses to run. To remove the enrollment, set both identities and
`authentik_jocasta_machine_access` to `state: absent` in that file and run the
same entrypoint; keep the blueprint name. Offline tokens stay valid for up to
300 seconds after removal.

The nightly drift check compares applications only with the access
blueprint's manifest, so it reports `chaldea-jocasta` as not managed by the
repository until that manifest is extended (see the end of this document).

## Tests

`roles/authentik_machine_identities/tests` runs in CI. The existing Jocasta
suite runs in the `authentik-machine-identities` job with a 60-minute budget;
the multi-service real-role matrix runs in `machine-profiles` with a separate
40-minute budget. Measured fixture runs exceed
the former 15-minute ceiling. The plain-data tests
cover the declaration and ownership refusals, closed profile selection, exact
OV/homelab/HA claims and cross-service mapping refusal. The role tests play the real role
with `ansible-playbook` against an in-memory fake of the Authentik API (only
what the role uses, applying the blueprint entry by entry), with the destination
hosts as local scratch directories: default off, check mode without writes or
key reads, enabled and disabled integrated check paths with a decryption
sentinel, trusted TLS, redirect refusal, complete blueprint and binding lookups,
explicit provider consumer data, foreign provider consumers, delivery and an
unchanged second run, refusal to take over an
existing account, refusal of a symlinked destination before Authentik changes,
rotation, absent identities, full removal, owner drift, dropping a declared
identity, and an application open to every account. Every run asserts that
neither the admin token nor any app password appears in the output. These are
fixtures; they prove the role's logic, not Authentik's behaviour.

## Not yet known: qualify before enabling

The fields used above were read in the Authentik 2026.8.4 sources
(`version/2026.8.4`, commit 410f46e): in a blueprint the token serializer
requires `user` and `intent`, refuses a change of user, and checks an app
password's expiry against the account's `token-maximum-lifetime` attribute
(`authentik/core/api/tokens.py`); app passwords are not rotated on expiry but
deleted (`authentik/core/models.py`); `client_credentials` with `username` and
`password` accepts only an active account's app password, checks the
application's policies and clamps scopes to the provider's mappings
(`authentik/providers/oauth2/token/client_credentials.py`, `base.py`); mapping
claims, including `aud`, override the defaults when
`include_claims_in_id_token` is set (`authentik/providers/oauth2/id_token.py`).
The 2025.8.6 token API behaviours in `docs/authentik-audit-2026-10-12.md` were
not re-observed. Source reading is not live proof. Before the first enabled
run, against the installed version:

- Installed version and digest; the blueprint imports, and a second apply is
  `changed=0`.
- The token exchange with the app password returns an RS256 JWT with `aud` the
  resource, `scope` `jocasta.read`, `jocasta_projects` the one project, and
  `exp - iat` 300; Jocasta accepts it for that project and refuses another.
- Requesting another scope or naming another provider's client ID adds nothing.
- The app password and the JWT get no administrative, configuration, grant or
  write authority through `/api/v3`: precise probes of user, token, group,
  provider, blueprint and policy writes and of `view_key` must be denied. A
  read of the account's own identity may succeed and is not by itself a
  failure.
- The account cannot change its own attributes, groups or roles, through the
  API or the user settings flow, and logging in to a flow with the app
  password grants nothing beyond that.
- Removal stops new issuance; a token issued before removal expires within
  300 seconds.
- Delivery, a second converge and cleanup on the named host.

The nightly drift check compares only the access blueprint's applications, so
once enabled it will report these machine applications as not managed by the
repository. Extending the drift manifest is a separate change, needed before
enabling.
