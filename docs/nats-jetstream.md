# Standalone JetStream: Ansible-managed Swarm

Superseded in part by [chaldea-infra-foundation.md](chaldea-infra-foundation.md):
the single `bootstrap-admin` password and the Docker-secret configuration
described under "Credentials and configuration" are replaced by NKey users in
the SYS and CHALDEA accounts and a configuration rendered from the inventory,
while the historical encrypted `secrets/nats.sops.yaml` remains retained but is no longer mounted by the NATS stack. Placement, storage, network, firewall,
edge routes and the install verification below still hold.
Owning repo base6b3d8b7c2bb2793f61c5ed12801f8b95f8486257. No Steward integration/config changes.
## Verified deployment (2026-10-13)
Root applied the approved scoped Ansible firewall and Swarm stack from this isolated tree; both exit0. Both edge dynamic routes applied via trusted laptop controller using its existing authorized keys and verified hostkeys. Laptop snapshot /tmp/nats-routes-check-20261013 came from staged tree d2bd530da61ddb7062d035092dc3f622aa4ab7ae; source base remains6b3d8b7c2bb2793f61c5ed12801f8b95f8486257 pending publication.
Exact command sequence (check passed before each):
- local private Ansible environment: /home/igor/.cache/nats-operator/ansible-env, Python3.12.14/core2.19.3; ANSIBLE_COLLECTIONS_PATH=/home/igor/.cache/nats-operator/collections:/home/igor/.ansible/collections.
- ansible-playbook -i inventory/hosts.yml playbooks/nats-network.yml --tags firewall --limit homelab
- ansible-playbook -i inventory/hosts.yml playbooks/swarm.yml --tags stacks --limit homelab -e only=nats
- ssh laptop 'cd /tmp/nats-routes-check-20261013 && ansible-playbook -i inventory/hosts.yml playbooks/nats-network.yml --tags routes --limit public-api,public-api-b'
The first WSS test timed out before backend connections reached homelab. Read-only UDM API and its actual PublicAPI→Internal chain confirmed existing service port group omitted9222, followed by DROP. Shared group also targets Normandy, so it was NOT extended.
UDM policy is controller-managed manually, not converged by this Ansible repository. Root created ONLY new policy6ace67701825b50cb13969b8, request rc127-nats-install-20261013: exact source20.33/20.4 → only destination70.223 TCP9222; index10009; automatic return traffic. Prior eight policy identities/hashes remained unchanged. No shared group, subnet-wide allow or Normandy scope change.
API credential pointer: laptop GNOME keyring serviceunifi-local/userclaude. Console certificate was read through authenticated SSH and explicitly trusted for verified hostnameunifi.local; credentials/cookies never printed. Root-approved scoped operator stores laptop:/tmp/nats-udm-operator-receipt.json plus before/after policy snapshots. Rollback deletes ONLY the saved new policyID after exact scope validation using that root-reviewed operator's --rollback; never modify/delete existing policies or generated return twins manually.
Final actual verification PASS: verified WSS TLS1.3/account authentication on NATS2.15.1; two file-backed probe messages; explicit-ACK durable floor1→2 across restart of ONLY nats_nats; unchanged durable creation identity; zero pending/ACK-pending/redeliveries; separate replay returned both exact IDs once. Invalid credentials failed Authorization Violation after authenticated success. Only INSTALL_PROBE was deleted; final streams0/consumers0/privatehealthok.
Actual enforced caps: service0.5CPU/512MiB, JetStream128MiB memory/4GiB disk. 4222/8222 unpublished/refused; nonedge9222 dropped with host rule counter2→4 during test. Both edge backend tests exit0/HTTP400 as expected without WS Upgrade. All six fleet hosts verified endpoint TLS; authenticated application test used PC only, without copying credentials to workers.
Durable evidence directory: /home/igor/.cache/rework/phase1/p1/scratch/overnight-20261013/nats-install-prep. Current summary live-verification.json separates superseded pre-UDM failure into historical_pre_udm. Exact command/time/exit/log-SHA receipts smoke-receipts.json; post-smoke-proof.json; fleet-tls-reachability.json; both-edge-backend-current.json; official-nats-udm-{operator-receipt,before,after}.json; evidence-index.json authenticates retained artifacts. Original failed logs remain intact.
Next work is a separately agreed integration campaign. Before production data: consistent backup/restore validation for backup-excluded local disk and named per-client credentials/ACL/revocation design. Bootstrap admin is not a fleet integration identity.

## Placement and deployment
Read-only observed: homelab Ready/Active Swarm Leader; Normandy Ready/Active manager; OCI Ready/Drain manager. Node hostname homelab and pin.nats constraints BOTH required: one replica, stop-first update/rollback, no rescheduling onto another node's empty disk. Do NOT move pin.nats using generic drain/migration scripts.
Data /srv/swarm/nats/data resolves under /mnt/docker-data/swarm on actual ext4 /dev/vdb1 (235GiB free). Preparation checks exact mount target+ext4. Node outage makes broker unavailable; durable data is preserved, not replicated. VM backup excludes this disk; explicit consistent JetStream backup/restore required before production streams.
Credentials are now prepared: encrypted secrets/nats.sops.yaml (NATS_CONFIG only); private operator context/config /home/igor/.cache/nats-operator/{context.json,nats.conf}, mode0600 under0700. Private config parser PASS; no plaintext password in source.
Run repository-local Ansible. FIRST prepare the persistent backend guard, then deploy broker, then edge routes:
ansible-playbook -i inventory/hosts.yml playbooks/nats-network.yml --tags firewall --limit homelab --check
Repeat without --check before broker deployment. AFTER broker deployment:
ansible-playbook -i inventory/hosts.yml playbooks/nats-network.yml --tags routes --limit public-api,public-api-b --check
Repeat without --check. These files are additive; no global Traefik changes/restarts.
Broker commands:
ansible-playbook -i inventory/hosts.yml playbooks/swarm.yml --tags stacks --limit homelab -e only=nats --check
Then same without --check. Existing roles/swarm/tasks/stack.yml provides lint, hashed file secrets, pin and docker stack config/deploy. This selector touches only new nats stack; never nodes/registry tags or swarm_rebuild.
Deployment directory /home/iryzhkov/swarm/nats. Service nats_nats. Limits0.5CPU/512MiB,reservations0.1CPU/128MiB; JetStream128MB memory/4GB file store. Observed available hostmemory5304MB; worker capacities unchanged.
## Credentials and configuration
Create encrypted secrets/nats.sops.yaml using existing repository recipients; key NATS_CONFIG contains complete rendered stacks/nats/templates/nats.conf.j2 with a newly generated bcrypt hash replacing its template expression. Preserve all other config values. No plaintext password in source/env/argv/history/logs. Corresponding password solely in operator private NATS CLI context0600.
Existing Swarm role writes private secret files0600 and hashes NATS_CONFIG into immutable Docker secret name; service reads /run/secrets/nats.conf asUID1000 mode0400. No anonymous user. Server config is a Docker secret, not interpolated YAML/environment; bcrypt dollar signs remain intact.
Before installation parse the private rendered config using verified nats-server2.15.1: nats-server -t -c PRIVATE_CONFIG. Root verified official amd64 tarSHA bf5edc7b1ce98e885c368cb43a886eb148dc5d0551d99f3cf6141fb5e1954c70.
OCI pin nats:2.15.1-alpine@sha256:9e47060a6c099ce118566e425245adc5c78139d02e1cc6fc48747b776445c4f8.
## Network and DNS
ONLY9222 is published, mode:host (no routing mesh), on homelab interfaces. Swarm cannot honor loopback host-IP publishing. 4222 and8222 are NOT published at all; private monitoring via docker exec inside the exact service container using loopback /healthz and /jsz.
Before service deploy, scope firewall9222 to edge192.168.20.33 and192.168.20.4 ONLY, including Docker-forward filtering; deny other source addresses. No broad portgroup or ingressmesh exposure.
Use playbooks/nats-network.yml --tags routes on the trusted laptop controller to install ONLY stacks/traefik/nats.yml as /home/igor/apps/traefik/file_provider/nats.yml on both edges. Dynamic hotreload, no Traefik restart/globalconfiguration. Existing certresolver letencrypt. WSS endpoint wss://nats.ryzhkov.dev:443 → backendhttp://192.168.70.223:9222.
LAN DNS nats.ryzhkov.dev→edgeVIP192.168.20.3 verified. PublicDNS/tunnel not verified/published; new route alone does not publishinternet. Confirm valid certificate and actual intended worker networkreachability.
## Reproduction of verified nats client checks (rerun only when justified)
Use independently verified official nats CLI and private authenticated context targeting WSS. Confirm account info succeeds. Create file-backed INSTALL_PROBE stream subjectinstall.probe,replicas1,limits retention,max_bytes1048576,max_age600000000000 via stream add --config JSON. Create INSTALL_DURABLE explicit-ACK pullconsumer filterinstall.probe via consumer add INSTALL_PROBE --config JSON.
Publish two unique nonsecret receiptIDs. Pull/ACK first using consumer next INSTALL_PROBE INSTALL_DURABLE --count1 --ack. Leave second pending. Restart ONLY service docker service update --force nats_nats (stop-first). Reconnect same context and pull/ACK second; confirm exact IDs, durable ACK sequence and stream persistence.
Create independent replayconsumer deliver_policy:all; retrieve/ACK both retained messages. After successful authenticated test, a separate unauthenticated/invalid-credential context MUST fail with authentication/authorization denial, not DNS/TLS/networkfailure. Inspect serverstate/streaminfo and preserve filtered commands, exitcodes,timestamps, exactpayloads and restart taskidentity. Delete only INSTALL_PROBE aftertests.
Native4222/monitor8222 inaccessible fromhost/network; monitor using exact container docker exec wget loopback. Verify9222 firewalldenial fromnonedgehost and WSS success throughauthorizededge.
## Rollback
Remove only added dynamicroute on both edges; docker stack rm nats; remove only new scoped9222 firewallrule. Keep /srv/swarm/nats/data,encryptedsecrets and receipts. No other stacks/services/restarts. Production backup/ACL/provider/cursor integration later; no automatic provider downgrade or dualdispatch.
## Primary sources
https://docs.nats.io/reference/config/websocket/
https://docs.nats.io/reference/config/
https://github.com/nats-io/nats-server/releases/tag/v2.15.1
