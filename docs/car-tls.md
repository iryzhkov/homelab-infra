# The car's certificate

The car serves HTTPS as `car.ryzhkov.dev` with a Let's Encrypt certificate that names that host
and nothing else. It is deliberately not the `*.ryzhkov.dev` wildcard every other router uses: a
key that leaves the house in a car must not be a key to every other hostname. Decided
2026-11-22.

## How it flows

1. **Issue.** `roles/traefik/templates/car.yml.j2` (in `traefik_rendered_files`) is a
   LAN-only router for `car.ryzhkov.dev` whose `tls` block asks for `domains: [{main:
   car.ryzhkov.dev}]` without SANs and does not include `router-tls.j2`, the one place
   `traefik_wildcard_only` is applied. Each edge's Traefik issues and renews its own copy over
   DNS-01, like the wildcard. Until a car exists, the route reaches the cartest simulator on
   gaming-pc over the mesh (`car_route_backend`).
2. **Export** on `car_tls_source` (public-api) only, so there is one source.
   `car-tls-export.service` (root, since Traefik writes `acme.json` as root, mode 0600) runs
   `/usr/local/sbin/car-tls-export`, which takes the entry for exactly `car.ryzhkov.dev` out of
   `~/apps/traefik/certs/acme.json`, checks that the certificate names nothing else, has not
   expired and matches its key, and writes `fullchain.pem`, `privkey.pem` and `bundle.pem` into
   `~/apps/traefik/export/car/` (owner igor, 0600, directory 0700), renamed into place and only
   when the certificate changed. Triggered by `car-tls-export.path` whenever `acme.json` changes
   (so a renewal is exported within seconds) and by `car-tls-export.timer` daily and at boot.
3. **Deliver** to `car_tls_consumer` (homelab). `car-tls-pull.service` runs as `pirene`
   with a key of its own, `/var/lib/car-tls-pull/id_ed25519`, against public-api's LAN address,
   with public-api's host key pinned in `/var/lib/car-tls-pull/known_hosts`. public-api accepts
   that key (`fleet_ssh_keys` entry `car-tls-pull`) only from homelab's LAN and mesh
   addresses, with `restrict` and a forced command that prints `bundle.pem`; it can do nothing
   else. `/usr/local/sbin/car-tls-pull` repeats the checks, splits the bundle into
   `/etc/pirene/car-tls/fullchain.pem` and `privkey.pem` (owner pirene, 0600, directory 0700),
   renamed into place and only when changed. `car-tls-pull.timer` runs daily at 03:30 and
   5 minutes after boot.

Let's Encrypt certificates last 90 days and Traefik renews 30 days before expiry, so a daily
pull leaves weeks of margin. Whatever serves the certificate from `/etc/pirene/car-tls` has to
read it again when it changes; nothing here restarts pirene.

## Applying

```
ansible-playbook playbooks/traefik.yml --check --diff     # the route, on both edges
ansible-playbook playbooks/car-tls.yml --check --diff     # export on public-api, pull on homelab
ansible-playbook playbooks/ssh.yml -l public-api --check --diff   # the pull key, once it exists
```

The pull key is generated on homelab by the first `car-tls.yml` run. A new key means a new
`car-tls-pull` entry in `group_vars/all.yml` and a run of `playbooks/ssh.yml -l public-api`.

## Checking

```
openssl s_client -connect 192.168.20.3:443 -servername car.ryzhkov.dev </dev/null |
  openssl x509 -noout -subject -ext subjectAltName -enddate
systemctl status car-tls-export.service      # on public-api
systemctl status car-tls-pull.service        # on homelab
```

## Rollback

1. On homelab: `systemctl disable --now car-tls-pull.timer`, remove the two units,
   `/usr/local/sbin/car-tls-pull`, `/var/lib/car-tls-pull` and `/etc/pirene/car-tls`.
2. Drop `car-tls-pull` from `fleet_ssh_authorized` in `host_vars/public-api.yml` and remove
   its line from `~/.ssh/authorized_keys` on public-api (`fleet_ssh` adds keys, it never
   removes them).
3. On public-api: `systemctl disable --now car-tls-export.path car-tls-export.timer`, remove
   the three units, `/usr/local/sbin/car-tls-export` and `~/apps/traefik/export/car`.
4. Drop `car.yml` from `traefik_rendered_files`, run `playbooks/traefik.yml`, and remove
   `~/apps/traefik/file_provider/car.yml` from both edges. The certificate stays in `acme.json`
   until it expires, unused.
