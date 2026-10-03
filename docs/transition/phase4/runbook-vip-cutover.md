# Runbook: edge VIP cutover and failover test (phase 4 steps 3 to 5)

Prepared 2026-10-02 by E18; the starting state is recorded in `2026-10-02-public-api-b.md`.
Igor waived his explicit go on 2026-10-02: the lead dispatches this runbook once every
precondition holds, and it runs without a further question. Any check that fails is a stop,
followed by the rollback for the section reached.

Addresses: public-api 192.168.20.33 / mesh 10.42.0.3 (VM 106, proxmox-a, `ens18`);
public-api-b 192.168.20.4 / mesh 10.42.0.10 (VM 108, proxmox-b, `eth0`); VIP 192.168.20.3.
Commands run from the laptop in `~/Work/homelab-infra` unless they name a host.

## Preconditions

1. E19's UniFi change (every policy that named 192.168.20.33 as source also names
   192.168.20.4) and the Home Assistant `trusted_proxies` change are applied. Checked
   2026-10-02 20:55: from public-api-b, homelab :8099, normandy :7391, HA :8123, gaming-pc
   :18800, proxmox-a :8006 and Black Pearl :8080 all accept connections.
2. Every name answers through .4 with the same status as through .33. Set `$names` once; the
   later sections reuse it:

   ```
   names=$(ssh public-api "curl -s 'http://127.0.0.1:8080/api/http/routers?per_page=500'" \
     | python3 -c 'import json,re,sys; print("\n".join(sorted({h for r in json.load(sys.stdin) for h in re.findall(r"Host\(`([^`]+)`\)", r["rule"])})))')
   for n in $names; do
     a=$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 --resolve $n:443:192.168.20.33 https://$n/)
     b=$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 --resolve $n:443:192.168.20.4 https://$n/)
     [ "$a" = "$b" ] || echo "DIFF $n .33=$a .4=$b"
   done
   ```

   On 2026-10-02 41 of 42 names matched; the one difference, `jocasta` (.33 401, .4 503), is
   fixed by section 0. After section 0 there must be no DIFF line.
3. `ansible-playbook playbooks/{site,base,traefik,nebula,mesh-dns}.yml --check` report
   changed=0 everywhere except: homelab (the Jocasta unit, section 0) and public-api (the
   drift manifest's `config_files`, section 3) in site.yml; public-api in keepalived.yml
   (section 1). `adguard.yml --check` fails at its DNS probe whenever the rendered file
   differs from the live one (a known check-mode limit of that role); read its diff instead.
4. Nothing answers on the VIP: `ssh public-api 'ping -c2 -W1 192.168.20.3'` gets no reply.

## 0. Let public-api-b reach Jocasta's backend

The unit's source list now carries both edges' mesh addresses (`10.42.0.3,10.42.0.10`);
the change only widens it.

```
ansible-playbook playbooks/site.yml -l homelab -e only=jocasta --check --diff
ansible-playbook playbooks/site.yml -l homelab -e only=jocasta
ssh homelab 'sudo iptables -S JOCASTA'
```

Expect `-A JOCASTA -s 10.42.0.3/32 -j RETURN`, the same for 10.42.0.10, then `-j DROP`. Rerun
precondition 2: no DIFF. Rollback: revert nothing in the repository; `ssh homelab 'sudo
systemctl restart jocasta-firewall'` after reverting the template would restore the single
source, but the wider list is harmless on its own.

## 1. keepalived on both, public-api first

1. In `roles/keepalived/defaults/main.yml` set `keepalived_enabled: true`, and delete the
   `keepalived_enabled: false` line from `inventory/host_vars/public-api-b.yml`.
2. In `inventory/group_vars/all.yml`, next to `edge_proxy_ip`, add
   `edge_vip: 192.168.20.3` (the keepalived role reads it). Leave `edge_proxy_ip` alone.
3. Start public-api alone:

   ```
   ansible-playbook playbooks/keepalived.yml -l public-api --check --diff
   ansible-playbook playbooks/keepalived.yml -l public-api
   ssh public-api 'ip -4 -br a show ens18; journalctl -u keepalived -n 5 --no-pager'
   ```

   Expect `192.168.20.3/24` on ens18 within about 4 s and `Entering MASTER STATE`;
   `curl --resolve vault.ryzhkov.dev:443:192.168.20.3 https://vault.ryzhkov.dev/` answers 200.
4. Start public-api-b:

   ```
   ansible-playbook playbooks/keepalived.yml -l public-api-b
   ssh igor@192.168.20.4 'ip -4 -br a show eth0; journalctl -u keepalived -n 5 --no-pager'
   ```

   Expect no .3 on eth0 and `Entering BACKUP STATE`; public-api's journal shows no new state
   line. If both hold .3, VRRP is not passing: stop keepalived on public-api-b and look with
   `sudo tcpdump -ni eth0 vrrp` (protocol 112 between .33 and .4, same VLAN, no UniFi rule
   in the path).

Rollback: section R, step 3.

## 2. Point the names at the VIP

1. In `inventory/group_vars/all.yml` change `edge_proxy_ip` to `"{{ edge_vip }}"`.
2. `ansible-playbook playbooks/adguard.yml --check --diff`: the rewrite answer moves from
   192.168.20.33 to 192.168.20.3, plus the known comment line. Then
   `ansible-playbook playbooks/adguard.yml`.
3. After adguardhome-sync has run:
   `for s in 192.168.20.154 192.168.20.106 192.168.20.83; do dig +short @$s vault.ryzhkov.dev; done`
   gives 192.168.20.3 three times.
4. Gatus' "Edge proxy TCP" check:
   `ansible-playbook playbooks/site.yml -l homelab,normandy -e only=gatus` (check first).
5. Every name through the VIP (the laptop resolves through AdGuard now):

   ```
   for n in $names; do printf '%s %s\n' "$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 https://$n/)" $n; done
   ```

   Same codes as precondition 2. From homelab and normandy (the cloudflared hosts)
   `getent hosts vault.ryzhkov.dev` gives 192.168.20.3, and a public name answers from
   outside the house.
6. Outside the repository, CT 110's healthcheck target `traefik=https://192.168.20.33:443`
   moves to the VIP (a hand edit on proxmox-a, `/etc/init.d/healthcheck` in CT 110), or CT 110
   is retired in favour of Gatus.

Rollback: section R.

## 3. public-api to wildcard-only certificates

public-api's acme.json holds the wildcard and 45 single-name certificates, which Traefik keeps
renewing. public-api-b already runs wildcard-only (one certificate). The switch on public-api
needs a short Traefik stop, so it is done while public-api-b holds the VIP.

1. In `roles/traefik/defaults/main.yml` set `traefik_wildcard_only: true`, and delete the
   `traefik_wildcard_only: true` line from `inventory/host_vars/public-api-b.yml`.
2. `ansible-playbook playbooks/traefik.yml --check --diff`: public-api-b changed=0; public-api
   gains a `domains:` block under every router's `tls:` and nothing else. Apply it:
   `ansible-playbook playbooks/traefik.yml -l public-api`. Every router's domains are now
   the wildcard, which is already in the store, so nothing is issued.
3. Move the VIP to public-api-b: `ssh public-api 'sudo systemctl stop keepalived'`. Confirm .3
   on public-api-b's eth0 and the names answering (section 2.5).
4. Prune the store on public-api, keeping a copy:

   ```
   ssh public-api 'cd ~/apps/traefik && docker compose stop traefik \
     && sudo cp -p certs/acme.json certs/acme.json.bak.wildcard-only-$(date +%Y%m%d-%H%M%S) \
     && sudo python3 -c "import json; p=\"certs/acme.json\"; d=json.load(open(p)); r=d[\"letencrypt\"]; r[\"Certificates\"]=[c for c in r[\"Certificates\"] if c[\"domain\"].get(\"main\")==\"ryzhkov.dev\"]; json.dump(d,open(p,\"w\"),indent=2)" \
     && sudo python3 -c "import json; print(len(json.load(open(\"certs/acme.json\"))[\"letencrypt\"][\"Certificates\"]))"'
   ansible-playbook playbooks/site.yml -l public-api
   ```

   Expect `1`. site.yml starts Traefik again and records `traefik.yml` in the drift manifest
   (the adopted static configuration, byte-identical, so Traefik is not restarted for it).
   Then `echo | openssl s_client -connect 192.168.20.33:443 -servername vault.ryzhkov.dev
   2>/dev/null | openssl x509 -noout -subject` shows `CN=ryzhkov.dev`, and precondition 2's
   loop shows no DIFF.
5. Return the VIP: `ssh public-api 'sudo systemctl start keepalived'` (it comes up BACKUP),
   then `ssh igor@192.168.20.4 'sudo systemctl restart keepalived'`; public-api takes .3
   within about 4 s. Confirm with `ip -4 -br a` on both.

Rollback: restore the `acme.json.bak.wildcard-only-*` copy with Traefik stopped, and set
`traefik_wildcard_only` back to false for public-api.

## 4. Mesh DNS on both edges

In place on both since 2026-10-02 (`playbooks/mesh-dns.yml`): each edge's dnsmasq answers
`*.ryzhkov.dev` with its own mesh address (10.42.0.3 and 10.42.0.10). The only configured mesh
client is the travel router, which is Igor's to change by hand when it is reachable on its LAN
address (192.168.90.158); agents do not touch it. In its `/etc/dnsmasq.d/ryzhkov.conf`,
replace `address=/ryzhkov.dev/10.42.0.3` and `server=10.42.0.3` with:

```
no-resolv
strict-order
server=1.1.1.1
server=10.42.0.10
server=10.42.0.3
server=/ryzhkov.dev/10.42.0.3
server=/ryzhkov.dev/10.42.0.10
```

Until then, mesh clients behind the travel router lose the home names while public-api is
down; that is a known gap of the failover test below, not a stop.

## 5. Failover test, which is also public-api's phase 3 stop and start

The single stop and start of VM 106 that phase 3 deferred. Its memory goes from 4 GB to 2 GB
as a pending change applied by the restart (public-api used 603 MB on 2026-10-02).

1. Watch from the laptop, one request a second, in the background with a log:

   ```
   while :; do printf '%s %s\n' "$(date +%T)" \
     "$(curl -s -o /dev/null -w '%{http_code}' --max-time 2 https://vault.ryzhkov.dev/)"; sleep 1; done > /tmp/vip-watch.log
   ```

   On public-api-b: `journalctl -fu keepalived`.
2. Stage the memory change and shut public-api down cleanly:

   ```
   ssh proxmox-a 'qm set 106 --memory 2048 && qm pending 106 | grep memory'
   ssh proxmox-a 'qm shutdown 106 --timeout 120'
   ```

   keepalived stops during the shutdown and sends priority 0, so public-api-b takes .3 at once
   (at worst about 4 s). Expect `Entering MASTER STATE` on public-api-b, a few non-200 seconds at
   most in the log, and .3 on its eth0. Run section 2.5 again: every name answers through
   public-api-b. Over the mesh, `dig @10.42.0.10 vault.ryzhkov.dev` answers 10.42.0.10.
3. `ssh proxmox-a 'qm start 106 && qm config 106 | grep ^memory'` shows `memory: 2048`.
4. No flap: public-api's keepalived comes up BACKUP and, with `nopreempt`, leaves .3 on
   public-api-b. For two minutes the log stays at 200, public-api-b's journal shows no new
   state line, and public-api's shows `Entering BACKUP STATE` only.
5. Return the VIP to public-api, the preferred holder:
   `ssh igor@192.168.20.4 'sudo systemctl restart keepalived'`, then confirm with `ip -4 -br a`.
6. Record the times, the count of non-200 lines in `/tmp/vip-watch.log`, and each journal's
   state lines in `docs/transition/phase4/<date>-vip-cutover.md`.

If VM 106 does not come back: the VIP stays on public-api-b and the names keep answering;
diagnose from proxmox-a (`qm status 106`, `qm terminal 106`), and `qm set 106 --memory 4096`
if the guest fails for lack of memory.

## R. Rollback

At any point after section 2, in this order:

1. `edge_proxy_ip` back to `"{{ hostvars[groups['edge_proxy'][0]].ansible_host }}"` in
   `inventory/group_vars/all.yml`.
2. `ansible-playbook playbooks/adguard.yml`; the three AdGuard instances answer
   192.168.20.33 again (the `dig` loop of 2.3); converge Gatus as in 2.4.
3. `keepalived_enabled: false` in `roles/keepalived/defaults/main.yml`, then
   `ansible-playbook playbooks/keepalived.yml`: the service stops on both and the VIP goes;
   `ping 192.168.20.3` gets no answer.

Before section 2, only step 3 is needed.

## Interim decisions on record

- Both edges use the same whole-zone Cloudflare DNS token (`secrets/public-api-traefik.sops.yaml`
  through `.env`); Igor accepted it for now on 2026-10-02. The scoped `_acme-challenge`
  delegation in `proposals/cloudflare-acme.md` remains open.
- No VRRP authentication; the AdGuard CTs' firewall rule against VRRP (plan step 2) is not in
  place yet.
