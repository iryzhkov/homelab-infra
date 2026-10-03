# Phase 4 steps 1 and 2: the second edge VM and the keepalived role

Date: 2026-10-02, 19:50 to 20:30 PDT (the guest clock reads UTC, 2026-10-03 02:50 to 03:30).
Executor: E18. Hosts: proxmox-b (VM 108 created), public-api-b (new), public-api (read only),
proxmox-a (read only), homelab and the AdGuard CTs (check mode only).

Nothing on public-api, its containers, AdGuard, `edge_proxy_ip`, UniFi or Cloudflare was
changed. The VIP cutover and the failover test are in `runbook-vip-cutover.md` and wait for
Igor's go.

## Survey

| Check | Command | Decisive output | Answer |
|---|---|---|---|
| public-api OS | `ssh public-api 'grep PRETTY /etc/os-release'` | `Debian GNU/Linux 12 (bookworm)`, kernel 6.1.0-52 | Debian 12 |
| public-api Docker source | `cat /etc/apt/sources.list.d/docker.sources`; `dpkg -l` | `URIs: https://download.docker.com/linux/debian`, `Signed-By: /etc/apt/keyrings/docker.asc`; docker-ce 5:29.7.2, compose plugin 5.5.0 | Docker from docker.com, installed by hand; no role installed it |
| Traefik static config | `cat ~/apps/traefik/traefik.yml` | entrypoints `:80`, `:443`; API insecure on 8080; resolver `letencrypt`, `dnsChallenge` provider cloudflare | hand-written, not in the repository until now; DNS-01 confirmed |
| Traefik listeners | `sudo ss -ltnp` | `0.0.0.0:443`, `0.0.0.0:80`, `127.0.0.1:8080` (docker-proxy) | binds all addresses, so the VIP needs no `ip_nonlocal_bind` (it is 0) |
| Traefik image | `docker image inspect` | v3.7.13 on public-api | public-api-b pulled `traefik:latest`, also v3.7.13 |
| Docker daemon config | `cat /etc/docker/daemon.json` | Loki log driver to 192.168.70.223:3100, `default-address-pools` | hand-managed; deliberately not copied (see Docker below) |
| Gateway and DNS | `ip r`; `/etc/resolv.conf` | `default via 192.168.20.1`; nameservers .154, .106, .83 | used for the clone |
| Existing wildcard | dates read from acme.json on public-api, keys not printed | `ryzhkov.dev` + `*.ryzhkov.dev`, notBefore 2026-08-30; 46 certificates in the store | one more issuance of that set is well inside the duplicate limit of 5 per week |
| Unmanaged dynamic file | `ls ~/apps/traefik/file_provider` | `cartest.yml` (Oct 1 20:59), not in the repository | public-api serves 47 routers, public-api-b 46; the difference is that file. Not mine to adopt or remove |
| Proxmox firewall | `pve-firewall status` on both nodes; VM firewall options | `Status: disabled/running`; `/etc/pve/firewall/` empty; VM 106 and 108 options `{}` (ipfilter unset, default off) | nothing drops the VIP. VM 106's `net0` has `firewall=1`, which has no effect while the cluster firewall is off; VM 108's `net0` has no firewall flag |

### Uses of the first edge member

Huyang search for `edge_proxy` and `docker_hosts` over roles, stacks, playbooks, inventory,
filter plugins and scripts:

- `inventory/group_vars/all.yml:113`, `edge_proxy_ip = hostvars[groups['edge_proxy'][0]].ansible_host`.
  Left as it is: public-api stays first in the group, so it still renders 192.168.20.33. The
  cutover changes this line.
- `stacks/jocasta/templates/jocasta-firewall.service.j2`, the first member's `mesh_host`.
  Generalised to every member that has a `mesh_host`, joined with commas (iptables `-s` takes
  a list). public-api-b has none, so the rendered unit is unchanged.
- `roles/traefik/templates/generated.yml.j2` header counted `groups['docker_hosts']`, which
  would have changed from 4 to 5 and rewritten public-api's generated.yml. It now counts the
  hosts that contribute routes, still 4.
- `filter_plugins/stacks.py` `stack_host` returns the first host declaring a stack; public-api-b
  is listed after public-api in `docker_hosts`, so `traefik` still resolves to public-api.
- Gatus templates iterate `stack.routes`; public-api-b's traefik entry has no routes, so they
  render unchanged.
- `playbooks/traefik.yml` already ran on the whole `edge_proxy` group.

### Docker install choice

No role installed Docker. Chosen: a minimal, idempotent install in `roles/base` behind
`docker_install` (default false), laid out as on public-api (key in
`/etc/apt/keyrings/docker.asc`, deb822 `docker.sources`, suite from the host's release, so
`trixie` on the clone). It installs docker-ce, docker-ce-cli, containerd.io, buildx and the
Compose plugin, enables the daemon, and adds the SSH user to the `docker` group. It never
restarts a running daemon. qemu-guest-agent is a separate flag, `qemu_guest_agent`.

The Loki log driver in public-api's daemon.json is not reproduced: public-api-b cannot reach
Loki on homelab until the UniFi policies name it, and an unreachable Loki driver delays
container logging. public-api-b logs to json-file. Adding Loki is a follow-up once E19's
proposals are applied.

## Address plan

| Address | Use |
|---|---|
| 192.168.20.33 | public-api, VM 106 on proxmox-a, unchanged |
| 192.168.20.4/24 | public-api-b, VM 108 on proxmox-b, gateway 192.168.20.1 |
| 192.168.20.3 | the VRRP VIP |
| 192.168.20.2 | left free |

All four are outside the DHCP pool (.6 to .254). Before the clone, `ping` from public-api got
no answer from .3 or .4 (neighbour entries `INCOMPLETE`/`FAILED`). E19 proposes the UniFi
changes in `proposals/unifi-edge.md`.

## VM creation

The brief suggested VMID 107. `pvesh get /cluster/resources --type vm` shows 107 is the
`adguard` CT on proxmox-a, so 107 was not used. `pvesh get /cluster/nextid --vmid 108`
answered `108`, with no `108.conf` under any node. VM 108 it is. The brief's "ipfilter on VM
106 and 107" is read as 106 and 108.

Commands, on proxmox-b:

```
qm clone 9001 108 --full 1 --name public-api-b --storage local-lvm
qm set 108 --cores 2 --memory 2048 --balloon 0 \
  --net0 virtio=BC:24:11:19:E9:AE,bridge=vmbr1,tag=20 \
  --ipconfig0 ip=192.168.20.4/24,gw=192.168.20.1 \
  --nameserver "192.168.20.154 192.168.20.106 192.168.20.83" \
  --ciuser igor --sshkeys /tmp/public-api-b.pub --onboot 1 --cpuunits 1024
qm resize 108 scsi0 16G
qm set 108 --tags edge          # the clone inherited the template's "template" tag
qm start 108
```

The key file was the laptop's `~/.ssh/id_ed25519.pub` (public half only), copied to proxmox-b
and deleted after `qm set`. `--ciupgrade` was left at its default, because VLAN 20 has egress
(`curl https://download.docker.com/...` from the VM answered 200).

| Check | Command | Output | Answer |
|---|---|---|---|
| Boot and cloud-init | `ssh igor@192.168.20.4 'cloud-init status; grep PRETTY /etc/os-release'` | `status: done`; `Debian GNU/Linux 13 (trixie)`, kernel 6.12.111+deb13-cloud-amd64 | up |
| Host key | `ssh-keyscan -t ed25519 192.168.20.4` from the laptop, proxmox-b, homelab and public-api | all four `SHA256:bEh5PIRKbFfwTDpzXS7+ONKj2u3InZY6waJuBSPGC/c` | same key over four paths; added to the laptop's known_hosts |
| Interface | `ip -4 -br a` | `eth0 192.168.20.4/24` (public-api has `ens18`) | keepalived reads the interface from facts |
| proxmox-b memory | `free -m` before and after | available 7119 MB before, 4923 MB after | inside the 7.2 GB measured in phase 3 |

## Convergence through the repository

Inventory: `public-api-b` added to `docker_hosts` (after blackpearl) and to `edge_proxy`
(after public-api); variables in `inventory/host_vars/public-api-b.yml`.

| Step | Command | Result |
|---|---|---|
| base | `ansible-playbook playbooks/base.yml -l public-api-b` | changed=6, then changed=0 on a rerun; docker-ce 29.8.2, Compose v5.6.0; `qm guest cmd 108 ping` answers |
| fleet SSH | `ansible-playbook playbooks/ssh.yml -l public-api-b` | changed=1 (homelab's key, `from="192.168.70.223"`) |
| traefik stack | `ansible-playbook playbooks/site.yml -l public-api-b` | changed=7; `stacks/traefik/prepare.yml` creates `file_provider/` and `certs/` as igor; `traefik.yml` sha256 `e2bac4c9...5673` on both hosts |
| wildcard first | `ansible-playbook playbooks/edge-bootstrap.yml -l public-api-b` | catch-all installed alone; `issuer=C=US, O=Let's Encrypt, CN=YR1` after about 50 s |
| routing | `ansible-playbook playbooks/traefik.yml -l public-api-b` | changed=3; API overview: 46 routers, 48 services, 0 errors |

Why the bootstrap playbook: public-api's store holds 46 certificates, most of them per-host,
which is what Traefik does when routers load before a covering certificate exists. Giving the
new VM only the catch-all first meant one issuance. After the full configuration loaded,
acme.json on public-api-b still holds exactly one certificate, `ryzhkov.dev` +
`*.ryzhkov.dev` (notBefore 2026-10-03 02:15:57 UTC, notAfter 2027-01-01), and Traefik's log
has no errors.

### What works without backend access

| Check | Command (from the laptop) | Result |
|---|---|---|
| TLS and catch-all | `curl --resolve nosuch.ryzhkov.dev:443:192.168.20.4 https://nosuch.ryzhkov.dev/` | `302 -> https://igor.ryzhkov.dev/404`, `ssl_verify_result=0` |
| HTTP redirect | `curl --resolve vault.ryzhkov.dev:80:192.168.20.4 http://vault.ryzhkov.dev/` | `301 https://vault.ryzhkov.dev/` |
| Certificate | `openssl s_client -connect 192.168.20.4:443 -servername vault.ryzhkov.dev` | `CN=ryzhkov.dev`, SAN `*.ryzhkov.dev, ryzhkov.dev`, issuer Let's Encrypt YR1 |
| A backend | `curl --resolve vault.ryzhkov.dev:443:192.168.20.4 ...` | timeout after 15 s; through .33 the same request answers 200 in 0.04 s |
| Backend reachability | `bash -c '</dev/tcp/192.168.70.223/8099'` and `.234/7391` from each VM | open from public-api, blocked from public-api-b; AdGuard .154:53 open from both |

Recorded answer: public-api-b serves TLS with its own certificate and every static route, and
reaches no backend until Igor applies the UniFi changes in `proposals/unifi-edge.md`.

Known gaps for public-api-b at that point, beyond UniFi: it was not on Nebula, so the routes
that reach a backend over the mesh (Jocasta's backend on 10.42.0.2, the laptop's T3 failover
to its mesh address) could not work from it; and Home Assistant's `trusted_proxies` and
gaming-pc's ufw rule for 18800 named .33 only. Both are resolved in the next section.

## keepalived role

`roles/keepalived` and `playbooks/keepalived.yml`: VRRP unicast between the members' LAN
addresses (`unicast_src_ip`, `unicast_peer` from the group), VIP 192.168.20.3/24,
`virtual_router_id 20`, both members `state BACKUP` with `nopreempt`, priority 150 for the first
member and 100 for the second, and `track_script` running `/etc/keepalived/check-traefik`
(`curl -fsS --max-time 2 http://127.0.0.1:8080/api/version`) every 2 s, fall 2, rise 2.
No `ip_nonlocal_bind`, since Traefik binds 0.0.0.0. No VRRP authentication: PASS sends the
password in clear on the VLAN it protects; the plan's control is the AdGuard CTs' firewall
denying VRRP, which is not in place yet and is a later item.

`keepalived_enabled` defaults to false: the role installs the package, writes the files, and
keeps the service stopped and disabled.

| Check | Command | Result | Answer |
|---|---|---|---|
| public-api-b | `ansible-playbook playbooks/keepalived.yml -l public-api-b`, then a rerun | changed=1 then changed=0; keepalived 1:2.3.3-1, `disabled`, `inactive`; `keepalived --config-test --use-file=...` rc 0; `check-traefik` exits 0; no 192.168.20.3 on eth0 | installed, not started |
| public-api, check only | `ansible-playbook playbooks/keepalived.yml -l public-api --check --diff` | would install keepalived, create `/etc/keepalived`, add `check-traefik`, write a config with `interface ens18`, `priority 150`, `unicast_src_ip 192.168.20.33`, `unicast_peer { 192.168.20.4 }`; changed=4 | nothing applied; `curl` is present there |
| VIP unclaimed | `ping -c1 192.168.20.3` from public-api | no answer | VIP free |

### Incident during the role's first run, on public-api-b only

The first version validated the configuration with `keepalived --config-test=%s`. In
keepalived 2.3 the argument to `--config-test` is a log file, not the configuration, so the
test read the default `/etc/keepalived/keepalived.conf` and appended its errors to the file it
was checking. The first run passed and left one log line in the deployed file. A manual
`sudo keepalived --config-test=/etc/keepalived/keepalived.conf` then looped on its own output:
the file reached 1782590154 bytes, the guest ran out of memory (`containerd invoked
oom-killer`), and the OOM killer ended an SSH session and the Traefik container (`Exited
(137)`). keepalived itself never started a VRRP instance: `systemctl is-active keepalived`
was `inactive` throughout, and .3 never answered.

Recovery: the test process was killed through the guest agent (`qm guest exec 108 -- pkill -9
keepalived`); the validate line was changed to `keepalived --config-test --use-file=%s`,
tested on a copy of the sample config (file unchanged, rc 5 on a bad keyword); the role rewrote
the 695-byte configuration; `site.yml -l public-api-b` brought Traefik back (restart count 0,
acme.json intact with its one certificate, so no new issuance). Disk use after: 1.7 GB of 16 GB.

## Checks on existing hosts

| Playbook | Command | Result |
|---|---|---|
| site | `ansible-playbook playbooks/site.yml --check` | changed=0 on homelab, normandy, blackpearl, public-api (and public-api-b) |
| base | `ansible-playbook playbooks/base.yml --check` | changed=0 on every host, including proxmox-a and proxmox-b |
| traefik | `ansible-playbook playbooks/traefik.yml --check` | changed=0 on public-api and public-api-b |
| adguard | `ansible-playbook playbooks/adguard.yml --check --diff` | failed in check mode, not because of this change: see below |
| ssh | `ansible-playbook playbooks/ssh.yml --check` | changed=1 on homelab, pre-existing: see below |

adguard.yml: the rendered `/tmp/adguard-100.yaml` differs from CT 100's live file by one
line, `# The abused-TLDs list blocks all of .computer; pierre.computer is wanted.`, a template
comment from commit 4376fbd (2026-09-08) that AdGuard drops when it rewrites its file. The
rewrite answer is identical (192.168.20.33). Because the checksums differ, the role enters its
install block, whose DNS probe is a `command` that does not run in check mode, so check mode
always fails there and reports a rollback that it skipped. Nothing on the CTs changed. This
predates phase 4 and is noted for whoever owns `roles/adguard`.

ssh.yml on homelab: `~/.ssh/config` names public-api as `HostName 192.168.20.33`, the
repository renders `10.42.0.3` (its `mesh_host`). Pre-existing drift, unrelated to this change,
left alone.

## Scope added by Igor's decisions (2026-10-02, 20:35 to 21:00 PDT)

### Wildcard certificate only

`roles/traefik/templates/router-tls.j2` is the one `tls:` block every router includes
(generated.yml, admin.yml, whisper.yml, t3.yml, cartest.yml). With `traefik_wildcard_only`
it adds `domains: ryzhkov.dev + *.ryzhkov.dev`, so a router asks for the wildcard instead of
a certificate for its own name; without it the block renders exactly as before. t3.yml moved
from `stacks/traefik/dynamic/` to `roles/traefik/templates/t3.yml.j2` (unchanged text) so it
can include the block; catchall.yml already names the wildcard and stays a static copy.

| Check | Command | Result |
|---|---|---|
| public-api unchanged | `ansible-playbook playbooks/traefik.yml --check --diff -l public-api` | changed=0, every file `ok`, including t3.yml and cartest.yml |
| public-api-b switched | `ansible-playbook playbooks/traefik.yml -l public-api-b` | changed=2; 47 routers, 0 errors |
| Issuances on public-api-b | acme.json read on the VM | still exactly 1 certificate (`ryzhkov.dev` + `*.ryzhkov.dev`); it never requested a single-name one, because the bootstrap gave it the wildcard first |

public-api keeps `traefik_wildcard_only` false until the cutover runbook's section 3, which
also prunes its 45 single-name certificates. The whole-zone Cloudflare token on both edges is
accepted by Igor for now.

### Hand-only files brought into the repository

- `cartest.yml`: `roles/traefik/templates/cartest.yml.j2`, in `traefik_rendered_files`, the
  gaming-pc address from the inventory. Renders byte-identical on public-api (sha256
  `7b41000b...0cf0` on the host; the task reports `ok`).
- `traefik.yml`: `stacks/traefik/templates/traefik.yml.j2`, a `config_templates` entry on both
  hosts' traefik stack. On public-api the template task reports `ok` and nothing restarts;
  `site.yml --check -l public-api` still reports changed=1, for the drift manifest, whose
  `config_files` for traefik gains `traefik.yml`. That metadata change is left for the
  runbook's section 3, which converges public-api anyway.
- `nebula-dns.conf`: below.

### Mesh: public-api-b on Nebula as 10.42.0.10

The repository held only Nebula's configuration; certificates and the binary were set up by
hand. The CA is on the laptop (`~/.nebula/ca.key`, `ca-new.key`, `ca-combined.crt`), so it was
not a blocker. public-api's certificate is version 2, group `server`, signed by `ca-new`
(issuer `ae86c940...`), and its `/etc/nebula/ca.crt` is byte-identical to `ca-combined.crt`.
The same way, on the laptop:

```
cd ~/.nebula/new-certs
nebula-cert sign -version 2 -ca-crt ../ca-new.crt -ca-key ../ca-new.key -name public-api-b \
  -networks 10.42.0.10/24 -groups server -out-crt public-api-b.crt -out-key public-api-b.key
```

Result: fingerprint `9afc1a1b...431a`, notAfter 2031-09-01 (the CA's). 10.42.0.10 did not
answer ping from public-api before.

`roles/nebula` gained `nebula_install` (default false): the 1.11.1 release binary (the version
on public-api, sha256-checked), the systemd unit as on public-api, and the CA, certificate and
key from the laptop's `~/.nebula`. The first-install path no longer tries to back up a
configuration that does not exist. `roles/nebula/templates/public-api-b.yml.j2` is public-api's
configuration with its own certificate.

| Check | Command | Result |
|---|---|---|
| Join | `ansible-playbook playbooks/nebula.yml -l public-api-b`, then a rerun | changed=10, then changed=0; `nebula1 10.42.0.10/24` |
| Mesh | ping from public-api-b | 10.42.0.1 (lighthouse) 53 ms, 10.42.0.2 (homelab) 0.5 ms, 10.42.0.3 24 ms |
| Others | `ansible-playbook playbooks/nebula.yml --check` | changed=0 on public-api, proxmox-a, proxmox-b |

With a `mesh_host`, public-api-b now enters the Jocasta firewall's source list:
`site.yml --check -l homelab` shows the unit's argument going from `10.42.0.3` to
`10.42.0.3,10.42.0.10`. Not applied (homelab is an existing host); it is section 0 of the
runbook.

### Mesh DNS

`roles/mesh_dns` and `playbooks/mesh-dns.yml`, for every `edge_proxy` member: dnsmasq and
bind9-dnsutils, and `/etc/dnsmasq.d/nebula-dns.conf` rendered with the host's own
`mesh_host` and the AdGuard instances as upstreams, then a probe that the host answers its
own mesh address for `vault.ryzhkov.dev`.

| Check | Command | Result |
|---|---|---|
| public-api byte-identical | `ansible-playbook playbooks/mesh-dns.yml --check --diff -l public-api` | changed=0 (host file sha256 `0c680cf2...71c0`, 503 bytes) |
| public-api-b | `ansible-playbook playbooks/mesh-dns.yml -l public-api-b`, then a rerun | changed=4, then 0; listens on 10.42.0.10:53 |
| From the laptop over the mesh | a DNS query to 10.42.0.10 | `vault.ryzhkov.dev` gives 10.42.0.10; `debian.org` resolves through AdGuard |
| Traefik over the mesh | `curl --resolve nosuch.ryzhkov.dev:443:10.42.0.10 ...` | 302, certificate verifies |

Travel router (not changed, reported to the lead): its `/etc/dnsmasq.d/ryzhkov.conf` must
drop `address=/ryzhkov.dev/10.42.0.3` and list both edges; the exact lines are in the
runbook's section 4.

### Backend access after E19's UniFi change

From public-api-b at 20:55, homelab :8099, normandy :7391, Home Assistant :8123, gaming-pc
:18800, proxmox-a :8006 and Black Pearl :8080 accept connections. All 42 host names from
public-api's routers compared through .33 and .4: 41 identical status codes; `jocasta` gives
401 through .33 and 503 through .4 until the Jocasta firewall change (runbook section 0).

### Final checks

| Playbook | changed |
|---|---|
| site.yml --check | 0 except homelab 1 (Jocasta unit, intended, pending) and public-api 1 (drift manifest metadata, pending) |
| base.yml, traefik.yml, nebula.yml, mesh-dns.yml --check | 0 on every host |
| keepalived.yml --check | public-api 4 (install, pending the cutover), public-api-b 0 |
| ssh.yml --check | homelab 1, the pre-existing `HostName` drift |

## Implications for later phases

- The cutover and the failover test are `runbook-vip-cutover.md`, dispatched by the lead
  once its preconditions hold.
- Plan step 4 (scoped `_acme-challenge` token read from a root-owned file) is not done: both
  edges use the whole-zone token through `.env`, accepted for now. E19's
  `proposals/cloudflare-acme.md` covers it.
- The AdGuard CTs' firewall rules against VRRP, from plan step 2, are not in place.
- Phase 5's fallback manager on public-api-b inherits the review's concerns (DMZ root over the
  cluster, shares proxmox-b with agent-b).
