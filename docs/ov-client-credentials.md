# OV client credential cutover

This is preparation for the OV 0.2.6 to 0.5.2 upgrade. The new playbook is
standalone and never imported by a normal converge. It changes only the
executing user's OV wrapper TOML when explicitly applied. It does not enroll
users, change backend authentication, restart clients or services, or deploy OV.
Root orchestration owns those effects and their authorization.

## Read-only binding inventory, 2026-10-24

Secret comparisons used constant-time byte comparison inside private processes.
No credential or short-key hash was printed. Every inspected key below matched
the current production ROOT key. This is the observed scope, not authorization
to write to every host.

| Host / service | Active or retained credential binding | Endpoint / owner |
| --- | --- | --- |
| Current host, omarchy-macbook-air-igor, user igor | GNOME keyring service=openviking, key=api; /home/igor/.config/ov-mcp/config.toml has no api_key | Running local MCP processes use https://ov.ryzhkov.dev. Claude, Codex and OpenCode registrations have OV_BASE_URL and OV_AGENT, no OV_API_KEY. Agent-tools supplies the wrapper; this standalone playbook can manage its TOML credential. |
| homelab, user iryzhkov | /home/iryzhkov/.config/ov-mcp/key, mode0600; config.toml has no api_key | Retained direct REST configuration http://192.168.70.223:1933. No live wrapper process was found in the exact argv scan. Agent-tools wrapper and user binding remain affected on next launch. |
| normandy, user igor | /home/igor/.config/ov-mcp/key, mode0600; config.toml has no api_key | Same retained direct REST address. No live wrapper process found in the exact argv scan. Agent-tools supplies the wrapper. |
| gaming-pc, user igor | /home/igor/.config/ov-mcp/key, mode0600; config.toml has no api_key | Running MCP processes and Claude/Codex/OpenCode registrations use https://ov.ryzhkov.dev, no OV_API_KEY override. Agent-tools supplies the wrapper. |
| agent-a, 192.168.130.2, user igor | /home/igor/.config/ov-mcp/key, mode0600; config.toml absent | Claude/Codex/OpenCode registrations use https://ov.ryzhkov.dev, no OV_API_KEY override. Package present. Additional rollout host requiring root's scope decision. |
| agent-b, 192.168.130.3, user igor | /home/igor/.config/ov-mcp/key, mode0600; config.toml absent | Same registrations, package and scope decision as agent-a. |
| homelab, dsh container | OV_API_KEY from /home/iryzhkov/app/dsh/.env; secrets/homelab-dsh.sops.yaml is its managed source | OV_URL=http://192.168.70.223:1933. homelab-infra Compose/secret deployment owns it. start.sh writes /root/.openviking/ovcli.conf at container boot. |
| homelab, ov-http container, private read MCP 10.42.0.2:18098 | /opt/web-mcp/ov-http/releases/canary-082ab88-mesh-a63e197f/backend-key, mounted /etc/ov-http/backend-key | runtime.toml backend_url=http://192.168.70.223:1933/api/v1. Existing roles/ov_http_compose and playbooks/ov-http.yml own releases. |

The current-host TOML contains the direct REST URL but its running registered
clients override it with OV_BASE_URL. Live installed wrappers were inspected:
current host and gaming PC use agent-tools release
8c5e2c3000da0152c806000fb3e369c499f04c8474a98f3a62bba4ef16504803;
homelab and Normandy use
46ef43050bf3119b383b173b8c64d87c98741fa098571a50a81738c55010bad2.
Both have the same credential fallback logic. The agent VMs have the former
release. No complete host-independent client discovery is claimed.

cv-site's api_key references in this repository are Immich credentials, not OV
bindings. Do not include that service in this rollover.

## Supported issuance and custody

Pinned OV 0.5.2 api_key authentication rejects ROOT on tenant REST data and
native MCP. It discards account/user headers as a way to scope ROOT. Retain
ROOT only under secure operator custody for supported admin and health work.
Do not introduce trusted auth, a proxy, private database edits or arbitrary
assignment of the old ROOT value as a tenant key.

Use one shared routine memory-service identity in account default unless
root discovers an existing client separation requirement. An ordinary user
is sufficient for the tested routine resource operations. A candidate-only
upgrade-memory-user with role=user listed the copied resources, made a unique
synthetic directory, used POST /api/v1/content/batch-write with
defer_processing=true to create a synthetic note, read its exact text, and
removed the directory, all with HTTP200/status=ok. Processing was deferred to
avoid provider calls. This proves tenant read/write authorization, not semantic
ingestion, reindexing or every MCP wrapper operation.

The candidate-only response remains at:
homelab:/home/iryzhkov/.cache/ov-selfhost-20261024/upgrade-stage/candidate-memory-user.json.
It was issued using the retained candidate admin response. Neither identity
nor key is an input for production deployment.

During the later controlled cutover, explicitly enroll a freshly approved
production identity with:

```http
POST /api/v1/admin/accounts/default/users
Authorization: Bearer <protected production admin credential>
Content-Type: application/json

{"user_id":"<approved shared service identity>","role":"user"}
```

The supported response contains result.user_key. Omit seed and user_config
unless separately required. Before a retry, inspect whether the user exists
and whether a protected successful response was retained. Do not replay an
uncertain issuance blindly. The supported key regeneration endpoint is
POST /api/v1/admin/accounts/default/users/{user_id}/key. Regeneration immediately
invalidates the previous key; it is a separate controlled operation, not an
idempotent way to retrieve a lost result.

Capture the successful response directly into a newly created private
operator file, using exclusive creation, mode0600 and a mode0700 directory
outside repositories, Chaldea inputs and fixtures. Extract result.user_key
inside that private process into an exclusive mode0600 raw-key file. Never put
the value in argv, shell history, environment, task output, a PR or a document.
Use an approved protected transport to the controller if issuance occurs on
homelab. Retain the original response and exact source version under operator
custody. Routine clients get the user key; an operator-only tenant admin may be
needed separately for resource reindexing. The ordinary user probe does not
qualify that administrative action.

## Local wrapper installation and rollback

scripts/ov-client-credentials.py manages only
$HOME/.config/ov-mcp/config.toml. It adds/replaces top-level api_key, preserving
other parsed TOML values and remaining text. Existing simple assignments are
supported; unsupported TOML editing refuses. It creates a missing config when
needed. The new credential overrides keyring and fallback files after the MCP
process reloads. It never edits agent registrations, packages or keyrings.

Install requires an explicit host alias, an explicit approved host list,
a bounded generation ID and a protected raw-key source. Default behavior is
dry-run. --apply is required for writes. --source checks a mode0600 regular
single-link file owned by the executing user, safe ancestors and no repository
ancestor. The playbook validates that protected source on its controller,
then passes the key through a no_log command's stdin. The remote executing user
must be the intended client owner. No become or arbitrary HOME/path override
is supported.

The tool keeps an exclusive mode0600 backup in the mode0700 directory
$HOME/.config/ov-mcp/credential-rollovers/<generation>.json. It contains exact
prior bytes/mode or absence, the selected new bytes and the host/generation
identity. Those bytes contain secrets. The backup must stay private. Files
with symlinks, hardlinks, unsafe ownership/permissions or unexpected changes
are refused. A per-user advisory lock serializes this tool; atomic replacement
and file/directory fsync protect writes. Independent same-user writers must
remain quiescent during application. A generation cannot be reused for changed
input. Rerunning after a backup-only interruption can finish the same update
or restore the original state. A partial/corrupt backup refuses and needs
operator inspection.

Create an absolute standalone inventory outside this repository containing
exactly the approved aliases, with each host's existing SSH identity and
correct remote user. Include the current host as a distinct local-connection
alias if approved. The playbook checks full inventory membership, even with
--limit. Do not include extra fleet hosts merely because they are reachable.

Create a protected nonsecret variables file outside the repository:

```yaml
ov_client_enabled: true
ov_client_hosts: [current-host, homelab, normandy, gaming-pc]
ov_client_generation: approved-cutover-generation
ov_client_action: install
ov_client_key_source: /absolute/protected/new-production-user-key
```

That example omits agent-a and agent-b pending the root's authority decision.
If they are approved, add both exact aliases to inventory and ov_client_hosts.
A partial rollout leaves their old ROOT credential unusable on OV 0.5.2.

```sh
# Dry-run; also safe with --check. Neither creates backups nor changes clients.
ansible-playbook -i /absolute/protected/ov-clients.ini \
  playbooks/ov-client-credentials.yml -e @/absolute/protected/ov-client-vars.yml --check

# Later, only under root's authorized activation:
ansible-playbook -i /absolute/protected/ov-clients.ini \
  playbooks/ov-client-credentials.yml -e @/absolute/protected/ov-client-vars.yml \
  -e ov_client_apply=true

# Preview restoration of the exact prior file/mode or absence:
ansible-playbook -i /absolute/protected/ov-clients.ini \
  playbooks/ov-client-credentials.yml -e @/absolute/protected/ov-client-vars.yml \
  -e ov_client_action=restore

# Applying restoration additionally needs ov_client_apply=true.
```

TOML is cached at module import as _CFG. Both installation and restoration
therefore require the root to close/respawn every affected local ov-memory MCP
process through its owning application, including already open sessions.
An existing process does not pick up a new TOML key. Fresh environment
OV_API_KEY overrides TOML; OV_CONFIG can select another file. Recheck the
registrations and live process metadata before activation. Stop if such a
binding is found; this tool deliberately does not change it.
The fallback file and keyring are queried per request, but updating them alone
cannot override an existing cached TOML key. Do not claim same-process TOML
rollback. Verify using an actual MCP request after each controlled reload.

Prior keyring/fallback files remain untouched to preserve the existing rollback
path. After acceptance and the rollback window, root must explicitly retire
old ROOT-bearing client copies and protect retained operator backups. File
restoration is configuration rollback, not credential revocation.

## Existing service owners

For ov-http, reuse playbooks/ov-http.yml and docs/ov-http-compose.md with a fresh
unique release ID, unchanged reviewed image/config where appropriate and the
new protected controller key source. Stage first, then use its explicit
activation gate. Never replace backend-key in the current immutable release
or reuse an ID with changed inputs. Runtime create_app reads config.secret()
once into a persistent httpx client. The new release must recreate that service
to load its key. Rollback selects the exact previous image, release, config and
credential through the same owner; a file edit cannot reload the running client.

For dsh, use its existing encrypted secrets/homelab-dsh.sops.yaml source and
normal scoped Compose deployment, playbooks/site.yml --limit homelab -e only=dsh.
The existing roles/compose_stack owner loads the SOPS values on the controller,
writes the private .env and converges that selected Compose stack.
Retain the original encrypted secret version
and private .env under operator custody. Change only OV_API_KEY through that
owner. The running container environment and boot-written
/root/.openviking/ovcli.conf stay unchanged until a controlled recreation.
Restore both the managed secret source and deployed environment through that
owner and recreate for rollback. This task adds no duplicate dsh updater and
performs none of those effects.

## Cutover order and acceptance

1. Root resolves the full client host scope, retains original backend cold
   data/config, current reader release, dsh source/environment and private client
   backups, and coordinates a bounded write outage.
2. Switch the backend under the adopted provider-upgrade runbook. The old root
   bindings will fail tenant data calls on 0.5.2 until rollover completes.
   Do not assume 0.2.6 supports the new tenant key or production enrollment.
3. Explicitly issue/export the fresh production user key through the supported
   0.5.2 admin API. Keep the backend ROOT and operator admin credentials private.
4. Dry-run then apply only the approved user configurations. Use the existing
   dsh and ov-http owners for their source updates and gated recreations.
   Root controls all MCP process reloads and service effects.
5. Verify real read/search requests and a separately authorized synthetic
   write/read/cleanup through the actual clients, with no real note mutation.
   Include existing open MCP sessions after their reload and both direct REST
   and public read-MCP paths. Semantic ingestion and vector reindex acceptance
   belong to the provider upgrade's separate checks.

Rollback requires another coordinated outage: stop writes, restore the exact
original 0.2.6 backend data/config from its retained backup, restore the old
client TOML/key sources, previous ov-http immutable release and dsh managed
secret/environment, and reload/recreate affected consumers through their
owners. Verify old credential paths against the old backend. Do not point a
new tenant key at 0.2.6 and call that rollback, or reopen 0.5.2-mutated data with
the old server. Root decides sequencing within the outage and handles any
writes accepted since cutover; this tool does not roll back server data.

## Source verification

Run scripts/test-ov-client-credentials.py only in Chaldea on agent-a, with
HOME equal to OV_CLIENT_TEST_HOME, a disposable disk directory containing
.cache/ov-selfhost-20261024 in its path, and TMPDIR beneath that HOME.
The suite asserts those boundaries before writes. It covers secret-update
failure boundaries, interrupted backup recovery, exact restoration, scope
refusals, source/destination links and permissions, private errors, and the
actual Ansible preview/apply/idempotence/restore flow against a local fixture.
Install pinned ansible-core==2.19.13 and PyYAML==6.0.3 inside the job scratch.
These fixture checks grant no production rollout or reload authority.
