# Shizune prototype

Shizune runs as UID 10001 on homelab, publishing only mesh address
10.42.0.2:1937 to the two edge proxies. Port 1937 was absent from inventory
and stack declarations and from a read-only `ssh homelab ss -ltn` inspection
on 2026-11-11. The dedicated Docker firewall admits only edge_proxy mesh IPs.
The web_mcp_route template restricts ingress to LAN clients without Cf-Ray,
requests an exact-host certificate, and routes only MCP and resource discovery.
There is no ordinary public stack route.

The fixed resource is https://shizune.mcp.ryzhkov.dev/mcp.
Authentik's shizune-mcp application uses client shizune-mcp-machine,
scope shizune.use with optional shizune.budget.write, a 300-second lifetime, and the existing RS256 keypair
8bbd6516-af33-4e64-a73b-caa39e692c29. Existing patterns permit key reuse.
Issuer is https://auth.ryzhkov.dev/application/o/shizune-mcp/ and JWKS is
that URL plus jwks/. Only the two declared service accounts in
shizune-mcp-machine-identities with exact chaldea_shizune attribute
`{"shizune_owner": "igor"}` receive the audience and owner claims.
This prototype uses one owner grant, with no per-resource ACL matrix.

## Operator inputs

Run from controller omarchy-pc in this checkout after source review.
Install the repository's requirements and use existing protected SOPS access.
Keep operator inventories, public baseline receipts and credential sources
outside the repository in a private directory, for example
`/home/igor/.cache/shizune-proto/operator`. No secret belongs in extra-vars,
argv, environment, diffs or PRs.

Create an absolute-path operator inventory, `$OP/identities.yml`, defining
exactly omarchy-pc and laptop with ssh_user igor and their real connections.
omarchy-pc may use ansible_connection: local only on controller omarchy-pc.
The role binds omarchy-pc to hostname omarchy-pc/UID 1000 and laptop to
omarchy-macbook-air-igor/UID 1001. Both credentials land in
~/.config/chaldea/authentik/shizune-interactive.app-password with mode 0600.
Accounts are chaldea-omarchy-pc-shizune-interactive and
chaldea-laptop-shizune-interactive, generation 1, expiry 2027-01-15T00:00:00Z.

For routes, create a separate absolute-path `$OP/edges.yml` with aliases
public-api and public-api-b, real connections and become access. The route
guard expects the existing root-owned file-provider directory or its owning
UID 1000; confirm that custody on both hosts before using the source declaration.
The route entrypoint intentionally refuses inventory/hosts.yml.
Do not override its fixed provider directory or certificate resolver.

## Enrollment and bootstrap boundary

The maintained authoring guide at
`tooling/guides/web-mcp-authentik-authoring-2026-10-19.md`, revision 45,
qualifies existing-owned convergence, not generic bootstrap collision discovery.
The generic role queries /providers/oauth2/?name= and ?client_id=;
a collision can return a foreign OAuth2 body including its secret even under
no_log. Before first online enrollment the parent must qualify nonsecret
collision discovery and original ownership. Do not run the first online CHECK
or REAL merely because the offline CHECK passes. This source change does not
qualify that generic path or create live ownership.

First creation cannot use existing_owned_only=true because no stored ownership
or independently confirmed provider/application baseline exists yet. After that
bootstrap prerequisite is resolved, the first creation commands are:

```sh
export OP=/home/igor/.cache/shizune-proto/operator
ansible-playbook -i "$OP/identities.yml" playbooks/authentik-shizune-identities.yml --check --diff
# Online CHECK, only after qualifying bootstrap discovery:
ansible-playbook -i "$OP/identities.yml" playbooks/authentik-shizune-identities.yml --check --diff -e shizune_identities_online_check=true -e shizune_identities_bootstrap=true -e authentik_machine_identity_existing_owned_only=false
# REAL, then identical repeat:
ansible-playbook -i "$OP/identities.yml" playbooks/authentik-shizune-identities.yml -e shizune_identities_apply=true -e shizune_identities_bootstrap=true -e authentik_machine_identity_existing_owned_only=false
ansible-playbook -i "$OP/identities.yml" playbooks/authentik-shizune-identities.yml -e shizune_identities_apply=true -e shizune_identities_bootstrap=true -e authentik_machine_identity_existing_owned_only=false
```

Retain successful stored blueprint content and API-returned IDs. Reconcile an
uncertain apply before retrying. Independently confirm provider_pk,
application_pk and SHA-512 of successful stored content in a protected public
baseline file, `$OP/shizune-baseline.yml`, under
authentik_machine_identity_existing_provider_baseline. Subsequent runs default
to existing_owned_only=true and read only that baseline-owned OAuth2 detail:

```sh
ansible-playbook -i "$OP/identities.yml" playbooks/authentik-shizune-identities.yml --check --diff -e shizune_identities_online_check=true -e @"$OP/shizune-baseline.yml"
ansible-playbook -i "$OP/identities.yml" playbooks/authentik-shizune-identities.yml -e shizune_identities_apply=true -e @"$OP/shizune-baseline.yml"
ansible-playbook -i "$OP/identities.yml" playbooks/authentik-shizune-identities.yml -e shizune_identities_apply=true -e @"$OP/shizune-baseline.yml"
```

Require a zero-change repeat. After changing successful blueprint content,
confirm its new baseline before later convergence.

## Deployment order

1. Build and push the service image to git.ryzhkov.dev/igor/shizune from homelab
   using the Shizune repository's build instructions and existing registry login.
   Record its full source commit and registry manifest digest.
2. Supply `shizune_image: git.ryzhkov.dev/igor/shizune:<40-hex-commit>@sha256:<64-hex-digest>`
   in `$OP/stack.yml`. Default UNSET deliberately refuses deployment. Also set
   shizune_actual_token_source and shizune_google_client_source there to
   protected controller file paths if available.
3. Deploy the stack, then routes on each edge, then enroll identities after
   resolving the bootstrap boundary above. Verify anonymous refusal and discovery
   before issuing runtime credentials. The parent owns live/native acceptance.

```sh
ansible-playbook playbooks/site.yml -l homelab -e only=shizune -e @"$OP/stack.yml" --check --diff
ansible-playbook playbooks/site.yml -l homelab -e only=shizune -e @"$OP/stack.yml"
ansible-playbook playbooks/site.yml -l homelab -e only=shizune -e @"$OP/stack.yml"

# CHECK, REAL and identical repeat for both independent edge selections:
for edge in public-api public-api-b; do
  ansible-playbook -i "$OP/edges.yml" playbooks/web-mcp-route.yml -e web_mcp_edge_target="$edge" -e @playbooks/vars/web-mcp-route-shizune.yml --check --diff
  ansible-playbook -i "$OP/edges.yml" playbooks/web-mcp-route.yml -e web_mcp_edge_target="$edge" -e @playbooks/vars/web-mcp-route-shizune.yml
  ansible-playbook -i "$OP/edges.yml" playbooks/web-mcp-route.yml -e web_mcp_edge_target="$edge" -e @playbooks/vars/web-mcp-route-shizune.yml
done
```

Data directories data/actual and data/google and secrets are mode 0700,
owned by 10001. Optional sources must be single-link regular mode-0600 files.
They are copied under no_log without diff to secrets/actual-session-token and
secrets/google-oauth-client.json, mode 0600 owned by 10001. Absent sources are skipped
with a message and preserve existing files. These names match the service defaults
/run/secrets/actual-session-token and /run/secrets/google-oauth-client.json.
No backend credentials are generated here. Google authorization state belongs
in data/google: after Igor completes consent with `shizune-google-auth` on his
desktop, install the resulting single credential file `<url-encoded email>.json`
once into data/google as a single-link regular file, mode 0600, owner 10001
(for example `install -m 0600 -o 10001 -g 10001` through sudo on homelab), and
delete the desktop copy. This is Shizune's own Google grant, not a provider login
cache, and workspace-mcp refreshes it in place. Actual state belongs in data/actual. Back up these private data
directories through the owning backup procedure before storing useful data.

## Budget writes and private audit relay

Both declared igor identities may request `shizune.use shizune.budget.write`.
Requesting only `shizune.use` retains use-only access. Budget write tools
require the optional scope. Issuer, audience, client and 300-second lifetime
stay fixed. The legacy blueprint procedure above is frozen after cohort
signer enrollment and must not be used or bypassed on the enrolled controller.
Deploying the relay is independent of scope enrollment.

Use the existing protected M3 packet and complete cohort, owner, receipt and
baseline inputs with `authentik_agent_create_shizune_budget_mapping=true`
through the signer's locked enrollment wrapper:

```sh
sudo /usr/local/sbin/authentik-signer enrollment ansible-playbook -i "$PROTECTED_INVENTORY" playbooks/authentik-agent-hosts.yml -e @"$PROTECTED_AGENT_HOSTS" -e authentik_agent_create_shizune_budget_mapping=true --check
# Inspect CHECK, then run REAL and an identical zero-change repeat.
```

This narrow producer creates only shizune.budget.write with an initial deny
expression, attaches its API-returned UUID to the owned Shizune provider, and
records each mutation in hosts.json. Unknown outcomes remain pending and refuse
replay. Its legacy fallback grants the two named legacy Shizune accounts,
with exact owner attribute and group membership and both scopes requested.
The M3 accounts chaldea-laptop and chaldea-omarchy-pc also receive the optional
scope through their existing Shizune owner grant; no other host has that grant.
The claims function is embedded in every coexistence mapping, so this operation
intentionally updates all those expression bytes under their existing receipts.
It refuses simultaneous host
lifecycle or rotation changes. Do not supply a new mapping baseline; provenance
comes from the producer's own journal. The legacy blueprint baseline stays
unchanged because no stored blueprint is reapplied.

After an accepted change, retain the new mapping ID/expression and attachment
receipt, update the protected signer's Shizune property_mappings contract to the
exact live attachment set, and run approve-contracts and publish through the
maintained signer procedure immediately after REAL. Until approve-contracts,
signer rotate/publish refuses the changed attachment set and leaves signing
unchanged; keep its timer off and retain the common enrollment lock boundary.
Enrollment repeat can precede approval because the wrapper does not run signer
preflight. Require zero-change enrollment repeat and verify
both use-only and combined-scope token claims. Never use bootstrap to evade
a signer or ownership refusal.

The stack prepare tasks create `data/feed-outbox`, mode 0700 owned by UID
10001, before container startup. The gateway atomically renames mode-0600
files named `<32 lowercase hex>.json` into that directory. Each contains
exactly `{"v":1,"event":"budget_delete","count":1,"at":"2026-11-22T01:02:03Z"}`.
Allowed events are budget_delete and budget_categorize; count is 1..500;
at is a valid UTC ISO timestamp ending in Z.

Root's shizune-feed-relay.service handles at most 50 files per run. Its path
unit watches changes, and its timer retries 15 minutes after the last run.
It rejects nonregular, symlink, multiple-link, oversized and nonprivate files,
duplicate JSON keys, extra fields and invalid values. Invalid files are removed
with a filename-only reason. A successful post removes the file; failure retains
it for retry. Feed output is suppressed. Only event kind and count reach the
feed through `runuser -u iryzhkov -- feed post TITLE --tag "shizune budget"`,
using that user's existing default producer. No producer enrollment is needed.
A crash after posting but before unlink may duplicate an audit entry.

Deploy with the targeted stack commands above without changing the image pin.
Verify the relay path/timer and storage custody. Drop one valid synthetic
count-1 event as UID 10001, confirm removal and its ID with
`feed recent --since 10m`, then confirm an invalid event is removed without
a post or private journal content. Disable both relay triggers when retiring
the stack; preserve any pending valid events until their disposition is decided.

## Rollback

Remove each owned route with a separate protected vars file containing the same
service and directory_uid and state: absent, using the same route entrypoint.
Stop the Shizune container through its unit and Compose project on homelab;
preserve data and secrets. Disable shizune-container.service when retiring it.
Keep its firewall while the port is published. For an image rollback, restore the
previous immutable pin and repeat the targeted stack converge.

Identity rollback needs a reviewed declaration setting only these two accounts
and their Shizune application absent through the maintained role, with the same
blueprint name and independently confirmed ownership baseline. The fixed
enrollment entrypoint refuses altered cohorts. Do not delete the shared signing
key or use a broad Authentik playbook. Account revocation stops new issuance;
already issued JWTs retain their lifetime plus verifier leeway.
