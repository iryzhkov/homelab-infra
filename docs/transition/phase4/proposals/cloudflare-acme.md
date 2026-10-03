# Phase 4 proposal: ACME challenge delegation and a scoped Cloudflare token

Drafted 2026-10-02, 20:00 to 20:10 PDT, by executor E19. Read only: Traefik's configuration
on public-api, the Cloudflare API with Traefik's own token (verify and GET calls run on
public-api, the token never printed or copied off the host) and with the laptop's read-only
snapshot token (keyring `service cloudflare-api account homelab-infra`). Nothing was changed
in Cloudflare, on public-api, or in the repository outside this file.

## 1. What Traefik uses today

| Item | Value | Source |
|---|---|---|
| Challenge | DNS-01, `provider: cloudflare`, `delayBeforeCheck: 10`, resolvers 1.1.1.1 and 8.8.8.8 | public-api `~/apps/traefik/traefik.yml:20-32`, resolver `letencrypt` |
| Credentials | `CF_API_EMAIL` and `CF_DNS_API_TOKEN` as container environment | `stacks/traefik/docker-compose.yml:16-17` |
| Where the token lives | `~/apps/traefik/.env`, mode 600 `igor:igor`, written by `roles/compose_stack` from `secrets/public-api-traefik.sops.yaml` | `inventory/host_vars/public-api.yml` (`secrets: public-api-traefik`) |
| Also visible in | `docker inspect traefik` (`Config.Env`) | checked, key names only |
| Static configuration in the repository | no: `traefik.yml` exists only on public-api | `stacks/traefik/` holds `docker-compose.yml` and `dynamic/` only |

Phase 0's question "HTTP-01 or DNS-01" is answered: DNS-01 already, so each edge VM can
issue independently today. Step 4's switch is not needed; only the delegation and the token
scope remain.

### The token's scope

- `GET /user/tokens/verify` with Traefik's token: `code 1000 Invalid API Token`.
  `GET /accounts/<account>/tokens/verify`: `success: true, status: active`, no `expires_on`,
  no `not_before`. It is an account-owned token with no expiry.
- `GET /zones`: one zone visible, `ryzhkov.dev`, with permissions `#dns_records:edit`,
  `#dns_records:read`, `#zone:read`. The account holds only this one zone (snapshot token
  agrees).
- Account-level lists (`cfd_tunnel`, `workers/scripts`, `access/apps`) return success with
  zero results, while the account has one tunnel; `accounts/<id>`, `members`, `r2/buckets`,
  zone `settings`, `rulesets`, `ssl/certificate_packs` return 9109 or 10000. So it holds no
  account permissions and nothing on the zone beyond DNS.
- The token's own policy (name, IP filter) could not be read: `GET
  /accounts/<id>/tokens/<id>` answers 9109 with Traefik's token, and the snapshot token
  answers 9109 on `accounts/<id>/tokens` and `user/tokens`. Whether it carries an IP
  restriction is unknown; Igor can see it under My Profile, API Tokens, or Manage Account,
  Account API Tokens.

Recorded answer: Traefik's token is "Zone DNS Edit + Zone Read on ryzhkov.dev", which is the
whole main zone, MX and tunnel CNAMEs included. A compromised edge VM can rewrite any record
in it.

### What the certificate store holds

`acme.json` (root, 0600) holds 46 certificates: the wildcard `ryzhkov.dev` +
`*.ryzhkov.dev` (notAfter 2026-11-28), and 45 single-name certificates such as
`vault.ryzhkov.dev`, `git.ryzhkov.dev`, `home.ryzhkov.dev`. The single-name ones are still
being renewed: twelve were reissued in late September (notAfter 2026-12-26), the rest expire
2026-11-04 to 2026-11-22. Each single-name renewal writes
`_acme-challenge.<name>.ryzhkov.dev`, not `_acme-challenge.ryzhkov.dev`. Three stale TXT
records from earlier runs are still in the zone: `_acme-challenge.dns`, `_acme-challenge.emby`,
`_acme-challenge.prowlarr`.

This matters twice:

1. One CNAME at `_acme-challenge.ryzhkov.dev` covers only the wildcard. After the token is
   scoped to the delegated zone, every single-name renewal fails unless it also has its own
   CNAME or the single-name certificates are dropped.
2. A fresh public-api-b with an empty `acme.json` and today's per-router `certResolver`
   can request up to 46 certificates at first start, close to Let's Encrypt's 50 new
   certificates per registered domain per week. Each rebuild during testing repeats it.

## 2. Delegation steps for Igor

Cloudflare's Free plan cannot host a subdomain as its own zone (`acme.ryzhkov.dev` as a
separate zone needs Enterprise), so "a separate zone" has to be a separate registered
domain or a different DNS provider. Three ways, in order of preference:

- A. A second, cheap domain added to the same Cloudflare account on the Free plan, used for
  nothing else (called `acme-zone.example` below). The token is scoped to that zone only.
  Stays with one provider and Traefik's `cloudflare` provider.
- B. deSEC (free) with a `dedyn.io` name and a token restricted to that domain; Traefik
  provider `desec`, `DESEC_TOKEN_FILE`. Adds a third party.
- C. Self-hosted acme-dns on OCI. Most moving parts; not recommended.

Steps for A, in this order (each is Igor's, in the dashboard):

1. Register the domain and add it to the account (Add a domain, Free plan), set the
   registrar's nameservers to the two Cloudflare names it shows, wait for status Active.
2. In `acme-zone.example`, nothing needs creating: lego writes the TXT records itself.
3. Create the token: My Profile (or Manage Account), API Tokens, Create Token, "Edit zone
   DNS" template. Permissions `Zone : DNS : Edit` and `Zone : Zone : Read`. Zone Resources:
   Include, Specific zone, `acme-zone.example` only. Client IP filtering: the house's WAN
   address, if it is stable. No TTL, or a long one with a calendar note. Copy the token once,
   straight into sops (step 5 of the repository change), never into a file in `/tmp`.
4. In `ryzhkov.dev`, DNS, Records: delete the three stale TXT records
   `_acme-challenge.dns`, `_acme-challenge.emby`, `_acme-challenge.prowlarr`. Then add
   `CNAME _acme-challenge` -> `_acme-challenge.acme-zone.example`, Proxy status DNS only,
   TTL Auto. No other record may exist at `_acme-challenge.ryzhkov.dev`; today none does.
5. Only after the single-name certificates are gone (section 3) or have their own CNAMEs,
   revoke the old token (Roll or Delete in the API Tokens list).

lego follows the CNAME by default (`LEGO_DISABLE_CNAME_SUPPORT` unset), finds the zone of
the target, and writes the TXT there with the scoped token. Check before cutting over:
`dig +short CNAME _acme-challenge.ryzhkov.dev @1.1.1.1` returns the target, and a staging
issuance (`caServer` set to the staging directory on public-api-b only) succeeds.

## 3. Repository change (draft; not applied)

Owner: whoever implements phase 4 step 4. Files named for orientation; E18 owns the edge
role changes, so this is to be merged into that work, not applied separately.

1. Secrets: a new `secrets/edge-acme.sops.yaml` with one key, `cf_dns_api_token` (the scoped
   token). Remove `CF_API_EMAIL` and `CF_DNS_API_TOKEN` from
   `secrets/public-api-traefik.sops.yaml` after the cutover; with an API token lego does not
   need the e-mail.
2. A prepare task file `stacks/traefik/prepare.yml`, run through the existing
   `prepare_tasks` hook of `roles/compose_stack`:

   ```yaml
   - name: Load the ACME DNS token
     community.sops.load_vars:
       file: "{{ playbook_dir }}/../secrets/edge-acme.sops.yaml"
       name: edge_acme
     delegate_to: localhost
     no_log: true

   - name: Directory for the edge proxy's secrets
     ansible.builtin.file:
       path: /etc/traefik
       state: directory
       owner: root
       group: root
       mode: "0700"
     become: true

   - name: Install the ACME DNS token
     ansible.builtin.copy:
       content: "{{ edge_acme.cf_dns_api_token }}"
       dest: /etc/traefik/cf_dns_api_token
       owner: root
       group: root
       mode: "0400"
     become: true
     no_log: true
   ```

3. `stacks/traefik/docker-compose.yml`: drop both `CF_*` variables and the
   `${CF_DNS_API_TOKEN:?...}` interpolation, add

   ```yaml
       environment:
         TZ: America/Los_Angeles
         CF_DNS_API_TOKEN_FILE: /run/secrets/cf_dns_api_token
       volumes:
         - /etc/traefik/cf_dns_api_token:/run/secrets/cf_dns_api_token:ro
   ```

   The `traefik` image runs as root, so a root-owned 0400 file is readable inside. lego reads
   any of its variables from a file when the name ends in `_FILE`.
4. The stack entry (moved to the `edge_proxy` group by E18): replace
   `secrets: public-api-traefik` with `env_extra: {}` (so `.env` still carries `HOST_IP`) and
   add `prepare_tasks: prepare.yml`.
5. Traefik's static `traefik.yml` into the repository as a `config_templates` entry, with
   the default certificate on the `websecure` entrypoint:

   ```yaml
   entryPoints:
     websecure:
       http:
         tls:
           certResolver: letencrypt
           domains:
             - main: ryzhkov.dev
               sans: ["*.ryzhkov.dev"]
   ```

   and the generated routers (`roles/traefik/templates/*.j2`, `stacks/traefik/dynamic/*.yml`)
   changed from `certResolver: letencrypt` to `tls: {}`, so no router asks for a
   single-name certificate again.
6. On each edge VM, once the wildcard is issued through the delegated zone, remove the 45
   single-name certificates from `acme.json` (stop Traefik, `jq` filter keeping the
   `ryzhkov.dev` entry, start). On public-api that is a change to VM 106 and needs Igor's
   go. For public-api-b, seed `acme.json` with the wildcard entry only (same ACME account),
   which also keeps its first start well inside the rate limits.

What the root-owned file buys, honestly: the token leaves `~/apps/traefik/.env`, the
container's environment and `docker inspect`, and a process running as `igor` outside the
`docker` group can no longer read it. Any member of the `docker` group can still read it
through a container. The real protection is the zone scope, not the file mode.

## 4. Is two edge VMs with today's token an acceptable interim?

Yes, for a bounded period, with conditions. The blast radius does not change (one DMZ VM
holding a whole-zone DNS token already exists), the exposure doubles (two DMZ VMs), and
public-api-b is fresh and fully converged from the repository, so it is the less likely of
the two to be compromised. Concurrent issuance is safe: lego adds its own TXT value and
deletes it by record ID, and two VMs renewing the same wildcard about every 60 days stays far
below the 5-duplicate-certificates-per-week limit.

Conditions:

- The token reaches public-api-b only through sops and `compose_stack`, never by hand, and
  no third host gets it.
- public-api-b starts with a seeded `acme.json` (wildcard only) or with routers that do not
  request single-name certificates, so that rebuilds during testing cannot exhaust the weekly
  limit.
- The delegation and the scoped token are done before phase 4 is accepted, or carried as a
  named precondition of phase 5 with a date.

## Decisions for Igor

1. Which separate zone: a second domain on Cloudflare (A, recommended), deSEC (B), or
   acme-dns (C).
2. Drop the 45 single-name certificates in favour of the wildcard (recommended), or keep them
   and add one `_acme-challenge.<name>` CNAME per name.
3. Accept the current whole-zone token on both edge VMs as an interim, and until when.
4. Whether the new token gets a client IP filter (needs a stable WAN address) and an expiry.
