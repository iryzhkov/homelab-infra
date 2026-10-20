# Authentik machine identities for Chaldea hosts

`roles/authentik_machine_identities` lets a named Chaldea host obtain short-lived
Jocasta access tokens from Authentik without holding any administrative
credential. It is off: `playbooks/vars/authentik.yml` sets
`authentik_machine_identities_enabled: false`, and `playbooks/authentik.yml`
includes the role only when that is true. Nothing in this repository enables it,
and no host or Authentik object has been changed by adding it. Complete infra
identity creation and host credential delivery before enabling the fleet enrollment.

## What it manages

One shared OAuth2 provider and application for Jocasta, and for each declared
host and Chaldea preset (profile):

- an ordinary service account, `chaldea-<host>-<profile>`, with no roles, whose
  only group is the one bound to the Jocasta application;
- one app password for that account, with an explicit owner, intent and expiry;
- one private file on the host, `~/.config/chaldea/authentik/<profile>.app-password`
  of the host's inventory `ssh_user`, mode 0600 in a 0700 directory, holding the
  exact key without a newline.

The provider grants only `client_credentials`, issues 300-second tokens signed by
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
shared application are untouched. The shared application can be removed only
once every identity is absent; deleting the application deletes its binding.
A host named by an absent identity must stay in the inventory until the run
that removes its file has succeeded.

Rotation is a higher `generation`: Authentik creates a new app password under a
new identifier, the old generations are declared absent and deleted, and the
host file is replaced. A later `expires` on the same generation only extends the
existing key; it is not rotation. Deleting an app password stops new token
issuance with it. Tokens already issued are verified offline by Jocasta and stay
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
5. Store and apply the blueprint, and require a successful import.
6. Look everything up again and compare: provider settings, mapping expression,
   application, the single group binding, the group's members, each account and
   each token. Ask `check_access` for every account and every application
   (`superuser_full_list`); each account must pass for the Jocasta application
   and fail for every other one. An application without bindings that admits
   every account therefore stops the run.
7. Only then, for each present identity, read the key with `view_key` on the
   controller under `no_log`, write it to the host as its user (no become,
   `no_log`, no diff) and check the result; for each absent identity remove
   the file.

The integrated playbook skips `authentik_config` in check mode, so it never
decrypts SOPS. Without explicit controller API facts it validates and shows an
offline blueprint and file plan; it cannot check live ownership or collisions.
When API facts are explicitly supplied, check mode also performs the read-only
preflight in steps 2 and 3. It reads no key, connects to no host and stores nothing.
Before the new role sends credentials, it requires a canonical HTTPS origin
followed by the fixed `/api/v3` path, without userinfo, query or fragment. Every
credential-bearing request in this role refuses redirects. Every API task is `no_log`, so a failure shows the role's
own message rather than a response. A second real run changes nothing (the
apply call is repeated, as for the access blueprint, and reports no change).

## A second service

Only Jocasta is implemented. The role keeps the two halves apart so that a
second web MCP service behind Authentik can be added without touching the
account lifecycle:

- Service-independent: account naming, app passwords, generations and expiry,
  the ownership record and its refusals, destination checks, delivery and
  removal (`machine_identity_spec`'s account part, `machine_identity_owned`,
  `_queries`, `_refusals`, `_problems`, `tasks/main.yml`, `tasks/host.yml`, and
  the account section of the template).
- Jocasta-specific: the service profile at the top of
  `filter_plugins/machine_identities.py` (`SERVICE`: scope `jocasta.read`, 300
  seconds, the `resource` field, the per-account `project` grant stored as
  `chaldea_jocasta`, and the mapping expression) and the provider section of
  the template.

Adding a service X is a separate reviewed change in these steps:

1. Add X's profile beside Jocasta's: a fixed scope name and token lifetime,
   X's application fields, a per-account grant validated against a closed
   pattern and stored in its own attribute, and a mapping expression whose
   `aud` is a literal operator value and whose other claims come only from that
   attribute and group membership. Never a claim from the token request, a
   wildcard grant, or an administrative or write scope that has not been
   qualified for X.
2. Make the application declaration a mapping keyed by service, each entry
   with its own slug, name, group, client ID, resource and signing key, and
   render one provider, application, mapping, group and binding per service.
   Keep Jocasta's names as they are, so its objects keep matching the
   ownership record and nothing is renamed.
3. Give each identity a `service` naming exactly one service. An account
   belongs to one service's group only: an app password authenticates its
   account to every `client_credentials` provider whose application admits
   the account, so one account in two groups would let one host credential
   obtain tokens for both services. A host that needs both gets two
   identities with two profiles (two presets): two accounts, two app passwords
   and two files. Usernames and host paths keep their form, because a profile
   names one preset and a preset enrols one service.
4. The post-apply check already requires each account to pass `check_access`
   for exactly one application; it becomes that account's own service's
   application.
5. Chaldea's policy parser and the fleet role's enrollment check accept only
   `form: app_password` and `scope: jocasta.read` today. They gain X's scope,
   audience and grant rule in the same change set, and X's verifier pins its
   own issuer, resource and claims.
6. Qualify X's gates as listed above, including that X refuses Jocasta's
   tokens and Jocasta refuses X's (distinct `aud`).

## Tests

`roles/authentik_machine_identities/tests` runs in CI. The plain-data tests
cover the declaration and ownership refusals. The role tests play the real role
with `ansible-playbook` against an in-memory fake of the Authentik API (only
what the role uses, applying the blueprint entry by entry), with the destination
hosts as local scratch directories: default off, check mode without writes or
key reads, integrated check mode with a decryption sentinel, trusted TLS,
redirect refusal, complete blueprint lookup, foreign provider consumers, delivery and an unchanged second run, refusal to take over an
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
once enabled it will report the Jocasta application as not managed by the
repository. Extending the drift manifest is a separate change, needed before
enabling.
