# Phase 3 acceptance record (homelab, normandy, black-pearl)

Date: 2026-10-02. Preparation `e825323`, window records `82c656d` and `7e4efa5`, review fix `b9ad880`, all on `transition/phase-3`. public-api's stop-and-start is deferred until after phase 4, as the plan requires, so the VIP covers it; Home Assistant has no phase 3 change.

## Verdict

Accepted for VMs 101, 102 and 202. Each was stopped and started exactly once with `qm shutdown` and `qm start`, one at a time, on Igor's explicit go:

| VM | Window (PDT) | Outage | Applied |
|---|---|---|---|
| black-pearl (102) | 17:29 to 17:33 | torrents 3 min 52 s | 2 GB, 2 vCPU, first boot without the old disk, `nosuid,nodev,noexec` on `/mnt/shared` and `/mnt/apps`, gluetun `FIREWALL_OUTBOUND_SUBNETS` removed |
| normandy (202) | 17:39 to 17:42 | Docker 3 min 2 s | 20 GB, `cpuunits` 1024, Docker and containerd roots on `/mnt/data`, `fstrim.timer` |
| homelab (101) | 19:18 to 19:23 | about 5 min | 20 GB, balloon 0, `cpuunits` 1024, `discard=on` and `cache=none` on virtio0, Docker root on a 300 GB `tank` zvol at `/mnt/docker-data`, `fstrim.timer`; trim freed 73.9 GiB and the thin pool fell from 71.8 % to 51.4 % |

Four Sonnet 5.5 reviewers: storage and Proxmox, service availability, and security ACCEPT; Ansible FIX REQUIRED with should-fix items only, both fixed in `b9ad880` (base.yml no longer fails while homelab's InfluxDB is down; runbooks mask the units that start Docker during a copy). The Fable 5.1 reviewer re-checked the hosts and accepted. `site.yml` and `base.yml` in check mode report changed=0 on every host.

## Decisions recorded

- Igor: go for each window; homelab restarted with the pre-check for T3 thread 32e69299 waived.
- Igor: keep both parts of VM 101's staged change (discard, cache=none); move both Docker's and containerd's roots on normandy; accept the qBittorrent login bypass for 192.168.20.0/24 as a risk.
- The plan's `ssd=1` for homelab's virtio0 is not possible on virtio-blk; the plan text is wrong, not the host.
- normandy's Frigate record path and Immich upload location needed no move: Frigate media was already on `/mnt/data` with recording disabled, and Immich uploads are on the NAS.

## Deviations recorded in the window files

- normandy: the Docker stop reported failed because Omarchy sets `DefaultTimeoutStopSec=5s`; containers had exited before the final copy, nothing was rolled back. Runbooks now stop the stacks before Docker on Omarchy hosts.
- homelab: Docker started twice on its own during the copy (`authentik-watchdog.timer` requires it). The availability reviewer showed no file on the old root changed after the last copy and the stateful services keep their data in bind mounts outside the moved root; no data was lost.

## Carried forward

- Phase 4 preconditions: tonight's 02:30 vzdump shows the `backup=0` zvols of 101 and 202 skipped; proxmox-b memory remeasured (7.2 GB available now) before public-api-b is added; black-pearl's backup copy through the now-`noexec` `/mnt/apps` (2026-10-03 01:00 and 01:10) and Monday's bulk backup succeed.
- Before phase 6: a week of watching swap and memory on homelab, normandy and black-pearl; Igor decides deletion of the retained roots (homelab `/var/lib/docker` 19 GB, normandy `/var/lib/docker` and `/var/lib/containerd` about 25 GB), followed by `fstrim -av`; proxmox-b headroom gates agent-b.
- Phase 5: normandy's unmanaged `no-block-boot.conf`; gluetun's control server published on `0.0.0.0:8001`; Forgejo registers its Authentik source at boot before Authentik is ready.
- Phase 7: Proxmox VM settings live in `qm` and the window records, not in the repository; the drain and restore runbooks need them.
