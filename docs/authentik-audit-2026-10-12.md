# Authentik: what was configured, and what is managed from here now

Audited 2026-10-12 through the API, on Authentik 2025.8.6 (the latest release
that day), and converged the same evening. Until then everything inside
Authentik had been set up by hand in the admin interface; the stack itself has
been deployed from this repository since the phase 5 cutover
(`inventory/group_vars/swarm/stacks-p2.yml`).

## Access for automation

A service account, `agent-iac`, member of `authentik Admins`, with an API token
that expires on **2027-04-10**. The token is in
`secrets/authentik-api.sops.yaml` with the API URL; `playbooks/authentik.yml`
is the only thing in this repository that reads it.

It is a superuser token, which makes it equivalent to control of every
application behind SSO and, because expression policies run Python on the
server, to code execution on the Authentik host. It was created with `ak
shell` in the server container. The first key was printed into an agent
session's transcript by mistake and was rotated within minutes; the rotated-out
key was checked to answer 403.

To renew it, delete the token `agent-iac-api` and create a new one for
`agent-iac` (Directory > Tokens and App passwords, intent API), then
`sops secrets/authentik-api.sops.yaml`.

A second service account, `drift-check`, is a member of the default
`authentik Read-only` group only: it can read every object and change none.
Its token, `drift-check-api`, expires on **2027-10-12** and sits in the same
SOPS file as `authentik_drift_token`; `playbooks/authentik.yml` installs it
on homelab for the nightly drift check.

Both tokens must be created or renewed with `ak shell` in the server
container (`Token.objects.create(..., intent=TokenIntents.INTENT_API,
expiring=True, expires=...)`), never through the API, because of three
behaviours of the token API in 2025.8.6, all met on 2026-10-12:

- An API-intent token always gets the default lifetime of 30 minutes;
  `expires` is overwritten (`authentik/core/api/tokens.py`, `validate`). An
  expiring token is then rotated to a new key when it lapses, so its holder
  starts getting 403.
- Any create or update that omits `user` assigns the token to the caller.
  A PATCH that only changed `expires` moved `drift-check-api` to `agent-iac`,
  which put a superuser token on homelab for about fifteen minutes before it
  was revoked. Check `user_obj` after touching a token.
- `POST /core/users/service_account/` issues an app password, which the API
  refuses as "Token invalid/expired".

The key can then be read with `GET /core/tokens/<identifier>/view_key/`, which
changes nothing.

## Findings

1. **Six applications were open to every account.** budget, forgejo, photos,
   komga, rss and jellyfin had no policy binding, and with the policy engine
   mode "any" an application without bindings admits anyone who can log in.
   Every account could open Forgejo and Budget.
2. **MFA was in use by one account and required of none.** One TOTP device
   existed, igor's. The default MFA stage validates a device when the user has
   one and skips itself otherwise.
3. **Jellyfin's application, provider and group were left over** after Emby
   replaced it. The group, `jellyfin_users`, was the closest thing to a
   household group.
4. **An expired Emby app password**: `emby-service-account` held an app
   password that expired on 2026-09-07, with no LDAP provider to use it with,
   apparently from an abandoned attempt at Emby login through Authentik.
   The token was deleted; the account is kept for a later LDAP attempt.
5. **Two unused groups**: `admins` (igor only, not a superuser group) and an
   empty `authentik Read-only`, neither bound to anything. `admins` was
   deleted after checking that no application reads a groups claim (Forgejo's
   OAuth source maps none). `authentik Read-only` was kept: the default
   blueprint `rbac-role-read-only.yaml` creates it, and the drift check's
   account now uses it.
6. Otherwise clean: all OIDC clients confidential, with exact redirect URIs;
   only the default flows, stages and policies; one embedded outpost; no
   sources beyond the built-in one; no failed logins but typos.

One misreading, recorded because it is easy to repeat: `GET
/core/applications/` lists only the applications the caller may open, unless
`superuser_full_list=true` is passed. The first pass, made as `agent-iac`,
therefore missed ai, home-assistant and vaultwarden, which are bound to igor
or to `home_assistant_users`, and reported their providers as unassigned. All
three were in fact configured and in use (last authorizations 2026-08-27,
2026-10-02 and 2026-09-07).

## What changed

- `jellyfin_users` was renamed `family` (through the API, before the first
  run, so the blueprint found the existing group rather than creating a second
  one), and tanya was added.
- Every application is now bound: `family` for budget, forgejo, komga, photos,
  rss and vaultwarden (vaultwarden was igor-only before); `home_assistant_users`
  for home-assistant; igor alone for ai.
- Jellyfin's application and provider were deleted.
- Members of `authentik Admins` without an MFA device are made to enroll one
  (TOTP or WebAuthn) at login. This was tested with a temporary admin account
  with no device, which was sent to enrollment, and a temporary `family`
  account, which logged straight in; both were deleted afterwards.
- The ai.ryzhkov.dev proxy provider, application and the embedded outpost's
  provider list were adopted unchanged (same provider, same client ID) into
  the `sso: forward` route field.

## How it is managed now

`playbooks/authentik.yml` renders `playbooks/vars/authentik.yml` into one
blueprint, "homelab-infra access", stores it in Authentik through the API and
applies it, then asks Authentik, for every person and every application in the
file, whether access passes, and fails if any answer differs from the file.
The blueprint lives in Authentik's database, not in a mounted directory, so it
needs no change to the stack and moves with it. The cost is that the worker
never re-applies it by itself (its hourly discovery walks blueprint files
only), so a change made in the admin interface to a managed object stays until
the playbook next runs, which puts it back.

Managed: the groups `family` and `home_assistant_users` and their members,
every application's bindings, the admin MFA stage and its policy, and the
providers and applications of `sso: forward` routes (`docs/traefik-routing.md`,
"Putting a service behind Authentik"). Not managed: the OIDC providers and
their client secrets, the `authentik Admins` group (so that a mistake in the
file cannot lock anyone out), users themselves, and the default flows.

Between runs, homelab's nightly drift check (`roles/drift_check`) compares
Authentik with what the last run applied, which `playbooks/authentik.yml`
leaves on homelab as `~/.local/share/homelab-infra/authentik-manifest.json`:
the blueprint's content and status, every managed application's policy mode
and bindings, every managed group's members, and any application the file
does not manage. It compares the configuration rather than asking who may
open what, because `check_access` honours `for_user` only for a superuser and
otherwise silently answers for the caller. Tested 2026-10-12 by adding a
binding by hand: reported, and clean again once removed.

The create path of `sso: forward` was tested the same day with a throwaway
route: a person in `family` went through authorization and the callback to
the backend, a person in no group stopped at Authentik's access-denied page.
The test found that the blueprint must create forward-auth applications
before it binds anything to them, since `!Find` resolves entry by entry.

Two blueprint behaviours to keep in mind:

- Removing an entry from the file does not remove the object from Authentik.
  Removal is declared, as `authentik_removed_bindings` and
  `authentik_removed_apps` do.
- The embedded outpost's provider list is replaced on every run, so a
  forward-auth provider created by hand would be dropped from it. Declare them
  as routes instead.

## Found on the way: Miniflux credentials in plaintext

`docs/current-state.md` had noted Miniflux's OIDC client secret hardcoded in
its Compose file. The live Compose file no longer had it, but
`~/app/miniflux/docker-compose.yml.bak` on homelab (2026-08-29, mode 0644)
still held the live OIDC client secret, admin password and Postgres password,
and a copy had gone to the bucket. All three were rotated on 2026-10-12 (the
admin password through Miniflux's API, the database password with ALTER
USER, the client secret in Authentik), the old values were checked to be
refused, and the `.bak` was deleted. No other Compose file under `~/app` on
homelab holds a literal secret.

## Not done

- Emby login through Authentik. Emby has no OIDC or SAML, and forward auth in
  front of it would break every native client, which logs in with its own API
  calls. The workable route is Authentik's LDAP outpost and Emby's LDAP
  plugin, which gives the same password everywhere rather than single sign-on.
  Deferred on 2026-10-12.
- Adopting the OIDC providers. Their client secrets would have to come from
  SOPS through blueprint `!Env` tags or the API, and each change risks a
  login outage for that application.
