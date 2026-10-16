# Chaldea infrastructure foundation

The self-hosted pieces the Chaldea rebuild needs from this repository: NATS
accounts, users and JetStream streams for work queues, Akasha events and quota
state; an S3-compatible object store for results and artifacts; and the log
store. Decisions 6 and 11 of the Chaldea rebuild plan
(`jocasta:b264e525acae2b5fa5f4a5376254e35b@3`,
tooling/plans/chaldea-rebuild-plan-2026-10-16.md) ask for them. Everything
is one node on homelab, with no clustering and no HA.

One requirement shapes all of it. The old platform never cleaned up after
itself, so every stream, bucket and log store here has an explicit bound in
size or age, and the bound is enforced by the server rather than by the
clients' good behaviour. `scripts/test-chaldea-retention.py` checks the
definitions in CI.

Nothing in this change has been deployed. The deploy steps are below.

## Current state found (2026-10-16, read-only)

NATS 2.15.1 is the Swarm stack `nats` on homelab, from commit 8093a79
(docs/nats-jetstream.md). "Standalone" in the plan means one unclustered
node, not integrated with the retired Steward, rather than a host package.

- Service `nats_nats` is 1/1 and healthy, image
  `nats:2.15.1-alpine@sha256:9e47060a…`, pinned by `node.hostname == homelab`
  and `pin.nats`.
- The state is `/srv/swarm/nats/data` on the ext4 pool disk: 20 KB, holding 0
  streams, 0 consumers and 1 account (`/jsz`).
- Only the WebSocket port 9222 is published, in host mode. The
  `NATS-EDGE` chain allows it from the two edges only, and both edges route
  `wss://nats.ryzhkov.dev` to it. 4222 and 8222 are not published.
- Authentication was a single `bootstrap-admin` user with a bcrypt password
  in the global account. The whole configuration was the Docker secret
  `nats_config_0e071732d2cd`, from `secrets/nats.sops.yaml`.
- Server-wide JetStream caps are 128 MB of memory and 4 GB of file store.

There was no object store anywhere in the fleet.

Logging is the Swarm stack `logging` on homelab: Loki 3.7.8 and Grafana
13.2.2.

- Loki keeps 30 days: `retention_period: 30d`, enforced by the compactor
  (`retention_enabled: true`).
- Retention works. The chunks directory holds 986 MB, the only file older
  than 31 days is a 250-byte one, and the compactor's marker loop runs every
  minute.
- Alloy ships logs with the labels `job` (journal, docker, toolfeedback,
  syslog), `host`, `unit` for journal entries, and `stack`, `service` and
  `container` for Docker entries.

The pool disk `/mnt/docker-data` is 295 GB, with 225 GB free. normandy is
not involved: nothing in this change runs there, although the routing mesh
also publishes the object store's port on it, as it does for every Swarm
port.

## NATS

### Authentication: configuration accounts with NKey users

The choice was between decentralized JWT authentication (an operator, nsc,
and a resolver) and accounts written in the server configuration. This
change uses accounts in the configuration, with every user authenticating by
NKey, for these reasons:

- There are seven identities on one server. JWT's strengths (delegated
  account administration, many servers, revocation without touching the
  server) buy nothing at this size.
- An NKey user is a public key in the configuration, and the seed never
  leaves its machine. The configuration therefore holds no secret: it is
  plain text in this repository, reviewable in a pull request, and
  `secrets/nats.sops.yaml` is gone.
- JWT would add an operator key and an account signing key to protect and
  back up, plus a resolver, which is exactly the kind of accreted state the
  rebuild exists to avoid.
- Revocation fails closed. Removing a key and redeploying restarts the
  broker, which drops every connection, and the removed key cannot
  reconnect. A restart costs a few seconds, and durable consumers survive
  it (verified in docs/nats-jetstream.md).

If the fleet outgrows this, or users must be added or revoked without a
restart, the move to JWT changes the server configuration and the client
credentials, not the subjects or streams.

### Accounts and users

`stacks/nats/templates/nats.conf.j2` renders from
`inventory/group_vars/swarm/nats.yml`:

| Identity | Account | May do |
|---|---|---|
| `sys-admin` | SYS | Server monitoring (`$SYS`). Operator only. |
| `chaldea-admin` | CHALDEA | Everything in CHALDEA; creates streams and consumers. Operator only. |
| `akasha` | CHALDEA | Pull from `AKASHA_EVENTS` through consumer `akasha-ingest`, read single events, and publish `akasha.akasha.>`, `akasha.feed.>` and `akasha.toolfeedback.>`, the last two being the bridges during the dual-write migration. |
| `agent-a`, `agent-b`, `omarchy-pc`, `laptop` | CHALDEA | Publish work to `chaldea.work.fleet.>` and to `chaldea.work.host.*.>`. Pull from consumer `fleet` and from its own `host-<name>`. Write `$KV.CHALDEA_QUOTA.<name>.>` and read or watch every quota key. Publish `akasha.<name>.>`. |

Every non-admin user may subscribe only to its own inbox prefix,
`_INBOX_<name>.>`, so no client can read another client's replies. A client
must set that prefix (`inboxPrefix` in nats.js, `--inbox-prefix` in the CLI).

Akasha's `<source>` token is the publishing identity. A host can publish
`akasha.agent-a.chaldea.run_started` but not `akasha.agent-b.…`, so the
source of an event is no longer self-declared, as the feed's `--source` is
today (audit, 2026-10-16). What happened goes in the remaining tokens and in
the payload.

The CHALDEA account's JetStream limits are 2 GB of file storage, 16 MB of
memory, 8 streams and 64 consumers. `max_bytes_required: true` makes the
server refuse any stream without `max_bytes`, and
`store_max_stream_bytes: 1GB` caps each stream, so even a stream created by
hand is bounded. The SYS account has no JetStream.

The permissions were exercised against nats-server 2.15.1 on a local scratch
server. Each allowed operation above succeeded. Each of the following was
refused: a host publishing another host's Akasha subject, a host pulling
another host's queue, a host writing another host's quota key, a host
creating a stream, `akasha` publishing a host's subject, and a host
subscribing to `akasha.>` directly.

### Streams and consumers, as code

The streams live in `stacks/nats/jetstream/streams/*.json` and the consumers
in `stacks/nats/jetstream/consumers/<STREAM>/*.json`.
`scripts/nats-jetstream-apply.sh` converges the server to them: `--check`
prints what is missing or different, and a real run creates or edits. A
second run changes nothing. It never deletes.

| Stream | Retention | Bounds |
|---|---|---|
| `CHALDEA_WORK` (`chaldea.work.fleet.>`, `chaldea.work.host.*.>`) | work queue: a message is removed when acknowledged | 7 days, 256 MiB, 10 000 messages, 256 KiB per message; full means `discard: new`, so a publish fails rather than queued work being dropped |
| `AKASHA_EVENTS` (`akasha.>`) | limits | 14 days or 1 GiB, oldest dropped first, 64 KiB per event; a replay buffer for the Akasha store, not the store |
| `KV_CHALDEA_QUOTA`, the key-value bucket `CHALDEA_QUOTA` | limits | 5 revisions per key, a key not written for 7 days expires, 64 MiB, 64 KiB per value |

The consumers:

- `fleet` serves work any host may take.
- `host-<name>` serves work addressed to one host.
- `akasha-ingest` is Akasha's.

All are pull consumers with explicit acknowledgement and bounded redelivery.
Work has `ack_wait` 5 minutes and `max_deliver` 5, so a client on a long task
sends progress acknowledgements. Akasha has `max_deliver` 10, and an event it
keeps failing stays readable by sequence until the stream's limits drop it.
Publishers should set `Nats-Msg-Id`. The duplicate windows (15 minutes for
work, 10 minutes for events) then turn a retried publish into one message.

The server's 4 GB file and 128 MB memory caps are unchanged, as are the
port, the firewall and the edge routes.

## Object store: Garage

For a single-node homelab the candidates were these:

- **NATS Object Store** has no S3 API, so no standard tool or SDK speaks to
  it. It would share the 4 GB JetStream cap and the broker's 512 MB memory
  limit with the work queue, coupling artifact volume to queue health.
- **MinIO**: upstream stopped publishing community binaries and images and
  put the community edition into maintenance in late 2025, after removing
  most of the console. A new deployment would start on an unmaintained
  fork.
- **Garage** is one static binary built for small self-hosted clusters. It
  provides the S3 API, per-bucket quotas on size and object count, and
  lifecycle rules for expiry and abandoned multipart uploads. It runs in
  under 512 MB, and v2.4.1 is current.

Garage it is, as the Swarm stack `objectstore`. The name is not `garage`
because `garage.ryzhkov.dev` is LubeLogger. It follows the repository's
stateful-stack pattern:

- It is pinned to homelab by `pin.objectstore`, after the same
  ext4-mount check NATS makes.
- State is under `/srv/swarm/objectstore/{meta,data}`, owned by 1000:1000
  with mode 0700.
- `config/garage.toml` comes from `stacks/objectstore/files`, and its hash
  is in the service environment, so a change redeploys.
- The only secret is the RPC secret, a Docker secret from
  `secrets/objectstore.sops.yaml`.
- S3 is on port 3900 through the routing mesh, routed as `s3.ryzhkov.dev`
  with Gatus probes generated from the `routes` entry. The route has no HTTP
  health check because an unsigned request is answered 403, which Traefik
  counts as down. Gatus probes the port instead.
- RPC and the admin API listen only on the container's loopback. There is no
  admin token, and administration goes through the `garage` CLI inside the
  container.

Buckets, quotas, keys and lifecycle rules are cluster state, not
configuration. `playbooks/garage-bootstrap.yml` runs
`scripts/garage-bootstrap.py` on homelab to converge them to
`stacks/objectstore/bootstrap.json`:

| Bucket | Quota | Lifecycle |
|---|---|---|
| `chaldea-results` | 10 GiB, 200 000 objects | expire after 90 days; abort uploads after 1 day |
| `chaldea-artifacts` | 50 GiB, 100 000 objects | expire after 14 days; abort uploads after 1 day |

The keys are these:

- `chaldea-ops` has read, write and owner on both buckets. The bootstrap
  uses it to set lifecycle rules, which exist only in the S3 API.
- `chaldea-<host>` has read and write on both buckets, one for each of
  agent-a, agent-b, omarchy-pc and laptop.

The layout gives the node 100 GB of capacity, more than the 60 GiB sum of
the quotas, so the quotas are what bind. The bootstrap never deletes, and it
reports keys and buckets it does not manage. No key secret is printed or
written anywhere: a created key's secret stays in Garage until a client is
given it (see "Secrets").

The bootstrap was run against Garage v2.4.1 in a local scratch container
with this repository's garage.toml, the read-only root filesystem and UID
1000. The run went from a fresh node to converged in one pass, and the
second pass reported no actions. A quota changed by hand and an extra owner
permission were both put back. A client key could write objects and was
refused CORS changes.

That test also found a limit worth knowing. In Garage v2.4.1 a key with only
write permission can replace or delete a bucket's lifecycle rule. A client
cannot change a quota, so the hard bound holds whatever a client does, but
a buggy client could stop the pruning; writes would then fail once the bucket
reached its quota. `ansible-playbook playbooks/garage-bootstrap.yml --check`
reports such drift, and a real run restores the rule. Whether to run it on a
timer is an open question below.

The buckets are not backed up: no `snapshot` key. Results and artifacts are
bounded, expire anyway, and can be regenerated. Losing homelab's disk loses
them.

## Loki and Grafana

Nothing in the logging stack must change. Loki already has an explicit,
enforced age bound of 30 days, and the check above shows it working.
Grafana keeps dashboards, not logs. The two new services log through Docker
with size limits: NATS uses json-file, 10 MB × 3, and Garage the shared
`swarm_stack_logging` default.

The label scheme for Chaldea and Akasha uses the labels that exist and adds
none:

- A Chaldea client or Akasha running as a systemd unit is
  `{job="journal", host="<host>", unit="chaldea-….service"}`, or
  `unit="akasha….service"`.
- Akasha deployed as a Swarm stack is
  `{job="docker", stack="akasha", service="akasha_<name>"}`.
- Run, task, command, thread, provider and model identifiers never become
  labels, because each value would create a new stream. They go in the JSON
  log line and are filtered at query time:
  `{job="journal", unit=~"chaldea.*"} | json | run_id="…"`.

One gap is left for the Chaldea client's design. Alloy's journal relabelling
reads `_SYSTEMD_UNIT`, so a systemd *user* unit is labelled
`unit="user@1000.service"`, indistinguishable from every other user unit.
If the client runs as a user unit, the collector
(`roles/observability_collector`) needs a rule for
`__journal__systemd_user_unit`. That is a fleet-wide collector change, so
it is left until the client's unit type is decided.

## Retention bounds

| Store | Bound |
|---|---|
| `CHALDEA_WORK` | 7 days, 256 MiB, 10 000 messages |
| `AKASHA_EVENTS` | 14 days, 1 GiB |
| `CHALDEA_QUOTA` (KV) | 5 revisions per key, 7-day TTL, 64 MiB |
| CHALDEA account | 2 GB file, 16 MB memory, 8 streams, 64 consumers, `max_bytes` required, 1 GB per stream |
| NATS server | 4 GB file, 128 MB memory |
| `chaldea-results` | 10 GiB / 200 000 objects quota, 90-day expiry, 1-day multipart abort |
| `chaldea-artifacts` | 50 GiB / 100 000 objects quota, 14-day expiry, 1-day multipart abort |
| Loki | 30 days, compactor retention |
| Container logs | nats 10 MB × 3; objectstore `swarm_stack_logging` |

## Secrets

This list names secrets by reference only. No value appears in this
repository or in this document.

- **`secrets/objectstore.sops.yaml`** must be created before the first
  objectstore deploy. It holds one key, `GARAGE_RPC_SECRET`: 32 random bytes
  as 64 hex characters, encrypted to the recipient in `.sops.yaml`. Creating
  it without the value touching a terminal or a plaintext file (sops 3.9 or
  later):

  ```sh
  printf 'GARAGE_RPC_SECRET: %s\n' "$(openssl rand -hex 32)" |
    sops --encrypt --filename-override secrets/objectstore.sops.yaml \
         --input-type yaml --output-type yaml /dev/stdin > secrets/objectstore.sops.yaml
  ```

- **NATS seeds**, one per identity, are each generated on the machine the
  identity belongs to, as `~/.config/nats/<name>.nk`, mode 0600. The
  operator's `sys-admin` and `chaldea-admin` seeds are on omarchy-pc. The
  `akasha` seed is generated on omarchy-pc for now and moves into Akasha's
  own deployment (a sops file of its stack) when Akasha is built. Only the
  public keys go into `inventory/group_vars/swarm/nats.yml`; they are not
  secrets.
- **Garage key secrets** stay inside Garage. When step 6 of the plan gives a
  client its key, the secret is read on homelab with
  `docker exec <objectstore_garage container> /garage json-api GetKeyInfo
  '{"search":"chaldea-<host>","showSecretKey":true}'`. It is piped straight
  into that host's credential file, mode 0600, and never displayed. How that
  delivery is automated belongs to the client's design.
- **Retired by this change.** `secrets/nats.sops.yaml`, holding the
  `bootstrap-admin` bcrypt hash, is deleted from the repository. After the
  NATS deploy is verified, three more copies go:
  - the Docker secret `nats_config_0e071732d2cd`;
  - `~/swarm/nats/secrets/NATS_CONFIG` on homelab;
  - the `bootstrap-admin` password in the operator context
    `/home/igor/.cache/nats-operator/context.json` on omarchy-pc, which no
    longer authenticates.

## Deploy

Run from a controller with this repository, the Ansible virtualenv, the sops
age key and the nats CLI, which is the laptop or omarchy-pc. Each step has
its own check run first.

### 1. NATS identities

1. On omarchy-pc, generate the operator and Akasha seeds:

   ```sh
   install -d -m 0700 ~/.config/nats
   for name in sys-admin chaldea-admin akasha; do
     nats auth nkey gen user --output ~/.config/nats/$name.nk
   done
   chmod 0600 ~/.config/nats/*.nk
   ```

2. On each of agent-a, agent-b, omarchy-pc and laptop, generate its own
   seed: `nats auth nkey gen user --output ~/.config/nats/<host>.nk`, then
   `chmod 0600` it.
3. Collect every public key with `nats auth nkey show ~/.config/nats/<name>.nk`.
4. Put the public keys in `inventory/group_vars/swarm/nats.yml` and commit
   them. The deploy refuses an empty key or anything that is not a public
   user key.

### 2. Reconfigure the broker

This restarts `nats_nats`, stop-first, for a few seconds. No client uses the
broker yet.

```sh
ansible-playbook playbooks/swarm.yml --tags stacks --limit homelab -e only=nats --check --diff
ansible-playbook playbooks/swarm.yml --tags stacks --limit homelab -e only=nats
```

The prepare step validates the rendered configuration with the pinned
image's `nats-server -t` before installing it.

### 3. Streams and consumers

```sh
nats context save chaldea-admin --server wss://nats.ryzhkov.dev --nkey ~/.config/nats/chaldea-admin.nk
nats context save sys-admin --server wss://nats.ryzhkov.dev --nkey ~/.config/nats/sys-admin.nk
NATS_CONTEXT=chaldea-admin bash scripts/nats-jetstream-apply.sh --check
NATS_CONTEXT=chaldea-admin bash scripts/nats-jetstream-apply.sh
```

### 4. Object store

1. Create `secrets/objectstore.sops.yaml` as shown under "Secrets" and
   commit it.
2. Deploy the stack:

   ```sh
   ansible-playbook playbooks/swarm.yml --tags stacks --limit homelab -e only=objectstore --check --diff
   ansible-playbook playbooks/swarm.yml --tags stacks --limit homelab -e only=objectstore
   ```

3. Converge Garage's layout, buckets, keys and lifecycle rules:

   ```sh
   ansible-playbook playbooks/garage-bootstrap.yml --check
   ansible-playbook playbooks/garage-bootstrap.yml
   ```

4. Route `s3.ryzhkov.dev` on both edges. LAN DNS already resolves it through
   AdGuard's `*.ryzhkov.dev` rewrite.

   ```sh
   ansible-playbook playbooks/traefik.yml --check --diff
   ansible-playbook playbooks/traefik.yml
   ```

5. Add the Gatus probes:

   ```sh
   ansible-playbook playbooks/site.yml -l normandy -e only=gatus --check --diff
   ansible-playbook playbooks/site.yml -l normandy -e only=gatus
   ansible-playbook playbooks/site.yml -l homelab -e only=gatus
   ```

## Verification

```sh
# Broker up, accounts loaded, limits as configured
nats --context sys-admin server list
nats --context chaldea-admin account info
nats --context chaldea-admin req '$JS.API.INFO' '' --raw   # limits.max_storage 2147483648, max_bytes_required true

# Streams and consumers match the repository (prints "matches" for all nine)
NATS_CONTEXT=chaldea-admin bash scripts/nats-jetstream-apply.sh --check

# A host can use its own names and not another's (from that host)
nats --context agent-a --inbox-prefix _INBOX_agent-a pub --jetstream akasha.agent-a.test.ping '{}'   # stored
nats --context agent-a --inbox-prefix _INBOX_agent-a pub --jetstream akasha.agent-b.test.ping '{}'   # times out: denied

# The old credential no longer works: the bootstrap-admin context must fail with an authorization error

# Garage healthy and converged
ssh homelab 'docker exec $(docker ps -q -f label=com.docker.swarm.service.name=objectstore_garage) /garage health'
ansible-playbook playbooks/garage-bootstrap.yml --check      # "actions": []

# Route and probe
curl -sS -o /dev/null -w '%{http_code}\n' https://s3.ryzhkov.dev/   # 403: Garage answering an unsigned request
```

Purge the test events afterwards with
`nats --context chaldea-admin stream purge AKASHA_EVENTS --subject 'akasha.*.test.>' -f`.

## Rollback

Each part rolls back on its own, and none of the rollbacks touches another
stack.

- **Streams.** `nats --context chaldea-admin stream rm <NAME> -f` for each
  stream created. Their data is test data until Chaldea runs.
- **Broker.** Revert this change's NATS commits and redeploy with
  `ansible-playbook playbooks/swarm.yml --tags stacks --limit homelab -e only=nats`.
  That restores `secrets/nats.sops.yaml`, the Docker secret configuration
  and `bootstrap-admin`. The JetStream directory is kept either way. Streams
  created under the CHALDEA account are not visible to the old global
  account, so remove them first.
- **Object store.** On homelab, `docker stack rm objectstore`. Then remove
  the `objectstore` entry from inventory and rerun `playbooks/traefik.yml`
  and the Gatus deploys. `/srv/swarm/objectstore` and the Docker secret stay
  until deliberately removed.

## Open questions

- **Quota of the work and event streams.** The numbers above are first
  guesses. 1 GiB and 14 days of events assumes Akasha drains the stream
  daily or better.
- **Lifecycle drift.** Garage lets a write key change a bucket's lifecycle
  rule. Should `garage-bootstrap.yml` run on a daily timer, or should the
  drift check (roles/drift_check) run its `--check`?
- **Reaching the services from off the LAN.** `nats.ryzhkov.dev` and
  `s3.ryzhkov.dev` resolve only on the LAN; the laptop away from home reaches
  neither. Options are Nebula or publishing through the tunnel.
- **Credential lifetime.** NKeys and S3 keys here are long-lived. Decision 9
  of the plan wants short-lived tokens minted per run; whether those sit in
  front of NATS and S3 or replace these identities is for step 2.
- **Backups.** JetStream state is excluded from the VM backup
  (docs/nats-jetstream.md). That is acceptable while the streams hold only
  queues and buffers, but CHALDEA_QUOTA and unacknowledged work would be lost
  with the disk.
- **The Akasha source token.** This change makes `<source>` the publishing
  identity. If the plan meant a producer type (feed, toolfeedback,
  deployments), the permissions change, not the streams.
