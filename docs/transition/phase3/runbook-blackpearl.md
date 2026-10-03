# Phase 3 runbook: black-pearl (VM 102) stop-and-start window

Prepared 2026-10-02. The survey behind it is in `2026-10-02-blackpearl-prep.md`.

The window applies these changes:

- Memory goes from 8 GB to 2 GB with the balloon device removed, and vCPUs go from 3 to 2. These are pending Proxmox changes that take effect at `qm start`.
- `/mnt/shared` gets `nosuid,nodev,noexec`.
- The hand-written `/mnt/apps` automount comes under Ansible with the same options plus `nosuid,nodev,noexec`. It is not retired, because black-pearl's nightly backups go through it.
- gluetun's `FIREWALL_OUTBOUND_SUBNETS` is removed.

It is also black-pearl's first boot without the retired 1.8 TB disk.

Run everything from the laptop in `~/Work/homelab-infra`, on the commit that holds the changes to `inventory/host_vars/blackpearl.yml` and `stacks/torrent/docker-compose.yml`.

Baseline values, all from the survey except the WAN IP, which comes from the phase 1 review:

| Item | Value |
|---|---|
| Guest memory used | 1184 of 7940 MB |
| VPN IP | 188.214.122.90 |
| WAN IP | 104.8.3.194 |
| Forwarded port | 46561 |
| qBittorrent | v5.1.4, 1237 torrents: 1224 stalledUP, 13 uploading, none stopped |

## 1. Pre-checks

1. Igor gives an explicit go for the black-pearl window. Without it, stop here.
2. Run `ssh proxmox-a 'qm pending 102'`. Every line must start with `cur`. If any line starts with `new` or `del`, find out why before going on.
3. Run `ssh proxmox-a 'qm config 102' | grep -E '^(memory|cores|balloon|hotplug)'`. The expected output is `memory: 8192` and `cores: 3`, with no `balloon` line and no `hotplug` line.
   - No `hotplug` line means the default `network,disk,usb`, so the memory and CPU changes cannot apply live.
   - No `balloon` line means the default balloon device, whose minimum equals the memory size.
   - If either line has appeared since the survey, record its value as the rollback value.
4. Run `ssh blackpearl 'findmnt /mnt/shared; systemctl is-active docker; docker ps --format "{{.Names}} {{.Status}}"'`. The NAS mount must be present. Docker must be active, with gluetun healthy and gt-qb, the port manager and diun up.
5. Check that the NAS answers, because Docker will not start at boot without `/mnt/shared`. Run `ssh blackpearl 'stat -f /mnt/shared >/dev/null && echo ok'`.
6. Run the dry runs:
   - `ansible-playbook playbooks/base.yml -l blackpearl --check --diff`. Expect changed=2: the fstab write and the mount task, for items `/mnt/shared` and `/mnt/apps`.
   - `ansible-playbook playbooks/site.yml -l blackpearl --check --diff`. Expect changed=1: the torrent Compose file, losing the `FIREWALL_OUTBOUND_SUBNETS` line.
   - Anything else changing means the host drifted. Stop and find out why.
7. Record the stopped torrents, so that step 7 restarts only what this window stopped:
   `ssh blackpearl "docker exec gt-qb curl -s 'http://127.0.0.1:8080/api/v2/torrents/info?filter=stopped'" | python3 -c 'import json,sys; print([t["hash"] for t in json.load(sys.stdin)])'`. The expected result is `[]`.

Write down the time. Each later step gives its expected duration.

## 2. Pending resource change (1 minute)

```
ssh proxmox-a 'qm set 102 --memory 2048 --balloon 0 --cores 2 && qm pending 102' | grep -E 'memory|balloon|cores'
```

Expect these lines:

- `cur memory: 8192` and `new memory: 2048`
- `cur cores: 3` and `new cores: 2`
- `new balloon: 0`

If instead `qm config` already shows the new values, the change applied live. Run `free -m` and `nproc` in the guest, record the result, and go on, since the shrink is far above the 1.2 GB in use.

`--balloon 0` removes the balloon device. Afterwards Proxmox no longer gets the guest's own free-memory figure, so judge memory from `free -m` inside the guest.

## 3. Stop torrents (1 minute)

```
ssh blackpearl "docker exec gt-qb curl -s -X POST -d hashes=all http://127.0.0.1:8080/api/v2/torrents/stop"
ssh blackpearl "docker exec gt-qb curl -s http://127.0.0.1:8080/api/v2/torrents/info" | python3 -c 'import json,sys,collections; d=json.load(sys.stdin); print(len(d), collections.Counter(t["state"] for t in d))'
```

Expect every torrent in `stoppedUP`, or `stoppedDL` for any that are incomplete. qBittorrent v5 uses `stop` and `start`, not `pause` and `resume`.

## 4. Converge black-pearl (5 minutes)

Base first. Do not run any `docker` command on black-pearl while this play runs. `docker.socket` would start Docker again in the middle of the remount.

```
ansible-playbook playbooks/base.yml -l blackpearl --diff
```

The play does the following:

- It rewrites both fstab lines.
- It runs `systemctl stop` on `mnt-shared.mount`, `mnt-apps.automount` and `mnt-apps.mount`. Stopping `mnt-shared.mount` also stops docker.service, through `RequiresMountsFor`, and with it every container.
- It reloads systemd and mounts both paths again.
- It does not start Docker again.

Check the result:

```
ssh blackpearl 'for m in /mnt/shared /mnt/apps; do findmnt -o TARGET,FSTYPE,OPTIONS --mountpoint $m; done; systemctl is-active docker'
```

Both NFS mounts must show `nosuid,nodev,noexec`. Docker is expected to be `inactive`. Then start Docker and check the containers:

```
ssh blackpearl 'sudo systemctl start docker && sleep 20 && docker ps --format "{{.Names}} {{.Status}}"'
```

Then converge the stacks:

```
ansible-playbook playbooks/site.yml -l blackpearl --diff
```

Expect the Compose file diff, followed by `Bring the stack up` changed. That recreates gluetun, and gt-qb and the port manager with it, since they share its network namespace. diun is unchanged.

Confirm the firewall:

```
ssh blackpearl 'docker exec torrent-gluetun-1 iptables -S OUTPUT'
```

The output must have no `192.168.1.0/24`, `192.168.20.0/24`, `10.0.0.0/16` or `172.21.0.0/16` rule. It must still have `lo`, `RELATED,ESTABLISHED`, the `172.16.2.0/24` rule, the WireGuard endpoint rule and `-o tun0`.

## 5. Stop and start the VM (2 minutes)

Use `qm shutdown` and `qm start`, never a reboot inside the guest, because the pending changes apply only at a Proxmox start.

```
ssh proxmox-a 'time qm shutdown 102 --timeout 180 && qm status 102'
ssh proxmox-a 'time qm start 102 && qm pending 102 | grep -E "^(new|del)"; qm config 102 | grep -E "^(memory|cores|balloon)"'
```

Expect the following:

- `status: stopped` after the shutdown.
- No `new` or `del` lines after the start.
- `memory: 2048`, `cores: 2` and `balloon: 0`.

If the shutdown times out, the guest did not answer ACPI or the agent. Check the console before using `qm stop`.

The public-api dry run gave about 15 s for shutdown and 14 s for boot to SSH.

## 6. Post-checks (10 minutes)

Run them in order. Stop at the first failure and see Rollback.

1. **Clean boot without the old disk.** Run `ssh blackpearl 'systemctl is-system-running; systemctl --failed --no-legend; lsblk -o NAME,SIZE,TYPE; journalctl -b -p err --no-pager | tail -20'`.
   - If SSH answers, the VM is not in emergency mode.
   - Expect `running`, no failed units, and only `sda` and `sr0`.
   - If SSH does not answer within 3 minutes, open the console: proxmox-a web UI, VM 102, Console.
2. **Resources.** Run `ssh blackpearl 'free -m; nproc'`. Expect a total of about 1.9 GB and 2 CPUs.
3. **Mounts.** Run `ssh blackpearl 'findmnt -o TARGET,FSTYPE,OPTIONS /mnt/shared; stat /mnt/apps/blackpearl >/dev/null && findmnt -o TARGET,FSTYPE,OPTIONS -t nfs /mnt/apps'`.
   - `/mnt/shared` must be NFS with `hard,...,nosuid,nodev,noexec`.
   - `/mnt/apps` must mount on access, with `soft,...,nosuid,nodev,noexec`.
4. **Docker after the mount.** Run `ssh blackpearl 'systemctl show -p ActiveEnterTimestamp mnt-shared.mount docker.service; docker ps --format "{{.Names}} {{.Status}}"'`. Docker's timestamp must be later than the mount's. gluetun, gt-qb, the port manager and diun must be up, with gluetun `(healthy)` within about 1 minute.
5. **VPN egress is not the WAN IP.** Run `ssh blackpearl 'curl -s http://127.0.0.1:8001/v1/publicip/ip; docker exec torrent-gluetun-1 wget -qO- -T 10 https://ipinfo.io/ip'`. Both must give the same address, and it must not be 104.8.3.194; ProtonVPN gave 188.214.122.90 before. If either shows the WAN IP, run `docker stop gt-qb` at once and see Rollback.
6. **Port forward renewed.** Run `ssh blackpearl 'curl -s http://127.0.0.1:8001/v1/portforward; docker logs --tail 5 torrent-gluetun-qbittorrent-port-manager-1'`.
   - Expect `{"port":N}` with N greater than 0. The port may differ from 46561.
   - Expect the log line `successfully updated qBittorrent to port N (confirmed)`.
   - Cross-check with `ssh blackpearl "docker exec gt-qb curl -s http://127.0.0.1:8080/api/v2/app/preferences" | python3 -c 'import json,sys; print(json.load(sys.stdin)["listen_port"])'`, which must give the same N.
7. **WebUI from homelab's *arr apps.** The test runs on homelab, so it crosses VLAN 70 to VLAN 60 and enters gluetun inbound:

```
ssh homelab 'for a in sonarr:8989:v3 radarr:7878:v3 lidarr:8686:v1; do IFS=: read c p v <<<"$a"; n=$(docker ps --format "{{.Names}}" | grep -i -x "$c"); k=$(docker exec "$n" sh -c "grep -o \"<ApiKey>[^<]*\" /config/config.xml | cut -d\\> -f2"); printf "%s " "$c"; curl -s -X POST -H "X-Api-Key: $k" http://127.0.0.1:$p/api/$v/downloadclient/testall; echo; done'
```

   Each must return `[{"id":...,"isValid":true,"validationFailures":[]}]`. A failure here means inbound traffic is being dropped, which the survey did not predict. See Rollback, gluetun.
8. **Torrents resume.** Run:

```
ssh blackpearl "docker exec gt-qb curl -s -X POST -d hashes=all http://127.0.0.1:8080/api/v2/torrents/start"
```

   - Only use `hashes=all` if step 1.7 recorded no stopped torrents; otherwise pass the other hashes joined with `|`.
   - After 5 minutes, repeat the count from step 3. Expect about 1237 torrents in `stalledUP` or `uploading`, none in `error` or `missingFiles`, and `connection_status` `connected` from `/api/v2/transfer/info`.
   - `firewalled` can show for a few minutes after a new port.
9. **Repository and hosts agree.** Run `ansible-playbook playbooks/base.yml -l blackpearl --check` and `ansible-playbook playbooks/site.yml -l blackpearl --check`. Both must report changed=0.

## Watch afterwards

- The next morning, check the 01:00 and 01:10 cron copies through `/mnt/apps`: run `ssh homelab 'ls -la --time-style=full-iso /mnt/backup/blackpearl/stack-definitions'`, and check that the user's mail on black-pearl has no cron errors.
- Check the next homelab bulk backup, Monday or Thursday at 06:00: `bulk-blackpearl.last-run` must show `exit=0`.
- For a week, watch `free -m` and swap on black-pearl. If swap use climbs above about 300 MB, take memory back up to 3 GB at the next stop.

## Rollback

- **Before the shutdown (step 5).** Undo the pending change with `ssh proxmox-a 'qm set 102 --revert memory,balloon,cores'`, then check `qm pending 102`.
- **Resources after boot.** Run `ssh proxmox-a 'qm set 102 --memory 8192 --cores 3 --delete balloon'`, then do another `qm shutdown 102` and `qm start 102`.
- **Mount options.** Revert the `nfs_mounts` hunk of `inventory/host_vars/blackpearl.yml` with git, then run `ansible-playbook playbooks/base.yml -l blackpearl --diff` (this stops Docker again), then run `ssh blackpearl 'sudo systemctl start docker'`. Reverting `/mnt/apps` restores the hand-written line's options; the line itself stays managed.
- **gluetun.** Restore the `FIREWALL_OUTBOUND_SUBNETS` line in `stacks/torrent/docker-compose.yml`, then run `ansible-playbook playbooks/site.yml -l blackpearl --diff`. If the VPN IP equals the WAN IP, keep gt-qb stopped until gluetun is fixed; the kill switch is what failed.
- **Emergency mode at boot.** Use the console on proxmox-a.
  1. Log in and run `journalctl -xb` to find the failing unit.
  2. Comment out the offending `/etc/fstab` line, then run `systemctl daemon-reload` and `systemctl default`. This hand edit is the one exception allowed.
  3. Record it.
  4. Fix the line through `host_vars` and converge base.
  5. The only local disk lines are `/` and swap by UUID. The NFS lines carry `_netdev`, so a NAS failure does not cause emergency mode; it leaves Docker stopped.
- **NAS unreachable at boot.** Docker stays down by design. Once `findmnt /mnt/shared` shows the mount, run `ssh blackpearl 'sudo systemctl start docker'`.

## Estimated duration

| Part | Time |
|---|---|
| Pre-checks and dry runs | 10 minutes |
| Steps 2 to 4 | 7 minutes |
| Stop and start | 2 minutes |
| Post-checks | 10 minutes |

That is about 30 minutes of attended work, with about 40 minutes as the budget. Torrents are stopped for about 20 minutes. The WebUI and the *arr connection are down for about 10 minutes: during the base converge and the gluetun recreation in step 4, and for the VM's 1 to 2 minute restart.
