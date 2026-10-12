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
   Left in place.
5. **Two unused groups**: `admins` (igor only, not a superuser group) and an
   empty `authentik Read-only`, neither bound to anything. Left in place.
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

Two blueprint behaviours to keep in mind:

- Removing an entry from the file does not remove the object from Authentik.
  Removal is declared, as `authentik_removed_bindings` and
  `authentik_removed_apps` do.
- The embedded outpost's provider list is replaced on every run, so a
  forward-auth provider created by hand would be dropped from it. Declare them
  as routes instead.

## Not done

- Emby login through Authentik. Emby has no OIDC or SAML, and forward auth in
  front of it would break every native client, which logs in with its own API
  calls. The workable route is Authentik's LDAP outpost and Emby's LDAP
  plugin, which gives the same password everywhere rather than single sign-on.
  Deferred on 2026-10-12.
- Adopting the OIDC providers. Their client secrets would have to come from
  SOPS through blueprint `!Env` tags or the API, and each change risks a
  login outage for that application.
