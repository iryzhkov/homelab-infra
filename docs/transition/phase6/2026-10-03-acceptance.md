# Phase 6 acceptance record

Date: 2026-10-03. Branch `transition/phase-6`, accepted at `db62cc0`.

## Verdict

Accepted as infrastructure. Four Sonnet 5.5 reviewers (VMs and Proxmox, Ansible and UpKeeper, isolation, availability) returned ACCEPT; their should-fix items were applied (agent CPU weight 20, agent-b memory 3 GB, the two unused Nebula policies deleted). The Fable 5.1 reviewer re-checked the VMs, the isolation from agent-a, the UniFi Agents zone and the converge, and accepted.

- VLAN 130 "Agents" (192.168.130.0/24) with its own zone, created through the UniFi Network API on Igor's decision. Agents reach only the VIP on 443, AdGuard on 53, Forgejo SSH on 192.168.70.223:2222, the gateway's DHCP, NTP and ICMP, and the internet. Everything household, the hypervisors, the NAS, cameras and IoT is closed. The switch's two hypervisor uplinks were re-provisioned to carry the VLAN with no household outage.
- agent-a (VM 111, proxmox-a: 10 vCPU, 20 GB, 400 GB `tank` data disk) and agent-b (VM 211, proxmox-b: 4 vCPU, 3 GB, 100 GB `tank` data disk), both `cpulimit` set and CPU weight 20, converged from the repository: Docker on the data disk, swap, guest agent, no automatic upgrades, the laptop key and a steward key accepted only from normandy.
- OpenViking clients on the laptop, homelab, normandy and gaming-pc reach agent memory through `ov.ryzhkov.dev` (both Swarm workers behind the VIP), through UpKeeper v0.1.23 and release `9ae93944f9b9beefb9cbd7a7a32aa57adf9d28a9d9a1298bd3760248c6f6c055` (Igor approved).

## Deviations from the plan, by decision

- The agents are not on Nebula: the household nodes' hand-written mesh firewalls accept any peer, so a mesh join would bypass the VLAN's isolation. Signed certificates are held, with fingerprints recorded.
- No NAS export is opened to agents; no steward project needs one.
- Household T3 servers, the steward and its workers stay on homelab and normandy (Igor: nothing moves yet).

## Incomplete

The agent VMs do no work yet. Registering them as steward workers needs provider logins that only Igor can make; the commands are in `2026-10-03-agent-vms.md`. The plan's dockerised runs depend on a steward feature that does not exist yet.

## Carried

- Agents reach each other on port 22 within VLAN 130; optional Proxmox VM firewall.
- The laptop key placed by cloud-init has no `from=` restriction.
- `ssh.yml` must be run with `-l` from this branch until phase 5 is merged.
- The `OV_AGENT` label is the same on every host (pre-existing).
- Stale `~/.config/ov-mcp/config.toml` files, overridden by `OV_BASE_URL`.
- A drain of proxmox-b stops agent-b first.
