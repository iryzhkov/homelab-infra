# Phase 7 acceptance record and transition outcome

Date: 2026-10-03. Records `e156fb9`, review fixes `5998a36`, this record and the runbook correction, on `transition/phase-7`.

## Verdict

Accepted, and the transition's stated outcome is met: either Proxmox host can be drained and shut down for maintenance on current hardware with household services continuing, apart from the accepted exceptions.

proxmox-b was off from 14:52 to 15:02 and proxmox-a from 15:38 to 15:50, each drained by `host-drain.yml`, held down about ten minutes with the cluster quorate on the QDevice, and returned by `host-return.yml`. Both reboots proved what had not been tested before: `tank` imports at boot, the worker data disks mount before containerd and Docker, and the autolocked Swarm managers unlock. Igor overrode the plan's nobody-home rule (`allow_home=true`) and waived the go.

Down while a host was off, as accepted: proxmox-b: Frigate, the steward coordinator, agent-b, one AdGuard replica. proxmox-a: Home Assistant, Zigbee and MQTT, Jocasta, dsh and the ai route, the feed, logging, torrents, agent-a, two of the three AdGuard servers. Each stateful stack was down 6 to 90 seconds at each move out and home.

Review: four Sonnet 5.5 reviewers (Proxmox and storage ACCEPT, security ACCEPT, availability ACCEPT, tooling FIX REQUIRED with should-fix items). Fixes in `5998a36`: a maintenance node label that makes `site.yml`, keepalived and the Swarm availability reset refuse during a drain; a read-only pre-pass on peer-copy age; a rescue that restores keepalived and the shed tier; a resumable `swarm-move.yml`; a probe over all 43 Traefik routes; the Jocasta backup watcher ordered after `/mnt/backup`. The Fable 5.1 reviewer re-ran the proxmox-a drain in check mode (changed=0, all routes answering), `site.yml --check` (changed=0), and confirmed the guard, the voice health check, the watcher, Loki and Gatus, then accepted. One stale runbook section was corrected afterwards.

## Where the outcome falls short

- voice failed about a quarter of requests for the whole proxmox-b window because its route had no health check; fixed, not yet exercised under an outage.
- Loki needs `docker service update --force logging_loki` after every homelab return; root cause open.
- irc and rss returned 503 in short bursts while other stacks moved.
- During a proxmox-a window only one AdGuard server answers, so clients asking the others first see slow lookups.
- The presence refusal has only check-mode evidence.
- The agent VMs are not yet steward workers, and the steward coordinator is on normandy, so agent runs stall during a proxmox-b window.
- 9.7 GB of `.moved` rollback copies sit on normandy by design.
- One edit to `swarm-move.yml` was made by a script rather than through Huyang; its diff was reviewed twice.

## For the next maintenance

The procedure is `runbook-host-maintenance.md`, also published as `homelab/runbooks/host-maintenance.md` in Jocasta. In short: run the hourly snapshot on the leaving worker, `--check`, a drain without power-off, verify by hand, then `-e power_off=true`; proxmox-b before proxmox-a and the second only after clean checks; wake by Wake-on-LAN from the other node; after proxmox-a returns, force-restart Loki and let Zigbee settle; retest voice, irc and rss; tell the household what goes down.
