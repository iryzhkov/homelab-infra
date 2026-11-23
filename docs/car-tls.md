# The car's certificate

The car serves HTTPS as `car.ryzhkov.dev` with a Let's Encrypt certificate that names that host
and nothing else. It is deliberately not the `*.ryzhkov.dev` wildcard every edge router uses: a
key that leaves the house in a car must not be a key to every other hostname. Decided
2026-11-22.

## Why not Traefik

The plan was for the edge Traefik to issue it, from a router that asks for `domains: [{main:
car.ryzhkov.dev}]` without including `router-tls.j2`. Traefik does not do that: before it
requests anything it drops every name that a certificate already in its store matches, wildcards
included (`getUncheckedDomains` and `isDomainAlreadyChecked` in
`pkg/provider/acme/provider.go`, v3.7.19), and the wildcard matches `car.ryzhkov.dev`. A second
resolver does not help, because the check reads the shared TLS store. Tried on 2026-11-22: the
router loaded and nothing was ever requested.

So the certificate is issued by lego, the ACME library inside Traefik, run on its own on the same
edge with the same Cloudflare token and DNS-01. The edge's `car.ryzhkov.dev` route
(`roles/traefik/templates/car.yml.j2`) still serves the wildcard, like every other route; the
dedicated certificate is for the car itself.

## How it flows

1. **Issue** on `car_tls_source` (public-api) only, so there is one source and one ACME account.
   `car-tls-issue.service` (root) runs `car_tls_lego_image` with `run`, which gets the
   certificate when there is none and renews it once a third of its lifetime is left, and
   otherwise does nothing. Its state (the ACME account and the certificate's EC P-256 key) is in
   `/var/lib/car-tls-issue`, root, 0700. The Cloudflare token comes from Traefik's
   `~/apps/traefik/.env`, passed with `--env-file`; it is never written anywhere else.
2. **Export**, the same service's second step. `/usr/local/sbin/car-tls-export` checks that the
   certificate names exactly `car.ryzhkov.dev`, has not expired and matches its key, and writes
   `fullchain.pem`, `privkey.pem` and `bundle.pem` into `~/apps/traefik/export/car/` (owner
   igor, 0600, directory 0700), renamed into place and only when the certificate changed.
   `car-tls-issue.timer` runs the service daily at 02:30 (up to 30 minutes later) and 5 minutes
   after boot.
3. **Deliver** to `car_tls_consumer` (homelab). `car-tls-pull.service` runs as `pirene` with a
   key of its own, `/var/lib/car-tls-pull/id_ed25519`, against public-api's LAN address, with
   public-api's host key pinned in `/var/lib/car-tls-pull/known_hosts`. public-api accepts that
   key (`fleet_ssh_keys` entry `car-tls-pull`) only from homelab's LAN and mesh addresses, with
   `restrict` and a forced command that prints `bundle.pem`; it can do nothing else.
   `/usr/local/sbin/car-tls-pull` repeats the checks, splits the bundle into
   `/etc/pirene/car-tls/fullchain.pem` and `privkey.pem` (owner pirene, 0600, directory 0700),
   renamed into place and only when changed. `car-tls-pull.timer` runs daily at 03:30 (up to 30
   minutes later) and 5 minutes after boot.

Let's Encrypt certificates last 90 days and lego renews with 30 left, so a renewal reaches
homelab within a day and with weeks of margin. Whatever serves the certificate from
`/etc/pirene/car-tls` has to read it again when it changes; nothing here restarts pirene.

If public-api is down for long, nothing renews: the certificate is issued in one place on
purpose. Rebuilding public-api loses `/var/lib/car-tls-issue`, and the next run simply issues a
new certificate under a new account.

## Applying

```
ansible-playbook playbooks/traefik.yml --check --diff     # the route, on both edges
ansible-playbook playbooks/car-tls.yml --check --diff     # issue on public-api, pull on homelab
ansible-playbook playbooks/ssh.yml -l public-api --check --diff   # the pull key, once it exists
```

The pull key is generated on homelab by the first `car-tls.yml` run. A new key means a new
`car-tls-pull` entry in `group_vars/all.yml` and a run of `playbooks/ssh.yml -l public-api`.

## Checking

```
sudo systemctl start car-tls-issue.service; journalctl -u car-tls-issue -n 20   # public-api
sudo systemctl start car-tls-pull.service;  journalctl -u car-tls-pull -n 5     # homelab
sudo openssl x509 -in /etc/pirene/car-tls/fullchain.pem -noout -subject -ext subjectAltName -enddate
```

## Rollback

1. On homelab: `systemctl disable --now car-tls-pull.timer`, remove the two units,
   `/usr/local/sbin/car-tls-pull`, `/var/lib/car-tls-pull` and `/etc/pirene/car-tls`.
2. Drop `car-tls-pull` from `fleet_ssh_authorized` in `host_vars/public-api.yml` and remove
   its line from `~/.ssh/authorized_keys` on public-api (`fleet_ssh` adds keys, it never
   removes them).
3. On public-api: `systemctl disable --now car-tls-issue.timer`, remove the two units,
   `/usr/local/sbin/car-tls-export`, `/var/lib/car-tls-issue` and `~/apps/traefik/export/car`.
4. To drop the route as well, remove `car.yml` from `traefik_rendered_files`, run
   `playbooks/traefik.yml`, and remove `~/apps/traefik/file_provider/car.yml` from both edges.
