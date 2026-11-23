# Drift ownership and signer recovery, 2026-11-22

The daily checker previously treated everything outside site.yml's Compose
manifest and authentik.yml's household blueprint as unmanaged. The standalone
deployment roles are also repository owners. roles/drift_check/files/drift-owners.json
records the reviewed exact application/provider IDs and host/project/Compose paths.
It is recognition of ownership, not an assertion about standalone configuration,
policy bindings, grants or secrets. Existing household access checks still run.
Unknown applications, changed provider identities and unknown Compose paths warn.

## Findings and owners

| Finding | Resolution |
| --- | --- |
| chaldea-akasha (114) | Recognise agent-host enrollment |
| chaldea-holonet (57) | Recognise isolated HoloNet enrollment |
| chaldea-host-rotation (113) | Recognise agent-host enrollment |
| chaldea-jocasta (102) | Recognise isolated Jocasta tooling enrollment |
| ha-http-a63e197f (48) | Recognise agent-host enrollment |
| homelab-cohort-signer-staging (115) | Recognise signer bootstrap; deny-all staging application |
| homelab-observer (45) | Recognise agent-host enrollment |
| jocasta-laptop-interactive (105) | Recognise isolated public PKCE enrollment |
| jocasta-managed-interactive (106) | Recognise managed-interactive enrollment; not evidence of active interactive use |
| ov-http-a63e197f (42) | Recognise isolated OV enrollment |
| shizune-mcp (112) | Recognise agent-host enrollment |
| homelab/observability-socket-proxy | Recognise observability_collector, exact protected Compose path |
| homelab/ov-http | Recognise ov_http_compose release owner and release Compose path |
| normandy/observability-socket-proxy | Recognise observability_collector, exact protected Compose path |
| normandy/openviking-embeddings | Recognise Swarm inference inventory's standalone runtime |

No application, provider, account, token or grant is deleted or changed.
Provider IDs were read from the root-protected signer contracts/staging record
and verified against live provider/application links by the signer's preflight.
The checker independently matches those IDs from its read-only provider list.
Compose paths came from docker compose ls on each host and were reconciled
against the owning roles. A same-name project at another path still warns.

Deploy the observer with playbooks/drift-check-observer.yml. It deliberately
retains the manifests from actual deployments, rather than rebaselining drift.
Normal drift_check role deployments install the same ownership registry.

## Signer

Feed entry 1000000000935 coincided with Shizune enrollment, contract approval
and publish. At diagnosis the journal was idle, original key active, next and
retiring null, no pending create. Contract preflight and token canaries passed.
Both edges failed the strict published-JWKS comparison: they served native
Authentik JWKS including certificate fields, while the published cohort document
contained the same RSA key with only the six admitted JWK fields.
The protected backend at homelab:8429/signer-jwks.json was serving the published
document. The cohort overlay files were absent on both edges. Repository
Traefik defaults disabled these routes, and edge inventory did not retain their
activation. Routine convergence could therefore remove them.

Persist activation in edge_proxy inventory, then install only the exact overlay
with playbooks/authentik-signer-routes.yml. Verify published bytes against the
current journal before activation; run authentik-signer publish and rotate
afterwards. Preserve the controller's publication and retirement waits; never
advance phase timestamps or force a premature rotation. The current journal's
rotation timestamp is 2026-11-13; a monthly rotation is not due on November 22.

The CLI also accepted rotate --help as a rotation request because argparse
REMAINDER consumed the option. Unexpected arguments now fail before state access.
Use authentik-signer --help for command help.

## Coordination and limits

PR #211 belongs to the HoloNet session. This change does not edit its sources or
reconverge its services. The Shizune budget session is settled.
The feed's homelab report additionally embeds public-api findings: its edited
Traefik Compose file is outside this two-host acceptance scope and is preserved.
The registry includes the known public-api socket proxy for future deployment,
but the observer rollout targets only homelab and normandy.
