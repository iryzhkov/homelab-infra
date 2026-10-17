# Chaldea infrastructure foundation

PR #10 is a draft source foundation. Nothing here has been deployed.
For Akasha v1, the binding scope and deployment procedure are now
[the Akasha runbook](akasha-v1-deployment.md), following
`jocasta:2d21001bcb0769a46f3331f858935cdb@1` (2026-10-17).

The required subset is the existing single NATS 2.15.1 node on homelab,
SYS/CHALDEA configuration accounts, three operator/gateway NKeys,
AKASHA_EVENTS and its durable akasha-ingest consumer. The trusted Akasha
gateway publishes approved source IDs in CHALDEA; host users have no
Akasha publication grant. Optional future host work/quota users are disabled
by default; deployment refuses to enable any while events share CHALDEA.
Their JetStream API reply subjects could bypass direct publish ACLs.
The service cannot administer streams or consumers.

NATS state remains /srv/swarm/nats/data/jetstream on homelab local disk.
AKASHA_EVENTS is a 14d/1 GiB replay buffer with a 327680-byte message cap,
not the durable query store. Akasha's SQLite state is /var/lib/akasha,
separate from feedd on :1934. The server's 4 GiB file cap and CHALDEA's
2 GiB cap remain, with max_bytes required and 1 GiB per stream. No
plaintext 4222 or monitoring 8222 publication is needed.

CHALDEA_WORK, fleet/host work consumers, CHALDEA_QUOTA, Garage/objectstore,
bootstrap/keys/secrets, NAS mounts and S3 routes/probes remain **parked**.
Their source files are retained for later work. The old 10/50 GiB local
Garage buckets are not the newer plan and must not be deployed. Use the
newer plan when real blob storage is needed, not these obsolete quotas.

Apply defaults to the v1 subset. A check changes nothing, a second apply
is idempotent, drift is repaired and nothing is deleted:

```sh
NATS_CONTEXT=chaldea-admin bash scripts/nats-jetstream-apply.sh --stream AKASHA_EVENTS --check
```

The original broad Garage/Traefik/Gatus rollout sequence and host-publish
examples have been withdrawn as current instructions and preserved in
[the parked historical proposal](parked-chaldea-infra-foundation.md).
Existing source references to "NATS accounts and users", "Deploy", secrets,
client key delivery, retention, rollback and open questions refer to that
archive; none authorizes deployment. The current runbook names every participating
host, pending trust/clock/backup interfaces, proposed <=24h backup RPO,
and parity/rollback gates. Loki's existing 30d raw-log retention is unchanged.

Validation is source-level and disposable-instance evidence only:
`scripts/test-chaldea-retention.py` checks bounds (including parked source
definitions), and `scripts/test-akasha-infra.py` checks real broker ACLs,
selected apply/check/idempotence/drift, message bounds and SQLite restoration.
