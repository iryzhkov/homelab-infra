# Phase 4 acceptance record

Date: 2026-10-02. Preparation `2227ad2`, cutover record `abbc0f6`, review fixes `7ab081d`, all on `transition/phase-4`.

## Verdict

Accepted. Plan phase 4 steps 1 to 5 are met:

1. public-api-b is VM 108 on proxmox-b (192.168.20.4, 2 vCPU, 2 GB, Debian 13 from template 9001), converged from the repository; the edge roles serve N members with byte-identical output for public-api. The rebuild procedure is in `docs/edge-proxy.md`.
2. keepalived runs VRRP unicast between .33 and .4 with VIP 192.168.20.3, `nopreempt`, `check_unicast_src`, and a health check that requires both Traefik's API and a TLS answer on :443.
3. `edge_proxy_ip` is the VIP; the AdGuard origin and both replicas answer .3. UniFi's eight backend policies use the `edge-proxies` group (.33 and .4); Home Assistant trusts .33, .4 and .3 (192.168.1.223 removed); gaming-pc allows 18800 from .4; Jocasta admits both edges' mesh addresses. Mesh DNS answers on both edges (10.42.0.3 and 10.42.0.10).
4. Both edges serve only the wildcard certificate, issued by DNS-01.
5. Failover: with public-api shut down, public-api-b took the VIP in 1.34 s and 638 of 638 requests succeeded; no flap on return; hand-back 0.55 s. That shutdown was also public-api's phase 3 stop and start (memory 4 to 2 GB).

Review: Sonnet 5.5 Proxmox FIX REQUIRED (an OOM on public-api-b with no swap; inconsistent upgrade policy), security, availability and Ansible ACCEPT with should-fixes. All fixed in `7ab081d` (1 GiB swap on both edges, unattended upgrades disabled on both, data-path health check, per-edge Gatus probes, LLMNR off, directory modes, AdGuard check-mode probe, reference copies, priority floor, rebuild doc). The Fable 5.1 reviewer re-checked the edges and accepted. All playbooks in check mode report changed=0 except the older SSH `HostName` drift on homelab.

## Decisions recorded

Igor, 2026-10-02: an executor applies UniFi and Home Assistant changes; the whole-zone Cloudflare token stays on both edges for now with wildcard-only certificates; public-api-b joins the mesh with its own dnsmasq; no explicit go for the cutover.

## Carried

- Traefik `oom_score_adj`: applying it recreates the container; do it one edge at a time while the other holds the VIP.
- VRRP spoofing from VLAN 20 (no VRRP authentication in keepalived 2.3.3): close it with the Proxmox CT firewall (`ipfilter`, `macfilter`) on the three AdGuard CTs or a UDM rule, in a window; add a keepalived `notify` that posts VIP holder changes to the feed.
- A real `adguard.yml` run always pushes the config and restarts AdGuard, because AdGuard strips a template comment; role fix needed.
- The Nebula role never upgrades an installed binary.
- Purge the residual unattended-upgrades package files on public-api-b; detach `deb_12.iso` from VM 106.
- CT 110's healthcheck still targets .33.
- The travel router needs both mesh resolvers; the lines are in `2026-10-02-travel-router-dns.md` for Igor.
- Tonight's 02:30 vzdump is the first to cover VM 108 and the first to run with the `backup=0` data zvols of 101 and 202.
