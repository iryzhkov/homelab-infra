# Akasha's required NATS foundation

This branch carries only the Akasha subset of parked PR #10, combined with
PR #11 and PR #12, directly against main. Garage, objectstore, NAS mounts,
work/quota streams and consumers, optional host identities and their source
deployment tools are excluded. PR #10 remains parked independently.

Required: NATS 2.15.1 on homelab, SYS and CHALDEA accounts, operator sys-admin /
chaldea-admin public NKeys and the gateway akasha public NKey, AKASHA_EVENTS
and akasha-ingest. No host identities may connect to the shared account:
JetStream reply subjects can bypass publish ACLs. The gateway cannot administer
streams or consumers. The operator provisions keys; no play generates them.

NATS data stays on local /srv/swarm/nats/data/jetstream. Limits: server 4 GiB,
account 2 GiB, stream 1 GiB/14 days, message 327680 bytes. SQLite history is
separate in /var/lib/akasha. See [the ordered rollout](akasha-v1-deployment.md)
for trust, clock, bootstrap, activation, backup, canaries and rollback.

Validation uses disposable NATS, SQLite and a synthetic local Ansible check.
No source validation contacts homelab or changes a host.
