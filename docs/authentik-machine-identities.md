# Authentik machine identities for Chaldea hosts

`roles/authentik_machine_identities` lets a named Chaldea host obtain short-lived
web MCP access tokens from Authentik without holding any administrative
credential. It is off in the integrated playbook: `playbooks/vars/authentik.yml`
sets `authentik_machine_identities_enabled: false`, and `playbooks/authentik.yml`
includes the role only when that is true. The declarations in this repository
are the laptop's Jocasta tooling enrollment and the OV HTTP a63e197f
identities, each run only by its own entrypoint; see
[Isolated Jocasta tooling enrollment](#isolated-jocasta-tooling-enrollment) and
[Isolated OV HTTP identities](#isolated-ov-http-identities).
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
client ID), which Jocasta refuses. Hosts authenticate with `client_id`,
`username` and their app password; no provider client secret is used or
delivered. The generic provider lookup can nevertheless return a client secret
to the controller. The isolated tooling entrypoint requires the existing-only
custody gate described below.

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
| OV | `https://ov.mcp.ryzhkov.dev/mcp` | `ov.read`; explicit `ov.write`, `ov.remove`, `ov.maintain` allowed | `ov_namespaces`; optional `ov_actions` |
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
The resources root itself, exactly `viking://resources` without a trailing
slash, is also one canonical root; nothing may be declared beside it, since
every other root overlaps it. User, agent and session stores are never an OV
grant.

OV mutation authority is an explicit signed per-identity allowlist, never the
requested OAuth scope alone. An identity may declare `ov_actions`, a canonical,
duplicate-free subset of `read`, `write`, `remove`, `maintain` in that order
that always includes `read`. Each action needs its scope family (`ov.read`,
`ov.write`, `ov.remove`, `ov.maintain`) in the application's declared scope,
which must start with `ov.read` and lists any optional families in that same
order. With only `ov.read` declared, the provider keeps its one original
mapping and the mapping text is byte-identical to earlier source. With more
families, each has its own owned scope mapping, and, as for HA, one mapping
elected from Authentik's authenticated `token.scope` emits the arrays exactly
once. Every request must include `ov.read` and only declared, distinct
families, or no resource claim is emitted. An identity without `ov_actions`
keeps its original claims, `aud` and `ov_namespaces` only, whatever it
requests: asking for `ov.write` with an old credential never signs a write
action. An explicit allowlist is reduced to the requested families and never
widened by them. The new HTTP adapter must check both the requested scope
family and the signed action, refuse unknown or duplicate actions and treat a
missing `ov_actions` claim as read only.

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

When this section was written the installed Chaldea credential helper and fleet
enrollment were Jocasta-only. chaldea-fleet-config's closed enrollment and
profile validators now accept OV, homelab, HA and HoloNet presets, and on
2026-10-25 the laptop's installed waits runtime (client-v0.4.18) answered
`session headers --preset NAME --audience jocasta|ov|homelab|ha`. Each other
host's installed runtime must still be checked, and token issuance, verifier
acceptance and native renewal remain qualification steps. The client contract
is that they accept a fixed named preset with `form: app_password`,
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

`playbooks/authentik-jocasta-tooling.yml` enrolls the laptop's read-only Jocasta
tooling accounts, `chaldea-laptop-jocasta-tooling-codex`,
`chaldea-laptop-jocasta-tooling-claude` and
`chaldea-laptop-jocasta-tooling-opencode`, declared in
`playbooks/vars/authentik-jocasta-tooling.yml`. The same declaration also adds
`chaldea-agent-a-jocasta-tooling-opencode`,
`chaldea-agent-b-jocasta-tooling-opencode` and
`chaldea-omarchy-pc-jocasta-tooling-opencode`, retaining all three laptop
accounts. It owns its own blueprint,
"homelab-infra Jocasta tooling laptop", with the application `chaldea-jocasta`
("Chaldea Jocasta tooling access"), client ID `chaldea-jocasta-tooling`, group
`chaldea-jocasta-tooling-identities`, the existing RSA signing key
`8bbd6516-af33-4e64-a73b-caa39e692c29`, scope `jocasta.read` and 300-second
tokens. Each account's only project is `tooling`. Codex and Claude have passed
native acceptance. OpenCode enrollment is source preparation; its protected
profile must pass separate source, CI and native acceptance gates before
persistent activation. Enrollment alone does not admit a Chaldea session.

The entrypoint loads the existing controller SOPS API credential the way
`authentik_config` does, refuses any origin other than
https://auth.ryzhkov.dev, and runs only this role. It never converges the
access blueprint, the shared "homelab-infra machine identities" blueprint or
anything else. The operator inventory must retain the laptop's existing
connection and destination and define the three added hosts as user `igor`,
home `/home/igor`, with `authentik_machine_identity_home: /home/igor`.
Use the approved transports `igor@192.168.130.2` for `agent-a`,
`igor@192.168.130.3` for `agent-b` and SSH alias `gaming-pc` for `omarchy-pc`.
Each added host receives only its own app password at
`/home/igor/.config/chaldea/authentik/jocasta-tooling-opencode.app-password`.
The role derives this path from inventory; home and path are not account
fields. Run the entrypoint with that operator inventory, never a remapping of
the laptop or the repository's bare DNS aliases:

```sh
ansible-playbook -i <operator-inventory> playbooks/authentik-jocasta-tooling.yml --check --diff
```

Check mode without API facts is the offline plan. It skips SOPS loading,
network requests, host inspection and credential reads or delivery. The
`jocasta_tooling_online_check=true` option enables the read-only ownership and
collision preflight, including the provider metadata reads described below.
Neither online check nor enrollment is authorized by this source change.
A second real run is intended to change nothing. The ownership, refusal and
recovery rules above apply unchanged: never replay an uncertain apply.

All six declarations expire at 2026-11-23T00:00:00Z. Manual rotation
remains due before November 22; this change preserves all three laptop
accounts at generation 1, their `tooling` grants, expiry and credential paths.
An expiry extension preserves the existing key; a higher generation rotates
it. No extension or rotation is performed here.

The root owns live enrollment, acceptance and rollback. Before an authorized
run, retain the last successful blueprint content and the existing accounts'
public generation, expiry and grant evidence. Withdrawing never-enrolled rows
needs no live cleanup. After confirmed enrollment, rollback marks only the
three added hosts' OpenCode rows absent, retaining every laptop row and the
existing provider/application and blueprint name. Confirm each new account,
its `-g1` app password and its host file is removed, with all prior credentials
and grants unchanged. Retain absent rows until cleanup is confirmed.
Root separately removes any newly installed OpenCode selection, if authorized
and installed. For an uncertain apply, reconcile the original operation and
ownership record before any retry or rollback. Offline tokens remain valid
for up to 300 seconds.
Full removal of all identities and the application is a separate root decision.

### Existing-only provider custody gate

The generic role retains its filtered `/providers/oauth2/?name=...` and
`?client_id=...` preflight. These are not broad enumeration, but the pinned
Authentik 2026.8.4 `OAuth2ProviderSerializer` includes `client_secret` for a
caller with change permission. A foreign name/client-ID collision can return
its secret before ownership refusal. `no_log` protects output, not custody.
The pinned serializer exposes no supported caller-selected field exclusion;
permission-dependent redaction is insufficient for this administrative actor.

The tooling declaration enables `authentik_machine_identity_existing_owned_only`,
which defaults off for other callers. It replaces both secret-bearing collection
queries with public `/providers/all/` summaries and one exact owned-PK OAuth2
detail read. Before any such detail or write, it requires:

- A root-supplied `authentik_machine_identity_existing_provider_baseline` mapping
  with `provider_pk: 102`, `application_pk:
  2931fd75-e7e3-4bbf-a8a8-f65c62318514` and `content_sha512`, the 128 lowercase
  hex digits of the exact successful pre-run stored blueprint content. These
  fixed IDs are retained prior public observations, not an original creation
  receipt. No hash is populated by this source change.
- The stored content and its successful `last_applied_hash` match that supplied
  hash, with the original provider/application marked `present`. The original
  typed blueprint provider name, unchanged client ID and application `!KeyOf`
  binding must agree with the declaration.
- A superuser actor, complete public provider inventory, exactly matching
  provider PK/name/OAuth2 type, and the exact public application UUID/slug bound
  to that PK. A same-name object with a different PK cannot be adopted.

Only then is `/providers/oauth2/102/` read. Its PK, name and client ID must
still agree before any mutation. The pinned `OAuth2Provider.client_id` model
field has `unique=True`; the unchanged owned client ID therefore excludes a
foreign duplicate under that schema without reading foreign OAuth2 bodies.
The provider name is also unique in the pinned base model. The regression seam
checks those pinned field declarations and serializer behavior. It does not
observe the live database's migration or index state.

Missing evidence, bootstrap, hash drift, absent ownership, changed declarations,
incomplete public data or changed bindings fail before private detail or writes.
A changed client ID on the owned detail fails before writes. This mode cannot
create, rename or remove the shared provider/application. It allows adding or
removing the OpenCode account while preserving the existing service. Generic
bootstrap is outside this path's custody qualification.

Root must qualify acquisition and provenance of the protected public baseline
and the installed schema before any online check or enrollment. The retained
operator-confirmed three-account baseline is historical evidence. Before the
six-account enrollment, root must independently reread and confirm the current
successful issuer content against the prior baseline; neither a new offline
render nor discovery may supply its own accepted hash. Never derive
an accepted hash from the response being checked. After a confirmed successful
content change, root must explicitly confirm the new public baseline hash for
the next operation; the role never refreshes it automatically. Pending or
uncertain operations require reconciliation of the original apply before any
new baseline, retry or rollback. There is no new receipt store or credential.
Application dependency checks, post-apply verification, `no_log` and the rule
against uncertain POST replay remain in the existing role. An owned provider
detail may contain its unused client secret; it is never delivered. Token
`view_key` remains after correlated completion and exact account/token/grant
verification. No live API was queried for this source correction.

The nightly drift check compares applications only with the access
blueprint's manifest, so it reports `chaldea-jocasta` as not managed by the
repository until that manifest is extended (see the end of this document).

## Isolated OV HTTP identities

`playbooks/authentik-ov-http-identities.yml` manages the existing owned OV
blueprint "homelab-infra ov http a63e197f identities", declared in
`playbooks/vars/authentik-ov-http-identities.yml`. It owns the application
`ov-http-a63e197f` ("OV HTTP a63e197f machine access", client ID
`ov-http-a63e197f-machine`, group `ov-http-a63e197f-machine-identities`,
signing key `8bbd6516-af33-4e64-a73b-caa39e692c29`, scope `ov.read`,
300-second tokens). It keeps the existing canary reader,
`chaldea-controller-ov-http-a63e197f-reader`, unchanged and adds two ordinary
laptop accounts:

- `chaldea-laptop-ov-tooling-codex` for the laptop's limited Chaldea Codex
  preset, at `/home/igor/.config/chaldea/authentik/ov-tooling-codex.app-password`;
- `chaldea-laptop-ov-interactive-codex`, a separate identity for the laptop's
  ordinary interactive Codex, at
  `/home/igor/.config/chaldea/authentik/ov-interactive-codex.app-password`.
  Only chaldea-fleet-config's closed native headers helper,
  `/usr/local/libexec/webmcp-interactive-headers ov laptop`, reads it.

The two laptop credentials are never shared: the limited preset never points
at the interactive file, and the interactive helper never reads the limited
one. All three accounts hold exactly
`ov_namespaces: [viking://resources/homeassistant-api/rest-api.md]`, expire at
2027-01-15T00:00:00Z and are generation 1. It is the same blueprint and
provider, not a second issuer or verifier. The interactive account adds no
signed identity claim: the OV scope mapping emits only `aud` and
`ov_namespaces`, as for the other two accounts, and the OV verifier
(`ov-mcp` `ov_http/auth.py`) authorizes by those signed roots, with a nonempty
subject, not by account name. The helper therefore binds the credential by its
fixed file and username and verifies the signed contract, not which of the
three accounts was issued the token.

The entrypoint runs only this role, like the Jocasta tooling one, and is off
unless explicitly requested. A real run needs `-e ov_http_identities_apply=true`;
`--check` is the offline plan, and `-e ov_http_identities_online_check=true`
adds the read-only preflight. Before loading any credential it refuses a
declaration that is not exactly the one owned OV application, that changes or
drops the controller identity or the limited laptop identity, that declares
any account other than these two and the interactive laptop account, that
moves the interactive account to another host, service or node, that disables
the existing-only gate or that leaves destinations unbound. The interactive
account's generation, expiry and state follow the role's usual lifecycle
rules. It loads the same controller SOPS API credential
and refuses any origin other than https://auth.ryzhkov.dev.

The existing-only provider custody gate described above now accepts exactly one
declared closed service profile with its application present, so this
blueprint's provider is read only by its baseline-proven PK. It still refuses
a second service, an absent application, bootstrap and every condition listed
there. Root must supply the prior public baseline:

```yaml
authentik_machine_identity_existing_provider_baseline:
  provider_pk: 42
  application_pk: 3621ae7a-e719-4cdf-9cb4-26bf110cfd78
  content_sha512: <128 hex digits of the exact successful stored content>
```

No hash is populated in source and the original enrollment receipts retain
none. The template and OV scope mapping code are unchanged since the original
enrollment's source, ebbde2778ea776acfaa6db43e24f9d0e4d723ca4, so root can
render the original controller-only declaration offline and compare its hash
with the stored blueprint. Never take the hash from the response being checked.

### Full interactive OV identities

The same entrypoint has one prepared alternative declaration,
`playbooks/vars/authentik-ov-http-full-interactive.yml`, loaded only with
`-e ov_http_identities_full_interactive=true`. Without that switch the
entrypoint refuses its scopes and accounts, so the default run stays exactly
the read-only cohort above. The full declaration extends the same owned
provider, application, client ID, issuer and signing key: no second issuer,
verifier slot or blueprint. It declares scope
`ov.read ov.write ov.remove ov.maintain`, which adds three owned scope
mappings beside the original `ov.read` one, and keeps the three existing
accounts unchanged, with no `ov_actions`, so they keep their exact read-only
claims. It adds one account per selected host, each with
`ov_namespaces: [viking://resources]` and all four actions:

- `chaldea-laptop-ov-full-interactive` on the laptop (UID 1001);
- `chaldea-omarchy-pc-ov-full-interactive` on omarchy-pc (UID 1000), bound to
  the same machine and account as the controller but declared under its fleet
  host name. The operator inventory must define `omarchy-pc` as well.

Both files are
`/home/igor/.config/chaldea/authentik/ov-full-interactive.app-password`.
Ordinary interactive Codex and Claude on one host share that host's account
through chaldea-fleet-config's closed helper mode
`/usr/local/libexec/webmcp-interactive-headers ov-full HOST`, as they share
the managed Jocasta identity; neither the limited preset nor the narrow
`ov laptop` helper mode reads it. In this mode the guard additionally fixes
both full accounts' host, profile, service, root and actions, and refuses a
partial pair, a third account or `ov_actions` on any existing account.

Selecting the full declaration is a later, separately authorized enrollment.
It requires the accepted server verifier, a freshly confirmed provider
baseline and the fleet consumer that refuses a narrower or wider token. After
it has been applied, the stored blueprint owns all four mappings, so the
default read-only run is refused ("declare it absent before dropping it")
rather than silently removing them. To stop issuance, run the full mode with
the two full accounts declared `state: absent`; the old accounts are
unaffected. Issued tokens remain valid until they expire.

### Destination binding

Inventory names and `ssh_user` cannot tell the two destinations apart: the
controller credential lives on gaming-pc (`omarchy-pc`) as UID 1000 and the
laptop's would live on `omarchy-macbook-air-igor` as UID 1001, both user `igor`
with home `/home/igor`, and a `local` connection means whichever machine runs
the play. The repository inventory marks the laptop `local`. The role variable
`authentik_machine_identity_destinations` binds each identity host to a node
name and numeric UID. When it is set, it must name exactly the declared
identity hosts. Before Authentik changes, and again immediately before
delivery, the role runs `id -u` and `uname -n` on each destination as its
configured user and requires the home's owner UID to match. A mismatch stops
the run before any app password is read. This entrypoint requires the binding;
other callers keep the name-only behavior.

Every run rereads the controller identity's app password and writes it back to
the controller destination unchanged, as the role does for every present
identity. With the binding, that write can only reach gaming-pc as UID 1000,
and the two laptop keys can only reach the laptop as UID 1001. Use an operator
inventory outside this repository that defines exactly `controller` and
`laptop` with their real connections, for example the controller as a local
connection on gaming-pc and the laptop over SSH as `igor`. The binding check
refuses any other mapping, including running the play on the laptop with the
controller mapped to its local connection.

Removing the interactive laptop account later uses the same entrypoint with
only `ov-interactive-codex` set to `state: absent`, after its fleet native
registration has been removed; the controller and limited laptop identities,
application and blueprint name stay unchanged. Removing the limited laptop
account needs a reviewed change to the entrypoint's literal guard first.
Offline tokens remain valid for up to 300 seconds after removal.

## Isolated HoloNet catalog identities

`playbooks/authentik-holonet-catalog.yml` manages the existing owned HoloNet
blueprint "homelab-infra holonet catalog identities", declared in
`playbooks/vars/authentik-holonet-catalog.yml`. It owns the application
`chaldea-holonet` ("Chaldea HoloNet catalog access", client ID
`chaldea-holonet-machine`, group `chaldea-holonet-machine-identities`, signing
key `8bbd6516-af33-4e64-a73b-caa39e692c29`, scope `holonet.read`, resource
https://media.mcp.ryzhkov.dev/mcp, 300-second tokens). The declaration is the
public historical cohort that the 2026-10-22 read-only issuer observation
matched: one persistent account, `chaldea-homelab-holonet-movies-catalog`
(profile `holonet-movies-catalog`, generation 1, expiry 2026-11-20T00:00:00Z,
`token-maximum-lifetime` days=180). Its grant is server
`3c86e6d7ff6c4a3296522cdf03d22bb0`, library `3` (Movies), catalog user
`54b7d36cef804d3c8478474553ed3a82`, empty history and target users,
`catalog.read` only and namespace `movies-catalog`.

It keeps that account unchanged and adds one ordinary laptop account,
`chaldea-laptop-holonet-movies-catalog-claude`, for the laptop's limited
Chaldea Claude catalog preset, at
`/home/igor/.config/chaldea/authentik/holonet-movies-catalog-claude.app-password`.
It holds the same fixed grant. The scope mapping changes only by adding that
username to its literal allowlist; a later run with only the laptop row
`absent` restores the historical mapping. It is the same blueprint, provider
and verifier, not a second issuer.

The entrypoint runs only this role and is off unless explicitly requested. A
real run needs `-e holonet_catalog_apply=true`; `--check` is the offline plan;
`-e holonet_catalog_online_check=true` adds the read-only preflight. Before
loading any credential it refuses a declaration that is not exactly the one
owned HoloNet application, changes or drops the homelab identity, widens,
moves or multiplies the laptop identity, changes `max_days` from 180 or the
baseline anchor, or disables the existing-only gate. After loading the
controller SOPS API credential, and before any request, it requires the
effective `authentik_url`, including any extra-vars override, to be the
exact literal `https://auth.ryzhkov.dev/api/v3`; no input can move where
that credential is sent. Before loading it, the entrypoint also refuses extra
vars for `machine_identity_api`, the role-derived URL every request uses, and
for its own baseline include inputs `machine_identity_baseline_anchor` and
`machine_identity_baseline_identities`. These guards check the declared
inputs and these reserved facts only: extra vars can still override other
internal facts, so run only the documented commands. The laptop account's
generation, expiry and state follow the role's lifecycle rules. Its source
expiry is aligned with the homelab account; root may change it within the
role's 1..180-day window by a source change. The homelab expiry is never
extended silently: the role refuses every run within one day of
2026-11-20, and extending it is a separate reviewed change.

Destination bindings are required but not in source, because the homelab
node name and UID are not repository facts. Root supplies
`authentik_machine_identity_destinations` naming exactly `homelab` and
`laptop`, for example:

```yaml
authentik_machine_identity_destinations:
  homelab: {hostname: <homelab uname -n>, uid: <iryzhkov UID>}
  laptop: {hostname: omarchy-macbook-air-igor, uid: 1001}
```

The operator inventory defines `homelab` as `iryzhkov` over SSH with home
`/home/iryzhkov` and `laptop` as `igor` with home `/home/igor`. As for OV, every
real run rereads the homelab app password and writes it back unchanged to
`/home/iryzhkov/.config/chaldea/authentik/holonet-movies-catalog.app-password`;
the binding ensures that write reaches only the bound machine and account.

### Optional ordinary HoloNet reader

The same isolated entrypoint can add `chaldea-laptop-holonet-interactive`
with `-e holonet_catalog_interactive_enabled=true`. It is off by default.
Its separate `holonet-interactive.app-password` is delivered only to the
bound laptop account and is consumed by the ordinary Codex managed helper.
It has the same fixed Movies catalog grant, with no history, target users or
write actions. It does not reuse the admission-bound preset credential.

The declared existing two accounts and all application/provider bindings stay
unchanged. The only mapping delta is the new exact username and its identical
grant. A fresh confirmed successful stored-content baseline is required before
apply and before repeat. The historical homelab-only baseline-read mode is
historical evidence and must refuse the current two- or three-account content;
do not reuse its old anchor as a current baseline.

Keep the interactive opt-in on every apply after enrollment. For retirement or
failed initial qualification, keep the opt-in true and explicitly set only
`holonet_catalog_interactive_identity.state: absent` through a complete
reviewed identity object. The same guard preserves its host, profile and grant;
the role owns lifecycle validation. Confirm new-only account/token/file absence
and preservation of both existing accounts. Dropping the opt-in is not the
reviewed removal procedure. Never use this entrypoint to alter Jocasta or OV
accounts, signing keys, global policies or the Emby backend identity.

Neither this source declaration nor an issuer success proves ordinary native
MCP use or renewal. Enrollment, helper deployment and live/native lifetime
qualification are separate guarded stages.

### Existing-only baseline read

The existing-only gate needs the current provider PK, application UUID and
SHA-512 of the successful stored blueprint. The retained read-only receipt
has only the content's SHA-256 and the blueprint PK, and no maintained
producer existed. `-e holonet_catalog_baseline_read=true` with `--check` (and
without apply or the online preflight) runs only the role's
`tasks/baseline.yml`, with the accepted SOPS/no_log credential path:

```sh
ansible-playbook -i <operator-inventory> playbooks/authentik-holonet-catalog.yml --check \
  -e holonet_catalog_baseline_read=true
```

It makes exactly four GETs, the same discovery the existing-only gate already
uses: the named blueprint, `/core/users/me/`, public `/providers/all/`
summaries and the one declared application by slug. It reads no OAuth2
collection or detail, token, key or host and writes nothing. The filter
`machine_identity_existing_baseline` then requires the pinned anchor
(blueprint `2514696c-15e3-494e-8df6-eb0c098a9efb`, content SHA-256
`aef30eb3096776f9edddff5d00cb3eb3aac7d1128b05a730aa138407bc577f58`), an
ownership record naming exactly the historical homelab identity, and every
condition of the existing-only gate for the derived values. Only then does it
show the allowlisted result: the anchor, the three baseline fields and fixed
match booleans. No body, expression, header or credential is shown. Content
with another hash yields nothing, so the response never supplies its own
accepted hash; the SHA-512 is of the bytes the independent SHA-256 names.

Root reviews that output and supplies it as
`authentik_machine_identity_existing_provider_baseline`. The anchor names
only the pre-enrollment content: after a successful apply it no longer
matches, and the next baseline needs explicit confirmation as described in
the existing-only gate section.

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
`test_ov_http_identities.py`, in the `machine-profiles` job, plays the actual
OV entrypoint with a SOPS sentinel that returns only the fake admin token. It
checks the exact source declaration, the destination binding refusals, the
existing-only gate for one OV application, extra-vars attempts to drop or
change the controller or limited laptop identity or to widen, move or add an
interactive account, refusal without a baseline, refusal of a remapped
destination before any write or key read, addition of both separate laptop
accounts and files with the controller account, token and file
byte-identical, refusal of the stale baseline, and a confirmed second run with
no changes.
`test_holonet_catalog_identities.py`, in the same job, checks the exact HoloNet
declaration, that current source renders the receipt-matched historical
mapping (expression SHA-256 `8874e2d1...`) and the homelab account unchanged,
the mapping's laptop-only addition and refusals, and the baseline producer's
refusal of every anchor, ownership, status, inventory, binding or actor
drift. It plays the role's baseline task (four discovery GETs, allowlisted
output, no secret) and the actual entrypoint: mode separation, literal guards,
destination requirements, the pinned anchor refusing fixture content, the
missing-baseline gate, remapped destinations, laptop addition with the homelab
account, token and file byte-identical, the stale baseline and a confirmed
second run with no changes. The unpatched entrypoint refuses a foreign
credential URL, or the URL and former origin input moved together by extra
vars, after SOPS loading and before any request, in apply, online-check and
baseline modes. With the legitimate literal URL, an extra-vars
`machine_identity_api` or either baseline include input is refused before SOPS
loading and before any request, in all three modes. Plays that must reach the fake use a scratch copy whose literal
API pin names the fixture origin; they prove the role's logic, not the
production pin. The entrypoint plays skip once the pinned homelab expiry is
within the role's one-day window.

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
