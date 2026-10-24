# OV client credential cutover

This is preparation for the OV 0.2.6 to 0.5.2 upgrade. The new playbook is
standalone and never imported by a normal converge. It changes only the
executing user's existing live keyring item or keyfile when explicitly applied.
It does not enroll users, change backend authentication, restart clients or
services, or deploy OV.
Root orchestration owns those effects and their authorization.

## Read-only binding inventory, 2026-10-24

Secret comparisons used constant-time byte comparison inside private processes.
No credential or short-key hash was printed. Every inspected key below matched
the current production ROOT key. This is the observed scope, not authorization
to write to every host.

| Host / service | Active or retained credential binding | Endpoint / owner |
| --- | --- | --- |
| Current host, omarchy-macbook-air-igor, user igor | GNOME keyring service=openviking, key=api; /home/igor/.config/ov-mcp/config.toml has no api_key | Running local MCP processes use https://ov.ryzhkov.dev. Claude, Codex and OpenCode registrations have OV_BASE_URL and OV_AGENT, no OV_API_KEY. Agent-tools supplies the wrapper; this standalone playbook selects its existing keyring item. |
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

## Existing live wrapper sources and rollback

The installed wrapper's _api_key() checks environment OV_API_KEY and cached
_CFG.api_key first. Without either override, every request invokes
secret-tool lookup service openviking key api, then falls back to
$HOME/.config/ov-mcp/key. The observed current-host keyring and headless
keyfiles therefore take effect on the next request in an already running MCP
process. Installation and restoration of these sources require no MCP restart.

scripts/ov-client-credentials.py requires an explicit backend, keyring or
keyfile. There is no TOML configuration mode. It refuses OV_API_KEY or
OV_CONFIG in its environment and any top-level api_key in the existing default
config.toml, including an empty assignment. It never edits configuration or
registrations. Root must recheck actual running-process environments, original
startup configuration and registrations before activation. The updater's
environment cannot prove that another process has no cached override. If one
exists, stop and resolve that concrete binding through its owner.

Use keyring only on omarchy-macbook-air-igor as user igor, in that user's
existing unlocked GNOME Secret Service session. The system python3 needs
PyGObject and the Secret-1 typelib. The backend calls libsecret directly,
searching all collections for service=openviking/key=api without an unlock
flag. It requires exactly one matching unlocked item, loads its text secret,
and updates that same item's secret. Its object path, attributes and label are
recorded and checked. There is no store/create operation or fallback to a
keyfile. Missing dependencies, an unavailable session, a locked item, ambiguity
or a library error refuse. Existing session access is a prerequisite; this
playbook does not arrange a session bus or unlock a keyring.
The [libsecret search API](https://gnome.pages.gitlab.gnome.org/libsecret/method.Service.search_sync.html)
documents that ALL returns every match and that unlocking requires UNLOCK,
which this updater never requests.

Use keyfile for homelab as user iryzhkov and Normandy/gaming-pc as user igor.
Agent-a and agent-b would use keyfile as user igor only after root resolves
their additional scope. This backend requires the existing
$HOME/.config/ov-mcp/key, owned by that user, regular, single-link and mode0600.
It never creates a missing keyfile. Root must retain the observed absence of a
successful keyring lookup on these headless bindings, since a working keyring
would precede the file in the wrapper. Selection is explicit; the tool does
not discover or change another store to force uniformity.

Install requires an explicit host alias, approved host list, bounded generation
ID and protected raw-key source. Default behavior is dry-run; --apply enables
writes. --source checks a mode0600 regular single-link file owned by the
executing user, safe ancestors and no repository ancestor. The playbook
validates that controller source, then passes the value through a no_log
command's stdin. Libsecret receives the value in process memory. Neither
backend passes secrets through argv, environment or output. The remote user
must be the intended client owner. No become or arbitrary HOME/path override
is supported.

An exclusive mode0600 backup is retained under the mode0700 directory
$HOME/.config/ov-mcp/credential-rollovers/<generation>.json, outside repositories.
Schema version 2 records exact old and selected new secret bytes, backend,
binding identity, host and generation. Existing keyfile mode0600 is required
and restored. Config bytes remain untouched. Keyring attributes and label
remain unchanged. Backups contain secrets and must stay private.

The version-1 tools backed up TOML rather than these live sources. Version-1 or
unrecognized records explicitly refuse; there is no automatic migration or
claim that a prior TOML override was removed. None were used in production.
If such a record or override is found, root must inspect and restore its actual
state through the version-appropriate owner before selecting a new generation.

Symlinks, hardlinks, unsafe ownership/modes/ancestors, changed binding identity
and unexpected secret changes refuse. A per-user advisory lock serializes this
tool, and it rereads the selected source immediately before writing. Keyfile
replacement uses an exclusive private temporary file, atomic rename and
file/directory fsync. Keyring writes use the existing item's set-secret call.
Both are read back before reporting success. Libsecret and independent file
writers provide no compare-and-swap transaction with this tool. Other writers
must remain quiescent across the final check and update. A generation cannot
be reused for a different key.

Restore requires the matching backend, host and generation record, and a live
source still equal to that generation's selected new bytes or its exact old
bytes. It refuses to clobber a user's different secret or a replaced keyring
item. Rerunning after a backup-only interruption can finish installation or
leave the original source unchanged on restore. A failure after a keyring
write may be uncertain; retain the backup and inspect the live source before
retrying. Partial/corrupt backups refuse. Restoration changes client custody;
it does not revoke a credential or restore backend data.

Create an absolute standalone inventory outside this repository with exactly
the approved aliases and existing SSH identities. Set ov_client_backend per
host, for example:

```ini
current-host ansible_connection=local ov_client_backend=keyring
homelab ansible_user=iryzhkov ov_client_backend=keyfile
normandy ansible_user=igor ov_client_backend=keyfile
gaming-pc ansible_user=igor ov_client_backend=keyfile
```

Use the actual existing SSH addresses or inventory aliases. The current-host
alias refers only to omarchy-macbook-air-igor, user igor. This example omits
agent-a/b pending root's decision. The playbook checks full inventory membership
even with --limit. Do not add hosts merely because they are reachable.

Create a protected nonsecret variables file outside the repository:

```yaml
ov_client_enabled: true
ov_client_hosts: [current-host, homelab, normandy, gaming-pc]
ov_client_generation: approved-cutover-generation
ov_client_action: install
ov_client_key_source: /absolute/protected/new-production-user-key
```

```sh
# Preview only. --check also prevents effects even if ov_client_apply is true.
ansible-playbook -i /absolute/protected/ov-clients.ini \
  playbooks/ov-client-credentials.yml -e @/absolute/protected/ov-client-vars.yml --check

# Later, only within root's authorized activation.
ansible-playbook -i /absolute/protected/ov-clients.ini \
  playbooks/ov-client-credentials.yml -e @/absolute/protected/ov-client-vars.yml \
  -e ov_client_apply=true

# Preview exact restoration. Applying also requires ov_client_apply=true.
ansible-playbook -i /absolute/protected/ov-clients.ini \
  playbooks/ov-client-credentials.yml -e @/absolute/protected/ov-client-vars.yml \
  -e ov_client_action=restore
```

Retain approved operator backups through the rollback window. Root owns
retirement of old ROOT-bearing client copies after acceptance. No process
restart is required for these wrapper sources in either direction.

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
4. Before client rollout, use the supported admin user inventory,
   GET /api/v1/admin/accounts/default/users, under protected operator custody.
   Confirm the approved production identity belongs to account default and has
   role=user. Retain a nonsecret record of that identity and role. Stop if absent,
   ambiguous or privileged. The helper copies opaque key bytes and cannot enforce
   role=user; issuance and this inventory check enforce it. Never inspect private
   key databases or infer the role from a successful data request.
5. Dry-run then apply only the approved existing keyring/keyfile sources. Use
   the existing dsh and ov-http owners for source updates and gated recreations.
   Root controls service effects through the existing service owners.
6. Verify real read/search requests and a separately authorized synthetic
   write/read/cleanup through the actual clients, with no real note mutation.
   Include existing open MCP sessions without restarting them, direct REST
   and public read-MCP paths. Semantic ingestion and vector reindex acceptance
   belong to the provider upgrade's separate checks.

Rollback requires another coordinated outage: stop writes, restore the exact
original 0.2.6 backend data/config from its retained backup, restore the old
client keyring/keyfile sources, previous ov-http immutable release and dsh managed
secret/environment, and recreate the service consumers through their owners.
The existing wrapper sources reload per request during rollback too. Verify
old credential paths against the old backend. Do not point a new tenant key at 0.2.6 and call that rollback, or reopen 0.5.2-mutated data with
the old server. Root decides sequencing within the outage and handles any
writes accepted since cutover; this tool does not roll back server data.

## Source verification

Run scripts/test-ov-client-credentials.py only in Chaldea on agent-a, with
HOME equal to OV_CLIENT_TEST_HOME, a disposable disk directory containing
.cache/ov-selfhost-20261024 in its path, and TMPDIR beneath that HOME.
The suite asserts those boundaries before writes. It covers secret-update
failure boundaries, interrupted backup recovery, exact restoration, scope and
override refusals, source/destination links and permissions, private errors,
version-1 backup refusal, and actual Ansible keyfile preview/apply/idempotence/
restore/failure against a local fixture. A recording synthetic libsecret API
exercises keyring calls, locked/missing/ambiguous service refusals and changed
item protection without touching a real user keyring. A single running
synthetic wrapper process reads changed keyfile bytes on successive requests.
This models the inspected installed lookup logic; live MCP acceptance remains
root's production cutover check.
Install pinned ansible-core==2.19.13 and PyYAML==6.0.3 inside the job scratch.
These fixture checks grant no production rollover or rollout authority.
