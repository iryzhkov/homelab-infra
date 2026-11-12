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
scope shizune.use, a 300-second lifetime, and the existing RS256 keypair
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
They are copied under no_log without diff to secrets/actual-token and
secrets/google-client.json, mode 0600 owned by 10001. Absent sources are skipped
with a message and preserve existing files. The service repository must consume
those mounted paths; confirm its runtime configuration before deployment.
No backend credentials are generated here. Google authorization state belongs
in data/google. Actual state belongs in data/actual. Back up these private data
directories through the owning backup procedure before storing useful data.

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
