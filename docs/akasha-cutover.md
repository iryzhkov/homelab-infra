# Akasha direct cutover

Plan of record: jocasta:b264e525acae2b5fa5f4a5376254e35b@28.
Igor's 2026-10-18 decision removes the shadow/parity and seven-day observation
windows. Stop the old writers, import once, switch clients, and repair problems
after cutover. This PR creates no keys and deploys nothing.

## Release and enrollment order

1. Merge the reviewed Chaldea collector CLI change. Publish a new main-only host
   release, verify its archive/manifest independently, and pin the version and
   SHA-256 in fleet-config and homelab-infra together. host-v0.2.1 lacks the new
   collector flags. Do not point a launcher at that version.
2. Merge the reviewed homelab-cli change and pin its full commit in
   fleet-config agent_tools_sources. Save each installed current symlink target
   and public feed config before updating. Keep the old immutable tool release,
   old shared-key files, database and shipper offsets for rollback.
3. Approve and complete the issuer enrollment below. Update the signed public
   ceilings, let Normandy publish, and verify the installed bundle and healthy
   clock on each issuer and on homelab. A Git merge alone does not enroll a key.
4. Deploy and verify ingress. Then cut over the hosts in the order below.
   Do not auto-merge a fleet activation/pin change before deployment approval:
   the pull timer follows main. Source PR acceptance is separate from rollout.

## Ingress deployment

Akasha's executable rejects a non-loopback bind. Keep 127.0.0.1:1935 and the
existing health/backup clients. The new systemd socket listens only on
192.168.70.223:1936 and forwards to loopback. Both socket and service have
IPAddressDeny=any; only 192.168.20.33 and 192.168.20.4 can use the listener.
The service may additionally connect to 127.0.0.1.

The file-provider route uses the existing LAN wildcard DNS, websecure entrypoint
and letencrypt resolver/wildcard certificate. There was no shared IP allowlist
middleware in the repository. akasha-lan explicitly admits the existing management,
service, client and agent VLANs 20/70/90/130, using the connection peer rather
than X-Forwarded-For. It excludes Nebula, guests and the internet. Add no
Cloudflare Tunnel hostname, public DNS record or router forwarding.

Only GET /healthz, /api/entries, numeric /api/entries/ID, /api/v1/events,
and /api/v1/receipts, plus POST /api/entries and /api/v1/events are routed.
No HTML, operator diagnostics, debug, metrics, arbitrary methods or path prefixes.
A 256 KiB request cap preserves feed's compatibility limit; Akasha's v1 API
still caps event bodies at 64 KiB. A per-peer rate of 20/s with burst 100 precede Akasha's
existing source rate and concurrency limits. The rate is per edge instance.
Akasha verifies every API bearer itself. /healthz remains the existing public
liveness endpoint within the LAN allowlist; sending a bearer there does not prove
that token valid. An authenticated API read below proves authentication.

From the pinned reviewed infra checkout, after rollout approval:

~~~sh
ansible-playbook playbooks/akasha-ingress.yml --limit homelab --check --diff
ansible-playbook playbooks/akasha-ingress.yml --limit homelab
ansible-playbook playbooks/akasha-ingress.yml --limit homelab --check --diff
# Require changed=0 before touching the edge configuration.
~~~

Before exposing the route, verify systemd's BPF firewall support and the effective
network restriction. systemd IPAddressDeny is ineffective on unsupported kernels.
Inspect the proxy socket/service journal for BPF failures. From an edge, HTTP to
192.168.70.223:1936/api/entries must return 401; from omarchy-pc or an agent it
must refuse or time out. If a non-edge reaches the proxy, stop/disable
akasha-ingress.socket and akasha-ingress.service and fix the restriction first.
The UDM must permit only both edges to homelab TCP 1936. Existing backend
policies may require that additional port; do not widen an entire VLAN.

After the backend restrictions pass, expose the route:

~~~sh
ansible edge_proxy -m ansible.builtin.uri \
  -a 'url=http://192.168.70.223:1936/api/entries status_code=401'
# On omarchy-pc, which is not an allowed proxy peer:
if curl --silent --show-error --connect-timeout 2 --max-time 3 \
    http://192.168.70.223:1936/healthz >/dev/null; then
  ansible homelab -b -m ansible.builtin.command \
    -a 'systemctl disable --now akasha-ingress.socket'
  ansible homelab -b -m ansible.builtin.command \
    -a 'systemctl stop akasha-ingress.service'
  echo 'Refusing route exposure: a non-edge reached the backend' >&2
  exit 1
fi

ansible-playbook playbooks/traefik.yml --limit edge_proxy --check --diff
ansible-playbook playbooks/traefik.yml --limit edge_proxy
ansible-playbook playbooks/traefik.yml --limit edge_proxy --check --diff
# Require changed=0 on both edge hosts; every loaded router must be enabled.
~~~

Run this on each enrolled LAN reader. The bearer travels in curl's private
configuration input, not argv, history, transcript, URL or a persistent token file:

~~~sh
set +x
test "$(curl -sS -o /dev/null -w '%{http_code}' https://akasha.ryzhkov.dev/api/entries)" = 401
(
  set -eu
  token=$(/usr/local/bin/chaldea-token-helper --audience akasha --action read \
    --source fleet-reader --host "$(hostname -s)" --kind feed_post)
  printf 'header = "Authorization: Bearer %s"\n' "$token" |
    curl --config - --fail --silent --show-error https://akasha.ryzhkov.dev/healthz
  printf 'header = "Authorization: Bearer %s"\n' "$token" |
    curl --config - --fail --silent --show-error \
      'https://akasha.ryzhkov.dev/api/entries?since=7d&limit=1' >/dev/null
)
~~~

Repeat with --resolve akasha.ryzhkov.dev:443:192.168.20.33 and
--resolve akasha.ryzhkov.dev:443:192.168.20.4 to exercise each edge.
From a Nebula peer, use its edge mesh address with --resolve and require 403
even with X-Forwarded-For: 192.168.90.212. From a genuine off-LAN source without
mesh, the LAN VIP is unroutable. Record that refusal, or record "not testable";
do not count a spoofed forwarded header alone as an off-LAN test.
The disposable ingress test exercises real Traefik path/method selection,
401, authenticated health, 413, 429 and peer-IP 403 with forged forwarded headers.

Reference for socket/service filter semantics and the unsupported-kernel caveat:
[systemd resource control](https://www.freedesktop.org/software/systemd/man/latest/systemd.resource-control.html#IPAddressAllow=ADDRESS%5B/PREFIXLENGTH%5D%E2%80%A6).

## Producers and their switch

Repository inventory was checked in fresh homelab-infra, homelab-cli, Chaldea,
fleet-config and omarchy-setup clones. Read-only live timer checks were made on
homelab, Normandy, agent-a, agent-b, omarchy-pc and laptop on 2026-10-18.
This thread's T3 scheduler lists no tasks; that is not an inventory of other
projects or other hosts. A read-only feedd aggregate at about 02:59Z found
518 rows, 162 within seven days and a largest body of 12,587 bytes. Recent
sources were drift-check, offsite, swarm, backup, chaldea-fleet,
chaldea-retention, chaldea-quota, claude-gaming-pc and igor. The last two and
swarm are interactive feed identities; timer sources change to HOST.fleet.
No titles or bodies were fetched. Export each host's full native T3 schedules before
cutover and reconcile the job names below. No remote T3 schedule was edited.

| Producer | Where and trigger | Switch |
| --- | --- | --- |
| Interactive agents' feed post, feed recent and show | Five named producer hosts, plus Normandy if still used interactively | New CLI, HTTPS URL, helper, exact source/host append grants and fleet-reader read grant. Read filters never change mint identity. Scoped open/url still refuses. |
| chaldea-fleet-pull and chaldea-retention, including OnFailure | All four agent hosts, systemd user timers | Enable existing akasha_collector; timer-reporting.json becomes delivery=queue. Collector emits HOST.fleet/job_finished. This avoids the old timer wrapper's key-file prerequisite. |
| Quota park/resume, switch failures, headroom and restart recovery | Active quota/restart services, currently omarchy-pc; any later enabled host | Configure events.json HTTPS/tokenHelper before restart. Existing source chaldea-quota uses the service publisher/outbox, never shared-key fallback. |
| Shared quota snapshots/readers | Where quota fleet is enabled | Source quota, profile quota-service, kind quota_headroom_snapshot, append/read; registry quota_fleet_read=true. Keep disabled until enrolled. |
| Routing score snapshots and pick changes | Approved FATE producer on homelab and announceHost, readers on agent hosts | Source route, existing route-service profile and exact kind selectors. Coordinate its separately owned activation/pins, do not overwrite its registry entries. |
| Tool instrumentation | Huyang, OV MCP and homelab-cli spool writers on every producing host; retained agent99/backfill input | Keep host-local spooling; one collector emits HOST.toolfeedback/tool_call. toolfeedback ship runs the same locked collector; report/events default to Akasha. windows/backfill/transcript inspection remain local. |
| toolfeedback-ship.timer | Live on homelab, Normandy, omarchy-pc and laptop; absent on agent-a/b at inspection | Stop/disable timer and oneshot before switching. Enable akasha-collector.timer only after grants and launcher are installed. Preserve .shipped.json and raw spool. |
| Feed shipper, if any older unit exists | No separate feed shipper unit found on homelab; generic name in old plan | Inventory *feed*/*ship* units, cron and native T3 jobs; stop/disable the discovered unit and record its name. Do not invent a service name or leave an old writer active. |
| Weekly docker-update and delegated docker-update-agent | homelab-cli docker-update-sweep and jobs/docker-update; scheduled job host must be confirmed | Same feed CLI, sources docker-update and docker-update-agent. Preserve original job flags; approve those exact caller-UID grants. |
| Weekly ov-memory-curation and tool-feedback-digest | Declared jobs formerly on Normandy; native scheduler export must confirm current host | Sources ov-memory-curation and toolfeedback. Digest prompt now reads Akasha and uses the shared collector. Replace already-copied prompts in native schedules; a repository edit does not update stored prompts. |
| t3-job launch notices and speed-audit | Caller host when invoked; source is job name or default feed | Enroll exact names docker-update, ov-memory-curation, tool-feedback-digest and feed. Unknown new source names fail closed until explicitly granted. |
| Drift reports | homelab and Normandy user timers; homelab also reports peers without feed access | roles/drift_check now installs HTTPS/helper config and never reloads or redistributes the shared feed key. Source drift-check. Keep delegated peer attribution in report body; token host is the reporting host. |
| Backup failures and restore verification | homelab backup hourly/daily/weekly/verify; Normandy hourly and native backup jobs where they call the wrappers | Existing homelab-backup-run, bulk backup, NAS/PG dump wrappers keep source backup and use the new CLI/helper. Retain independent phone/Discord alerts. |
| Offsite backup freshness/failure | homelab-offsite.timer and homelab-offsite-run | Source offsite through new CLI/helper. Akasha offsite/health/snapshot timers keep Gatus heartbeat alerts. |
| Normandy trust publisher renewal/age/failure | chaldea-publisher.timer and chaldea-publisher-age.timer under locked publisher UID | Remove the obsolete optional feed subprocess. Keep journal plus the independent authenticated Gatus heartbeat/Discord route. A trust failure must not require a valid Akasha bearer to alert. No publisher issuer key or new sudo grant. |
| Retention | Akasha hourly maintenance; collector run; fleet retention timer | Akasha retains feed summaries 180d and tool calls 90d. Collector prunes host input at 90d and counts expired unshipped input. Existing fleet cleanup remains; retained rollback data is excluded from deletion until a separate cleanup decision. |

Normandy still has live backup, drift and toolfeedback timers. Its actual hostname
is omarchy-normandy. Retaining those producers adds a sixth issuer enrollment,
even though the request named five hosts. Alternatively the orchestrator must
retire those timers or move their reporting before declaring the producer list
complete. The trust publisher itself needs neither a sixth issuer nor Akasha.

## Credential proposal for Igor

Create a NEW Ed25519 issuer signing key on each producing host. Do not copy the
agent-a key or the deployment anchor. Even agent-a gets a new cutover key.
Keep its old agent-a-2026-10 issuer record during overlap, then separately retire it.
The files below are credential references, not credentials.

| Host alias | Token host / issuer | New kid and private key basename | Producer account |
| --- | --- | --- | --- |
| agent-a | agent-a / chaldea:agent-a | agent-a-2026-10-cutover.pem | igor |
| agent-b | agent-b / chaldea:agent-b | agent-b-2026-10-cutover.pem | igor |
| omarchy-pc | omarchy-pc / chaldea:omarchy-pc | omarchy-pc-2026-10-cutover.pem | igor |
| laptop | omarchy-macbook-air-igor / chaldea:omarchy-macbook-air-igor | omarchy-macbook-air-igor-2026-10-cutover.pem | igor |
| homelab | homelab / chaldea:homelab | homelab-2026-10-cutover.pem | iryzhkov |
| Normandy, if retained | omarchy-normandy / chaldea:omarchy-normandy | omarchy-normandy-2026-10-cutover.pem | igor |

For EACH host the orchestrator creates and records:

- Locked chaldea-issuer Unix identity with an unused local UID, no login and no
  producer group membership. Retain agent-a uid 995; verify UID availability on
  the others rather than assuming 995 is free.
- Private /var/lib/chaldea-issuer/keys/KID.pem, issuer-owned 0600 in 0700 directories,
  and an initialized /var/lib/chaldea-issuer/access/state.sqlite owned by that UID.
  Use the independently verified release's access initialize-state as that UID;
  never initialize an existing database or reset its high-water marks.
- One public record in fleet access-trust/issuer-grants.json with issuer, unique kid,
  exported public_key {kty:OKP,crv:Ed25519,x:PUBLIC_X}, not_before, issue_until and
  verify_until. Approve actual Unix-second timestamps. Keep issue lifetime bounded
  to the approved enrollment interval; verify_until must cover the final 780s
  token plus the existing 30s skew rule. No private key goes into Git or Jocasta.
- Ceilings and registry entries in the matrix below, only for producers present
  on that host. Every ceiling has audience akasha, exact profile/project/source,
  and grants [{resource:SOURCE,actions:[APPROVED_ACTIONS]}]. No wildcard resource.
- Root-owned /etc/chaldea/token-helper.json with this UID/key/name/kid/state,
  signed bundle/anchor/clock paths and exact producer-UID grants for all five
  helper flag pairs. Confirm the actual producer UID using getent/id. Sudoers
  grants only the fixed issuer wrapper with those exact arguments, NOSETENV.
  Timer/backup service UIDs get append only. Global reader grants are only for
  approved interactive operator UIDs. Restricted scheduled runs still use
  RunLeases; do not give model profiles unrestricted issuer sudo access.
- Matching Akasha registry entry per distinct issuer/source/host, unique source_id,
  exact profile/project, mode and allowed kinds. For the fixed sources below,
  use source_id HOST--SOURCE with dots changed to underscores, such as
  agent-a--agent-a_toolfeedback and homelab--backup. Preserve existing IDs.
  Update vars/akasha-registry.yml
  only after public-key/ceiling approval, preserving other lanes' registrations.
  Source registration grants nothing without a valid signed ceiling.
- Trust updater and clock-health installation/enabling. Reuse fleet roles
  chaldea_access_trust and chaldea_token_helper on agent hosts. Homelab and
  Normandy stay owned by homelab-infra: port the same verified wrapper/config
  contract there through a separately reviewed enrollment change, not fleet site.yml.
- Install the collector artifact, launcher and user timer under each producer
  account. Homelab/Normandy require the same single-owner lock/database paths.
  No collector or helper is installed by these source PRs.

| Source | Profile / project | Mode / kinds | Ceiling actions and local selectors |
| --- | --- | --- | --- |
| feed and each exact legacy source used locally: backup, offsite, drift-check, docker-update, docker-update-agent, ov-memory-curation, toolfeedback, tool-feedback-digest, swarm, claude-gaming-pc, igor | feed-writer / chaldea | service / feed_post | append; one helper grant per source, kind feed_post |
| HOST.fleet, four agent hosts | fleet-timers / chaldea | service / job_finished | append; kind job_finished |
| HOST.toolfeedback, every instrumentation host | toolfeedback / chaldea | service / tool_call | append; kind tool_call |
| fleet-reader | fleet-reader / chaldea | operator / feed_post, tool_call; global_read=true | read only; separate helper selectors feed_post and tool_call |
| chaldea-quota, hosts actually running quota/restart | quota-service / chaldea | service / quota_parked, quota_reset_unknown, quota_resumed, provider_switched, provider_switch_failed, restart_resumed, quota_headroom_snapshot, quota_headroom_changed, quota_headroom_low | append; exact selector for each enabled kind |
| quota, approved fleet capacity hosts | quota-service / chaldea | service / quota_headroom_snapshot; quota_fleet_read=true | append and read, separate helper grants |
| route, coordinated with FATE lane | existing approved profile/project | preserve its route_score_snapshot/route_pick_changed and route_scores_read policy | only its approved append/read grants |
| chaldea.daily-health.agent-a, existing scheduled lease | daily-health / homelab | report / report | preserve existing append ceiling; do not convert to helper sudo |

This explicitly authorizes the global reader to see imported legacy-feed history
and other hosts' feed/tool_call events. A source-bound service reader cannot read
legacy import rows or a fleet aggregate. Source/host filters never widen a grant.
If global read is not approved, retain source isolation and report that fleet
recent/history and aggregate toolfeedback are unavailable to that UID.

## Cutover commands

After enrollment and the reviewed artifact/CLI pins, prepare each host's public
configuration. Use its actual hostname, including the laptop's long name:

~~~sh
# In each producing account; back up the old file first without printing it.
install -d -m 700 "$HOME/.config/feed"
test ! -e "$HOME/.config/feed/config.before-akasha"
if test -e "$HOME/.config/feed/config"; then
  cp -p "$HOME/.config/feed/config" "$HOME/.config/feed/config.before-akasha"
fi
printf '%s\n' 'FEED_URL=https://akasha.ryzhkov.dev' \
  'FEED_TOKEN_HELPER=/usr/local/bin/chaldea-token-helper' \
  'FEED_READ_SOURCE=fleet-reader' >"$HOME/.config/feed/config"
chmod 600 "$HOME/.config/feed/config"
# Remove inherited FEED_TOKEN from service environments and agent launchers.
# Preserve the old key file. A helper failure must never fall back to it.
~~~

For each agent host, from its pinned fleet checkout with approved inventory pins
and explicit issuer/grant settings, run one host at a time:

~~~sh
ansible-playbook -i inventory site.yml --limit "$(hostname -s)" \
  --tags chaldea_access_trust,chaldea_token_helper --check --diff
ansible-playbook -i inventory site.yml --limit "$(hostname -s)" \
  --tags chaldea_access_trust,chaldea_token_helper
ansible-playbook -i inventory site.yml --limit "$(hostname -s)" \
  --tags chaldea_access_trust,chaldea_token_helper --check --diff
~~~

The laptop inventory alias is laptop, so replace the --limit value there.
Use the approved per-host vars that turn on the helper/trust roles; this command
does not create keys/state. Require changed=0 on the final check.
Collector activation uses akasha_collector_enabled=true,
akasha_collector_url=https://akasha.ryzhkov.dev and
akasha_collector_token_helper=/usr/local/bin/chaldea-token-helper. Reconcile those
in a separate reviewed fleet activation change, with pinned CLI/host artifacts.
Run check, real, second check on that host; verify changed=0. Configure events.json
for quota/restart services before restarting them, preserving unrelated fields.

Freeze old writes and take the final consistent snapshot on homelab. Pause
scheduled feed writers and drain their current invocation first. The outage
is intentional; do not dual-write.

~~~sh
# As iryzhkov on homelab. feedd is the actual live user unit.
systemctl --user disable --now feedd.service
install -d -m 700 "$HOME/.local/share/feedd/akasha-cutover"
python3 - <<'PY'
import os, pathlib, sqlite3
root = pathlib.Path.home() / ".local/share/feedd"
snapshot = root / "akasha-cutover/feed.db"
assert not snapshot.exists(), "never overwrite an earlier rollback snapshot"
source = sqlite3.connect((root / "feed.db").as_uri() + "?mode=ro", uri=True)
target = sqlite3.connect(snapshot)
source.backup(target)
assert target.execute("PRAGMA integrity_check").fetchone() == ("ok",)
target.close(); source.close(); os.chmod(snapshot, 0o600)
PY
~~~

Confirm FEED_DB was not overridden in the installed unit before using that path.
Copy the snapshot, with operator privileges, into a 0700 directory readable by
akasha, such as /var/lib/akasha/cutover/feed.db, mode 0600 owner akasha.
Stop the Akasha consumer and maintenance oneshots during the local import;
keep independent trust/clock updater timers running. On homelab as root:

~~~sh
systemctl stop akasha-health.timer akasha-backup.timer akasha-offsite.timer
systemctl stop akasha-health.service akasha-backup.service akasha-offsite.service
systemctl stop akasha.service
~~~

As akasha, using the pinned Node and service artifact, run:

~~~sh
# Define these from the reviewed installed artifact, not an arbitrary checkout.
: "${AKASHA_NODE:?pinned Node}" "${AKASHA_SERVICE:?pinned akasha-service.mjs}"
set -eu
for batch in $(seq 1 10000); do
  result=$("$AKASHA_NODE" "$AKASHA_SERVICE" import --config /etc/akasha/config.json \
    --snapshot /var/lib/akasha/cutover/feed.db)
  printf '%s\n' "$result"
  test "$(printf '%s' "$result" | python3 -c 'import json,sys; print(json.load(sys.stdin)["rejected"])')" = 0
  scanned=$(printf '%s' "$result" | python3 -c 'import json,sys; print(json.load(sys.stdin)["scanned"])')
  if test "$scanned" -lt 100; then break; fi
  test "$batch" -lt 10000
done
"$AKASHA_NODE" "$AKASHA_SERVICE" parity --config /etc/akasha/config.json \
  --snapshot /var/lib/akasha/cutover/feed.db
~~~

This is one import-integrity comparison, not a shadow operation window. The
existing importer preserves IDs, display host/source, created_at and 180-day
history, checkpoints every row and safely resumes after interruption. Investigate
any rejection rather than silently ignoring history. New IDs start above the old
range. The source snapshot is opened read-only. Retain it and the original DB.

Restart Akasha and the existing maintenance schedules as root on homelab:

~~~sh
systemctl start akasha.service
systemctl start akasha-backup.service
systemctl start akasha-health.timer akasha-backup.timer akasha-offsite.timer
systemctl start akasha-health.service
~~~

Then switch agent-a first, followed immediately by agent-b, omarchy-pc, laptop and
homelab. Include Normandy if its producers remain. On each producer account:

~~~sh
systemctl --user disable --now toolfeedback-ship.timer
systemctl --user stop toolfeedback-ship.service
# Absent old units on agent-a/b are expected; inspect before running these.
systemctl --user enable --now akasha-collector.timer
toolfeedback ship --dry-run
toolfeedback ship
feed recent --since 7d --json
feed post "Akasha cutover verified" --source feed --host "$(hostname -s)"
toolfeedback report --since 7d --json
~~~

Require a known imported seven-day entry via feed recent under fleet-reader,
a committed post and a centrally visible host tool_call. Check collector counters
for rejected/foreign_host/expired_unshipped/deferred input. Imported or pulled
foreign-host toolfeedback spools must be shipped on their originating host;
the collector intentionally refuses forged host attribution. Preserve historical
Loki and pulled spools for reference, without continuing their writer path.

Stop/disable any discovered separate feed shipper and copied old scheduled
toolfeedback ship/Loki commands. The new command reuses one collector, so an
accidentally retained trigger cannot upload to Loki. Reload stored digest prompts.
Deploy the publisher/drift source changes through their normal check/real/check
plays, requiring changed=0. Keep the independent Gatus/Discord and phone paths
active. A failure on one producer does not justify restarting feedd or dual-writing
elsewhere; repair its enrollment/config and retained queue.

## Retirement and rollback

Stop and disable feedd.service and old shippers immediately as above. Retain
~/.local/share/feedd, ~/.config/feedd/key, client key files, raw toolfeedback spool,
.shipped.json, the Loki data and old tool release. Do not delete the general Loki
logging stack, disable backups or reset access-state as part of this cutover.
No fixed seven-day observation is required. Data cleanup is a later explicit
decision.

For rollback, first pause new producers, stop the collector timers, and stop
Akasha with its consumer/maintenance units drained. Export new feed rows with the
existing Akasha exporter to a NEW private file. The exporter contains only new
IDs, so merging it into the retained old DB is necessary to keep both histories.

~~~sh
# As akasha, with the same pinned AKASHA_NODE / AKASHA_SERVICE.
"$AKASHA_NODE" "$AKASHA_SERVICE" export --config /etc/akasha/config.json \
  --output /var/lib/akasha/cutover/new-feed.db
~~~

Copy that export into the retained feedd private cutover directory as iryzhkov,
then, while both writers remain stopped:

~~~sh
python3 - <<'PY'
import pathlib, sqlite3
root = pathlib.Path.home() / ".local/share/feedd"
source = sqlite3.connect((root / "akasha-cutover/new-feed.db").as_uri() + "?mode=ro", uri=True)
target = sqlite3.connect(root / "feed.db")
with target:
    for row in source.execute("SELECT id,created_at,host,source,severity,title,body,tags,link FROM entries"):
        old = target.execute("SELECT id,created_at,host,source,severity,title,body,tags,link FROM entries WHERE id=?", (row[0],)).fetchone()
        assert old is None or old == row, "rollback ID collision"
        if old is None:
            target.execute("INSERT INTO entries VALUES(?,?,?,?,?,?,?,?,?)", row)
assert target.execute("PRAGMA integrity_check").fetchone() == ("ok",)
target.close(); source.close()
PY
systemctl --user enable --now feedd.service
~~~

Restore each account's saved config and old immutable CLI release, then explicitly
set FEED_URL=http://192.168.70.223:1934 and FEED_TOKEN_HELPER='' with its retained
key file. Restore TOOLFEEDBACK_BACKEND=local or explicit --loki for archived
reports. Only after old binaries/config are restored may toolfeedback-ship.timer
be enabled again. Preserve new Akasha/collector state and tool spools for replay.
Stop/disable akasha-ingress.socket and akasha-ingress.service if reverting ingress;
do not change NATS accounts, trust ceilings or receipt/high-water state to roll back.
