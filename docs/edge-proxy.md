# Edge proxy

Two edge VMs run the same Traefik with the same routes. keepalived floats one address in front
of them:

| Host | VM | Node | LAN | Mesh |
|---|---|---|---|---|
| public-api | 106 | proxmox-a | 192.168.20.33 (`ens18`) | 10.42.0.3 |
| public-api-b | 108 | proxmox-b | 192.168.20.4 (`eth0`) | 10.42.0.10 |
| VIP (`edge_vip`) | | | 192.168.20.3 | none |

AdGuard's `*.ryzhkov.dev` rewrite answers with the VIP (`edge_proxy_ip: "{{ edge_vip }}"`).
On the mesh, each edge's dnsmasq answers with its own mesh address (`roles/mesh_dns`).
keepalived runs VRRP unicast between the two LAN addresses with `nopreempt`, and accepts
advertisements only from the listed peer. Its check passes only while Traefik's API answers
and the websecure entrypoint serves the catch-all. public-api is preferred (priority 150).
Every router asks for the `ryzhkov.dev` + `*.ryzhkov.dev` wildcard (`traefik_wildcard_only`),
so each edge holds one certificate, issued over DNS-01 with the Cloudflare token in
`secrets/public-api-traefik.sops.yaml`.

How it was built and cut over: `docs/transition/phase4/`.

## Rebuild public-api-b from scratch

The same steps rebuild public-api, using its VMID, node, template and addresses. While one edge
is being rebuilt, the other holds the VIP: stop keepalived on the one being replaced first
(`sudo systemctl stop keepalived`).

1. On proxmox-b, from template 9001 (see `docs/templates.md`). After `qm destroy 108 --purge
   1` of the old VM, check that the VMID is free with `pvesh get /cluster/nextid --vmid 108`.

   ```
   qm clone 9001 108 --full 1 --name public-api-b --storage local-lvm
   qm set 108 --cores 2 --memory 2048 --balloon 0 \
     --net0 virtio,bridge=vmbr1,tag=20 \
     --ipconfig0 ip=192.168.20.4/24,gw=192.168.20.1 \
     --nameserver "192.168.20.154 192.168.20.106 192.168.20.83" \
     --ciuser igor --sshkeys <laptop ~/.ssh/id_ed25519.pub copied to the node> \
     --onboot 1 --cpuunits 1024 --tags edge
   qm resize 108 scsi0 16G
   qm start 108
   ```

   A new VM gets a new MAC address. UniFi holds no reservation for .4, which is outside the
   DHCP pool, so nothing there follows the MAC. Replace the laptop's `known_hosts` entry for
   192.168.20.4 after comparing `ssh-keyscan` from two other hosts.

2. Nebula certificate, on the laptop, the only machine with the CA key. The existing
   certificate in `~/.nebula/new-certs/public-api-b.crt` stays valid until 2031-09-01 and can
   be reused. Sign a new one only if the key is lost:

   ```
   cd ~/.nebula/new-certs
   nebula-cert sign -version 2 -ca-crt ../ca-new.crt -ca-key ../ca-new.key -name public-api-b \
     -networks 10.42.0.10/24 -groups server -out-crt public-api-b.crt -out-key public-api-b.key
   ```

3. Playbooks, in this order, each limited with `-l public-api-b`:

   ```
   ansible-playbook playbooks/base.yml          # agent, Docker, swap, no unattended-upgrades, LLMNR off
   ansible-playbook playbooks/ssh.yml           # homelab's fleet key
   ansible-playbook playbooks/nebula.yml        # binary, unit, certificates, config
   ansible-playbook playbooks/site.yml          # the traefik stack, static traefik.yml
   ansible-playbook playbooks/edge-bootstrap.yml   # the wildcard certificate first, alone
   ansible-playbook playbooks/traefik.yml       # every route
   ansible-playbook playbooks/mesh-dns.yml      # dnsmasq on 10.42.0.10
   ansible-playbook playbooks/keepalived.yml    # joins as BACKUP
   ```

   Do not run `traefik.yml` before `edge-bootstrap.yml`: with an empty acme.json and the
   wildcard not yet issued, it could request certificates in a burst, against Let's Encrypt's
   limits. Then run `site.yml -l homelab -e only=jocasta` if the mesh address changed.

4. Outside the repository. These name the edge addresses, not the VM, so a rebuild at the same
   addresses needs no change; they need one only if an address changes:
   - UniFi: the eight "public-api -> ..." policies in the Public API zone list .33 and .4 as
     sources (`docs/transition/phase4/proposals/unifi-edge.md`).
   - gaming-pc ufw: port 18800 is open to .33 and .4.
   - Home Assistant `http.trusted_proxies` lists .33 and .4.

5. Check: the comparison loop in `docs/transition/phase4/runbook-vip-cutover.md`
   (precondition 2) shows no difference between .33 and .4, and Gatus' "Edge public-api-b
   TCP" probe is up.

## Known limits

- VRRP spoofing from VLAN 20: a host there that forges the peer's source address, or answers
  ARP for the VIP, can divert edge traffic. Closing it needs the Proxmox CT firewall with
  `ipfilter` and `macfilter` on the AdGuard CTs, or a UDM rule, in a later window.
- `roles/nebula` installs the Nebula binary once (`creates:`); changing `nebula_version`
  does not upgrade an existing host. Upgrades are a hand step until the role handles them.
- Traefik's OOM priority is unchanged: `oom_score_adj` in the Compose service recreates the
  container, so it waits for a window, taken on one edge at a time while the other holds the
  VIP.
- The travel router lists only public-api for mesh DNS until Igor applies
  `docs/transition/phase4/2026-10-02-travel-router-dns.md`.
