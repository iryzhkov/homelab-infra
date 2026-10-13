# Observability rollout baseline (2026-10-13)

Source baseline: ce8bdf60ef4287f4d8200bca955c6ef3cb49412d. Plan:
jocasta:570fac08e15ae215442fa3085f316234@8.

Igor authorized interactive execution on 2026-10-13, without Steward campaigns.
New alert rules and notification routing are excluded. Existing Gatus ownership
remains. Preserve Influx history; do not delete a bucket to complete the rollout.

Baseline observations from the laptop controller:

- All 31 Swarm stacks: check mode, changed=0, failures=0.
- Logging stack: check mode, changed=0, failures=0.
- Both Traefik proxies: check mode, changed=0, 51 enabled routers each.
- Base role: no application configuration drift. The Docker apt key differs on
  public-api-b, agent-a and agent-b; leave that unrelated housekeeping alone.
- Compose: application definitions and mounted configuration match. Check mode
  reports directory modes and outdated drift checker/reporting scripts on
  homelab, Normandy and public-api. Do not redeploy unrelated stacks for this.
- Normandy was autolocked after its Docker restart, Down/Unreachable in Swarm.
  Existing playbooks/swarm.yml --tags nodes -l normandy restored it with exactly
  one changed task (unlock). All three managers now Ready; Hugo restored to 2/2.
- Grafana was repeatedly OOM-killed before this session, at its 1 GiB cgroup
  limit. Kernel messages include 00:32, 00:47, 01:22, 01:38 and 01:50 PDT.
  A consistent SQLite snapshot was made before repair at
  /srv/swarm/logging/grafana/grafana.pre-observability-20261013.db; integrity=ok.

Evidence: /tmp/monitoring-baseline-{site,all-stacks,logging,traefik,base,swarm-nodes}.log
on the laptop. These are private transient logs, not durable source artifacts.

## Deployment gates

Add metrics storage and collection before retiring writers. Validate real
samples and queries, ingestion authentication and path restrictions, log
redaction, restore of a consistent metrics snapshot, image availability on both
workers, and surviving-host memory. Keep logging placement restrictions until
legacy clients stop naming homelab and two real moves pass readiness and
historical-data checks. Keep all household services healthy.
