# Phase 7: drain, power-off and return of proxmox-a

Date: 2026-10-03, 15:14 to 16:10 PDT. Executor E28, on the lead's go after the proxmox-b record
(`2026-10-03-drain-proxmox-b.md`) checked clean. Hosts: proxmox-a (hp-elite-mini-01) drained and
powered off; proxmox-b, normandy, the edges, OCI and Home Assistant read. Presence override as for
proxmox-b (Igor's decision, `-e allow_home=true`).

## Preconditions (15:14 to 15:16)

- homelab's hourly snapshot job run first (15:14:41 to 15:15:43, `Result=success`), so every peer copy
  on normandy was fresh. Last vzdump on proxmox-a: OK, 02:30.
- Managers: OCI Reachable, homelab Leader, normandy Reachable; OCI answers on the mesh from both
  workers. Quorum and QDevice as before. Both Gatus instances green. VIP on public-api (taken over
  during the proxmox-b drain; `nopreempt` kept it there).

## Drain (15:15:52 to 15:34:03)

`ansible-playbook playbooks/host-drain.yml -e host=proxmox-a -e allow_home=true` (drain only, so the
checks below could run before the power-off), rc 0, `ok=42 changed=5`.

- Plan: worker homelab to normandy, edge public-api to public-api-b; 13280 MiB available on normandy
  against 7648 MiB reserved by what moves (19 stacks plus Emby's 2 GiB estimate).
- VIP: keepalived stopped on public-api; public-api-b took 192.168.20.3.
- 19 Swarm moves, serially: actual, autobrr, bazarr, cv-site, komga, lidarr-music, lubelogger,
  navidrome, obsidian, ombi, openviking, prowlarr, radarr-movie, sonarr-tv, thelounge, then
  authentik, miniflux, vaultwarden, forgejo last. `logging` stayed (down with homelab).
- Emby moved to normandy by `compose-move.yml` (MediaItems 42355 on both copies).
- homelab drained; dsh, jocasta, gatus and cloudflared stopped on homelab, torrent and diun on
  black-pearl.

Checked on normandy before the power-off: Authentik's four services there, `auth` ready 200, users
9, applications 9, flows 14, tokens 2; forward auth (`ai.ryzhkov.dev` 302 to
`auth.ryzhkov.dev/application/o/authorize/`); OIDC redirects for rss (302) and Forgejo (307);
Vaultwarden `/alive` 200, `/api/config` 200, 313 ciphers and 1 user in its database; Emby 200 through
the VIP and by OCI's public address. VIP on public-api-b, keepalived inactive on public-api.

## Power-off (15:35:15 to 15:38:34)

A second `host-drain.yml ... -e power_off=true` refused nothing after a fix: its pre-check had read
both Gatus instances and refused any failure, but homelab's Gatus was stopped and the expected
failures were present. A re-run now detects the already drained worker, reads only the survivor's
Gatus and ignores the failures `host_maintenance_gatus_expected` allows (check mode first, then the
real run, rc 0, `ok=44 changed=4`). Guests shut down in order: agent-a, black-pearl, homelab,
homeassistant, zigbee2mqtt, mqtt, healthcheck, public-api, CT 107, CT 100. proxmox-b stayed quorate.

## Host down (15:38 to 15:48)

- proxmox-b: `Total votes 2`, `Flags: Quorate Qdevice`, QDevice `Connected`, at 15:38:43 and
  15:48:01. No `pvecm expected 1` was needed.
- Swarm: homelab Down/Unreachable; normandy Leader; OCI Reachable.
- Through the VIP from 15:39 to 15:48, failing only: home, zigbee, logs, jocasta, torrent (246 of
  246 rounds each). Everything else answered, including auth, vault, git, photos, emby, ov.
  `ai.ryzhkov.dev` answered its forward-auth redirect; dsh behind it was down.
- normandy's Gatus: failing only jocasta, edge public-api, home, mqtt, zigbee, gatus on homelab,
  the steward worker on homelab. The public Gatus: only `public_home-ryzhkov-dev`.
- Emby by OCI's public address and photos through Cloudflare: no failure in the window.
- DNS: normandy resolved `vault.ryzhkov.dev` to the VIP with CT 100 and CT 107 down (CT 201 answers).

## Return (15:48:09 to 16:10)

- Wake-on-LAN to proxmox-a's `7c:4d:8f:99:6b:56`, sent from proxmox-b at 15:48:09; ping at 15:50:20.
- `host-return.yml -e host=proxmox-a`, 15:50:25 to 16:07:45, rc 0, `ok=28 changed=6`: quorum,
  tank, guests, mount order, unlock, undrain, Compose stacks, 19 stacks moved home in the same order
  (normandy's hourly snapshot first), Emby moved home, Gatus green, every route through the VIP.
- tank: `zfs-import@tank` "Finished" at 15:48:39, healthy. **Boot import proven** on proxmox-a.
- `/mnt/docker-data` (mount active 2.68 s after boot) before containerd (2.91 s) and Docker (5.71 s).
- Guests 101, 102, 103, 106, 111 and CTs 100 (AdGuard origin), 104 (MQTT), 105 (Zigbee2MQTT), 107,
  110 running.
- homelab came back locked (twice "Swarm is encrypted and needs to be unlocked" in dockerd's log) and
  was unlocked by the nodes play; `Ready Active Reachable`. normandy stays Leader.
- Home Assistant: `home.ryzhkov.dev` 200 from 15:51:16. Zigbee2MQTT: "Currently 34 devices are
  joined", "Zigbee2MQTT started!" at 15:50:38; seven `NWK_NO_ROUTE` ping failures in its first
  minutes while the mesh re-formed, none in the five minutes before 16:08, 161 MQTT publishes in
  them. Home Assistant logged a few lights unavailable at 15:51 and 16:04 (re-joining devices).
- Loki: after the undrain its task ran but `/ready` stayed "Ingester not ready: waiting for 15s after
  being ready" (the ring behaviour seen in phase 5). `docker service update --force logging_loki` at
  16:08:22; `ready` at 16:08:52; a query returns streams again (frigate and others).
- Jocasta `https://jocasta.ryzhkov.dev/healthz` 200; the feed answers on :1934 (401 without a key)
  and lists the moves; dsh up.
- keepalived active on both edges, the VIP on public-api-b. The placement override file is gone.

## Per-service unavailability

From the probe of 36 routes through the VIP every 2 s (1365 rounds, 15:15:52 to 16:08:58). Moves out
ran 15:16 to 15:34, moves home 15:52 to 16:07.

| Service | Out (drain) | Host down | Home (return) |
|---|---|---|---|
| home (Home Assistant), zigbee | from 15:36:59 and 15:37:39 | down, accepted exception | back 15:51:16 and 15:50:48; 14 min 17 s and 13 min 9 s in all |
| jocasta, torrent | from 15:32:48 and 15:32:52 (Compose stopped) | down, accepted exception | back 15:50:48; about 18 min |
| logs (Grafana) | from 15:32:14 (homelab drained) | down, accepted exception | back 15:51:08, then 503 in short bursts until Loki's restart at 16:08 |
| ai (dsh behind forward auth) | 15:25:01 to 15:25:51 (50 s, Authentik moving) | redirect answered; dsh down | 16:00:04 to 16:00:39 (35 s) |
| auth | 15:25:01 to 15:25:51 (50 s) | up | 16:00:02 to 16:00:39 (37 s) |
| git (Forgejo) | 15:27:22 to 15:28:29 (67 s) | up | 16:02:10 to 16:03:38 (88 s) |
| vault | 15:26:54 to 15:27:04 (10 s) | up | 16:01:40 to 16:01:48 (8 s) |
| rss (Miniflux) | 15:26:18 to 15:26:37 (19 s), then 503 for 6 to 12 s four times 15:28 to 15:31 | up | 16:01:01 to 16:01:22 (21 s), then short 503s to 16:06:35 |
| irc (thelounge) | 15:24:15 to 15:24:46 (31 s), then 12 to 39 s of 503 five times 15:28 to 15:31 | up | 15:59:25 to 15:59:46 (21 s), then 15 s and 94 s (16:05:01 to 16:06:35) |
| emby | 15:31:23 to 15:31:47 (24 s); by OCI 24 failed samples at 15:31 | up | 16:06:18 to 16:06:46 (28 s); by OCI 12 at 16:06 |
| photos | none through the VIP; 3 failed samples through Cloudflare at 15:31 | up | none |
| sonarr, radarr, lidarr, prowlarr, bazarr, manga, request, autobrr, music, garage, obsidian, budget, ov, igor | 6 to 33 s each, at its own move | up | 5 to 27 s each at its move home; sonarr and radarr 13 to 14 s more at 16:03 and 16:05 |
| draw, speed, uptime, voice, wiki | 18 to 27 s at 15:32:14 (the drain rescheduling them) | up | none |
| blog, dns-sync, cctv | one sample or none | up | none |

A single round at 15:35:58 failed with 502 on a dozen routes at once while public-api's Traefik
stopped with its VM; the VIP was already on public-api-b.

Agents: agent-a down 15:35 to about 15:50; the feed and Jocasta as above.

## Findings

- irc and rss failed in short bursts while other stacks moved (15:28 to 15:31, 16:02 to 16:06), not
  only at their own move. Their health checks (5 s interval, 3 s timeout) likely time out while the
  worker is loaded by the move copies; Traefik then takes the backend out. Worth a longer timeout on
  those two routes.
- Forgejo is the slowest move (67 s out, 88 s home), as expected for the largest state.
- Loki needs a forced restart after a homelab return (`docker service update --force logging_loki`);
  added to the runbook.
- Wake-on-LAN works for both nodes from the other node (Python magic packet to 192.168.1.255); the
  runbook's "power on by hand" is not necessary.

## Review fixes (16:20 to 17:10)

Tooling review, FIX REQUIRED (should-fix only). Repository and check mode only, except item 5.

1. Maintenance marker: `host-drain.yml` sets the node label `maintenance=<host>` on the survivor
   after its read-only checks; `host-return.yml` removes it last. New `roles/maintenance_guard`,
   included by `playbooks/site.yml` (pre_tasks of both plays), `roles/keepalived` and
   `roles/swarm/tasks/labels.yml` (only when the availability would be reset), refuses while it
   is set unless `-e maintenance_override=true`; `compose-move.yml` and `host-return.yml` pass the
   override to the converges they run. Tested with the hook `-e maintenance_guard_test_marker=...`
   in check mode: `site.yml` and `keepalived.yml` both refuse ("Maintenance in progress: ..."),
   every host of the play stops (`any_errors_fatal`), and the override lets them run.
2. Read-only pre-pass in the drain: the peer copy of every stack in `hm_moves` must be under two
   hours old on the survivor, else it refuses before the marker or anything else. `host-return.yml`
   ends with an hourly snapshot on the returned worker.
3. Recovery: steps 2 to 8 of the drain run as a block whose rescue starts keepalived again on the
   edge the run stopped and redeploys the shed tier, then fails. `--check -e host=proxmox-b -e
   hm_test_fail_after_vip=true`: `rescued=1`, the rescue's keepalived start and the final message
   ran. `swarm-move.yml` resumes a move interrupted after the old copy was set aside (no owner on
   the old node, `.moved/<stack>-*` present, the target the owner): `--check -e stack=lubelogger
   -e to=normandy -e swarm_move_test_resume=true`: "Resuming an interrupted move", the replica
   counts read from the `.moved` copy, the four copy steps skipped.
4. Runbook: rule 7 rewritten after checking every prepare file (20 read the pin label, 6 name no
   pin), rule 8 (the marker), the `.moved` and compose cold copies (normandy holds 19 `.moved`
   copies, 9.7 GB), voice, irc and rss to retest, Loki's forced restart required with the cause
   open, the probe list taken from Traefik's routers (43 hosts; `ov.ryzhkov.dev` added to the
   household list with `/health`, the only router the first run reported).
5. `jocasta-backup-watch.service` failed at homelab's boot (15:50:39, `/mnt/backup` not mounted
   yet): `RequiresMountsFor=/mnt/backup` in `stacks/jocasta/templates/jocasta-backup-watch.service.j2`;
   `site.yml -l homelab -e only=jocasta` at 16:29:07, `changed=2` (the unit, and the prepare
   step's daemon reload and restart of the oneshot `jocasta-firewall.service`, which re-applies the
   same chain; `iptables -S JOCASTA` unchanged, `jocasta.ryzhkov.dev/healthz` 200). Started by
   hand: `Result=success`; `RequiresMountsFor=/mnt/backup` and `mnt-backup.mount` in its
   dependencies. Last `jocasta-backup.service` run 16:00:06, `Result=success`.

Checks after the 17:00 hourly runs (homelab 17:02:10, normandy 17:01:50, both success):
`host-drain.yml --check` proxmox-a `ok=46 changed=0 failed=0`, the pre-pass passing, no
unlisted router failing after the `ov` fix; proxmox-b `ok=42 changed=1 failed=0` (the simulated
keepalived stop). `host-return.yml --check` both `ok=21 changed=0 failed=0`. `site`, `swarm`,
`traefik`, `backup`, `keepalived` in check mode: changed=0 on every host.

## Final checks (16:09 to 16:15)

Both Gatus instances (75 and 80 endpoints) and the public one (16): none failing. Check mode:
`site.yml` changed=0 on all five Docker hosts; `swarm.yml` changed=0 on homelab, normandy, oci;
`traefik.yml` changed=0 on both edges; `backup.yml` changed=0 everywhere.
