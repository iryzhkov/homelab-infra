# Disabled first messaging route

The MESSAGES source is disabled. This change adds no enrolled key, host install,
service start, stream application or broker redeploy. Q0a metadata on
2026-10-21 confirms the existing homelab broker runs NATS 2.15.1 on local ext4
storage with a 512 MiB service memory limit. It does not qualify live grants,
the reverse proxy, backups or a target T3 profile.

The existing Chaldea message sender and receiver require a dedicated account.
CHALDEA contains Akasha events, and JetStream pull replies must not bridge into
that account. MESSAGES has no imports or exports. It uses the existing NKey
custody model, with three distinct public identities: operator admin, source pc
and target agent-a. No new issuer or renewal process is introduced. Source and
target grants match the exact route documented below; neither can inspect or
manage broker resources or use generic reply permissions.

## Source and limits

- Account template: stacks/nats/templates/nats.conf.j2.
- Default inventory: nats_messages_enabled=false and nats_messages_nkeys={}.
- Guarded enrollment: roles/swarm/tasks/stack.yml includes
  stacks/nats/messaging-validate.yml before lint, deployment directories,
  stack/secrets writes or pin labels. Validation requires boolean selection,
  complete public keys and separation from every SYS/CHALDEA identity.
- Streams and consumers: stacks/nats/messaging/, separate from the existing
  Akasha jetstream/ directory. scripts/nats-jetstream-apply.sh, including --all,
  continues to select only AKASHA_EVENTS. It cannot provision MESSAGES.

The first source is omarchy-pc, destination agent-a, caller pc-agent-a-v1.
The exact command subject is chaldea.message.command.pc.agent_a and receipt
subject chaldea.message.receipt.pc.agent_a. Pull consumers are receive_pc_agent_a
on MESSAGE_COMMANDS and source_pc_agent_a on MESSAGE_RECEIPTS. Inbox prefixes are
_INBOX_message.pc and _INBOX_message.agent_a. These labels alone grant no access
to a native T3 thread.

Each WorkQueue file stream caps storage at 1 MiB, messages at 256, each subject
at 32, each message at 8192 bytes and consumers at eight. Discard-new protects
pending work. Commands expire after 24 hours, receipts after 120 seconds.
Consumers require explicit ACK, max_deliver=5, max_ack_pending=32, max_waiting=32,
max_batch=1 and max_expires=1s. Production ack_wait=60s exceeds the bounded
receiver dispatch path. The account caps file storage at 4 MB, memory at 1 MB,
streams at two and total consumers at 16, within existing broker bounds.

## Approval and application boundary

Prepare an exact operator packet before enabling nats_messages_enabled or
changing its public key map. It must name:

1. Current source/release/config hashes and existing NATS/edge behavior.
2. Three supported local custody contexts and public keys, without copying seeds.
3. One exact existing disposable T3 thread/environment and target profile UID,
   root-controlled runtime/config paths, supported target-local token issuance
   and renewal, and current protected admission qualification where used.
4. Current consistent broker backup/restore and rollback evidence.
5. Consumer-before-producer order, exact authorized hosts and no generic restart.

No seed or T3 bearer belongs in inventory, rendered public config, a job snapshot
or a remote sender. A global fleet client pin can auto-pull multiple hosts and
requires separate applicable authority. Published client-v0.4.16 does not mean a
host has selected or installed it.

After explicit approval, render and review the exact public configuration and
its hash, validate with the pinned broker parser, and use the existing scoped
NATS deployment path. Apply the two stream files followed by their consumers
using a dedicated MESSAGES operator context and explicit --config file arguments.
Do not run the existing Akasha apply script against MESSAGES. The sender and
receiver retain no broker management grants. Consumer creation must precede
sender enablement. Exact application commands, live key generation and receiver
installation remain absent until their operator packet is qualified.

The receiver configuration and disabled user unit already ship in Chaldea.
Reuse docs/message.md and packaging/systemd/chaldea-message.service from the
qualified worker release. Do not add a second service or initializer. The
database must be initialized explicitly by its process owner with the supported
binding, never reset to resolve uncertainty.

## Verification and rollback

scripts/test-nats-messaging.py executes the actual Swarm wrapper through its
first deployment directory and stack-file writes in scratch. Invalid enrollment
must fail before those effects. It renders the actual template and provisions
the actual JSON definitions against checksum-pinned disposable NATS 2.15.1.
It checks command/receipt delivery and ACK, separate CHALDEA account state,
forbidden subjects/pulls/ACKs/inboxes/management/direct reads, actual server
limits, oversized messages and full-subject refusal. Existing Akasha broker
tests retain default-selection coverage. Tests use only generated scratch keys.

This proof does not qualify live enrollment, WSS, target-local renewal, native
T3 dispatch or backup/restore. Those remain separately authorized canary gates.

On a live failure disable new intake first, stop only the new receiver and
preserve both journals, stable IDs, pending streams and uncertainty. Reconcile
the original native target before any explicit retry. Revoke only new route
keys and verify their existing connections close. Restore the previous reviewed
account selection without deleting SYS/CHALDEA or Akasha data. Never remove
journals or pending MESSAGES streams as a recovery shortcut.
