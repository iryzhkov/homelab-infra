# Phase 0 acceptance record

Date: 2026-10-02. Implementation commit: `4fdf028` on `transition/phase-0`. Plan: `homelab/plans/serviceable-transition-2026-10-02.md` revision 4 in Jocasta.

## Verdict

Accepted. Four independent reviewers (Sonnet 5.5: storage and Proxmox, Ansible correctness and idempotence, service availability, security and network) each re-ran read-only checks on the hosts and returned ACCEPT with no blocking findings. A final reviewer (Fable 5.1) read their reports and the evidence, spot-checked the UniFi firewall policies, the pending changes on VMs 101 and 106, the NAS archives, the SMART and GPU services and the normandy reboot, and accepted every item of the plan's phase 0 list.

`ansible-playbook playbooks/site.yml --check --diff` and `playbooks/base.yml --check --diff` report changed=0 on every host at `4fdf028`, re-run by the Ansible reviewer.

## Errata to the evidence files

- `2026-10-02-pve-config-copy.md` says the encrypted archives are mode 0600. On the NAS they are `0660`, owner `977:988`, because the NFS export squashes ownership. The contents are age-encrypted, so the mode does not expose them.
- `2026-10-02-address-inventory.md` omits one address added during phase 0: `/etc/pve/status.cfg` on the cluster names `server 192.168.70.223` (port 8087) for the Proxmox metric server. It is rendered by the base role from the inventory, but it is a literal on the hypervisors, in the same class as the Loki push targets, and must change by phase 5. Home Assistant's `trusted_proxies` also lists `192.168.1.223/32`, which nothing explains; verify it in phase 4.
- `2026-10-02-frigate-writes.md` mentions that the normandy guest had rebooted. That reboot was a clean shutdown at 2026-10-01 20:49 to 20:50 PDT for a kernel upgrade (`7.1.9-arch1-2` to `7.2.5-3-omarchy`, from `last -x` and `journalctl -b -1`). It predates phase 0, and it is why the guest's disk counters start then.
- Service interruptions during phase 0 were two: the planned public-api stop and start (about 32 s of ingress, 13:06:35 to 13:07:07 PDT) and a restart of normandy's Gatus container at 13:04:07 PDT while the SMART endpoints were deployed (seconds of monitoring only). The VM 202 restore (12:57 to 13:21) and the homelab Docker cleanup overlapped the dry run in time, but neither affected a service: the scratch VM had its links down and the cleanup removed only unused objects.

## Reviewer findings and where they go

| Finding | Resolution |
|---|---|
| How a restore treats a `backup=0` disk is untested; VM 202 has none, only VM 102's `scsi1` | Phase 1 precondition: test it on a scratch restore of VM 102 before step 5, or record that the 1.8 TB disk's contents are accepted as lost (they are copied to the NAS first). |
| `nas-backup/pve-config` changed to 770, previous mode unknown | Record only; Igor to confirm no other NAS reader needed it. |
| The `proxmox` bucket on homelab's InfluxDB grows at a 10 s interval for every guest | Phase 3 precondition: read the bucket size after one week. |
| Gatus SMART push token rendered into normandy's world-readable `config.yaml` | Phase 5, when Gatus is converted: render it from the 0600 `.env`. The token can only push SMART results. |
| The metric-server token is on the `pvesh` command line during its one run, and `/proc` has no `hidepid` | Record only: the token is write-only to one bucket and the task runs only at creation. |
| A changed `smart_on_calendar` would not restart the timer | Fix with the next base-role edit. |
| InfluxDB is published on `0.0.0.0:8087` on homelab, and the Proxmox nodes send to it over plain HTTP | Record only now; a source restriction belongs to phase 5. |
| Leftover `/tmp/rrd-*.json` on both nodes (from the 2026-10-02 audit) and `/tmp/ct100.yaml`, `/tmp/ct107.yaml` on proxmox-a (from 2026-09-06, one holds an AdGuard bcrypt hash) | Housekeeping, outside acceptance; delete them. |
| public-api's dnsmasq answers mesh clients for `ryzhkov.dev` with 10.42.0.3 | Phase 4. |

## Risks carried forward

- normandy's NVMe root (WD SN810 256 GB) receives about 170 to 290 GB/day, mostly from the t3-steward and T3 Code SQLite state on copy-on-write btrfs inside LUKS. Wear is 2 % after 1202 hours. The owner is t3-steward, outside this plan.
- VM 101's staged pending change also switches its disk cache from `writethrough` to `none`; confirm before the phase 3 stop and start.
- The nightly Authentik restore check in the backup role leaves one anonymous Postgres volume per run, because the container is removed without `-v`.
- VM 250's ten archives (10.9 GB) on `nas-backup` will never be pruned automatically; the store is at 71.8 %.
- The second temperature sensor of proxmox-a's 2 TB P3 reads about 79 C against a 70 C alert threshold; expect alerts under sustained writes in phase 2.
