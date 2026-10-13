# Observability worker portability acceptance — 2026-10-13

The additive metrics and authenticated ingest stack completed two real cold moves:
homelab → normandy → homelab. Its historical metrics survived and ingestion
resumed on both placements. Existing logging, Grafana, Loki and InfluxDB stayed
on homelab; their move guard was neither changed nor bypassed.

## Scope and evidence

The shared laptop checkout stayed clean at
`888a0884eab901dbe1d6f83b81549d6ec9417f4b` throughout both plays.
The existing `playbooks/swarm-move.yml` was used unchanged. It requires all
three managers Ready/Reachable, OCI mesh reachability from both workers,
exactly one pin, matching existing ownership, a fresh genuine peer copy,
and no conflicting target ownership. Each `--check` passed before its move.

Before the outbound move, normandy had approximately 301 GB free on its state
pool and 5.1 GB memory available. Observability state was approximately 9.6 MB.
The pinned nginx ingest image was pre-pulled on normandy before stopping the
stack; the VM image and native helper images were already available.
The native backup service was inactive at the outbound stop.

Genuine existing hourly mirrors supplied the move freshness stamps:

| Direction | Mirror owner | Peer stamp |
| --- | --- | --- |
| To normandy | homelab | 1791885988 homelab |
| To homelab | normandy | 1791886122 normandy |

No timestamp files were fabricated or manually refreshed.
The move copied the entire quiescent state with numeric ownership, wrote the
target owner marker, preserved the old cold copy under `.moved/`, flipped
only `pin.observability`, restored both replicas, and passed its task health
and ownership checks. Move playbook results:

| Placement | Result |
| --- | --- |
| normandy | ok=41, changed=12, unreachable=0, failed=0 |
| homelab | ok=41, changed=11, unreachable=0, failed=0 |

Thirty pre-move `up` series were saved at fixed evaluation time
`1791886016`. After each move, a query with that exact time returned the same
labels, timestamps and values. Verification used shell-quoted remote URLs so
the `time` parameter actually reached VictoriaMetrics.
Fresh source-sample timestamps subsequently advanced to
`1791886126.611` on normandy and `1791886246.611` after return. The
current series count rose to 33 as the additive collector rollout continued.
These fresh samples traversed the existing authenticated ingest pipeline.

Both workers' routing-mesh `:8429/ready` endpoints answered 200 on both
placements. After return, the stable TLS VIP endpoint
`metrics-push.ryzhkov.dev:8443/api/v1/write` rejected an unauthenticated
POST with 401, while the query route returned 404. No query/admin port was
published.

Eight household routes answered HTTP 200 in checks before, during and after
the moves: Vaultwarden, Authentik, Home Assistant, Immich, Emby, Grafana,
Forgejo and Gatus. These were sampled HTTP checks, not a continuous uptime
measurement. Application services were not restarted by either move.
Moving this single-replica monitoring stack temporarily stops monitoring;
the queued collectors resumed delivery afterwards.

## Backup acceptance after changing owners

The move's standard tidy removes obsolete snapshots and peer staging from
the former placement. Refresh native backups on the new owner after a move;
do not assume the previous owner's local snapshot directory still exists.

On normandy, native backup `20261013T100836Z-20cb8c26` succeeded.
After return, homelab created
`20261013T101119Z-8c290cec`, mirrored it to normandy, and proved an isolated
restore locally and from the peer. Each restore returned the exact 33-series
historical proof. Restore containers had no network, published ports, secrets
or live state mounts and were removed after acceptance.

After final collector and backup-transport changes, homelab created native
backup `20261013T113629Z-773cfa5c`. Both the owner's isolated restore and
normandy's restore from
`/mnt/data/peer-snapshots/homelab/observability/snapshots/victoriametrics/20261013T113629Z-773cfa5c`
returned the exact 41-series historical proof. The genuine ordinary hourly
mirror completed again afterwards. Snapshot authentication travels through a
private stdin pipe; neither host nor container process arguments contain its
key. BusyBox nc needs its input pipe held open through the response window;
the bounded 30-second hold adds roughly one minute to a complete backup.
A snapshot request timeout still requires inspecting residual native snapshots
before another attempt; do not delete snapshots of unknown ownership.

At acceptance the sole observability pin and owner marker named homelab;
logging's pin still named homelab. Homelab's pool retained approximately
252.8 GB free. The final genuine homelab hourly mirror completed successfully
and wrote `1791886362 homelab` on normandy, restoring the complete peer
freshness material for the next planned move.

## Alpine syslog acceptance

The additive Alpine forwarding path was also verified on both guests. Parent
receivers accept only ct110 192.168.70.250 → PVE-a 192.168.1.5 UDP1514 and
ct201 192.168.20.83 → PVE-b 192.168.1.29 UDP1514, plus local receiver probes.
They bind the specific parent IPv4 address and route RFC3164 records through
the same Alloy redaction component with fixed guest host labels. Harmless
UDP probes from homelab (a non-guest source) incremented each parent chain's
DROP counter by one, proving the source restrictions on both receivers.

ct110 initially passed an actual UDP socket delivery probe. ct201 initially
failed that socket test; observing a packet on its bridge did not prove delivery.
The parent added the exact ct201 source/destination UDP1514 policy and verified
the existing policies stayed unchanged; the parent owns its network evidence.

After that change, both direct test datagrams and actual BusyBox
logger → syslogd → Alloy → Loki messages passed all nine dummy-secret patterns:
Bearer, Basic, API key, password, URL credential, GitHub token, Slack token, AWS
key and JWT. The received streams had host=ct110/ct201 and job=syslog, nine
[REDACTED] replacements, and none of the raw fixture secrets.

The sender role retained existing -t semantics and appended -R PARENT:1514 -L.
Harmless acceptance markers continued to appear in each guest's original
/var/log/messages. Original options are saved for rollback. First deployment's
OpenRC syslog restart also restarted dependent crond; Healthcheck PID344 and
AdGuard PID339 stayed unchanged. The role now uses --nodeps for future syslog
restarts so those dependencies are not restarted.

Fresh source inspection found both Promtail configs read only /var/log/messages.
After the complete source and redaction handoff passed, explicitly gated
retirement stopped only Promtail and removed its default boot membership on
both guests. Init scripts, configs and binaries remain available for rollback.
ct201's first default five-second stop failed; the scoped OpenRC retry schedule
TERM/10/KILL/5 completed its own daemon shutdown. Subsequent role convergence
confirmed both daemons stopped and no Promtail runlevel membership.

After retirement, new actual BusyBox logger fixtures still appeared in each
local /var/log/messages and in Loki with all nine patterns redacted. Healthcheck
PID344, AdGuard PID339, crond PIDs1810/2063 and syslogd PIDs1784/2037 stayed
unchanged during retirement. All eight household HTTP checks remained 200.
The role defaults both forwarding and retirement opt-ins off and rejects
retirement if its current old source paths differ from the verified coverage.

## Repeating a planned move

Run from the stable controller checkout and avoid concurrent stack convergence
or source updates during the move:

```sh
# On the current owner: produce a genuine current config/snapshot mirror.
sudo /usr/local/sbin/homelab-swarm-snapshot

# On the controller: check first, then perform the authorized cold move.
ansible-playbook playbooks/swarm-move.yml -e stack=observability -e to=normandy --check
ansible-playbook playbooks/swarm-move.yml -e stack=observability -e to=normandy

# On the new owner: refresh and verify a native recovery point.
sudo /usr/local/sbin/observability-vm-backup backup
sudo /usr/local/sbin/observability-vm-backup restore-check
```

Check household routes, both workers' ingress readiness, existing historical
metrics at a fixed pre-move time and new source timestamps after the move.
Use the same routine with `to=homelab` to return. Keep the latest consistent
native backup mirrored to the other worker; its `restore-check --source PATH`
works even when that non-owner has no local backup root.

This verifies planned relocation while both workers and the three-manager
control plane are available. It does not make single-replica VM storage an
automatic high-availability database. Recovery after losing a worker still
requires choosing the newest verified native backup, restoring into stopped
storage, setting the rightful ownership and moving the pin through an
authorized recovery procedure. Do not remove logging's existing guard while
legacy Loki drivers and Influx producers still depend on its current host.
